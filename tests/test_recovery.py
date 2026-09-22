"""Recovery transactions and replay races against real PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event, current_thread
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from notification_service.api import create_app
from notification_service.delivery import DeliveryResult
from notification_service.models import DeliveryAttempt, Notification
from notification_service.queue import claim_task, finish_attempt
from notification_service.replay import ReplayConflict, replay
from notification_service.submissions import Submission, submit

pytestmark = pytest.mark.integration


def enqueue(engine):
    return UUID(
        submit(
            engine,
            Submission(
                url="http://provider.test/path?token=secret",
                headers={"X-Token": "secret"},
                json_body={"event": "secret"},
            ),
            "recovery:key",
        )["id"]
    )


def expire(engine, task_id):
    with engine.begin() as connection:
        connection.execute(
            update(Notification)
            .where(Notification.id == task_id)
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )


def test_concurrent_recovery_invalidates_stale_results(migrated_database):
    engine, _, settings = migrated_database
    task_id = enqueue(engine)
    old = claim_task(engine, settings)
    assert claim_task(engine, settings) is None
    expire(engine, task_id)
    barrier = Barrier(8)

    def claim(_):
        barrier.wait(timeout=5)
        return claim_task(engine, settings)

    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = [task for task in pool.map(claim, range(8)) if task is not None]
    assert len(claimed) == 1
    new = claimed[0]
    assert new.id == old.id and new.attempt == 2 and new.token != old.token
    assert finish_attempt(engine, settings, old, DeliveryResult("succeeded", 200)) is None
    with Session(engine) as session:
        previous = session.scalars(
            select(DeliveryAttempt).where(DeliveryAttempt.attempt_number == 1)
        ).one()
        assert previous.outcome == "unknown_outcome"
        assert previous.error_category == "unknown_outcome" and previous.status_code is None
        assert previous.finished_at is not None
        assert session.get(Notification, task_id).lease_token == new.token
    assert finish_attempt(engine, settings, new, DeliveryResult("succeeded", 204)) == "succeeded"
    assert finish_attempt(engine, settings, old, DeliveryResult("permanent_failure", 400)) is None
    with Session(engine) as session:
        assert session.get(Notification, task_id).last_status_code == 204


@pytest.mark.parametrize("max_attempts", [1, 3])
def test_repeated_crashes_cannot_exceed_attempt_limit(migrated_database, max_attempts):
    engine, _, settings = migrated_database
    settings = settings.model_copy(update={"max_attempts": max_attempts})
    task_id = enqueue(engine)
    for count in range(1, max_attempts + 1):
        task = claim_task(engine, settings)
        assert task.attempt == count
        expire(engine, task_id)
    for _ in range(3):
        assert claim_task(engine, settings) is None
    with Session(engine) as session:
        task = session.get(Notification, task_id)
        assert task.status == "failed" and task.attempt_count == max_attempts
        assert task.last_error_category == "unknown_outcome"
        assert task.last_status_code is None
        assert task.lease_token is None and task.lease_expires_at is None
        assert task.next_attempt_at is None
        attempts = session.scalars(select(DeliveryAttempt)).all()
        assert len(attempts) == max_attempts
        assert all(a.outcome == "unknown_outcome" and a.finished_at for a in attempts)


def test_recovery_commit_failure_rolls_back_history_and_claim(migrated_database):
    engine, _, settings = migrated_database
    task_id = enqueue(engine)
    old = claim_task(engine, settings)
    expire(engine, task_id)

    def fail(session):
        raise OperationalError("COMMIT", {}, Exception("secret"))

    event.listen(Session, "before_commit", fail)
    try:
        with pytest.raises(OperationalError):
            claim_task(engine, settings)
    finally:
        event.remove(Session, "before_commit", fail)
    with Session(engine) as session:
        task = session.get(Notification, task_id)
        assert task.attempt_count == 1 and task.lease_token == old.token
        attempts = session.scalars(select(DeliveryAttempt)).all()
        assert len(attempts) == 1 and attempts[0].finished_at is None
    assert claim_task(engine, settings).attempt == 2


def test_replay_preserves_request_and_history(migrated_database):
    engine, _, settings = migrated_database
    task_id = enqueue(engine)
    first = claim_task(engine, settings)
    finish_attempt(engine, settings, first, DeliveryResult("permanent_failure", 400, "http_error"))
    with Session(engine) as session:
        task = session.get(Notification, task_id)
        original = (
            task.url,
            task.method,
            task.headers,
            task.body,
            task.body_type,
            task.request_digest,
            task.idempotency_key,
            task.created_at,
        )
        old_attempt = session.scalars(select(DeliveryAttempt)).one()
        history = (old_attempt.id, old_attempt.outcome, old_attempt.finished_at)
    with TestClient(create_app(settings)) as client:
        response = client.post(f"/notifications/{task_id}/replay")
        assert response.status_code == 202
        assert response.json() == {
            "id": str(task_id),
            "status": "pending",
            "status_url": f"/notifications/{task_id}",
        }
        status = client.get(response.headers["Location"]).json()
        assert status["round"] == 2 and status["attempt_count"] == 0
        assert status["latest_attempt"] == {"status_code": 400, "error_category": "http_error"}
        assert "secret" not in str(status)
        assert client.post(f"/notifications/{task_id}/replay").status_code == 409
        assert client.post(f"/notifications/{uuid4()}/replay").status_code == 404
        assert client.post("/notifications/invalid/replay").status_code == 422
    with Session(engine) as session:
        task = session.get(Notification, task_id)
        assert (
            task.url,
            task.method,
            task.headers,
            task.body,
            task.body_type,
            task.request_digest,
            task.idempotency_key,
            task.created_at,
        ) == original
        assert task.next_attempt_at is not None and task.lease_token is None
    second = claim_task(engine, settings)
    assert second.round == 2 and second.attempt == 1 and second.token != first.token
    assert finish_attempt(engine, settings, first, DeliveryResult("succeeded", 200)) is None
    finish_attempt(engine, settings, second, DeliveryResult("succeeded", 204))
    with Session(engine) as session:
        attempts = session.scalars(select(DeliveryAttempt).order_by(DeliveryAttempt.round)).all()
        assert len(attempts) == 2
        assert (attempts[0].id, attempts[0].outcome, attempts[0].finished_at) == history
    with TestClient(create_app(settings)) as client:
        assert client.post(f"/notifications/{task_id}/replay").status_code == 409


@pytest.mark.parametrize("status", ["pending", "retry_wait", "in_progress", "succeeded"])
def test_replay_rejects_nonfailed_states(migrated_database, status):
    engine, _, settings = migrated_database
    task_id = enqueue(engine)
    with engine.begin() as connection:
        connection.execute(
            update(Notification).where(Notification.id == task_id).values(status=status)
        )
    with TestClient(create_app(settings)) as client:
        assert client.post(f"/notifications/{task_id}/replay").status_code == 409


def test_concurrent_api_replays_create_one_round(migrated_database):
    engine, _, settings = migrated_database
    task_id = enqueue(engine)
    first = claim_task(engine, settings)
    finish_attempt(engine, settings, first, DeliveryResult("permanent_failure", 400, "http_error"))
    barrier = Barrier(8)
    with TestClient(create_app(settings)) as client:

        def send(_):
            barrier.wait(timeout=5)
            return client.post(f"/notifications/{task_id}/replay").status_code

        with ThreadPoolExecutor(max_workers=8) as pool:
            codes = list(pool.map(send, range(8)))
    assert codes.count(202) == 1 and codes.count(409) == 7
    with Session(engine) as session:
        task = session.get(Notification, task_id)
        assert task.round == 2 and task.attempt_count == 0 and task.status == "pending"
        assert session.scalar(select(func.count()).select_from(DeliveryAttempt)) == 1


def test_waiting_replay_cannot_consume_newer_failed_round(migrated_database):
    engine, _, settings = migrated_database
    task_id = enqueue(engine)
    first = claim_task(engine, settings)
    finish_attempt(engine, settings, first, DeliveryResult("permanent_failure", 400, "http_error"))
    blocked, release = Event(), Event()

    def pause_update(connection, cursor, statement, parameters, context, executemany):
        if current_thread().name.startswith("old-replay") and statement.startswith("UPDATE"):
            blocked.set()
            assert release.wait(timeout=5)

    event.listen(engine, "before_cursor_execute", pause_update)
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="old-replay") as pool:
            waiting = pool.submit(replay, engine, task_id)
            try:
                assert blocked.wait(timeout=5)
                assert replay(engine, task_id)["status"] == "pending"
                second = claim_task(engine, settings)
                finish_attempt(
                    engine, settings, second, DeliveryResult("permanent_failure", 400, "http_error")
                )
            finally:
                release.set()
            with pytest.raises(ReplayConflict):
                waiting.result(timeout=5)
    finally:
        event.remove(engine, "before_cursor_execute", pause_update)
    with Session(engine) as session:
        task = session.get(Notification, task_id)
        assert task.round == 2 and task.status == "failed"


def test_replay_commit_failure_never_acknowledges(migrated_database):
    engine, _, settings = migrated_database
    task_id = enqueue(engine)
    first = claim_task(engine, settings)
    finish_attempt(engine, settings, first, DeliveryResult("permanent_failure", 400, "http_error"))

    def fail(session):
        raise OperationalError("COMMIT", {}, Exception("secret"))

    with TestClient(create_app(settings)) as client:
        event.listen(Session, "before_commit", fail)
        try:
            response = client.post(f"/notifications/{task_id}/replay")
        finally:
            event.remove(Session, "before_commit", fail)
        assert response.status_code == 503
        assert response.json() == {"detail": "Database unavailable"}
    with Session(engine) as session:
        task = session.get(Notification, task_id)
        assert task.round == 1 and task.status == "failed"
