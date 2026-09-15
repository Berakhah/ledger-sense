import pathlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet

from ledger_sense.store import session as store_session
from ledger_sense.store.blobs import BlobStore
from ledger_sense.store.retention import findings, purge_expired, retention_days
from tests.store.conftest import EXPIRED_DOCUMENT_ID, SeededDocuments


def test_a_document_past_its_retention_period_is_purged(
    seeded_documents: SeededDocuments,
) -> None:
    assert purge_expired(now=datetime.now(UTC)).documents_purged == 1


def test_a_document_inside_its_retention_period_survives(
    seeded_documents: SeededDocuments,
) -> None:
    earlier = datetime.now(UTC) - timedelta(days=400)
    assert purge_expired(now=earlier).documents_purged == 0


def test_purging_removes_the_derived_claims_too(seeded_documents: SeededDocuments) -> None:
    """A deletion that leaves the extracted bank details behind is not a deletion."""
    purge_expired(now=datetime.now(UTC))
    assert seeded_documents.claims_for("doc-expired") == []


def test_purging_keeps_the_audit_entry_that_records_the_purge(
    seeded_documents: SeededDocuments,
) -> None:
    """The audit trail records the deletion; it is not itself deleted."""
    purge_expired(now=datetime.now(UTC))
    assert any(e.action == "purge" for e in seeded_documents.audit_for("doc-expired"))


def test_the_retention_period_is_per_tenant_not_a_global_constant() -> None:
    """It is a term in a signed data agreement, not a number we choose."""
    import inspect
    assert "tenant_id" in inspect.signature(retention_days).parameters


# --- added here, not given in the brief: the ciphertext is client data too ---


def test_purging_deletes_the_encrypted_original_too(
    seeded_documents: SeededDocuments, tmp_path: pathlib.Path
) -> None:
    """A deletion that leaves the encrypted PDF on disk is not a deletion
    either — the claims test's argument applies to the blob it came from."""
    store = BlobStore(root=str(tmp_path / "blobs"), kek=Fernet.generate_key())
    blob_id = store.put(seeded_documents.tenant_id, b"%PDF-1.4 ACCOUNT 123456789")
    seeded_documents.add_document("doc-with-blob", age_days=500, blob_id=blob_id)

    report = purge_expired(now=datetime.now(UTC), blobs=store)

    assert report.blobs_deleted == 1
    assert store.raw(blob_id) is None


def test_a_purge_that_cannot_reach_the_ciphertext_refuses_to_run(
    seeded_documents: SeededDocuments,
) -> None:
    """Deleting the row and stranding the encrypted original is worse than
    not running: nothing afterwards knows the file is there to delete."""
    seeded_documents.add_document("doc-with-blob", age_days=500, blob_id="0" * 32)

    with pytest.raises(RuntimeError, match="BlobStore"):
        purge_expired(now=datetime.now(UTC))

    assert seeded_documents.documents_for("doc-expired") != []


def test_purge_removes_the_findings_derived_from_an_expired_document(
    seeded_documents: SeededDocuments,
) -> None:
    with store_session.app_engine().begin() as conn:
        conn.execute(findings.insert().values(
            id=uuid.uuid4().hex, tenant_id=seeded_documents.tenant_id,
            document_id=EXPIRED_DOCUMENT_ID, rule_id="A1", field="total_completed_and_stored",
            message="does not foot", expected="1.00", actual="2.00",
            page=0, x0=1.0, top=1.0, x1=2.0, bottom=2.0, created_at=datetime.now(UTC)))
    report = purge_expired(now=datetime.now(UTC), blobs=None)
    assert report.findings_purged == 1
    with store_session.app_engine().connect() as conn:
        left = conn.execute(
            sa.select(findings).where(findings.c.document_id == EXPIRED_DOCUMENT_ID)).all()
    assert left == []
