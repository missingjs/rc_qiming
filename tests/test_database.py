"""Exercise migrations and constraints in an isolated PostgreSQL schema."""

import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from notification_service.api import create_app
from notification_service.config import Settings
from notification_service.models import DeliveryAttempt, Notification

pytestmark = pytest.mark.integration


@pytest.fixture
def migrated_database(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated PostgreSQL test database")
    schema = f"test_{uuid4().hex}"
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    # libpq applies PGOPTIONS to application and Alembic connections alike.
    monkeypatch.setenv("PGOPTIONS", f"-c search_path={schema}")
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    try:
        # Verify that connectivity alone is insufficient for readiness.
        settings = Settings(_env_file=None, database_url=url)
        with TestClient(create_app(settings)) as client:
            assert client.get("/health/live").status_code == 200
            assert client.get("/health/ready").status_code == 503
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            yield engine, config, settings
        finally:
            engine.dispose()
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_migration_roundtrip_and_readiness(migrated_database):
    engine, config, settings = migrated_database
    assert {"notifications", "delivery_attempts"} <= set(inspect(engine).get_table_names())
    indexes = {index["name"] for index in inspect(engine).get_indexes("notifications")}
    assert {"ix_notifications_due", "ix_notifications_expired_lease"} <= indexes
    command.upgrade(config, "head")
    command.check(config)
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/ready").status_code == 200
        command.downgrade(config, "base")
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        command.upgrade(config, "head")
        assert client.get("/health/ready").status_code == 200


def test_request_storage_and_database_constraints(migrated_database):
    engine, _, _ = migrated_database

    def notification(key=None):
        return Notification(
            url="https://example.test/events",
            method="POST",
            headers={"Content-Type": "application/json"},
            body_type="json",
            body=b'{"event":"registered"}',
            request_digest="a" * 64,
            idempotency_key=key,
        )

    with Session(engine) as session:
        task = notification("system:event-1")
        session.add(task)
        session.commit()
        session.refresh(task)
        assert task.body == b'{"event":"registered"}'
        assert task.created_at.tzinfo is not None
        task_id = task.id
        session.add(notification("system:event-1"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        session.add_all([notification(), notification()])
        session.commit()
        for _ in range(2):
            session.add(
                DeliveryAttempt(
                    notification_id=task_id, round=1, attempt_number=1, lease_token=uuid4()
                )
            )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        session.add(
            DeliveryAttempt(notification_id=uuid4(), round=1, attempt_number=1, lease_token=uuid4())
        )
        with pytest.raises(IntegrityError):
            session.commit()
