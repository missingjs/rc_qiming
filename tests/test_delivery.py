"""HTTP transport behavior and deterministic retry policy checks."""

import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from notification_service.config import Settings
from notification_service.delivery import classify_status, deliver, retry_after_seconds, retry_delay
from notification_service.mock_provider import create_app


@pytest.mark.parametrize(
    "code,outcome",
    [
        (200, "succeeded"),
        (204, "succeeded"),
        (299, "succeeded"),
        (301, "permanent_failure"),
        (400, "permanent_failure"),
        (401, "permanent_failure"),
        (408, "retryable_failure"),
        (429, "retryable_failure"),
        (500, "retryable_failure"),
        (503, "retryable_failure"),
        (599, "retryable_failure"),
    ],
)
def test_status_classification(code, outcome):
    result = classify_status(code, "30")
    assert result.outcome == outcome
    assert result.retry_after == ("30" if code in {429, 503} else None)
    assert result.error_category == (None if outcome == "succeeded" else "http_error")


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, 0),
        ("0", 0),
        (" 20 ", 20),
        ("-1", 0),
        ("1.5", 0),
        ("junk", 0),
        ("NaN", 0),
        ("inf", 0),
        ("Wed, 21 Oct 2015 07:28:00 GMT", 0),
        ("Thu, 01 Jan 2026 00:00:10 GMT", 10),
        ("Thu, 01 Jan 2026 00:00:10", 0),
    ],
)
def test_retry_after(value, expected):
    assert retry_after_seconds(value, datetime(2026, 1, 1, tzinfo=UTC)) == expected


def test_bounded_jitter_and_retry_after(monkeypatch):
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://unused/db")
    now = datetime.now(UTC).replace(microsecond=0)
    monkeypatch.setattr("notification_service.delivery.random.uniform", lambda a, b: a)
    assert [retry_delay(settings, n, None, now) for n in range(1, 5)] == [4, 8, 16, 32]
    monkeypatch.setattr("notification_service.delivery.random.uniform", lambda a, b: b)
    assert [retry_delay(settings, n, None, now) for n in range(1, 5)] == [6, 12, 24, 48]
    assert retry_delay(settings, 1, "20", now) == 20
    assert retry_delay(settings, 1, format_datetime(now + timedelta(seconds=30)), now) == 30
    assert retry_delay(settings, 1, "9" * 5000, now) == 60
    assert retry_delay(settings, 10000, None, now) == 60


@pytest.mark.parametrize(
    "error,category,outcome",
    [
        (httpx.ConnectError("secret"), "network_error", "retryable_failure"),
        (httpx.ReadTimeout("secret"), "timeout", "retryable_failure"),
        (httpx.LocalProtocolError("secret"), "invalid_request", "permanent_failure"),
    ],
)
def test_transport_errors_are_sanitized(error, category, outcome):
    async def handler(request):
        raise error

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await deliver(
                client,
                method="POST",
                url="https://example.test?secret=value",
                headers={},
                body=None,
                timeout=1,
            )
            assert result.error_category == category
            assert result.outcome == outcome
            assert "secret" not in repr(result)

    asyncio.run(check())


def test_total_deadline():
    async def handler(request):
        # MockTransport has no phase timeouts; only the outer deadline can stop this.
        await asyncio.sleep(10)
        raise AssertionError("Delivery deadline was not enforced")

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await asyncio.wait_for(
                deliver(
                    client,
                    method="GET",
                    url="https://example.test",
                    headers={},
                    body=None,
                    timeout=0.02,
                ),
                timeout=1,
            )
            assert result.error_category == "timeout"

    asyncio.run(check())


def test_response_body_is_not_read_redirects_and_cookies_are_not_followed():
    class UnreadBody(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            raise AssertionError("Provider body must not be read")
            yield b"unreachable"

        async def aclose(self):
            self.closed = True

    bodies = []
    requests = []

    async def handler(request):
        requests.append(request)
        assert "cookie" not in request.headers
        stream = UnreadBody()
        bodies.append(stream)
        return httpx.Response(
            302, headers={"Location": "/secret", "Set-Cookie": "token=secret"}, stream=stream
        )

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            client.cookies.set("old", "secret")
            for _ in range(2):
                result = await deliver(
                    client,
                    method="GET",
                    url="https://example.test",
                    headers={},
                    body=None,
                    timeout=1,
                )
                assert result.outcome == "permanent_failure"
            assert len(requests) == 2
            assert all(body.closed for body in bodies)

    asyncio.run(check())


def test_mock_provider_scenarios():
    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://mock.test"
        ) as client:
            assert (await client.get("/health")).status_code == 200
            assert (await client.post("/success/one")).status_code == 204
            assert [(await client.post("/flaky/two")).status_code for _ in range(3)] == [
                503,
                503,
                204,
            ]
            assert (await client.post("/permanent/three")).status_code == 400
            assert (await client.post("/unavailable/four")).status_code == 503
            result = await deliver(
                client,
                method="POST",
                url="http://mock.test/timeout/five?delay=10",
                headers={},
                body=None,
                timeout=0.02,
            )
            assert result.error_category == "timeout"
            assert (await client.get("/counts/flaky/two")).json() == {"count": 3}

    asyncio.run(check())
