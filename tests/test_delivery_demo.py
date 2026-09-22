"""Run the documented demo with independent API, worker, and mock HTTP processes."""

import os
import socket
import subprocess
import sys
import time
from contextlib import ExitStack

import httpx
import pytest

pytestmark = pytest.mark.integration


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_independent_process_delivery_demo(migrated_database, tmp_path):
    _, _, settings = migrated_database
    api_port, provider_port = free_port(), free_port()
    while provider_port == api_port:
        provider_port = free_port()
    environment = {
        **os.environ,
        "DATABASE_URL": settings.database_url,
        "WORKER_POLL_SECONDS": "0.02",
        "DELIVERY_TIMEOUT_SECONDS": "0.2",
        "LEASE_SECONDS": "6",
        "MAX_ATTEMPTS": "5",
        "RETRY_BASE_SECONDS": "0.02",
        "RETRY_MAX_SECONDS": "0.1",
    }
    commands = {
        "api": [
            "-m",
            "uvicorn",
            "notification_service.api:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(api_port),
            "--no-access-log",
        ],
        "provider": [
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
        "worker": ["-m", "notification_service.worker"],
    }
    processes = []
    with ExitStack() as stack:
        try:
            for name, command in commands.items():
                log = stack.enter_context((tmp_path / f"{name}.log").open("w"))
                processes.append(
                    subprocess.Popen(
                        [sys.executable, *command],
                        env=environment,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )
                )
            with httpx.Client(trust_env=False, timeout=1) as client:
                deadline = time.monotonic() + 10
                for url in (
                    f"http://127.0.0.1:{api_port}/health/ready",
                    f"http://127.0.0.1:{provider_port}/health",
                ):
                    while True:
                        assert all(p.poll() is None for p in processes), "Demo process exited"
                        try:
                            if client.get(url).status_code == 200:
                                break
                        except httpx.RequestError:
                            pass
                        if time.monotonic() >= deadline:
                            pytest.fail("Demo processes did not become ready")
                        time.sleep(0.05)
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/demo_delivery.py",
                    "--api-base",
                    f"http://127.0.0.1:{api_port}",
                    "--provider-base",
                    f"http://127.0.0.1:{provider_port}",
                    "--timeout",
                    "15",
                ],
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            assert "All delivery demonstrations passed." in result.stdout
            assert "flaky: succeeded, attempts=3" in result.stdout
            assert "timeout: failed, attempts=5" in result.stdout
        finally:
            for process in reversed(processes):
                process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    worker_log = (tmp_path / "worker.log").read_text()
    assert '"event":"worker_stopped"' in worker_log
    assert "http://" not in worker_log and "Traceback" not in worker_log
