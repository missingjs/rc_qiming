import pytest
from pydantic import ValidationError

from notification_service.config import Settings


@pytest.mark.parametrize(
    "overrides",
    [
        {"database_url": "sqlite:///test.db"},
        {"lease_seconds": 19, "delivery_timeout_seconds": 15},
        {"worker_poll_seconds": 0},
        {"delivery_timeout_seconds": float("inf")},
        {"retry_max_seconds": float("nan")},
        {"max_attempts": 0},
        {"retry_base_seconds": 10, "retry_max_seconds": 5},
    ],
)
def test_invalid_settings_are_rejected(overrides):
    values = {"database_url": "postgresql+psycopg://test:test@localhost/test"} | overrides
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)
