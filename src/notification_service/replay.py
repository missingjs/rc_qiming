"""Replay one observed failed round without changing the original request or history."""

from uuid import UUID

from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from notification_service.models import Notification


class NotificationNotFound(Exception):
    pass


class ReplayConflict(Exception):
    pass


def replay(engine: Engine, notification_id: UUID) -> dict:
    with Session(engine) as session, session.begin():
        observed = session.execute(
            select(Notification.status, Notification.round).where(
                Notification.id == notification_id
            )
        ).one_or_none()
        if observed is None:
            raise NotificationNotFound
        if observed.status != "failed":
            raise ReplayConflict
        # Include the observed round: a waiting replay cannot consume a newer failed round.
        task_id = session.scalar(
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.status == "failed",
                Notification.round == observed.round,
            )
            .values(
                status="pending",
                round=observed.round + 1,
                attempt_count=0,
                next_attempt_at=func.clock_timestamp(),
                updated_at=func.clock_timestamp(),
                lease_token=None,
                lease_expires_at=None,
            )
            .returning(Notification.id)
        )
        if task_id is None:
            raise ReplayConflict
        result = {
            "id": str(task_id),
            "status": "pending",
            "status_url": f"/notifications/{task_id}",
        }
    return result
