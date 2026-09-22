"""FastAPI application factory and independent liveness/readiness checks."""

from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from notification_service.config import Settings
from notification_service.database import build_engine, database_ready
from notification_service.models import Notification
from notification_service.replay import NotificationNotFound, ReplayConflict, replay
from notification_service.submissions import IdempotencyConflict, Submission, submit


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

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # Pydantic errors may echo bodies, credentials, URLs, or custom validator input.
        return JSONResponse({"detail": "Invalid request"}, status_code=422)

    @app.exception_handler(SQLAlchemyError)
    async def database_failure(request: Request, exc: SQLAlchemyError):
        return JSONResponse({"detail": "Database unavailable"}, status_code=503)

    @app.post("/notifications", status_code=202)
    def create_notification(body: Submission, request: Request) -> JSONResponse:
        keys = request.headers.getlist("idempotency-key")
        key = keys[0] if keys else None
        if len(keys) > 1 or (
            key is not None
            and (not 1 <= len(key) <= 200 or any(ord(c) < 33 or ord(c) > 126 for c in key))
        ):
            raise HTTPException(422, "Invalid Idempotency-Key")
        try:
            result = submit(request.app.state.engine, body, key)
        except IdempotencyConflict:
            raise HTTPException(409, "Idempotency key already used for different content") from None
        return JSONResponse(result, status_code=202, headers={"Location": result["status_url"]})

    @app.post("/notifications/{notification_id}/replay", status_code=202)
    def replay_notification(notification_id: UUID, request: Request) -> JSONResponse:
        try:
            result = replay(request.app.state.engine, notification_id)
        except NotificationNotFound:
            raise HTTPException(404, "Notification not found") from None
        except ReplayConflict:
            raise HTTPException(409, "Only the current failed round can be replayed") from None
        return JSONResponse(result, status_code=202, headers={"Location": result["status_url"]})

    @app.get("/notifications/{notification_id}")
    def get_notification(notification_id: UUID, request: Request) -> dict:
        # Select only public status columns; request content never enters the response.
        with Session(request.app.state.engine) as session:
            row = (
                session.execute(
                    select(
                        Notification.id,
                        Notification.status,
                        Notification.round,
                        Notification.attempt_count,
                        Notification.created_at,
                        Notification.updated_at,
                        Notification.next_attempt_at,
                        Notification.last_status_code,
                        Notification.last_error_category,
                    ).where(Notification.id == notification_id)
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise HTTPException(404, "Notification not found")
        result = dict(row)
        code = result.pop("last_status_code")
        category = result.pop("last_error_category")
        # Treat stored diagnostics as untrusted; delivery code uses these categories.
        allowed = {
            "network_error",
            "timeout",
            "http_error",
            "unknown_outcome",
            "invalid_request",
            "attempt_limit",
        }
        result["latest_attempt"] = (
            {
                "status_code": code,
                "error_category": category
                if category in allowed
                else "unknown_error"
                if category
                else None,
            }
            if code is not None or category is not None
            else None
        )
        return result

    return app
