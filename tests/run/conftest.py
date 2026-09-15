"""Fixtures for the runner tests.

A real SQLite database built by `alembic upgrade head` (the same override
tests/api and tests/store use) plus a real BlobStore reached the way the
runner reaches it — LEDGER_SENSE_KEK / LEDGER_SENSE_BLOB_ROOT — so the test
exercises load_document's decrypt path, not a stub.

`RunDb.store_pdf` puts bytes through the labelled door as CORPUS/SYNTHETIC
(the only label a test may assert) and the one writer; it is the only way a
test here gets a document row.
"""

import logging
import pathlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from sqlalchemy import Row
from sqlalchemy.engine import Engine

from ledger_sense.auth.models import create_tenant
from ledger_sense.boundary import Origin
from ledger_sense.ingest.intake import accept_document
from ledger_sense.store import session as store_session
from ledger_sense.store.blobs import blob_store_from_environment
from ledger_sense.store.documents import store_document
from ledger_sense.store.retention import claims, documents, findings

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


class RunDb:
    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self.engine = engine
        self.tenant_id = tenant_id

    def store_pdf(self, pdf: bytes, *, tenant_id: str | None = None,
                  received_at: datetime | None = None, filename: str = "payapp.pdf") -> str:
        doc = accept_document(pdf, origin=Origin.CORPUS, provenance="tests/run/conftest.py")
        with self.engine.begin() as conn:
            return store_document(
                conn, tenant_id=tenant_id or self.tenant_id, doc=doc,
                blobs=blob_store_from_environment(),
                received_at=received_at or datetime.now(UTC), filename=filename)

    def document(self, document_id: str) -> Row[tuple[object, ...]]:
        with self.engine.connect() as conn:
            return conn.execute(
                sa.select(documents).where(documents.c.id == document_id)).one()

    def claims_for(self, document_id: str) -> list[Row[tuple[object, ...]]]:
        with self.engine.connect() as conn:
            return list(conn.execute(
                sa.select(claims).where(claims.c.document_id == document_id)).all())

    def findings_for(self, document_id: str) -> list[Row[tuple[object, ...]]]:
        with self.engine.connect() as conn:
            return list(conn.execute(
                sa.select(findings).where(findings.c.document_id == document_id)
                .order_by(findings.c.rule_id, findings.c.field)).all())

    def new_tenant(self) -> str:
        return create_tenant(f"tenant-{uuid.uuid4().hex[:8]}")


@pytest.fixture
def db(tmp_path: pathlib.Path) -> Iterator[RunDb]:
    from alembic import command
    from alembic.config import Config

    database = (tmp_path / "run.db").as_posix()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("LEDGER_SENSE_DB_URL", f"sqlite:///{database}")
        patch.setenv("LEDGER_SENSE_KEK", Fernet.generate_key().decode())
        patch.setenv("LEDGER_SENSE_BLOB_ROOT", str(tmp_path / "blobs"))
        store_session.reset_app_engine()
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        command.upgrade(config, "head")
        # migrations/env.py calls logging.config.fileConfig(alembic.ini),
        # whose default disable_existing_loggers=True silently disables
        # every logger already created in this process (e.g.
        # ledger_sense.run.document's), so log.exception() elsewhere in the
        # suite stops reaching caplog after the first test using this
        # fixture. Undo that side effect rather than let it leak forward.
        for logger in logging.Logger.manager.loggerDict.values():
            if isinstance(logger, logging.Logger):
                logger.disabled = False
        engine = store_session.app_engine()
        yield RunDb(engine, create_tenant(f"tenant-{uuid.uuid4().hex[:8]}"))
    store_session.reset_app_engine()
