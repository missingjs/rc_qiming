import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from notification_service.api import create_app
from notification_service.config import Settings


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
