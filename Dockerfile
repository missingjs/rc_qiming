FROM ghcr.io/astral-sh/uv:0.12.5 AS uv
FROM python:3.14-slim-bookworm
COPY --from=uv /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --locked --no-dev --no-editable
RUN useradd --system --uid 10001 app
USER app
CMD ["uvicorn", "notification_service.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
