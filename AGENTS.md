# Repository Collaboration Guidelines

## Documentation Entry Points

This project is an internal HTTP notification service MVP. See [DESIGN.md](DESIGN.md) for design contracts and [PLAN.md](PLAN.md) for implementation progress. Read the relevant documentation and existing code before making changes.

## Tools and Code Conventions

- Write all repository content and future deliverables in English, including documentation, code, comments, docstrings, logs, and test descriptions. Use Chinese only for conversation with the user.
- Manage Python dependencies with uv and maintain `pyproject.toml` and `uv.lock`.
- Follow the existing code structure and style, keep responsibilities clear, and avoid unrelated refactoring or premature abstractions.
- Use versioned migrations for database schema changes.

## Verification Requirements

- Run checks relevant to changed behavior. Tests of database-specific behavior must use the actual database engine.
- Use controlled substitutes for external dependencies in automated tests; do not depend on real provider accounts or public APIs.
- Use README.md as the entry point for development and verification commands. Verify and document new or changed commands.
- Report only checks actually performed and their results. Explicitly identify checks that were not run or were blocked.

## Documentation Maintenance

- `README.md`: project entry point, verified operating instructions, and demonstrations.
- `PLAN.md`: task status and acceptance criteria; mark tasks complete only after acceptance.
- `DESIGN.md`: the detailed source of truth for behavior contracts, reliability mechanisms, and design tradeoffs. Update it when changing APIs, data structures, state transitions, or retry behavior.
- `AI_USAGE.md`: an ongoing factual record of AI contributions, user decisions, and rejected suggestions. Do not invent the user's reasoning or rejection history.
- Record design changes and their rationale in DESIGN.md rather than duplicating service mechanisms or runtime parameters here. Clearly distinguish planned capabilities from implemented behavior in all documentation.
