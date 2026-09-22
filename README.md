# rc_qiming

An internal HTTP notification service MVP: accept external API requests prepared by business systems, persist them, and deliver them through a separate worker with retries, status queries, and manual replay.

The project follows an API notification system assignment, emphasizing system boundaries, reliability, and engineering tradeoffs.

## Current Status

Phases 1 and 2 provide configuration, database models and migrations, API health checks, a separate worker scaffold, Docker Compose, durable submission, and status queries with concurrent idempotency protection. Delivery, retries, and replay are not implemented yet. The worker reports database readiness but does not claim or deliver tasks.

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

Environment variables override `.env`. `DATABASE_URL` is required and must use `postgresql+psycopg`. See [.env.example](.env.example) for scheduling defaults. Delivery settings are validated now but will be used by later milestones. The lease must exceed the total delivery timeout by at least five seconds.

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

Tests cover configuration validation, liveness during database failure, missing-schema readiness, migration upgrade/downgrade/re-upgrade, model/migration consistency, scheduling indexes, binary body persistence, and database uniqueness and foreign-key constraints. Submission tests additionally cover validation and redaction, canonical request comparison, persisted body bytes, status queries, eight concurrent matching or conflicting submissions, and rollback on an injected commit failure. All database behavior tests use real PostgreSQL; no real provider is contacted.

## Submit and Query Notifications

With Compose running, submit a task (this demonstration key can be reused):

```bash
curl --fail-with-body -i http://localhost:8000/notifications \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo:phase2' \
  -d '{"url":"https://example.test/events","json_body":{"event":"registered"}}'
```

The response is `202` with `id`, `status`, and `status_url`. Copy the returned `status_url` into the query command:

```bash
curl --fail http://localhost:8000/notifications/REPLACE_WITH_RETURNED_ID
```

Repeat the submission unchanged to receive the same task ID. Changing content with the same key returns `409`; omit the key to create a new task each time. Invalid input returns `422`, and database errors return `503`. Acceptance occurs only after commit. Queries return status, counters, timestamps, and a sanitized latest attempt summary, without request data. Tasks remain `pending` because delivery belongs to phase 3.

Supported methods are GET, POST (default), PUT, PATCH, and DELETE. Supply at most one of `json_body` or `text_body`; JSON null is a body, while omission means no body. JSON uses `application/json`; text defaults to `text/plain; charset=utf-8`. See [DESIGN.md](DESIGN.md) for header restrictions and exact idempotency comparison rules.

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

The first version is for a trusted internal demonstration. Authentication, destination allowlists, provider adapters, and an administration UI are out of scope. A mock provider and full notification demonstrations will be added in later phases.

## Documentation

| Document | Contents |
| --- | --- |
| [DESIGN.md](DESIGN.md) | System boundaries, APIs, data model, failure handling, and tradeoffs |
| [PLAN.md](PLAN.md) | Implementation phases, progress, and acceptance criteria |
| [AGENTS.md](AGENTS.md) | Repository collaboration guidelines for coding agents |
| [AI_USAGE.md](AI_USAGE.md) | Factual record of AI contributions, user decisions, and suggestions not adopted |

Repository documentation and code are written in English; conversation with the project author remains in Chinese.
