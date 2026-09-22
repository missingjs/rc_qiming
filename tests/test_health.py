from fastapi.testclient import TestClient

from notification_service.api import create_app
from notification_service.config import Settings


def test_database_failure_does_not_affect_liveness():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://test:test@127.0.0.1:1/unavailable",
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/live").json() == {"status": "alive"}
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready"}
        assert "test:test" not in response.text
