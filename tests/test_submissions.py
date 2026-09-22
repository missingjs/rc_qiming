"""Submission validation, durable acceptance, and PostgreSQL idempotency races."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from notification_service.api import create_app
from notification_service.config import Settings
from notification_service.models import Notification
from notification_service.submissions import Submission


@pytest.fixture
def offline_client():
    settings = Settings(
        _env_file=None, database_url="postgresql+psycopg://test:test@127.0.0.1:1/unavailable"
    )
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.mark.parametrize(
    "changes",
    [
        {"url": "ftp://example.test"},
        {"url": "not-a-url"},
        {"url": "https:example.test"},
        {"url": "https:///example.test"},
        {"url": "https://example.test\\path"},
        {"url": "https://example.test/\n"},
        {"method": "HEAD"},
        {"method": "post"},
        {"unknown": True},
        {"json_body": None, "text_body": ""},
        {"text_body": None},
        {"text_body": 1},
        {"headers": {"Host": "secret"}},
        {"headers": {"Content-Length": "3"}},
        {"headers": {"Connection": "close"}},
        {"headers": {"TE": "trailers"}},
        {"headers": {"X-Token": "secret", "x-token": "other"}},
        {"headers": {"Bad Name": "secret"}},
        {"headers": {"X-Token": "secret\r\nInjected: yes"}},
        {"headers": {"Authorization": " Bearer secret"}},
        {"headers": {"Authorization": "Bearer secret "}},
        {"headers": {"X-Token": " secret "}},
        {"headers": {"X-Token": " "}},
        {"headers": {"X-Token": 123}},
        {"text_body": "\ud800"},
    ],
)
def test_invalid_submission_is_redacted(offline_client, changes):
    response = offline_client.post(
        "/notifications",
        content=json.dumps({"url": "https://example.test", **changes}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}


@pytest.mark.parametrize("value", ["", "Bearer secret", "one  two"])
def test_valid_header_values_are_preserved(value):
    request = Submission(url="https://example.test", headers={"X-Token": value})
    assert request.persisted_request()["headers"]["x-token"] == value


@pytest.mark.parametrize("key", ["", "has space", "x" * 201])
def test_invalid_key(offline_client, key):
    assert (
        offline_client.post(
            "/notifications", json={"url": "https://example.test"}, headers={"Idempotency-Key": key}
        ).status_code
        == 422
    )


def test_database_failure_and_invalid_query(offline_client):
    for response in (
        offline_client.post("/notifications", json={"url": "https://example.test"}),
        offline_client.get(f"/notifications/{uuid4()}"),
    ):
        assert response.status_code == 503
        assert response.json() == {"detail": "Database unavailable"}
    assert offline_client.get("/notifications/not-a-uuid").status_code == 422
    assert offline_client.post("/notifications", content="{").status_code == 422
    assert (
        offline_client.post(
            "/notifications",
            content='{"url":"https://example.test","json_body":NaN}',
            headers={"Content-Type": "application/json"},
        ).status_code
        == 422
    )
    assert (
        offline_client.post(
            "/notifications",
            json={"url": "https://example.test"},
            headers=[("Idempotency-Key", "one"), ("Idempotency-Key", "two")],
        ).status_code
        == 422
    )


def test_canonical_content_and_body_presence():
    def prepared(**kwargs):
        return Submission(url="https://example.test", **kwargs).persisted_request()

    first = prepared(headers={"X-Token": "secret"}, json_body={"b": 2, "a": "你好"})
    second = prepared(headers={"x-token": "secret"}, json_body={"a": "你好", "b": 2})
    assert first == second
    assert first["body"] == '{"a":"你好","b":2}'.encode()
    assert first["headers"]["content-type"] == "application/json"
    assert (
        len(
            {
                prepared()["request_digest"],
                prepared(json_body=None)["request_digest"],
                prepared(text_body="")["request_digest"],
            }
        )
        == 3
    )
    assert prepared(json_body=None)["body"] == b"null"
    assert prepared()["body"] is None
    assert prepared(text_body="你好")["body"] == "你好".encode()
    assert prepared(text_body="x")["headers"]["content-type"] == "text/plain; charset=utf-8"
    assert (
        prepared(text_body="x", headers={"Content-Type": "custom"})["headers"]["content-type"]
        == "custom"
    )


@pytest.mark.integration
def test_persistence_idempotency_and_safe_status(migrated_database):
    engine, _, settings = migrated_database
    body = {
        "url": "https://example.test?token=secret",
        "headers": {"Authorization": "secret"},
        "json_body": {"b": 2, "a": "secret"},
    }
    with TestClient(create_app(settings)) as client:
        first = client.post("/notifications", json=body, headers={"Idempotency-Key": "test:one"})
        assert first.status_code == 202
        task_id = UUID(first.json()["id"])
        with Session(engine) as session:
            task = session.get(Notification, task_id)
            assert task.body == b'{"a":"secret","b":2}'
            assert task.status == "pending"
            assert task.next_attempt_at is not None
            assert task.attempt_count == 0 and task.round == 1
        response = client.get(first.headers["location"])
        assert response.status_code == 200
        assert set(response.json()) == {
            "id",
            "status",
            "round",
            "attempt_count",
            "created_at",
            "updated_at",
            "next_attempt_at",
            "latest_attempt",
        }
        assert response.json()["latest_attempt"] is None
        assert "secret" not in response.text
        assert client.get(f"/notifications/{uuid4()}").status_code == 404
        equivalent = {
            **body,
            "headers": {"authorization": "secret"},
            "json_body": {"a": "secret", "b": 2},
        }
        duplicate = client.post(
            "/notifications", json=equivalent, headers={"Idempotency-Key": "test:one"}
        )
        assert duplicate.status_code == 202 and duplicate.json() == first.json()
        for change in (
            {"url": "https://example.test/other"},
            {"method": "PUT"},
            {"headers": {}},
            {"json_body": None},
        ):
            assert (
                client.post(
                    "/notifications",
                    json={**body, **change},
                    headers={"Idempotency-Key": "test:one"},
                ).status_code
                == 409
            )
        ids = {client.post("/notifications", json=body).json()["id"] for _ in range(2)}
        assert len(ids) == 2 and str(task_id) not in ids
        with Session(engine) as session, session.begin():
            task = session.get(Notification, task_id)
            task.status = "failed"
            task.last_status_code = 401
            task.last_error_category = "secret provider response"
        status = client.get(first.headers["location"]).json()
        assert status["latest_attempt"] == {"status_code": 401, "error_category": "unknown_error"}
        assert "secret" not in str(status)
        duplicate = client.post(
            "/notifications", json=body, headers={"Idempotency-Key": "test:one"}
        )
        assert duplicate.json()["status"] == "failed"


@pytest.mark.integration
@pytest.mark.parametrize("different_content", [False, True])
def test_concurrent_submissions(migrated_database, different_content):
    engine, _, settings = migrated_database
    barrier = Barrier(8)
    with TestClient(create_app(settings)) as client:

        def send(index):
            barrier.wait(timeout=10)
            return client.post(
                "/notifications",
                json={
                    "url": "https://example.test",
                    "json_body": index if different_content else "same",
                },
                headers={"Idempotency-Key": "race"},
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(send, range(8)))
    accepted = [r for r in responses if r.status_code == 202]
    assert len(accepted) == (1 if different_content else 8)
    assert all(r.status_code in {202, 409} for r in responses)
    assert len({r.json()["id"] for r in accepted}) == 1
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Notification)) == 1


@pytest.mark.integration
def test_commit_failure_never_acknowledges_or_persists(migrated_database):
    engine, _, settings = migrated_database

    def fail_commit(session):
        raise OperationalError("COMMIT", {}, Exception("secret database error"))

    with TestClient(create_app(settings)) as client:
        event.listen(Session, "before_commit", fail_commit)
        try:
            response = client.post("/notifications", json={"url": "https://example.test"})
        finally:
            event.remove(Session, "before_commit", fail_commit)
        assert response.status_code == 503
        assert response.json() == {"detail": "Database unavailable"}
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Notification)) == 0
        assert (
            client.post("/notifications", json={"url": "https://example.test"}).status_code == 202
        )
