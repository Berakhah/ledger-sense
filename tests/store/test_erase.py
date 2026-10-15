"""A client erasure request: delete a tenant's documents now, not at term end.

Same deletion as the retention purge — blob first, then claims, findings and
the vendor-ledger history, then the document — with the operator named in the
audit trail and the request's reason recorded. Dry run is the default because
the deletion cannot be undone.
"""

import pathlib
import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet

from ledger_sense.auth.models import tenants
from ledger_sense.boundary import Origin, Sensitivity
from ledger_sense.store import session as store_session
from ledger_sense.store.blobs import BlobStore
from ledger_sense.store.retention import (
    PurgeReport,
    documents,
    erase_documents,
    payment_details,
    retention_audit_log,
    vendors,
)
from tests.store.conftest import EXPIRED_DOCUMENT_ID, SeededDocuments

LIVE = "doc-live"
OTHER_TENANT_DOC = "doc-other"


def _erase(
    seeded: SeededDocuments,
    *,
    tenant_id: str | None = None,
    document_ids: list[str] | None = None,
    blobs: BlobStore | None = None,
    execute: bool = True,
) -> PurgeReport:
    return erase_documents(
        tenant_id or seeded.tenant_id,
        actor="operator:ada",
        reason="client request 2026-10-15",
        document_ids=document_ids,
        blobs=blobs,
        execute=execute,
    )


def _other_tenant() -> str:
    other = uuid.uuid4().hex
    with store_session.app_engine().begin() as conn:
        conn.execute(tenants.insert().values(
            id=other, name="other", created_at=datetime.now(UTC), retention_days=365))
        conn.execute(documents.insert().values(
            id=OTHER_TENANT_DOC, tenant_id=other, blob_id=None,
            received_at=datetime.now(UTC), sensitivity=Sensitivity.SYNTHETIC.value,
            origin=Origin.CORPUS.value, provenance="tests", filename="tests"))
    return other


def test_erases_every_document_of_the_tenant_including_unexpired(
    seeded_documents: SeededDocuments,
) -> None:
    seeded_documents.add_document(LIVE, age_days=1)

    report = _erase(seeded_documents)

    assert seeded_documents.documents_for(LIVE) == []
    assert seeded_documents.documents_for(EXPIRED_DOCUMENT_ID) == []
    assert report.documents_purged == 2


def test_audit_row_names_the_operator_the_reason_and_the_erasure_action(
    seeded_documents: SeededDocuments,
) -> None:
    _erase(seeded_documents)

    (row,) = seeded_documents.audit_for(EXPIRED_DOCUMENT_ID)
    assert row.action == "erasure"
    assert row.actor == "operator:ada"
    assert "client request 2026-10-15" in row.reason
    assert row.claims_purged == 1


def test_document_ids_scope_the_erasure(seeded_documents: SeededDocuments) -> None:
    seeded_documents.add_document(LIVE, age_days=1)

    _erase(seeded_documents, document_ids=[LIVE])

    assert seeded_documents.documents_for(LIVE) == []
    assert seeded_documents.documents_for(EXPIRED_DOCUMENT_ID) != []


def test_another_tenants_documents_are_untouched(seeded_documents: SeededDocuments) -> None:
    _other_tenant()

    _erase(seeded_documents)

    assert seeded_documents.documents_for(OTHER_TENANT_DOC) != []


def test_a_document_id_from_another_tenant_is_refused_and_nothing_is_deleted(
    seeded_documents: SeededDocuments,
) -> None:
    _other_tenant()

    with pytest.raises(LookupError, match=OTHER_TENANT_DOC):
        _erase(seeded_documents, document_ids=[EXPIRED_DOCUMENT_ID, OTHER_TENANT_DOC])

    assert seeded_documents.documents_for(EXPIRED_DOCUMENT_ID) != []
    assert seeded_documents.documents_for(OTHER_TENANT_DOC) != []


def test_an_unknown_tenant_is_refused(seeded_documents: SeededDocuments) -> None:
    with pytest.raises(LookupError, match="no such tenant"):
        _erase(seeded_documents, tenant_id="nobody")


def test_dry_run_reports_but_deletes_and_audits_nothing(
    seeded_documents: SeededDocuments,
) -> None:
    report = _erase(seeded_documents, execute=False)

    assert report.documents_purged == 1
    assert report.claims_purged == 1
    assert seeded_documents.documents_for(EXPIRED_DOCUMENT_ID) != []
    assert seeded_documents.claims_for(EXPIRED_DOCUMENT_ID) != []
    assert seeded_documents.audit_for(EXPIRED_DOCUMENT_ID) == []


def test_the_encrypted_original_is_deleted_with_the_rows(
    seeded_documents: SeededDocuments, tmp_path: pathlib.Path,
) -> None:
    blobs = BlobStore(root=str(tmp_path / "blobs"), kek=Fernet.generate_key())
    blob_id = blobs.put(seeded_documents.tenant_id, b"%PDF-1.7 pay app")
    seeded_documents.add_document(LIVE, age_days=1, blob_id=blob_id)

    report = _erase(seeded_documents, blobs=blobs, document_ids=[LIVE])

    assert report.blobs_deleted == 1
    assert blobs.delete(seeded_documents.tenant_id, blob_id) is False  # already gone


def test_dry_run_leaves_the_encrypted_original_in_place(
    seeded_documents: SeededDocuments, tmp_path: pathlib.Path,
) -> None:
    blobs = BlobStore(root=str(tmp_path / "blobs"), kek=Fernet.generate_key())
    blob_id = blobs.put(seeded_documents.tenant_id, b"%PDF-1.7 pay app")
    seeded_documents.add_document(LIVE, age_days=1, blob_id=blob_id)

    _erase(seeded_documents, blobs=blobs, document_ids=[LIVE], execute=False)

    assert blobs.delete(seeded_documents.tenant_id, blob_id) is True  # was still there


def test_documents_with_originals_and_no_blob_store_are_refused_untouched(
    seeded_documents: SeededDocuments,
) -> None:
    seeded_documents.add_document(LIVE, age_days=1, blob_id="somewhere")

    with pytest.raises(RuntimeError, match="BlobStore"):
        _erase(seeded_documents)

    assert seeded_documents.documents_for(LIVE) != []
    assert seeded_documents.documents_for(EXPIRED_DOCUMENT_ID) != []


def test_vendor_ledger_history_goes_with_the_erased_documents(
    seeded_documents: SeededDocuments,
) -> None:
    tenant = seeded_documents.tenant_id
    vendor = uuid.uuid4().hex
    with store_session.app_engine().begin() as conn:
        conn.execute(vendors.insert().values(id=vendor, tenant_id=tenant, name="Acme"))
        conn.execute(payment_details.insert().values(
            id=uuid.uuid4().hex, tenant_id=tenant, vendor_id=vendor,
            account_last4="6789", routing_last4="0021", account_hmac="ab" * 32,
            observed_at=datetime.now(UTC), source_document=EXPIRED_DOCUMENT_ID))

    report = _erase(seeded_documents)

    assert report.payment_details_purged == 1
    assert report.vendors_purged == 1
    with store_session.app_engine().connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(vendors)).scalar() == 0


def test_a_rerun_is_a_no_op_and_the_first_audit_row_survives(
    seeded_documents: SeededDocuments,
) -> None:
    _erase(seeded_documents)
    second = _erase(seeded_documents)

    assert second.documents_purged == 0
    with store_session.app_engine().connect() as conn:
        count = conn.execute(
            sa.select(sa.func.count()).select_from(retention_audit_log)).scalar()
    assert count == 1
