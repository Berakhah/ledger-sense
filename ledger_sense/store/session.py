"""Postgres connection factory (F-19/G-3, "one connection factory").

The Postgres-side equivalent of store/sqlite.py. This is the only module in
the codebase allowed to call psycopg.connect or sqlalchemy.create_engine
(enforced by
tests/test_platform_skeleton.py::test_no_module_opens_a_database_connection_directly)
— every future Postgres-backed store, and migrations/env.py, get a
connection or engine from here rather than building one itself.

Connection parameters come from the environment, matching the POSTGRES_*
variables docker-compose.yml already defines (see .env.example), so the same
factory works unchanged under either the `client` or `demo` compose profile.
No table/session model exists yet — Task P2 adds the first migration and the
ORM metadata it needs; this module only has to hand out a working connection
and engine for that to build on.
"""

import os
from typing import Any

import psycopg
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL

# Fail fast against a down/misconfigured database rather than hang: verified
# empirically that `alembic upgrade head` with no server listening hung past
# 120s with no timeout set anywhere in this module.
CONNECT_TIMEOUT_SECONDS = 5


def _params() -> dict[str, str]:
    """Read connection parameters from the environment. Defaults mirror
    docker-compose.yml's own ${VAR:-default} fallbacks."""
    return {
        "host": os.environ.get("POSTGRES_HOST", "localhost"),
        "port": os.environ.get("POSTGRES_PORT", "5432"),
        "user": os.environ.get("POSTGRES_USER", "ledger_sense"),
        "password": os.environ.get("POSTGRES_PASSWORD", "changeme"),
        "dbname": os.environ.get("POSTGRES_DB", "ledger_sense"),
    }


def connect() -> psycopg.Connection[tuple[Any, ...]]:
    """Open a new psycopg connection using the env-configured parameters.

    Passes host/user/password/etc as keyword args rather than building a
    "user:password@host" URL string by hand — a password containing "@",
    ":", "/", or "%" (realistic for a generated secret) would otherwise be
    misparsed. connect_timeout bounds how long a bad host/down database can
    block a caller.
    """
    p = _params()
    return psycopg.connect(
        host=p["host"],
        port=p["port"],
        user=p["user"],
        password=p["password"],
        dbname=p["dbname"],
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
    )


def engine() -> Engine:
    """SQLAlchemy engine for Alembic (and any future ORM use), built from
    the same env-configured parameters as connect(). create_engine() is
    lazy — this does not open a socket until the engine is used.

    Uses URL.create() rather than an f-string for the same reason connect()
    uses keyword args: URL.create() escapes each component, so a password
    with special characters round-trips correctly instead of being
    misparsed as part of the host/path. connect_args carries the same
    connect_timeout connect() sets.
    """
    p = _params()
    url = URL.create(
        "postgresql+psycopg",
        username=p["user"],
        password=p["password"],
        host=p["host"],
        port=int(p["port"]),
        database=p["dbname"],
    )
    return create_engine(url, connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS})
