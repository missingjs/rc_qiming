"""Independent sequential worker; database transactions never span HTTP delivery."""

import asyncio
import json
import logging
import signal
from time import monotonic

import httpx
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from notification_service.config import Settings
from notification_service.database import build_engine
from notification_service.delivery import deliver
from notification_service.queue import claim_task, finish_attempt

logger = logging.getLogger(__name__)


def log_event(event: str, **fields) -> None:
    logger.info(json.dumps({"event": event, **fields}, separators=(",", ":")))


async def process_one(engine: Engine, settings: Settings, client: httpx.AsyncClient) -> bool:
    claimed = await asyncio.to_thread(claim_task, engine, settings)
    if claimed is None:
        return False
    fields = {"task_id": str(claimed.id), "round": claimed.round, "attempt": claimed.attempt}
    log_event("attempt_started", **fields)
    started = monotonic()
    result = await deliver(
        client,
        method=claimed.method,
        url=claimed.url,
        headers=claimed.headers,
        body=claimed.body,
        timeout=settings.delivery_timeout_seconds,
    )
    duration_ms = round((monotonic() - started) * 1000)
    try:
        status = await asyncio.to_thread(finish_attempt, engine, settings, claimed, result)
    except SQLAlchemyError:
        log_event(
            "outcome_not_saved", **fields, error_category="database_error", duration_ms=duration_ms
        )
        raise
    log_event(
        "attempt_finished" if status else "outcome_discarded",
        **fields,
        status=status,
        status_code=result.status_code,
        error_category=result.error_category,
        duration_ms=duration_ms,
    )
    return True


async def run_worker(engine: Engine, settings: Settings, stop: asyncio.Event) -> None:
    async with httpx.AsyncClient(verify=True, trust_env=False, follow_redirects=False) as client:
        log_event("worker_started")
        while not stop.is_set():
            try:
                if await process_one(engine, settings, client):
                    continue
            except SQLAlchemyError:
                # Never log exception text: drivers can include SQL parameters and credentials.
                log_event("database_unavailable", error_category="database_error")
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
            except TimeoutError:
                pass
        log_event("worker_stopped")


async def run(settings: Settings) -> None:
    engine = build_engine(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    try:
        await run_worker(engine, settings, stop)
    finally:
        engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # HTTPX INFO request logs include destination URLs and query strings.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    asyncio.run(run(Settings()))


if __name__ == "__main__":
    main()
