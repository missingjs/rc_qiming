"""Create durable notification tasks and attempt history."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("headers", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("body_type", sa.String(10), server_default="none", nullable=False),
        sa.Column("body", sa.LargeBinary(), nullable=True),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("round", sa.Integer(), server_default="1", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error_category", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_notifications_idempotency_key"),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'retry_wait', 'succeeded', 'failed')",
            name="ck_notifications_status",
        ),
        sa.CheckConstraint(
            "body_type IN ('none', 'json', 'text')", name="ck_notifications_body_type"
        ),
        sa.CheckConstraint("round >= 1 AND attempt_count >= 0", name="ck_notifications_counts"),
    )
    op.create_index(
        "ix_notifications_due",
        "notifications",
        ["next_attempt_at", "id"],
        postgresql_where=sa.text("status IN ('pending', 'retry_wait')"),
    )
    op.create_index(
        "ix_notifications_expired_lease",
        "notifications",
        ["lease_expires_at"],
        postgresql_where=sa.text("status = 'in_progress'"),
    )
    op.create_table(
        "delivery_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("notification_id", sa.Uuid(), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(30), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("error_category", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"]),
        sa.UniqueConstraint(
            "notification_id", "round", "attempt_number", name="uq_attempt_sequence"
        ),
        sa.CheckConstraint("round >= 1 AND attempt_number >= 1", name="ck_attempt_counts"),
    )


def downgrade() -> None:
    op.drop_table("delivery_attempts")
    op.drop_index("ix_notifications_expired_lease", table_name="notifications")
    op.drop_index("ix_notifications_due", table_name="notifications")
    op.drop_table("notifications")
