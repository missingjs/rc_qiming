"""Verify an isolated Compose project and remove only its generated containers and volume."""

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_until(check, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise RuntimeError("Timed out waiting for Compose verification state")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-build", action="store_true", help="Use the existing rc-qiming:local image"
    )
    args = parser.parse_args()
    ports = set()
    while len(ports) < 3:
        ports.add(free_port())
    api_port, database_port, provider_port = sorted(ports)
    environment = {
        **os.environ,
        "API_PORT": str(api_port),
        "POSTGRES_PORT": str(database_port),
        "MOCK_PROVIDER_PORT": str(provider_port),
        "POSTGRES_USER": "notifications",
        "POSTGRES_PASSWORD": "notifications",
        "POSTGRES_DB": "notifications",
        "WORKER_POLL_SECONDS": "0.05",
        "DELIVERY_TIMEOUT_SECONDS": "1",
        "LEASE_SECONDS": "6",
        "MAX_ATTEMPTS": "5",
        "RETRY_BASE_SECONDS": "0.05",
        "RETRY_MAX_SECONDS": "0.2",
    }
    project = f"rc-qiming-verify-{uuid4().hex[:12]}"
    command = [
        "docker",
        "compose",
        "--project-name",
        project,
        "-f",
        "compose.yaml",
        "-f",
        "compose.demo.yaml",
    ]

    def compose(*arguments, capture=False):
        return subprocess.run(
            [*command, *arguments],
            cwd=ROOT,
            env=environment,
            check=True,
            text=True,
            capture_output=capture,
            timeout=600,
        )

    base = f"http://127.0.0.1:{api_port}"
    print(f"Verifying isolated project {project}", flush=True)
    try:
        compose("config", "--quiet")
        compose("up", "--no-build" if args.no_build else "--build", "-d", "--wait")
        with httpx.Client(base_url=base, timeout=10, trust_env=False) as client:
            assert client.get("/health/live").status_code == 200
            assert client.get("/health/ready").status_code == 200
            subprocess.run(
                [
                    sys.executable,
                    "scripts/demo_delivery.py",
                    "--api-base",
                    base,
                    "--timeout",
                    "60",
                    "--replay",
                ],
                cwd=ROOT,
                check=True,
                timeout=90,
            )

            def submit(path):
                response = client.post(
                    "/notifications",
                    json={
                        "url": f"http://mock-provider:8000/{path}",
                        "json_body": {"event": "compose"},
                    },
                )
                assert response.status_code == 202, response.text
                return response.json()["status_url"]

            def status(path):
                response = client.get(path)
                response.raise_for_status()
                return response.json()

            compose("stop", "worker")
            backlog = [submit(f"success/{uuid4().hex}") for _ in range(2)]
            assert all(status(path)["status"] == "pending" for path in backlog)
            compose("start", "worker")
            for path in backlog:
                wait_until(lambda: status(path)["status"] == "succeeded")
            print("Worker restart processed the persisted backlog.", flush=True)

            key = uuid4().hex
            crash_path = submit(f"timeout/{key}?delay=0.5")

            def calls():
                response = client.get(f"http://127.0.0.1:{provider_port}/counts/timeout/{key}")
                response.raise_for_status()
                return response.json()["count"]

            wait_until(lambda: calls() == 1)
            compose("kill", "--signal", "SIGKILL", "worker")
            assert status(crash_path)["status"] == "in_progress"
            compose("start", "worker")
            wait_until(lambda: status(crash_path)["status"] == "succeeded")
            assert calls() == 2 and status(crash_path)["attempt_count"] == 2
            task_id = UUID(status(crash_path)["id"])
            history = compose(
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "notifications",
                "-d",
                "notifications",
                "-At",
                "-c",
                "SELECT outcome FROM delivery_attempts "
                f"WHERE notification_id = '{task_id}' ORDER BY round, attempt_number",
                capture=True,
            ).stdout.splitlines()
            assert history == ["unknown_outcome", "succeeded"], history
            print(
                "SIGKILL recovery demonstrated duplicate delivery and retained history.", flush=True
            )

            compose("stop", "postgres")
            assert client.get("/health/live").status_code == 200
            assert client.get("/health/ready").status_code == 503
            compose("up", "-d", "--wait", "postgres")
            wait_until(lambda: client.get("/health/ready").status_code == 200)
            after_outage = submit(f"success/{uuid4().hex}")
            wait_until(lambda: status(after_outage)["status"] == "succeeded")
            print("API and worker recovered after PostgreSQL restart.", flush=True)

            compose("down")
            compose("up", "--no-build", "-d", "--wait")
            assert status(crash_path)["status"] == "succeeded"
            assert status(crash_path)["attempt_count"] == 2
            assert all(status(path)["status"] == "succeeded" for path in backlog)
            print("Container recreation retained task data in the named volume.", flush=True)
        print("All Compose verification checks passed.", flush=True)
    except BaseException:
        subprocess.run(
            [*command, "logs", "--no-color", "--tail", "80"],
            cwd=ROOT,
            env=environment,
            timeout=30,
            check=False,
        )
        raise
    finally:
        compose("down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    main()
