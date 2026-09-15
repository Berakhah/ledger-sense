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
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ledger_sense.auth.models import metadata, tenants
from ledger_sense.boundary import Origin, Sensitivity
from ledger_sense.store import session as store_session
from tests.store.conftest import SeededDocuments

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

        # 0003's CHECKs are named so SQLite batch recreate (which Phase 2 and 4
        # will run on this table again) keeps them. Reflection sees the names.
        checks = {c["name"] for c in inspector.get_check_constraints("documents")}
        assert {"ck_documents_sensitivity", "ck_documents_origin"} <= checks, checks

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


_PRE_0003_ROW = {
    "id": "doc-pre-0003",
    "tenant_id": "tenant-pre-0003",
    # SQLAlchemy's SQLite DateTime storage format, so the Table can read it back.
    "received_at": "2026-09-14 00:00:00.000000",
}


def _seed_a_pre_0003_document(conn: sa.Connection) -> None:
    conn.execute(tenants.insert().values(
        id=_PRE_0003_ROW["tenant_id"], name="pre-0003",
        created_at=datetime.now(UTC), retention_days=365))
    conn.execute(sa.text(
        "INSERT INTO documents (id, tenant_id, blob_id, received_at) "
        "VALUES (:id, :tenant_id, NULL, :received_at)"), _PRE_0003_ROW)
    conn.execute(sa.text(
        "INSERT INTO claims (id, tenant_id, document_id) "
        "VALUES ('claim-pre-0003', :tenant_id, :id)"), _PRE_0003_ROW)


def test_0003_round_trips_on_a_populated_database(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A migration that fails on a populated table fails in front of a client.
    A row that predates the label is backfilled as confidential-from-an-upload
    — the only honest guess — and survives downgrade and re-upgrade. The claim
    row rides along: SQLite batch recreate on a table that claims references
    must not lose either side."""
    database = (tmp_path / "populated.db").as_posix()
    monkeypatch.setenv("LEDGER_SENSE_DB_URL", f"sqlite:///{database}")
    store_session.reset_app_engine()
    try:
        config = _alembic_config()
        command.upgrade(config, "0002_retention")
        with store_session.app_engine().begin() as conn:
            _seed_a_pre_0003_document(conn)

        command.upgrade(config, "head")
        with store_session.app_engine().connect() as conn:
            row = conn.execute(sa.text(
                "SELECT sensitivity, origin, provenance FROM documents WHERE id = :id"),
                {"id": _PRE_0003_ROW["id"]}).one()
        assert tuple(row) == ("CLIENT_CONFIDENTIAL", "UPLOAD", "unknown:pre-0003")

        command.downgrade(config, "0002_retention")
        columns = {c["name"] for c in sa.inspect(store_session.app_engine())
                   .get_columns("documents")}
        assert columns == {"id", "tenant_id", "blob_id", "received_at"}
        with store_session.app_engine().connect() as conn:
            assert conn.execute(sa.text("SELECT COUNT(*) FROM documents")).scalar() == 1
            assert conn.execute(sa.text("SELECT COUNT(*) FROM claims")).scalar() == 1

        command.upgrade(config, "head")
        with store_session.app_engine().connect() as conn:
            assert conn.execute(sa.text("SELECT COUNT(*) FROM documents")).scalar() == 1
    finally:
        store_session.reset_app_engine()


def _raw_insert(conn: sa.Connection, tenant_id: str, *, document_id: str,
                sensitivity: str, origin: str) -> None:
    conn.execute(sa.text(
        "INSERT INTO documents (id, tenant_id, blob_id, received_at, sensitivity, origin, "
        "provenance) VALUES (:id, :tenant_id, NULL, :received_at, :sensitivity, :origin, "
        "'tests/store/test_migrations.py')"),
        {"id": document_id, "tenant_id": tenant_id, "received_at": _PRE_0003_ROW["received_at"],
         "sensitivity": sensitivity, "origin": origin})


def test_the_server_defaults_are_gone_after_upgrade(
    seeded_documents: SeededDocuments,
) -> None:
    """0003's second batch block drops the server defaults added by the
    first. If that block silently failed to drop them, a raw INSERT omitting
    the label columns would succeed with an invented value — exactly the
    failure the docstring's decision 2 says the drop exists to prevent."""
    with pytest.raises(sa.exc.IntegrityError), store_session.app_engine().begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO documents (id, tenant_id, blob_id, received_at) "
            "VALUES (:id, :tenant_id, NULL, :received_at)"),
            {"id": "doc-no-label", "tenant_id": seeded_documents.tenant_id,
             "received_at": _PRE_0003_ROW["received_at"]})


def test_the_check_refuses_a_label_outside_the_vocabulary(
    seeded_documents: SeededDocuments,
) -> None:
    with pytest.raises(sa.exc.IntegrityError), store_session.app_engine().begin() as conn:
        _raw_insert(conn, seeded_documents.tenant_id, document_id="doc-bad-label",
                    sensitivity="FREE_TIER_OK", origin="UPLOAD")
    with pytest.raises(sa.exc.IntegrityError), store_session.app_engine().begin() as conn:
        _raw_insert(conn, seeded_documents.tenant_id, document_id="doc-bad-origin",
                    sensitivity="SYNTHETIC", origin="EMAIL")


@pytest.mark.parametrize("sensitivity", list(Sensitivity))
@pytest.mark.parametrize("origin", list(Origin))
def test_the_check_accepts_every_enum_value(
    seeded_documents: SeededDocuments, sensitivity: Sensitivity, origin: Origin
) -> None:
    """The enum and the CHECK agree in both directions: the migration wrote the
    values out as literals, so this is the test that they were copied right."""
    with store_session.app_engine().begin() as conn:
        _raw_insert(conn, seeded_documents.tenant_id,
                    document_id=f"doc-{sensitivity.value}-{origin.value}",
                    sensitivity=sensitivity.value, origin=origin.value)
