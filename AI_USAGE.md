# AI Usage Statement

## Scope of This Record

This document records actual AI collaboration on the project and will evolve during development. Work completed so far includes requirements discussions, design, project documentation, and the phase 1 implementation. The foundation and its tests are implemented; notification submission and delivery remain planned. Reasons offered by the AI are not automatically attributed to the author as personal motivations.

## AI Contributions

- Read the original requirements and identified the MVP scope: durable submission, independent delivery, bounded retries, queries, and replay.
- Proposed a PostgreSQL task table and separate worker, explaining complexity tradeoffs compared with adding a message broker.
- Identified duplicate delivery scenarios, including lost responses and unsaved success results, and distinguished submission idempotency from provider business idempotency.
- Proposed leases, short claim transactions, expiry recovery, and token checks to address crashes and concurrent execution.
- Presented choices for storage, request preparation, access boundaries, submission idempotency, and retry windows for the user to select.
- Consulted official PostgreSQL, HTTPX, and uv documentation to refine the design and proposed verification using real PostgreSQL and a mock provider.
- Proposed separate collaboration, implementation, and design documents, then wrote README, AGENTS, PLAN, DESIGN, and this statement at the user's request.
- Simplified AGENTS after the user identified service design details mixed into collaboration rules, then translated all current project documents into English at the user's request.

## User Decisions and Known Rationale

| Decision | Facts established in the conversation |
| --- | --- |
| Python, FastAPI, and uv | Explicitly specified by the user; personal reasons have not yet been provided |
| Use the existing GitHub repository | The user had already created it and explicitly ruled out recreating it |
| PostgreSQL and a separate worker | Selected by the user; the AI's rationale was to demonstrate persistence, concurrency, and crash recovery |
| Caller prepares provider requests | Selected by the user; the AI recommended focusing the service on reliable delivery without provider business coupling |
| Trusted internal demonstration | The user chose to omit authentication and destination allowlists for the first version, with the boundary documented |
| Optional submission idempotency key | The user chose to handle duplicate submissions; this does not guarantee once-only execution at the provider |
| Short retry window | The user selected a demonstration lasting a few minutes rather than the AI's recommended roughly one-day window |
| Five-document structure | The user agreed to the AGENTS, PLAN, and DESIGN split and requested README and AI_USAGE as well |
| Focused collaboration guidelines | The user identified design details in AGENTS and approved removing them in favor of references to DESIGN |
| English repository content | The user explicitly requested English for all files and future documentation and code, with Chinese reserved for conversation |

Specific timeouts, lease duration, API fields, and library choices are AI-proposed defaults documented in [DESIGN.md](DESIGN.md), not details individually specified by the user.

## Suggestions and Alternatives Not Adopted

- **Recommended authentication and destination allowlists:** the AI recommended these access controls; the user selected a trusted internal demonstration, so they are omitted from the first version.
- **Recommended retry window of roughly one day:** the AI recommended a longer window; the user chose a short demonstration window, leading to bounded attempts and short delays.
- **Alternatives not selected: SQLite with one worker, and built-in provider adapters:** the AI presented these as options; the user chose PostgreSQL and caller-prepared requests. These are not examples of rejecting the AI's preferred option.
- **Design details inside AGENTS:** the initial AI-authored guidelines included service mechanisms and project-stage information. The user questioned this overlap and approved a revision that keeps stable collaboration rules and references the design document.

A separate message broker, Celery, and a full administration UI were considered unnecessary complexity by the AI in the proposed design. There is no evidence that the user individually rejected each of them, so they are not recorded as user rejections.

## Future Additions

- The author can provide the actual reasons for choosing Python, FastAPI, and uv, plus personal reasoning behind other key choices. Do not invent these motivations before they are provided.
- During implementation, record modules generated or modified with AI assistance, the author's review and corrections, and actual rejected suggestions with reasons.
- Record tests, defects, and design adjustments based on observed results; do not present planned verification as completed.

See [DESIGN.md](DESIGN.md) for design contracts and [PLAN.md](PLAN.md) for implementation progress.

## Phase 1 Implementation Record

At the user's request, the AI implemented the first milestone and updated its acceptance checklist. Work includes the uv project and lockfile, shared environment settings, SQLAlchemy models, a versioned Alembic migration, FastAPI liveness and readiness endpoints, a worker process scaffold, Docker Compose, example environment configuration, and foundation tests.

The implementation uses Python 3.14 to match the available host interpreter and container runtime, and PostgreSQL 17 for the demonstration database. These are implementation defaults chosen by the AI, not versions explicitly requested by the user. Readiness verifies expected table columns rather than connectivity alone. Tests use isolated schemas in a dedicated PostgreSQL database and do not substitute SQLite.

Verification completed: locked dependency installation, Ruff checks, eight passing tests, model/migration consistency, migration downgrade and re-upgrade, Compose migration-before-application startup, both live HTTP health endpoints, and container recreation with the database volume retained. The first sandboxed checks could not access the local database; the full test suite passed after local connectivity was authorized. Two upstream TestClient deprecation warnings were observed and are not suppressed.

The worker currently checks database readiness only and explicitly logs that task delivery is not implemented. No claim is made that notification reliability, retries, or replay have been implemented or tested. No Git commit, push, or deployment was performed. Author review of the implementation is still pending.
