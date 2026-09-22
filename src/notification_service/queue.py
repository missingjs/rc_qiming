"""Short PostgreSQL transactions for claiming tasks and recording attempt results."""

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import Engine, and_, func, or_, select
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
    claimed = None
    recovery = None
    with Session(engine) as session, session.begin():
        task = session.scalars(
            select(Notification)
            .where(
                or_(
                    and_(
                        Notification.status.in_(("pending", "retry_wait")),
                        Notification.next_attempt_at <= func.now(),
                    ),
                    and_(
                        Notification.status == "in_progress",
                        Notification.lease_expires_at <= func.now(),
                    ),
                )
            )
            .order_by(
                func.coalesce(Notification.next_attempt_at, Notification.lease_expires_at),
                Notification.id,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        ).one_or_none()
        if task is None:
            return None
        now = session.scalar(select(func.clock_timestamp()))
        expired = task.status == "in_progress"
        if expired:
            previous = session.scalars(
                select(DeliveryAttempt).where(
                    DeliveryAttempt.notification_id == task.id,
                    DeliveryAttempt.round == task.round,
                    DeliveryAttempt.attempt_number == task.attempt_count,
                    DeliveryAttempt.lease_token == task.lease_token,
                )
            ).one()
            previous.finished_at = now
            previous.outcome = "unknown_outcome"
            previous.error_category = "unknown_outcome"
            previous.status_code = None
            task.last_status_code = None
            task.last_error_category = "unknown_outcome"
            recovery = {
                "event": "lease_recovered",
                "task_id": str(task.id),
                "round": task.round,
                "attempt": task.attempt_count,
            }
        if task.attempt_count >= settings.max_attempts:
            task.status = "failed"
            task.next_attempt_at = None
            task.lease_token = None
            task.lease_expires_at = None
            task.last_error_category = "unknown_outcome" if expired else "attempt_limit"
        else:
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
        task.updated_at = now
        if recovery is not None:
            recovery["status"] = task.status
    # Recovery is not reported until its transaction has committed.
    if recovery is not None:
        logging.getLogger(__name__).info(json.dumps(recovery, separators=(",", ":")))
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
