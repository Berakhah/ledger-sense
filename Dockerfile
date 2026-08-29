# Ledger Sense app image. Non-root, read-only rootfs at runtime (see
# docker-compose.yml: read_only + tmpfs scratch) — this is the service that
# parses hostile PDFs.

FROM python:3.12-slim AS base

RUN groupadd --gid 1000 ledger \
    && useradd --uid 1000 --gid ledger --create-home --shell /usr/sbin/nologin ledger

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /usr/local/bin/

WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

RUN chown -R ledger:ledger /app
USER ledger

CMD ["uv", "run", "python", "-m", "ledger_sense"]
