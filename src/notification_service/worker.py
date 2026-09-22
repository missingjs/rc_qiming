"""Worker process foundation. Task claiming and delivery arrive in phase 3."""

import logging
import signal
from threading import Event

from notification_service.config import Settings
from notification_service.database import build_engine, database_ready

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()
    engine = build_engine(settings)
    stop = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    logger.info("Worker scaffold started; task delivery is not implemented")
    previous = None
    try:
        while not stop.is_set():
            ready = database_ready(engine)
            if ready != previous:
                logger.info("Database readiness: %s", "ready" if ready else "not_ready")
                previous = ready
            stop.wait(settings.worker_poll_seconds)
    finally:
        engine.dispose()
        logger.info("Worker stopped")


if __name__ == "__main__":
    main()
