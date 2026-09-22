# rc_qiming

An internal HTTP notification service MVP: accept external API requests prepared by business systems, persist them, and deliver them through a separate worker with retries, status queries, and manual replay.

The project follows an API notification system assignment, emphasizing system boundaries, reliability, and engineering tradeoffs.

## Current Status

Phases 1 through 4 provide durable submission, concurrent idempotency protection, queries, independent HTTP delivery, bounded retries, attempt records, expired-lease recovery, manual replay, Docker Compose configuration, and a mock provider. Crash, shutdown, and database connection recovery are verified against real PostgreSQL. CI and updated Compose runtime verification remain phase 5 work.

The stack is Python 3.14, FastAPI, uv, PostgreSQL 17, SQLAlchemy, psycopg, Alembic, and HTTPX. Runtime and development dependencies are locked in `uv.lock`.

## Start with Docker Compose

Install Docker with Compose support. From the repository root:

```bash
cp .env.example .env
docker compose up --build -d --wait
curl --fail http://localhost:8000/health/live
curl --fail http://localhost:8000/health/ready
docker compose logs migrate worker
```

The migration service must finish successfully before the API and worker start. The API is available at `http://localhost:8000`, with OpenAPI documentation at `/docs`. Liveness returns `200` when the API is running. Readiness returns `200` only when the database and expected table columns are available, otherwise `503`. API readiness does not check the worker.

The default PostgreSQL port is `55432` and the API port is `8000`, both bound to localhost. Change `POSTGRES_PORT` or `API_PORT` in `.env` if needed. Compose builds the internal database URL from `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`; the local `DATABASE_URL` is for host processes. If changing the PostgreSQL port, update the local URL too. Example credentials are for local demonstrations only; URL-encode special characters in credentials when constructing a database URL.

Stop containers without deleting database data:

```bash
docker compose down
```

The named PostgreSQL volume survives container removal. Do not remove it unless you intend to delete the demonstration data. Changing database initialization credentials does not update an existing volume's database users.

## Local Development

Install uv and Python 3.14. Copy `.env.example` to `.env` if it does not exist, then run:

```bash
uv sync --locked
docker compose up -d --wait postgres
uv run --locked alembic upgrade head
uv run --locked uvicorn notification_service.api:create_app --factory --reload
```

In a separate terminal:

```bash
uv run --locked python -m notification_service.worker
```

Applications do not create tables automatically. Apply migrations before running the worker or expecting readiness to succeed. To check migration state and model consistency:

```bash
uv run --locked alembic current
uv run --locked alembic check
```

Environment variables override `.env`. `DATABASE_URL` is required and must use `postgresql+psycopg`. See [.env.example](.env.example) for scheduling defaults. The worker uses these settings for polling, HTTP timeouts, leases, retry limits, and backoff. The lease must exceed the total delivery timeout by at least five seconds.

## Verification

```bash
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -m 'not integration'
```

For database integration tests, create a dedicated test database in the local Compose instance once:

```bash
docker compose exec postgres createdb -U notifications notifications_test
TEST_DATABASE_URL=postgresql+psycopg://notifications:notifications@localhost:55432/notifications_test uv run --locked pytest
```

Adjust credentials and port if you changed the defaults. If the test database already exists, skip `createdb`. Each database test creates and removes its own randomly named schema. The test user needs schema creation privileges; do not point tests at a production database. Without `TEST_DATABASE_URL`, PostgreSQL integration tests are explicitly skipped.

Tests cover configuration validation, liveness during database failure, missing-schema readiness, migration upgrade/downgrade/re-upgrade, model/migration consistency, scheduling indexes, binary body persistence, and database uniqueness and foreign-key constraints. Submission tests additionally cover validation and redaction, canonical request comparison, persisted body bytes, status queries, eight concurrent matching or conflicting submissions, and rollback on an injected commit failure. Delivery tests cover status classification, backoff/jitter, Retry-After, deadlines, unread response bodies, redirects, cookie isolation, forwarding, concurrent claims, and claim/result transaction failures. A process-level test starts independent API, worker, and mock-provider processes and runs the five-scenario demo script with short timing settings. Recovery tests add real worker signals and kills, natural lease expiry, final-attempt crashes, stale-result rejection, concurrent replay, and selective database connection loss through a local TCP relay. The relay affects only test connections and does not stop the shared database. All database behavior tests use real PostgreSQL; no real provider is contacted.

## Delivery Demonstration

For a local demonstration without rebuilding images, start the API and worker using the local development commands above. Start the mock provider in another terminal:

```bash
uv run --locked uvicorn notification_service.mock_provider:create_app --factory --host 127.0.0.1 --port 8002 --no-access-log
```

Then run the five-scenario demonstration (normally a few minutes with default timeouts):

```bash
uv run --locked python scripts/demo_delivery.py --provider-base http://127.0.0.1:8002
```

Expected outcomes: `success` succeeds in one attempt, `flaky` succeeds in three, `permanent` fails in one, and `unavailable` and `timeout` fail after five attempts. The script uses new tasks each time and exits nonzero on unexpected results. If you change `MAX_ATTEMPTS`, pass the matching `--max-attempts` value (at least three). The timeout scenario requires the worker's delivery timeout to stay below the provider's 30-second delay. Tasks and attempt history remain in the demonstration database.

The same demonstration is available through the Compose overlay:

```bash
docker compose -f compose.yaml -f compose.demo.yaml config --quiet
docker compose -f compose.yaml -f compose.demo.yaml up --build -d --wait
uv run --locked python scripts/demo_delivery.py
```

Verification status: the overlay configuration was validated, but the latest image build timed out after 120 seconds during dependency downloads. Updated container startup has not been verified. The same demo script passed using independent local API, worker, and provider processes against real PostgreSQL.

The default script provider URL is `http://mock-provider:8000`, reachable from the container worker. Host workers must use `--provider-base http://127.0.0.1:8002`. Run a single provider process because its counters are in memory. The overlay publishes the provider on localhost port 8002. Stop the demo containers while retaining database data with `docker compose -f compose.yaml -f compose.demo.yaml down`.

Worker logs contain JSON events and sanitized results. The worker does not follow redirects, read provider response bodies, retain provider cookies, or inherit environment HTTP proxies. On SIGINT/SIGTERM, the worker finishes its current attempt before exiting. After a forced stop, another worker recovers the task once its lease expires; a final-attempt crash becomes a failed task with `unknown_outcome`. If a provider accepted the request before a crash or database failure, recovery may deliver it again.

## Submit and Query Notifications

With the Compose demonstration running, submit a task (this demonstration key can be reused):

```bash
curl --fail-with-body -i http://localhost:8000/notifications \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo:phase3' \
  -d '{"url":"http://mock-provider:8000/success/manual","json_body":{"event":"registered"}}'
```

The response is `202` with `id`, `status`, and `status_url`. Copy the returned `status_url` into the query command:

```bash
curl --fail http://localhost:8000/notifications/REPLACE_WITH_RETURNED_ID
```

Repeat the submission unchanged to receive the same task ID. Changing content with the same key returns `409`; omit the key to create a new task each time. Invalid input returns `422`, and database errors return `503`. Acceptance occurs only after commit. Queries return status, counters, timestamps, and a sanitized latest attempt summary, without request data. The worker moves due tasks through `in_progress` to `succeeded`, `retry_wait`, or `failed`; `latest_attempt` reports the most recent HTTP code or sanitized error category.

Supported methods are GET, POST (default), PUT, PATCH, and DELETE. Supply at most one of `json_body` or `text_body`; JSON null is a body, while omission means no body. JSON uses `application/json`; text defaults to `text/plain; charset=utf-8`. See [DESIGN.md](DESIGN.md) for header restrictions and exact idempotency comparison rules.

## Replay and Recovery

Replay only failed tasks, using the ID returned by submission:

```bash
curl --fail-with-body -i -X POST http://localhost:8000/notifications/REPLACE_WITH_FAILED_ID/replay
curl --fail http://localhost:8000/notifications/REPLACE_WITH_FAILED_ID
```

A successful replay returns `202`, keeps the same task ID and request, increments `round`, resets `attempt_count` to zero, and schedules immediate delivery. History remains intact. The latest summary may still describe the previous round until a new attempt completes. Other states return `409`, missing tasks return `404`, and database errors return `503`. If the replay response is lost, query the round before replaying again.

To demonstrate failure followed by a successful replay with the running local API, worker, and mock provider:

```bash
uv run --locked python scripts/demo_delivery.py --provider-base http://127.0.0.1:8002 --replay
```

For a container worker, omit `--provider-base`. The added scenario fails after five attempts in round 1 and succeeds in one attempt in round 2, using the same request and task ID. Pass the matching `--max-attempts` value if configured differently (3–100 for this demo).

To reproduce the isolated recovery checks against the dedicated test database:

```bash
TEST_DATABASE_URL=postgresql+psycopg://notifications:notifications@localhost:55432/notifications_test uv run --locked pytest -v tests/test_recovery.py tests/test_recovery_processes.py
```

These tests start and clean up their own mock provider, worker processes, TCP relay, and database schemas. They verify graceful drain, backlog processing after restart, SIGKILL with natural lease expiry, final-attempt exhaustion, and duplicate delivery after a provider response could not be persisted. They do not terminate an existing development worker or stop the database. With default service settings, production-like manual observation of a crashed task requires waiting for its 60-second lease; these tests use seven-second leases.

## Intended Notification Workflow

```text
Business system submits request -> PostgreSQL persists task -> Task ID returned
                                            |
                                   Worker delivers request
                                            |
                                Success / Retry wait / Failure
                                                          |
                                                     Manual replay
```

Callers prepare the URL, headers, and body. The service acknowledges acceptance only after persistence, without waiting for the provider response. Duplicate delivery is possible and automatic retries are bounded. HTTP success does not prove provider business success; business deduplication requires cooperation between the caller and provider.

The first version is for a trusted internal demonstration. Authentication, destination allowlists, provider adapters, and an administration UI are out of scope. The mock provider supports delivery demonstrations; replay and process-level recovery demonstrations are available, while Compose runtime verification remains phase 5 work.

## Documentation

| Document | Contents |
| --- | --- |
| [DESIGN.md](DESIGN.md) | System boundaries, APIs, data model, failure handling, and tradeoffs |
| [PLAN.md](PLAN.md) | Implementation phases, progress, and acceptance criteria |
| [AGENTS.md](AGENTS.md) | Repository collaboration guidelines for coding agents |
| [AI_USAGE.md](AI_USAGE.md) | Factual record of AI contributions, user decisions, and suggestions not adopted |

Repository documentation and code are written in English; conversation with the project author remains in Chinese.
