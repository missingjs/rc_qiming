"""In-memory demonstration provider. Use test data and a single server process."""

import asyncio
from collections import Counter
from typing import Literal

from fastapi import FastAPI, Query, Response


def create_app() -> FastAPI:
    app = FastAPI(title="Notification Mock Provider")
    counts: Counter[tuple[str, str]] = Counter()

    @app.get("/health")
    async def health():
        return {"status": "ready"}

    @app.get("/counts/{scenario}/{key}")
    async def count(scenario: str, key: str):
        return {"count": counts[scenario, key]}

    @app.api_route("/{scenario}/{key}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def receive(
        scenario: Literal["success", "flaky", "permanent", "unavailable", "timeout"],
        key: str,
        failures: int = Query(default=2, ge=0, le=100),
        delay: float = Query(default=30, ge=0, le=120),
    ):
        counts[scenario, key] += 1
        if scenario == "timeout":
            await asyncio.sleep(delay)
        if scenario == "permanent":
            return Response(status_code=400)
        if scenario == "unavailable" or (scenario == "flaky" and counts[scenario, key] <= failures):
            return Response(status_code=503, headers={"Retry-After": "0"})
        return Response(status_code=204)

    return app
