"""HTTP delivery and bounded retry policy without provider-specific interpretation."""

import asyncio
import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from notification_service.config import Settings


@dataclass(frozen=True)
class DeliveryResult:
    outcome: str
    status_code: int | None = None
    error_category: str | None = None
    retry_after: str | None = None


def classify_status(status_code: int, retry_after: str | None = None) -> DeliveryResult:
    if 200 <= status_code < 300:
        return DeliveryResult("succeeded", status_code)
    retryable = status_code in {408, 429} or 500 <= status_code < 600
    return DeliveryResult(
        "retryable_failure" if retryable else "permanent_failure",
        status_code,
        "http_error",
        retry_after if status_code in {429, 503} else None,
    )


def retry_after_seconds(value: str | None, now: datetime) -> float:
    if value is None:
        return 0
    value = value.strip()
    if value.isascii() and value.isdigit():
        # Float avoids integer conversion limits on untrusted, very long headers.
        return float(value)
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            return 0
        return max(0, (date.astimezone(UTC) - now).total_seconds())
    except ValueError, TypeError, OverflowError:
        return 0


def retry_delay(settings: Settings, attempt: int, retry_after: str | None, now: datetime) -> float:
    # Bound the exponent before calculating it, including with unusually large attempt limits.
    exponent = min(max(0, attempt - 1), 1023)
    try:
        base = math.ldexp(settings.retry_base_seconds, exponent)
    except OverflowError:
        base = math.inf
    local_delay = base * random.uniform(0.8, 1.2)
    return min(settings.retry_max_seconds, max(local_delay, retry_after_seconds(retry_after, now)))


async def deliver(
    client: httpx.AsyncClient,
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> DeliveryResult:
    try:
        async with asyncio.timeout(timeout):
            # Direct requests avoid merging cookies from previous provider responses.
            request = httpx.Request(method, url, headers=headers, content=body)
            request.extensions["timeout"] = httpx.Timeout(timeout).as_dict()
            response = await client.send(request, stream=True, follow_redirects=False)
            try:
                return classify_status(response.status_code, response.headers.get("retry-after"))
            finally:
                await response.aclose()
    except TimeoutError, httpx.TimeoutException:
        return DeliveryResult("retryable_failure", error_category="timeout")
    except httpx.InvalidURL, httpx.UnsupportedProtocol, httpx.LocalProtocolError:
        return DeliveryResult("permanent_failure", error_category="invalid_request")
    except httpx.RequestError:
        return DeliveryResult("retryable_failure", error_category="network_error")
    finally:
        # The sequential worker does not need a cross-request cookie jar.
        client.cookies.clear()
