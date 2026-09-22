"""Database connections and readiness checks; schema changes belong to Alembic."""

from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.exc import SQLAlchemyError

from notification_service.config import Settings
from notification_service.models import DeliveryAttempt, Notification


def build_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_timeout=5,
        connect_args={"connect_timeout": 3},
    )


def database_ready(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SET LOCAL statement_timeout = '3s'"))
            # Check every mapped column without reading task data.
            connection.execute(select(Notification).limit(0))
            connection.execute(select(DeliveryAttempt).limit(0))
        return True
    except SQLAlchemyError:
        return False
