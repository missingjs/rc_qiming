# Implementation Plan

## Current Status and Scope

Requirements discussions and initial documentation are complete; the application has not been implemented. See [DESIGN.md](DESIGN.md) for behavior contracts and proposed defaults. This plan covers the minimum reliable delivery workflow, excluding production deployment and provider-specific business adapters.

- [x] Review the original requirements and confirm the technology stack and MVP boundaries.
- [x] Create the README, collaboration guidelines, implementation plan, design, and AI usage statement.

## Phase 1: Project Foundation and Database

- [ ] Create the uv project, configure FastAPI, database access, the HTTP client, pytest, and Ruff, and commit the lockfile.
- [ ] Add configuration, notification and attempt tables, an initial Alembic migration, and scheduling indexes.
- [ ] Provide Docker Compose services for PostgreSQL, a one-time migration job, the API, and a separate worker; use a persistent database volume.
- [ ] Provide example environment variables without real credentials and add verified startup and verification commands to the README.

Acceptance: a clean environment can install locked dependencies, run migrations, and start the API. Health checks distinguish process liveness from database readiness. The API and worker start after migrations succeed.

## Phase 2: Durable Submission and Status Queries

- [ ] Implement request validation, submission, and status queries.
- [ ] Implement optional idempotency keys, request comparison, and database uniqueness constraints, including concurrent submissions.
- [ ] Acknowledge acceptance only after transaction commit; return an explicit failure when the database is unavailable.

Acceptance: valid requests return a task ID after persistence; invalid requests are rejected. The same key and request return the original task; the same key with different content causes a conflict. Concurrent duplicate submissions create only one task. Queries do not expose sensitive request data.

## Phase 3: Delivery and Retries

- [ ] Implement short claim transactions, lease tokens, independent worker polling, and HTTP delivery.
- [ ] Implement timeouts, status classification, backoff, jitter, Retry-After handling, and attempt limits.
- [ ] Persist attempt outcomes and diagnostic information, and provide structured logs without sensitive content.
- [ ] Add a mock provider supporting success, temporary failures followed by success, permanent failures, and timeouts.

Acceptance: URLs, methods, business headers, and bodies are delivered according to the contract. Success stops retries, retryable failures are rescheduled, and permanent failures or exhausted attempts become failed tasks. No claim transaction remains open while waiting for HTTP responses.

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

Complete phases in order; each depends on the preceding phase. If implementation reveals a design issue, update the relevant DESIGN contract and rationale, then align acceptance criteria. All implementation phases are currently unstarted; completed documentation does not constitute implementation acceptance.
