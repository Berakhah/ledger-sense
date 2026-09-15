"""Fixtures for the store tests.

`seeded_documents` gives the retention tests a real database: a SQLite file
built by running the real migrations (`alembic upgrade head`) through
LEDGER_SENSE_DB_URL, the same override tests/api/conftest.py uses. Not
create_all and not an in-memory list of Python objects — the whole claim under
test is that `purge_expired` deletes rows from the schema a deployment
actually has, and a fake store would pass that test while the product leaked
every document it was asked to delete.

Function-scoped: each retention test either purges the seeded document or
asserts it survived, so they need their own database, not a shared one whose
state depends on execution order.

The seeded document is 500 days old against a 365-day retention term. That
puts it past expiry as of `now` and comfortably inside it as of 400 days ago
— the two clocks the given tests evaluate.
"""

import pathlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy import Row

from ledger_sense.auth.models import tenants
from ledger_sense.boundary import Origin, Sensitivity
from ledger_sense.store import session as store_session
from ledger_sense.store.retention import claims, documents, retention_audit_log

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

EXPIRED_DOCUMENT_ID = "doc-expired"
RETENTION_DAYS = 365
DOCUMENT_AGE_DAYS = 500


class SeededDocuments:
    """The seeded rows, plus the two read-backs the given tests make.

    Both helpers query the live tables rather than remembering what was
    inserted: after `purge_expired` runs, "what does the database now hold"
    is the only question worth asking.
    """

    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id

    def _rows(self, statement: sa.Select[tuple[object, ...]]) -> list[Row[tuple[object, ...]]]:
        with store_session.app_engine().connect() as conn:
            return list(conn.execute(statement).all())

    def claims_for(self, document_id: str) -> list[Row[tuple[object, ...]]]:
        return self._rows(sa.select(claims).where(claims.c.document_id == document_id))

    def audit_for(self, document_id: str) -> list[Row[tuple[object, ...]]]:
        return self._rows(
            sa.select(retention_audit_log)
            .where(retention_audit_log.c.document_id == document_id)
        )

    def documents_for(self, document_id: str) -> list[Row[tuple[object, ...]]]:
        return self._rows(sa.select(documents).where(documents.c.id == document_id))

    def add_document(self, document_id: str, *, age_days: int,
                     blob_id: str | None = None) -> str:
        """Seed one more document, for tests that need a blob attached.

        A raw insert, not store_document: these rows carry blob_id=None (a
        purged or never-landed blob), which the one writer cannot produce.
        The label columns are explicit because the schema refuses a row
        without them — that refusal is the point of 0003.
        """
        with store_session.app_engine().begin() as conn:
            conn.execute(documents.insert().values(
                id=document_id,
                tenant_id=self.tenant_id,
                blob_id=blob_id,
                received_at=datetime.now(UTC) - timedelta(days=age_days),
                sensitivity=Sensitivity.SYNTHETIC.value,
                origin=Origin.CORPUS.value,
                provenance="tests/store/conftest.py",
                filename="tests/store/conftest.py",
            ))
        return document_id


@pytest.fixture
def seeded_documents(tmp_path: pathlib.Path) -> Iterator[SeededDocuments]:
    from alembic import command
    from alembic.config import Config

    database = (tmp_path / "retention.db").as_posix()
    with pytest.MonkeyPatch.context() as patch:
        # as_posix(): a Windows path's backslashes and drive colon do not
        # survive SQLAlchemy's URL parser.
        patch.setenv("LEDGER_SENSE_DB_URL", f"sqlite:///{database}")
        store_session.reset_app_engine()

        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        command.upgrade(config, "head")

        tenant_id = uuid.uuid4().hex
        with store_session.app_engine().begin() as conn:
            conn.execute(tenants.insert().values(
                id=tenant_id,
                name="seeded-tenant",
                created_at=datetime.now(UTC) - timedelta(days=DOCUMENT_AGE_DAYS + 1),
                retention_days=RETENTION_DAYS,
            ))
            conn.execute(documents.insert().values(
                id=EXPIRED_DOCUMENT_ID,
                tenant_id=tenant_id,
                blob_id=None,
                received_at=datetime.now(UTC) - timedelta(days=DOCUMENT_AGE_DAYS),
                sensitivity=Sensitivity.SYNTHETIC.value,
                origin=Origin.CORPUS.value,
                provenance="tests/store/conftest.py",
                filename="tests/store/conftest.py",
            ))
            conn.execute(claims.insert().values(
                id=uuid.uuid4().hex,
                tenant_id=tenant_id,
                document_id=EXPIRED_DOCUMENT_ID,
                field="total_completed_and_stored",
                value="0.00",
                value_type="money",
                method="deterministic",
                page=0, x0=0.0, top=0.0, x1=0.0, bottom=0.0,
                observed_at=datetime.now(UTC),
            ))

        yield SeededDocuments(tenant_id)
    store_session.reset_app_engine()
