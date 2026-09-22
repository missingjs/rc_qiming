"""Persistent notification requests and their delivery attempt history."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'retry_wait', 'succeeded', 'failed')",
            name="ck_notifications_status",
        ),
        CheckConstraint("body_type IN ('none', 'json', 'text')", name="ck_notifications_body_type"),
        CheckConstraint("round >= 1 AND attempt_count >= 0", name="ck_notifications_counts"),
        UniqueConstraint("idempotency_key", name="uq_notifications_idempotency_key"),
        Index(
            "ix_notifications_due",
            "next_attempt_at",
            "id",
            postgresql_where=text("status IN ('pending', 'retry_wait')"),
        ),
        Index(
            "ix_notifications_expired_lease",
            "lease_expires_at",
            postgresql_where=text("status = 'in_progress'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    url: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(String(10))
    headers: Mapped[dict[str, str]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'")
    )
    body_type: Mapped[str] = mapped_column(String(10), default="none", server_default="none")
    body: Mapped[bytes | None] = mapped_column(LargeBinary)
    request_digest: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    round: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_status_code: Mapped[int | None]
    last_error_category: Mapped[str | None] = mapped_column(String(100))


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"
    __table_args__ = (
        UniqueConstraint("notification_id", "round", "attempt_number", name="uq_attempt_sequence"),
        CheckConstraint("round >= 1 AND attempt_number >= 1", name="ck_attempt_counts"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    notification_id: Mapped[UUID] = mapped_column(ForeignKey("notifications.id"))
    round: Mapped[int]
    attempt_number: Mapped[int]
    lease_token: Mapped[UUID]
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(30))
    status_code: Mapped[int | None]
    error_category: Mapped[str | None] = mapped_column(String(100))
