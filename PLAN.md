# Implementation Plan

## Current Status and Scope

Phases 1 through 3 are complete: the foundation, durable submission, idempotency, queries, independent HTTP delivery, bounded retries, and mock provider are implemented and verified. Expired-lease recovery and replay remain planned. See [DESIGN.md](DESIGN.md) for behavior contracts and proposed defaults. This plan covers the minimum reliable delivery workflow, excluding production deployment and provider-specific business adapters.

- [x] Review the original requirements and confirm the technology stack and MVP boundaries.
- [x] Create the README, collaboration guidelines, implementation plan, design, and AI usage statement.

## Phase 1: Project Foundation and Database

- [x] Create the uv project, configure FastAPI, database access, the HTTP client, pytest, and Ruff, and include the lockfile in the project.
- [x] Add configuration, notification and attempt tables, an initial Alembic migration, and scheduling indexes.
- [x] Provide Docker Compose services for PostgreSQL, a one-time migration job, the API, and a separate worker; use a persistent database volume.
- [x] Provide example environment variables without real credentials and add verified startup and verification commands to the README.

Acceptance: a clean environment can install locked dependencies, run migrations, and start the API. Health checks distinguish process liveness from database readiness. The API and worker start after migrations succeed.

Verification: locked dependency installation, Ruff lint and formatting checks, and all eight tests passed, including real PostgreSQL migration and constraint tests. Compose startup confirmed that migrations finish before the API and worker start; both health endpoints returned 200. Container recreation retained the database volume and the services became ready again. Two upstream TestClient deprecation warnings remain; there were no test failures.

## Phase 2: Durable Submission and Status Queries

- [x] Implement request validation, submission, and status queries.
- [x] Implement optional idempotency keys, request comparison, and database uniqueness constraints, including concurrent submissions.
- [x] Acknowledge acceptance only after transaction commit; return an explicit failure when the database is unavailable.

Acceptance: valid requests return a task ID after persistence; invalid requests are rejected. The same key and request return the original task; the same key with different content causes a conflict. Concurrent duplicate submissions create only one task. Queries do not expose sensitive request data.

Verification: Ruff lint and formatting checks passed. All 38 tests passed, including real PostgreSQL persistence and query tests, eight concurrent matching submissions returning one task, eight conflicting submissions with exactly one acceptance, and rollback after an injected commit-stage failure. Validation and database failures return redacted responses. Existing migration and constraint tests still pass. Two upstream TestClient deprecation warnings remain. A temporary local Uvicorn API against the Compose PostgreSQL database also passed HTTP checks for readiness, submission, duplicate IDs, conflicts, validation, and queries. A fresh Compose image build was interrupted after dependency downloads stalled; startup with the new image was not verified.

## Phase 3: Delivery and Retries

- [x] Implement short claim transactions, lease tokens, independent worker polling, and HTTP delivery.
- [x] Implement timeouts, status classification, backoff, jitter, Retry-After handling, and attempt limits.
- [x] Persist attempt outcomes and diagnostic information, and provide structured logs without sensitive content.
- [x] Add a mock provider supporting success, temporary failures followed by success, permanent failures, and timeouts.

Acceptance: URLs, methods, business headers, and bodies are delivered according to the contract. Success stops retries, retryable failures are rescheduled, and permanent failures or exhausted attempts become failed tasks. No claim transaction remains open while waiting for HTTP responses.

Verification: all 88 tests passed, including real PostgreSQL transactions, four concurrent claimers, five supported HTTP methods and body variants, retry scheduling and exhaustion, result-token and expiry guards, and injected claim/result commit failures. The demo script passed with independent API, worker, and mock-provider processes using real local HTTP: success in one attempt, temporary failures followed by success in three, permanent failure in one, and unavailable/timeout failures in five. Ruff lint and formatting checks passed. Compose overlay configuration validation passed. The new image build timed out after 120 seconds while downloading locked dependencies, so updated container startup was not verified; existing containers were not replaced. Two upstream TestClient deprecation warnings remain.

## Phase 4: Recovery and Manual Replay

- [ ] Implement expired lease recovery and protection against writes using stale lease tokens.
- [ ] Implement graceful shutdown, backlog processing after restart, and recovery from temporary database unavailability.
- [ ] Allow replay only for failed tasks, retain attempt history, and prevent concurrent duplicate replays.

Acceptance: tests against real PostgreSQL confirm that multiple workers cannot claim the same valid lease simultaneously. Tasks recover after crashes, stale workers cannot overwrite newer results, and a crash on the final attempt cannot cause unlimited retries. Verify that delivery may repeat when the provider accepted a request but the local outcome was not saved.

## Phase 5: Delivery and Verification

- [ ] Complete unit tests, real PostgreSQL integration tests, and Compose demonstration checks.
- [ ] Add GitHub Actions to run Ruff and tests, without automatic deployment.
- [ ] Complete README examples for startup, submission, querying, replay, and testing.
- [ ] Reconcile DESIGN with the final implementation and update actual tradeoffs and AI usage records.

Acceptance: following the README reproduces successful delivery, recovery from temporary failure, retry exhaustion, and manual replay. Tasks remain processable after a worker restart. Required checks pass, and limitations and unimplemented capabilities are disclosed accurately.

## Execution and Maintenance

Complete phases in order; each depends on the preceding phase. If implementation reveals a design issue, update the relevant DESIGN contract and rationale, then align acceptance criteria. Phases 1 through 3 have passed acceptance. Phase 4 remains incomplete and phase 5 remains unstarted. Phase 3 already guards result writes with lease tokens and expiry and includes basic signal handling/database retry polling, but expired-lease recovery, replay, and the full crash/shutdown/outage acceptance suite remain phase 4 work. Tasks can currently remain in_progress after a crash or unsaved result.
