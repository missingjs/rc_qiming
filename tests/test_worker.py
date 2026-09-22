"""Real PostgreSQL delivery transactions with controlled provider transports."""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from notification_service.delivery import DeliveryResult
from notification_service.models import DeliveryAttempt, Notification
from notification_service.queue import claim_task, finish_attempt
from notification_service.submissions import Submission, submit
from notification_service.worker import process_one

pytestmark = pytest.mark.integration


def enqueue(engine, **fields):
    return UUID(
        submit(
            engine,
            Submission(url="https://example.test/path?token=secret", **fields),
            "submission-secret",
        )["id"]
    )


def make_due(engine, task_id):
    with engine.begin() as connection:
        connection.execute(
            update(Notification)
            .where(Notification.id == task_id)
            .values(next_attempt_at=func.now())
        )


def process(engine, settings, handler):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await process_one(engine, settings, client)

    return asyncio.run(run())


@pytest.mark.parametrize(
    "method,body_fields,expected_body,content_type",
    [
        ("GET", {}, b"", None),
        ("POST", {"json_body": None}, b"null", "application/json"),
        ("PUT", {"json_body": {"event": "你好"}}, '{"event":"你好"}'.encode(), "application/json"),
        ("PATCH", {"text_body": "你好"}, "你好".encode(), "text/plain; charset=utf-8"),
        ("DELETE", {"text_body": ""}, b"", "text/plain; charset=utf-8"),
    ],
)
def test_forwarding_and_short_transactions(
    migrated_database, caplog, method, body_fields, expected_body, content_type
):
    engine, _, settings = migrated_database
    task_id = enqueue(engine, method=method, headers={"X-Token": "secret"}, **body_fields)
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == method
        assert str(request.url) == "https://example.test/path?token=secret"
        assert request.headers["x-token"] == "secret"
        assert "idempotency-key" not in request.headers
        assert request.content == expected_body
        assert request.headers.get("content-type") == content_type
        # Another connection can lock the task during delivery: the claim has committed.
        with Session(engine) as session, session.begin():
            task = session.scalars(
                select(Notification).where(Notification.id == task_id).with_for_update(nowait=True)
            ).one()
            assert task.status == "in_progress" and task.attempt_count == 1
            attempt = session.scalars(select(DeliveryAttempt)).one()
            assert attempt.lease_token == task.lease_token and attempt.finished_at is None
        return httpx.Response(204)

    caplog.set_level(logging.INFO, logger="notification_service.worker")
    assert process(engine, settings, handler)
    assert not process(engine, settings, handler)
    assert len(calls) == 1
    with Session(engine) as session:
        task = session.get(Notification, task_id)
        assert task.status == "succeeded" and task.attempt_count == 1
        assert task.lease_token is None and task.lease_expires_at is None
        assert task.next_attempt_at is None and task.last_status_code == 204
        attempt = session.scalars(select(DeliveryAttempt)).one()
        assert attempt.outcome == "succeeded" and attempt.finished_at >= attempt.started_at
    records = [
        json.loads(r.message) for r in caplog.records if r.name == "notification_service.worker"
    ]
    assert [r["event"] for r in records] == ["attempt_started", "attempt_finished"]
    assert records[-1]["duration_ms"] >= 0
    assert "secret" not in json.dumps(records)


@pytest.mark.parametrize(
    "codes,expected",
    [
        ([503, 503, 200], "succeeded"),
        ([429, 204], "succeeded"),
        ([408, 204], "succeeded"),
        ([400], "failed"),
        ([302], "failed"),
        ([503, 503, 503], "failed"),
    ],
)
def test_retries_and_attempt_limits(migrated_database, codes, expected):
    engine, _, settings = migrated_database
    settings = settings.model_copy(update={"max_attempts": 3})
    task_id = enqueue(engine, json_body={"event": "secret"})
    for index, code in enumerate(codes, start=1):
        assert process(
            engine, settings, lambda request: httpx.Response(code, headers={"Retry-After": "30"})
        )
        with Session(engine) as session:
            task = session.get(Notification, task_id)
            assert task.attempt_count == index
            attempts = session.scalars(
                select(DeliveryAttempt).order_by(DeliveryAttempt.attempt_number)
            ).all()
            assert len(attempts) == index
            assert len({a.lease_token for a in attempts}) == index
            assert all(a.finished_at is not None for a in attempts)
            assert attempts[-1].status_code == code
            if index < len(codes):
                assert task.status == "retry_wait"
                delay = (task.next_attempt_at - task.updated_at).total_seconds()
                assert 0 < delay <= 60
                if code in {429, 503}:
                    assert delay >= 30
            else:
                assert task.status == expected
                assert task.next_attempt_at is None
        assert not process(engine, settings, lambda request: pytest.fail("Unexpected HTTP request"))
        if index < len(codes):
            make_due(engine, task_id)


@pytest.mark.parametrize(
    "error,category",
    [(httpx.ConnectError("secret"), "network_error"), (httpx.ReadTimeout("secret"), "timeout")],
)
def test_network_errors_are_recorded_and_exhausted(migrated_database, error, category):
    engine, _, settings = migrated_database
    settings = settings.model_copy(update={"max_attempts": 1})
    task_id = enqueue(engine)

    def handler(request):
        raise error

    assert process(engine, settings, handler)
    with Session(engine) as session:
        task = session.get(Notification, task_id)
        assert task.status == "failed" and task.last_error_category == category
        attempt = session.scalars(select(DeliveryAttempt)).one()
        assert attempt.outcome == "retryable_failure" and attempt.error_category == category
        assert attempt.status_code is None


def test_concurrent_claims_and_result_token_guard(migrated_database):
    engine, _, settings = migrated_database
    task_id = enqueue(engine)
    barrier = Barrier(4)

    def claim(_):
        barrier.wait(timeout=10)
        return claim_task(engine, settings)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [result for result in pool.map(claim, range(4)) if result is not None]
    assert len(results) == 1
    claimed = results[0]
    with Session(engine) as session, session.begin():
        task = session.get(Notification, task_id)
        task.lease_token = uuid4()
    assert finish_attempt(engine, settings, claimed, DeliveryResult("succeeded", 200)) is None
    with Session(engine) as session:
        assert session.get(Notification, task_id).status == "in_progress"
        assert session.scalars(select(DeliveryAttempt)).one().finished_at is None


def test_expired_lease_cannot_save_result(migrated_database):
    engine, _, settings = migrated_database
    task_id = enqueue(engine)
    claimed = claim_task(engine, settings)
    with engine.begin() as connection:
        connection.execute(
            update(Notification)
            .where(Notification.id == task_id)
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )
    assert finish_attempt(engine, settings, claimed, DeliveryResult("succeeded", 200)) is None


def test_failed_claim_commit_sends_nothing(migrated_database):
    engine, _, settings = migrated_database
    task_id = enqueue(engine)

    def fail(session):
        raise OperationalError("COMMIT", {}, Exception("secret"))

    event.listen(Session, "before_commit", fail)
    try:
        with pytest.raises(OperationalError):
            process(engine, settings, lambda request: pytest.fail("Uncommitted task was sent"))
    finally:
        event.remove(Session, "before_commit", fail)
    with Session(engine) as session:
        task = session.get(Notification, task_id)
        assert task.status == "pending" and task.attempt_count == 0
        assert session.scalar(select(func.count()).select_from(DeliveryAttempt)) == 0


def test_failed_result_commit_is_not_reported_as_success(migrated_database, caplog):
    engine, _, settings = migrated_database
    task_id = enqueue(engine)

    def fail(session):
        raise OperationalError("COMMIT", {}, Exception("secret"))

    def handler(request):
        event.listen(Session, "before_commit", fail)
        return httpx.Response(200)

    caplog.set_level(logging.INFO, logger="notification_service.worker")
    try:
        with pytest.raises(OperationalError):
            process(engine, settings, handler)
    finally:
        event.remove(Session, "before_commit", fail)
    with Session(engine) as session:
        assert session.get(Notification, task_id).status == "in_progress"
        assert session.scalars(select(DeliveryAttempt)).one().finished_at is None
    assert "outcome_not_saved" in caplog.text
    assert "attempt_finished" not in caplog.text and "secret" not in caplog.text


def test_lowered_attempt_limit_stops_queued_task(migrated_database):
    engine, _, settings = migrated_database
    task_id = enqueue(engine)
    assert process(engine, settings, lambda request: httpx.Response(503))
    make_due(engine, task_id)
    reduced = settings.model_copy(update={"max_attempts": 1})
    assert not process(engine, reduced, lambda request: pytest.fail("Attempt limit exceeded"))
    with Session(engine) as session:
        task = session.get(Notification, task_id)
        assert task.status == "failed" and task.attempt_count == 1
        assert task.last_error_category == "attempt_limit" and task.next_attempt_at is None
        assert session.scalar(select(func.count()).select_from(DeliveryAttempt)) == 1
