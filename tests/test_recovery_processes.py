"""Real worker signals, crashes, and database connection loss using local processes."""

import os
import select as socket_select
import signal
import socket
import socketserver
import subprocess
import sys
import time
from threading import Event, Thread
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from notification_service.models import DeliveryAttempt, Notification
from notification_service.submissions import Submission, submit

pytestmark = pytest.mark.integration


def wait_until(check, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.03)
    pytest.fail("Timed out waiting for the expected process or database state")


class DatabaseProxy(socketserver.ThreadingTCPServer):
    """Relay PostgreSQL bytes, with a switch that disconnects only test connections."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, upstream):
        self.upstream = upstream
        self.available = Event()
        self.available.set()
        super().__init__(("127.0.0.1", 0), ProxyConnection)


class ProxyConnection(socketserver.BaseRequestHandler):
    def handle(self):
        if not self.server.available.is_set():
            return
        try:
            with socket.create_connection(self.server.upstream, timeout=2) as upstream:
                self.request.settimeout(2)
                while self.server.available.is_set():
                    ready, _, _ = socket_select.select([self.request, upstream], [], [], 0.02)
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        destination = upstream if source is self.request else self.request
                        destination.sendall(data)
        except OSError:
            pass


@pytest.fixture
def database_proxy(migrated_database):
    _, _, settings = migrated_database
    original = make_url(settings.database_url)
    with DatabaseProxy((original.host, original.port or 5432)) as proxy:
        thread = Thread(target=proxy.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        try:
            url = original.set(host="127.0.0.1", port=proxy.server_address[1])
            yield proxy.available, url.render_as_string(hide_password=False)
        finally:
            proxy.available.clear()
            proxy.shutdown()
            thread.join(timeout=3)


@pytest.fixture
def runtime(migrated_database, tmp_path):
    engine, _, settings = migrated_database
    processes = []
    streams = []
    environment = {
        **os.environ,
        "DATABASE_URL": settings.database_url,
        "WORKER_POLL_SECONDS": "0.03",
        "DELIVERY_TIMEOUT_SECONDS": "2",
        "LEASE_SECONDS": "7",
        "MAX_ATTEMPTS": "3",
        "RETRY_BASE_SECONDS": "0.02",
        "RETRY_MAX_SECONDS": "0.1",
    }

    def start(name, command, **overrides):
        path = tmp_path / f"{name}-{len(processes)}.log"
        stream = path.open("w")
        streams.append(stream)
        process = subprocess.Popen(
            [sys.executable, *command],
            env={**environment, **overrides},
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
        processes.append(process)
        return process, path

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        provider_port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{provider_port}"
    try:
        start(
            "provider",
            [
                "-m",
                "uvicorn",
                "notification_service.mock_provider:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(provider_port),
                "--no-access-log",
                "--timeout-graceful-shutdown",
                "1",
            ],
        )
        with httpx.Client(trust_env=False, timeout=1) as client:

            def ready():
                try:
                    return client.get(f"{base}/health").status_code == 200
                except httpx.RequestError:
                    return False

            wait_until(ready)

            class Runtime:
                def worker(self, **overrides):
                    return start("worker", ["-m", "notification_service.worker"], **overrides)

                def enqueue(self, scenario="success", query=""):
                    key = uuid4().hex
                    task_id = UUID(
                        submit(
                            engine,
                            Submission(
                                url=f"{base}/{scenario}/{key}{query}", json_body={"event": "test"}
                            ),
                            None,
                        )["id"]
                    )
                    return task_id, lambda: client.get(f"{base}/counts/{scenario}/{key}").json()[
                        "count"
                    ]

                def task(self, task_id):
                    with Session(engine) as session:
                        return session.get(Notification, task_id)

            yield Runtime()
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for stream in streams:
            stream.close()


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_graceful_shutdown_drains_current_attempt_and_restart_processes_backlog(runtime, signum):
    task_id, calls = runtime.enqueue("timeout", "?delay=0.6")
    worker, log = runtime.worker()
    wait_until(lambda: calls() == 1)
    pending_id, pending_calls = runtime.enqueue()
    worker.send_signal(signum)
    assert worker.wait(timeout=5) == 0
    assert runtime.task(task_id).status == "succeeded"
    assert runtime.task(pending_id).status == "pending"
    assert pending_calls() == 0
    assert '"event":"worker_stopped"' in log.read_text()
    restarted, _ = runtime.worker()
    wait_until(lambda: runtime.task(pending_id).status == "succeeded")
    assert pending_calls() == 1 and calls() == 1
    assert restarted.poll() is None


@pytest.mark.parametrize("limit,expected,calls_expected", [(3, "succeeded", 2), (1, "failed", 1)])
def test_killed_worker_recovers_after_real_lease_expiry(
    runtime, migrated_database, limit, expected, calls_expected
):
    engine, _, _ = migrated_database
    task_id, calls = runtime.enqueue("timeout", "?delay=0.4")
    worker, _ = runtime.worker(MAX_ATTEMPTS=str(limit))
    wait_until(lambda: calls() == 1)
    original = runtime.task(task_id)
    worker.kill()
    worker.wait(timeout=5)
    restarted, log = runtime.worker(MAX_ATTEMPTS=str(limit))
    wait_until(lambda: '"event":"worker_started"' in log.read_text())
    assert runtime.task(task_id).lease_token == original.lease_token
    assert calls() == 1
    wait_until(lambda: runtime.task(task_id).status == expected)
    task = runtime.task(task_id)
    assert task.attempt_count == calls_expected
    assert calls() == calls_expected
    with Session(engine) as session:
        attempts = session.scalars(
            select(DeliveryAttempt)
            .where(DeliveryAttempt.notification_id == task_id)
            .order_by(DeliveryAttempt.attempt_number)
        ).all()
        assert len(attempts) == calls_expected
        assert attempts[0].outcome == "unknown_outcome" and attempts[0].finished_at is not None
        if limit == 1:
            assert task.last_error_category == "unknown_outcome"
        else:
            assert attempts[1].outcome == "succeeded"
            assert attempts[1].lease_token != attempts[0].lease_token
    assert '"event":"lease_recovered"' in log.read_text()
    assert restarted.poll() is None


def test_database_outage_before_claim_recovers_without_worker_restart(runtime, database_proxy):
    available, url = database_proxy
    available.clear()
    task_id, calls = runtime.enqueue()
    worker, log = runtime.worker(DATABASE_URL=url)
    wait_until(lambda: '"event":"database_unavailable"' in log.read_text())
    assert runtime.task(task_id).status == "pending" and calls() == 0
    available.set()
    wait_until(lambda: runtime.task(task_id).status == "succeeded")
    assert calls() == 1 and worker.poll() is None
    assert "Traceback" not in log.read_text()


def test_provider_success_during_database_outage_can_be_delivered_twice(
    runtime, database_proxy, migrated_database
):
    available, url = database_proxy
    engine, _, _ = migrated_database
    task_id, calls = runtime.enqueue("timeout", "?delay=0.6")
    worker, log = runtime.worker(DATABASE_URL=url)
    wait_until(lambda: calls() == 1)
    available.clear()
    wait_until(lambda: '"event":"outcome_not_saved"' in log.read_text())
    assert runtime.task(task_id).status == "in_progress"
    assert runtime.task(task_id).last_status_code is None
    available.set()
    wait_until(lambda: runtime.task(task_id).status == "succeeded")
    assert calls() == 2
    with Session(engine) as session:
        attempts = session.scalars(
            select(DeliveryAttempt)
            .where(DeliveryAttempt.notification_id == task_id)
            .order_by(DeliveryAttempt.attempt_number)
        ).all()
        assert [attempt.outcome for attempt in attempts] == ["unknown_outcome", "succeeded"]
        assert attempts[1].status_code == 204
    assert worker.poll() is None
    assert "Traceback" not in log.read_text()
