"""Verify delivery and optional replay through a running service and local mock provider."""

import argparse
import time
from uuid import uuid4

import httpx


def wait_for_tasks(client, tasks, expected, timeout, round_number=1):
    deadline = time.monotonic() + timeout
    pending = dict(tasks)
    while pending and time.monotonic() < deadline:
        for scenario, path in list(pending.items()):
            response = client.get(path)
            response.raise_for_status()
            task = response.json()
            if task["status"] in {"succeeded", "failed"}:
                actual = (task["status"], task["attempt_count"])
                if actual != expected[scenario] or task["round"] != round_number:
                    raise RuntimeError(
                        f"{scenario}: unexpected result {actual}, round={task['round']}"
                    )
                print(
                    f"{scenario}: {actual[0]}, attempts={actual[1]}, round={task['round']}",
                    flush=True,
                )
                del pending[scenario]
        if pending:
            time.sleep(0.2)
    if pending:
        raise RuntimeError(f"Timed out waiting for: {', '.join(pending)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default="http://localhost:8000")
    parser.add_argument("--provider-base", default="http://mock-provider:8000")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument(
        "--replay", action="store_true", help="Also verify failure followed by replay"
    )
    args = parser.parse_args()
    if not 3 <= args.max_attempts <= 100:
        parser.error("--max-attempts must be between 3 and 100 for these mock scenarios")
    expected = {
        "success": ("succeeded", 1),
        "flaky": ("succeeded", 3),
        "permanent": ("failed", 1),
        "unavailable": ("failed", args.max_attempts),
        "timeout": ("failed", args.max_attempts),
    }
    if args.replay:
        expected["replay"] = ("failed", args.max_attempts)
    tasks = {}
    with httpx.Client(base_url=args.api_base, trust_env=False, timeout=10) as client:
        client.get("/health/ready").raise_for_status()
        for scenario in expected:
            destination = f"{scenario}/{uuid4().hex}"
            if scenario == "replay":
                destination = f"flaky/{uuid4().hex}?failures={args.max_attempts}"
            response = client.post(
                "/notifications",
                json={
                    "url": f"{args.provider_base}/{destination}",
                    "json_body": {"event": "demo"},
                },
            )
            response.raise_for_status()
            tasks[scenario] = response.json()["status_url"]
        wait_for_tasks(client, tasks, expected, args.timeout)
        if args.replay:
            response = client.post(f"{tasks['replay']}/replay")
            response.raise_for_status()
            if response.status_code != 202 or response.json()["status_url"] != tasks["replay"]:
                raise RuntimeError("Replay did not preserve the task ID")
            wait_for_tasks(
                client,
                {"replay": tasks["replay"]},
                {"replay": ("succeeded", 1)},
                args.timeout,
                round_number=2,
            )
    print("All delivery demonstrations passed.")


if __name__ == "__main__":
    main()
