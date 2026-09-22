"""Verify delivery through a running API, worker, database, and local mock provider."""

import argparse
import time
from uuid import uuid4

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default="http://localhost:8000")
    parser.add_argument("--provider-base", default="http://mock-provider:8000")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--max-attempts", type=int, default=5)
    args = parser.parse_args()
    expected = {
        "success": ("succeeded", 1),
        "flaky": ("succeeded", 3),
        "permanent": ("failed", 1),
        "unavailable": ("failed", args.max_attempts),
        "timeout": ("failed", args.max_attempts),
    }
    tasks = {}
    with httpx.Client(base_url=args.api_base, trust_env=False, timeout=10) as client:
        client.get("/health/ready").raise_for_status()
        for scenario in expected:
            response = client.post(
                "/notifications",
                json={
                    "url": f"{args.provider_base}/{scenario}/{uuid4().hex}",
                    "json_body": {"event": "demo"},
                },
            )
            response.raise_for_status()
            tasks[scenario] = response.json()["status_url"]
        deadline = time.monotonic() + args.timeout
        pending = dict(tasks)
        while pending and time.monotonic() < deadline:
            for scenario, path in list(pending.items()):
                response = client.get(path)
                response.raise_for_status()
                task = response.json()
                if task["status"] in {"succeeded", "failed"}:
                    actual = (task["status"], task["attempt_count"])
                    if actual != expected[scenario]:
                        raise RuntimeError(
                            f"{scenario}: expected {expected[scenario]}, got {actual}"
                        )
                    print(f"{scenario}: {actual[0]}, attempts={actual[1]}", flush=True)
                    del pending[scenario]
            if pending:
                time.sleep(0.2)
        if pending:
            raise RuntimeError(f"Timed out waiting for: {', '.join(pending)}")
    print("All delivery demonstrations passed.")


if __name__ == "__main__":
    main()
