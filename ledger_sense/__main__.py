"""`python -m ledger_sense` — the container's CMD (see Dockerfile) and the
documented dev entrypoint (pyproject.toml). Migrates, then serves api.app:app
over ASGI.

Nothing else in the stack runs `alembic upgrade head` against the real
Postgres (tests/api/conftest.py does it for the SQLite fixture only) — this
is the one place `docker compose up` on a clean machine gets a schema.
"""

import pathlib

import uvicorn
from alembic import command
from alembic.config import Config

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _migrate() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "head")


def main() -> None:
    _migrate()
    # 0.0.0.0: this process IS the container's edge for port 8000
    # (docker-compose.yml maps "8000:8000") — binding to localhost only would
    # make it unreachable from the host or from `web`.
    uvicorn.run("ledger_sense.api.app:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
