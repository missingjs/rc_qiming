"""Validate and persist immutable outbound requests."""

import hashlib
import json
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, JsonValue, TypeAdapter, model_validator
from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from notification_service.models import Notification

MANAGED_HEADERS = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")


def json_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    url: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    json_body: JsonValue = None
    text_body: str | None = None

    @model_validator(mode="after")
    def validate_request(self):
        # Validate syntax without rewriting the URL used for delivery or comparison.
        TypeAdapter(HttpUrl).validate_python(self.url)
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or "\\" in self.url:
            raise ValueError("A complete HTTP(S) URL is required")
        if any(ord(char) <= 32 or ord(char) == 127 for char in self.url):
            raise ValueError("URL contains whitespace or control characters")
        if {"json_body", "text_body"} <= self.model_fields_set:
            raise ValueError("Only one body field is allowed")
        if "text_body" in self.model_fields_set and self.text_body is None:
            raise ValueError("Text body must be a string")
        names = set()
        for name, value in self.headers.items():
            normalized = name.lower()
            if not HEADER_NAME.fullmatch(name) or normalized in MANAGED_HEADERS:
                raise ValueError("Invalid or client-managed header")
            if normalized in names:
                raise ValueError("Duplicate header name")
            names.add(normalized)
            if any(ord(char) < 32 or ord(char) > 126 for char in value):
                raise ValueError("Header values must contain printable ASCII only")
        # Reject non-finite numbers and unpaired surrogates before persistence.
        try:
            json_bytes(self.model_dump())
        except ValueError, UnicodeError:
            raise ValueError("Request must contain valid UTF-8 and finite JSON numbers") from None
        return self

    def persisted_request(self) -> dict:
        headers = {name.lower(): value for name, value in self.headers.items()}
        body_type, body = "none", None
        if "json_body" in self.model_fields_set:
            body_type, body = "json", json_bytes(self.json_body)
            headers["content-type"] = "application/json"
        elif "text_body" in self.model_fields_set:
            body_type, body = "text", self.text_body.encode("utf-8")
            headers.setdefault("content-type", "text/plain; charset=utf-8")
        # Compare supplied headers, including explicit content-type, per the API contract.
        canonical = json_bytes(
            {
                "url": self.url,
                "method": self.method,
                "headers": {name.lower(): value for name, value in self.headers.items()},
                "body_type": body_type,
                "body": body.decode("utf-8") if body is not None else None,
            }
        )
        return dict(
            url=self.url,
            method=self.method,
            headers=headers,
            body_type=body_type,
            body=body,
            request_digest=hashlib.sha256(canonical).hexdigest(),
        )


class IdempotencyConflict(Exception):
    pass


def submit(engine: Engine, request: Submission, key: str | None) -> dict:
    values = request.persisted_request()
    with Session(engine) as session, session.begin():
        statement = insert(Notification).values(
            **values, idempotency_key=key, next_attempt_at=func.now()
        )
        if key is not None:
            statement = statement.on_conflict_do_nothing(
                constraint="uq_notifications_idempotency_key"
            )
        task = session.scalars(statement.returning(Notification)).one_or_none()
        if task is None:
            # READ COMMITTED gives this statement a new snapshot after the unique-key wait.
            task = session.scalars(
                select(Notification).where(Notification.idempotency_key == key)
            ).one()
            if task.request_digest != values["request_digest"]:
                raise IdempotencyConflict
        result = {
            "id": str(task.id),
            "status": task.status,
            "status_url": f"/notifications/{task.id}",
        }
    # The transaction context has successfully committed before acceptance is returned.
    return result
