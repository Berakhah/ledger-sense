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

# `uv run` (the CMD below) writes to its own cache dir on every invocation,
# separate from UV_PROJECT_ENVIRONMENT above — default is $HOME/.cache/uv,
# which does not exist under this image's read_only: true + tmpfs-only /tmp
# and /app/scratch (docker-compose.yml). /tmp is the tmpfs one of those two
# still free for this: /app/scratch is reserved for the documents this
# service actually parses.
ENV UV_CACHE_DIR=/tmp/uv-cache

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

RUN chown -R ledger:ledger /app

# C1: docker-compose.yml bind-mounts a named volume at this exact path for
# the client profile. Docker initialises an empty named volume's mountpoint
# by copying whatever already exists there in the image — root:root 0755 if
# nothing does — so without this the directory BlobStore.put() writes into
# is owned by root while the process runs as `ledger`, and every upload
# fails with PermissionError. Creating and chowning it here, before the
# volume is ever mounted, is what Docker copies onto the volume on first
# mount.
RUN mkdir -p /var/lib/ledger-sense/blobs && chown ledger:ledger /var/lib/ledger-sense/blobs

USER ledger

# --frozen --no-dev: the exact flags the two `uv sync` layers above already
# built the venv with. Without them, `uv run` re-syncs on every start against
# the *full* dependency set (dev group included — mypy, ruff, hypothesis...),
# which is both wrong for a runtime image and, under read_only: true, a crash:
# it cannot write the packages it decides to add to /app/.venv.
CMD ["uv", "run", "--frozen", "--no-dev", "python", "-m", "ledger_sense"]
