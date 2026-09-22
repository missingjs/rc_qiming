# AI Usage Statement

## Post-Review Header Validation Fix

The AI review reproduced a mismatch: submission validation accepted header values with leading or trailing spaces, but the HTTP/1.1 transport rejected them. The user approved rejecting these values with `422` without trimming them, preserving the existing contract for business headers. The AI implemented the validation, documented its rationale in DESIGN, and added four invalid-input regression cases plus three cases checking preservation of empty values and interior spaces.

Verification: 70 non-integration tests passed, including the new cases; Ruff lint, formatting, and whitespace checks passed. The 43 database integration cases were deselected, and Compose and hosted CI were not rerun. Two existing dependency deprecation warnings remain. No schema or dependency changes were required.

## Scope of This Record

This document records actual AI collaboration on the project and will evolve during development. Work completed so far includes requirements discussions, design, project documentation, and all five implementation phases. The service, mock provider, recovery/replay, tests, CI configuration, and local Compose verification are complete. The user has confirmed that CI passed on GitHub. Reasons offered by the AI are not automatically attributed to the author as personal motivations.

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
| Caller prepares provider requests | The user reasons that callers know provider details and can adapt when those details change or new providers are added. A standard submission contract isolates those changes on the caller side and lets this service focus on request processing. The user relates this separation to the open/closed and dependency inversion principles. |
| Bounded retries and manual replay | The user requires a maximum attempt count and human intervention after exhaustion. They expect exhausted failures to be uncommon, making manual handling an acceptable and flexible cost while avoiding additional automated recovery complexity. This frequency is an expectation, not a measured result. |
| Trusted internal demonstration | The user chose to omit authentication and destination allowlists for the first version, with the boundary documented |
| Optional submission idempotency key | The user expects network instability to cause callers to retry submissions. Callers know which requests are new and which are retries, so they must provide a stable key when they need deduplication. The user clarified that this service's responsibility is to create only one task for repeated submissions with the same key, not to guarantee once-only business execution at the provider. |
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

## Phase 2 Implementation Record

At the user's request, the AI implemented durable submission and status queries. Changes include strict request validation, canonical UTF-8 body serialization and SHA-256 comparison, optional submission keys, PostgreSQL conflict handling for concurrent requests, commit-before-acceptance behavior, and public status projections. The existing migration already provides all required columns and the idempotency uniqueness constraint, so no schema migration or dependency change was needed. The existing isolated-schema fixture was shared with new integration tests.

The AI selected explicit validation defaults for keys (one header, 1–200 visible ASCII characters), header syntax, finite JSON numbers, valid UTF-8, and complete URLs. It also made validation and database error responses generic to avoid echoing credentials, and allowlisted status error categories. These implementation choices and canonicalization details are recorded in DESIGN.md; they are not attributed to individual user decisions.

Verification: 38 tests passed against real PostgreSQL, including two eight-request concurrency scenarios, independently observed persistence after acceptance, commit-stage failure injection and rollback, validation/redaction, safe status queries, and existing migration checks. Ruff lint and formatting checks passed. The initial sandboxed test process stalled and was interrupted; the complete suite succeeded with local database access authorized. The same two upstream TestClient deprecation warnings remain. Delivery, retries, recovery, and replay were not implemented or tested in this phase. No Git commit or push was performed.

Live HTTP verification also passed against a temporary Uvicorn process on port 8001 connected to the local Compose PostgreSQL database: readiness 200, submission 202, repeated submission with the same ID, conflicting submission 409, invalid submission 422, and status query 200. The `demo:phase2` task remains as demonstration data. The temporary API was stopped afterward. A fresh Compose build was interrupted because dependency downloads stalled; the updated container startup was not verified. Existing Compose containers were retained.

## Phase 3 Implementation Record

At the user's request, the AI implemented independent delivery and retries: short PostgreSQL claim/result transactions, attempt records and lease tokens, an asyncio worker, HTTPX streaming delivery, status classification, bounded exponential backoff with jitter, Retry-After parsing, and structured JSON logs. It added an in-memory mock provider, a Compose demonstration overlay, and a reusable five-scenario demo script. No schema or dependency changes were required.

Implementation choices made by the AI include finite timing validation; explicit permanent failure for invalid client-side requests; disabling environment proxies; constructing requests without inherited provider cookies; clearing the cookie jar; and suppressing HTTPX INFO logs that contain URLs. Result writes already check tokens and lease expiry, since safe result persistence needs that guard. Expired-lease recovery and replay remain unimplemented. Basic signal handling and database polling retries exist, but the broader phase 4 failure/recovery acceptance suite has not been performed.

Verification completed: Ruff lint and format checks; 88 passing tests with real PostgreSQL; exact forwarding, no response body consumption, redirect and cookie behavior; retry timing and exhaustion; concurrent claiming; and claim/result commit failure injection. A process-level integration test launched independent API, worker, and mock-provider processes, ran the documented demo script over local HTTP, and verified all five scenarios, including five-attempt timeout exhaustion. Test data used isolated schemas and no real provider or public API. Temporary processes were stopped and schemas removed. Compose overlay syntax/merge validation passed. Two upstream TestClient deprecation warnings remain. No Git commit or push was performed.

A new Compose image build was attempted with a 120-second limit. It timed out while uv was downloading locked dependencies; updated container startup was not verified, and existing containers were not replaced. This environment limitation is recorded separately from the successful local process-level delivery verification.

## Phase 4 Implementation Record

At the user's request to continue the next phase, the AI implemented recovery of expired in-progress tasks and the failed-task replay endpoint. Recovery atomically records an unknown previous outcome, either creates the next leased attempt or terminates an exhausted task, and logs only after commit. Replay preserves the original request and history and uses the observed round plus failed state in its conditional update. The round check was an AI implementation choice to prevent an already waiting replay from consuming a newer failed round. The existing worker shutdown, connection pre-ping, and database retry loop passed the new process-level checks without requiring runtime changes. No schema or dependency changes were needed.

The AI added PostgreSQL recovery/replay race and rollback tests, process-level SIGINT/SIGTERM/SIGKILL tests, and a local TCP relay that disconnects only test worker database connections. Tests wait for real seven-second lease expiry after process kills. The relay verifies recovery before claiming and after a successful mock provider response cannot be saved, without stopping the shared database. The delivery demonstration script now supports `--replay`, and its independent-process test verifies round 1 failure followed by round 2 success with the same task ID.

Verification completed: 106 passing tests against real PostgreSQL, followed by a passing targeted process-level test after adding the replay demonstration. Eight simultaneous replay requests produced one acceptance; stale results and delayed replays were rejected. A provider-success/database-loss case recorded two provider calls and unknown-outcome/succeeded attempt history. The final-attempt crash case recorded failure without a second call. Ruff lint/format and whitespace checks passed. The same two upstream TestClient deprecation warnings remain. Temporary processes, relay sockets, and test schemas were cleaned up. No real provider or public API was contacted.

Compose image building and container startup were not rerun in phase 4; the previous dependency download limitation remains recorded, and phase 5 will complete the remaining runtime verification. No Git commit or push was performed. No additional user technology choices or rejected suggestions were inferred.

## Phase 5 Implementation Record

At the user's request, the AI added a GitHub Actions workflow for locked installation, Ruff, the full PostgreSQL test suite, Compose configuration validation, and isolated Compose verification. It runs on push, pull request, or manual dispatch with read-only permissions and pinned official action commits, and does not deploy. The AI consulted official uv Docker and GitHub Actions documentation for the build-cache and workflow patterns. Python/runtime and dependency versions remain unchanged.

The AI split third-party installation into a separate Docker layer with BuildKit uv cache mounts. Investigation showed that the package index was reachable while wheel downloads were slow on both host and container. A timed host download retrieved only part of the wheel, and diagnostic downloads/builds were stopped. The project-specific existing uv cache was then imported into BuildKit using a temporary helper Dockerfile, preserving the locked package versions. The final application image built successfully, and the subsequent build reused its layers. A complete uncached network download was not successfully repeated.

The new `scripts/verify_compose.py` creates a randomly named project, available localhost ports, and a fresh database volume. Actual verification passed delivery and replay scenarios, stopped-worker backlog processing, SIGKILL recovery with two provider calls and unknown-outcome/succeeded history, PostgreSQL outage/readiness and subsequent processing, and data retention after container recreation. The script removed its own generated resources and did not replace the existing development services. `MOCK_PROVIDER_PORT` was added to make the provider's host port configurable.

Final checks performed: locked uv synchronization, Ruff lint and formatting, 106 passing tests against real PostgreSQL, workflow YAML parsing and required-command checks, Docker image build/cached rebuild, and the full isolated Compose script. Two upstream TestClient deprecation warnings remain. README, DESIGN, and PLAN were reconciled with implemented behavior and the actual verification evidence. At the time of this phase 5 record, the GitHub-hosted workflow had not been executed because the changes had not been pushed. No Git commit, push, or deployment was performed.

## User-Confirmed GitHub CI Result

The user checked GitHub and confirmed that CI passed, then requested a documentation status update. The AI updated the current status in README, DESIGN, PLAN, and this statement while preserving historical local verification records. The AI did not independently inspect the hosted run logs or test count, rerun tests, or perform a deployment for this documentation update.
