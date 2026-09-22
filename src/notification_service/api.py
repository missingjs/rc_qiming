"""FastAPI application factory and independent liveness/readiness checks."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from notification_service.config import Settings
from notification_service.database import build_engine, database_ready


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.engine = build_engine(settings)
        try:
            yield
        finally:
            app.state.engine.dispose()

    app = FastAPI(title="HTTP Notification Service", version="0.1.0", lifespan=lifespan)

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready(request: Request) -> JSONResponse:
        available = database_ready(request.app.state.engine)
        return JSONResponse(
            {"status": "ready" if available else "not_ready"},
            status_code=200 if available else 503,
        )

    return app
