"""Short PostgreSQL transactions for claiming tasks and recording attempt results."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from notification_service.config import Settings
from notification_service.delivery import DeliveryResult, retry_delay
from notification_service.models import DeliveryAttempt, Notification


@dataclass(frozen=True)
class ClaimedTask:
    id: UUID
    round: int
    attempt: int
    token: UUID
    url: str
    method: str
    headers: dict[str, str]
    body: bytes | None


def claim_task(engine: Engine, settings: Settings) -> ClaimedTask | None:
    with Session(engine) as session, session.begin():
        task = session.scalars(
            select(Notification)
            .where(
                Notification.status.in_(("pending", "retry_wait")),
                Notification.next_attempt_at <= func.now(),
            )
            .order_by(Notification.next_attempt_at, Notification.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).one_or_none()
        if task is None:
            return None
        if task.attempt_count >= settings.max_attempts:
            # A lower configured limit must not leave already queued tasks stuck forever.
            task.status = "failed"
            task.next_attempt_at = None
            task.last_error_category = "attempt_limit"
            return None
        now = session.scalar(select(func.clock_timestamp()))
        task.status = "in_progress"
        task.attempt_count += 1
        task.next_attempt_at = None
        task.lease_token = uuid4()
        task.lease_expires_at = now + timedelta(seconds=settings.lease_seconds)
        session.add(
            DeliveryAttempt(
                notification_id=task.id,
                round=task.round,
                attempt_number=task.attempt_count,
                lease_token=task.lease_token,
                started_at=now,
            )
        )
        claimed = ClaimedTask(
            task.id,
            task.round,
            task.attempt_count,
            task.lease_token,
            task.url,
            task.method,
            dict(task.headers),
            task.body,
        )
    return claimed


def finish_attempt(
    engine: Engine, settings: Settings, claimed: ClaimedTask, result: DeliveryResult
) -> str | None:
    with Session(engine) as session, session.begin():
        task = session.scalars(
            select(Notification)
            .where(
                Notification.id == claimed.id,
                Notification.status == "in_progress",
                Notification.lease_token == claimed.token,
            )
            .with_for_update()
        ).one_or_none()
        if task is None:
            return None
        now = session.scalar(select(func.clock_timestamp()))
        if task.lease_expires_at <= now:
            return None
        attempt = session.scalars(
            select(DeliveryAttempt).where(
                DeliveryAttempt.notification_id == claimed.id,
                DeliveryAttempt.round == claimed.round,
                DeliveryAttempt.attempt_number == claimed.attempt,
                DeliveryAttempt.lease_token == claimed.token,
            )
        ).one()
        attempt.finished_at = now
        attempt.outcome = result.outcome
        attempt.status_code = result.status_code
        attempt.error_category = result.error_category
        task.last_status_code = result.status_code
        task.last_error_category = result.error_category
        task.lease_token = None
        task.lease_expires_at = None
        task.updated_at = now
        if result.outcome == "succeeded":
            task.status = "succeeded"
        elif result.outcome == "retryable_failure" and task.attempt_count < settings.max_attempts:
            task.status = "retry_wait"
            task.next_attempt_at = now + timedelta(
                seconds=retry_delay(settings, task.attempt_count, result.retry_after, now)
            )
        else:
            task.status = "failed"
        status = task.status
    return status
