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


def _url(scheme: str = "postgresql") -> str:
    """Connection URL for `scheme`, built from the env-configured
    parameters. `psycopg.connect()` accepts a plain "postgresql://" URI
    directly; SQLAlchemy needs the "postgresql+psycopg" driver prefix to
    pick the psycopg3 dialect."""
    p = _params()
    return f"{scheme}://{p['user']}:{p['password']}@{p['host']}:{p['port']}/{p['dbname']}"


def connect() -> psycopg.Connection[tuple[Any, ...]]:
    """Open a new psycopg connection using the env-configured parameters."""
    return psycopg.connect(_url())


def engine() -> Engine:
    """SQLAlchemy engine for Alembic (and any future ORM use), built from
    the same env-configured parameters as connect(). create_engine() is
    lazy — this does not open a socket until the engine is used."""
    return create_engine(_url("postgresql+psycopg"))
