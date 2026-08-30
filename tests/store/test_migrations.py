"""The migration is the schema a deployment actually gets; auth/models.py's
MetaData is the schema the code queries. Nothing forces those to agree, so
this applies 0001_tenancy to a database (through LEDGER_SENSE_DB_URL, the same
override the API tests use) and diffs the result against the Table
definitions.

Always run against SQLite. When LEDGER_SENSE_LIVE_POSTGRES_URL is set (the
`backend` CI job's Postgres service container does this; nothing sets it
locally unless a developer opts in) the same assertions run again against
that live Postgres — the one place anything proves the schema alembic
builds is the schema Postgres actually accepts, not just SQLite plus an
offline DDL render.
"""

import os
import pathlib

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ledger_sense.auth.models import metadata
from ledger_sense.store import session as store_session

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def _alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


def _assert_migration_builds_the_schema_the_code_queries() -> None:
    store_session.reset_app_engine()
    try:
        config = _alembic_config()
        command.upgrade(config, "head")

        inspector = sa.inspect(store_session.app_engine())
        assert set(metadata.tables) <= set(inspector.get_table_names())
        for name, table in metadata.tables.items():
            migrated = {column["name"] for column in inspector.get_columns(name)}
            assert migrated == {column.name for column in table.columns}, name
            if name != "tenants":  # a tenant's own id is its tenant
                assert "tenant_id" in migrated, name

        command.downgrade(config, "base")
        left = set(sa.inspect(store_session.app_engine()).get_table_names())
        assert left - {"alembic_version"} == set()
    finally:
        store_session.reset_app_engine()


def test_the_migration_builds_the_schema_the_code_queries(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = (tmp_path / "migrated.db").as_posix()
    monkeypatch.setenv("LEDGER_SENSE_DB_URL", f"sqlite:///{database}")
    _assert_migration_builds_the_schema_the_code_queries()


@pytest.mark.skipif(
    not os.environ.get("LEDGER_SENSE_LIVE_POSTGRES_URL"),
    reason="set LEDGER_SENSE_LIVE_POSTGRES_URL to validate against a live Postgres",
)
def test_the_migration_builds_the_schema_the_code_queries_on_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEDGER_SENSE_DB_URL", os.environ["LEDGER_SENSE_LIVE_POSTGRES_URL"])
    _assert_migration_builds_the_schema_the_code_queries()


def test_no_table_escapes_the_tenancy_rule() -> None:
    """The standing constraint every later phase inherits: a table without
    tenant_id cannot be ownership-checked, and owned() is the whole boundary."""
    for name, table in metadata.tables.items():
        if name == "tenants":
            continue
        assert "tenant_id" in table.c, name
        assert not table.c.tenant_id.nullable, name
