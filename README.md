# rc_qiming

An internal HTTP notification service MVP: accept external API requests prepared by business systems, persist them, and deliver them through a separate worker with retries, status queries, and manual replay.

The project follows an API notification system assignment, emphasizing system boundaries, reliability, and engineering tradeoffs.

## Current Status

Requirements discussions and initial design documentation are complete. Application code, dependency configuration, database migrations, and tests have not been created. The capabilities below are implementation goals; the service cannot be started yet.

## Technology Stack and Intended Workflow

The confirmed stack is Python, FastAPI, uv, PostgreSQL, and a separate Python worker. See [DESIGN.md](DESIGN.md) for proposed libraries and runtime defaults.

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

The first version is for a trusted internal demonstration. Authentication, destination allowlists, provider adapters, and an administration UI are out of scope.

## Documentation

| Document | Contents |
| --- | --- |
| [DESIGN.md](DESIGN.md) | System boundaries, APIs, data model, failure handling, and tradeoffs |
| [PLAN.md](PLAN.md) | Implementation phases, progress, and acceptance criteria |
| [AGENTS.md](AGENTS.md) | Repository collaboration guidelines for coding agents |
| [AI_USAGE.md](AI_USAGE.md) | Factual record of AI contributions, user decisions, and suggestions not adopted |

## Intended Operation and Demonstrations

The plan is to manage and lock dependencies with uv, run PostgreSQL, migrations, the API, and the worker through Docker Compose, and provide a mock HTTP provider without requiring a real provider account. Verified installation, startup, migration, testing, and API commands will be added here once the project foundation is implemented.

Planned demonstrations cover first-attempt success, success after temporary failure, retry exhaustion, manual replay, and continued processing after worker restart. Tests will cover concurrent claiming, idempotency constraints, and lease recovery against real PostgreSQL.

Python dependencies will be maintained in `pyproject.toml` and `uv.lock`. Repository documentation and code are written in English; conversation with the project author remains in Chinese.
