"""§4: the label survives a restart. A stored document reaches the gateway as
the same Labelled it came in as, never re-labelled by hand."""

import pathlib
from datetime import UTC, datetime
from typing import cast

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Connection

from ledger_sense.boundary import Labelled, Origin, Sensitivity, SensitivityViolation
from ledger_sense.gateway import Lane, ProviderPolicy, admit
from ledger_sense.ingest.intake import RedactionVerdict, accept_document
from ledger_sense.store import session as store_session
from ledger_sense.store.blobs import BlobStore
from ledger_sense.store.documents import DocumentNotFound, load_document, store_document
from tests.store.conftest import SeededDocuments

RECEIVED_AT = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)


@pytest.fixture
def blobs(tmp_path: pathlib.Path) -> BlobStore:
    return BlobStore(root=str(tmp_path / "blobs"), kek=Fernet.generate_key())


def _labelled(origin: Origin) -> Labelled[bytes]:
    verdict = None
    if origin is Origin.REDACTED_UPLOAD:
        verdict = RedactionVerdict(verified=True, verifier="test")
    return accept_document(b"%PDF-1.4 " + origin.value.encode(), origin=origin,
                           provenance=f"test:{origin.value}", redaction=verdict)


@pytest.mark.parametrize("origin", list(Origin))
def test_a_document_round_trips_with_its_label(
    seeded_documents: SeededDocuments, blobs: BlobStore, origin: Origin
) -> None:
    doc = _labelled(origin)
    with store_session.app_engine().begin() as conn:
        document_id = store_document(conn, tenant_id=seeded_documents.tenant_id, doc=doc,
                                     blobs=blobs, received_at=RECEIVED_AT)
    with store_session.app_engine().connect() as conn:
        loaded = load_document(conn, tenant_id=seeded_documents.tenant_id,
                               document_id=document_id, blobs=blobs)
    assert loaded == doc


def test_the_row_carries_the_label_and_the_blob_is_not_plaintext(
    seeded_documents: SeededDocuments, blobs: BlobStore
) -> None:
    doc = _labelled(Origin.UPLOAD)
    with store_session.app_engine().begin() as conn:
        document_id = store_document(conn, tenant_id=seeded_documents.tenant_id, doc=doc,
                                     blobs=blobs, received_at=RECEIVED_AT)
    (row,) = seeded_documents.documents_for(document_id)
    assert (row.sensitivity, row.origin, row.provenance) == (
        "CLIENT_CONFIDENTIAL", "UPLOAD", "test:UPLOAD")
    assert row.blob_id is not None
    ciphertext = blobs.raw(row.blob_id)
    assert ciphertext is not None
    assert doc.payload not in ciphertext


def test_another_tenants_document_is_not_found(
    seeded_documents: SeededDocuments, blobs: BlobStore
) -> None:
    """404-not-403: the row exists; for this tenant it does not."""
    with store_session.app_engine().begin() as conn:
        document_id = store_document(conn, tenant_id=seeded_documents.tenant_id,
                                     doc=_labelled(Origin.CORPUS), blobs=blobs,
                                     received_at=RECEIVED_AT)
    with store_session.app_engine().connect() as conn, pytest.raises(DocumentNotFound):
        load_document(conn, tenant_id="someone-else", document_id=document_id, blobs=blobs)


def test_an_unknown_id_is_not_found(seeded_documents: SeededDocuments, blobs: BlobStore) -> None:
    with store_session.app_engine().connect() as conn, pytest.raises(DocumentNotFound):
        load_document(conn, tenant_id=seeded_documents.tenant_id, document_id="nope",
                      blobs=blobs)


def test_a_row_without_a_blob_is_not_found(
    seeded_documents: SeededDocuments, blobs: BlobStore
) -> None:
    """A purged (or never-landed) blob leaves a row with blob_id NULL. That is
    not a document with an empty payload; it is not a document."""
    seeded_documents.add_document("doc-no-blob", age_days=1, blob_id=None)
    with store_session.app_engine().connect() as conn, pytest.raises(DocumentNotFound):
        load_document(conn, tenant_id=seeded_documents.tenant_id, document_id="doc-no-blob",
                      blobs=blobs)


def test_a_purged_blob_is_not_found(seeded_documents: SeededDocuments, blobs: BlobStore) -> None:
    with store_session.app_engine().begin() as conn:
        document_id = store_document(conn, tenant_id=seeded_documents.tenant_id,
                                     doc=_labelled(Origin.CORPUS), blobs=blobs,
                                     received_at=RECEIVED_AT)
    (row,) = seeded_documents.documents_for(document_id)
    assert blobs.delete(seeded_documents.tenant_id, row.blob_id)
    with store_session.app_engine().connect() as conn, pytest.raises(DocumentNotFound):
        load_document(conn, tenant_id=seeded_documents.tenant_id, document_id=document_id,
                      blobs=blobs)


class _FailingConnection:
    def execute(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("simulated insert failure")


def test_a_failed_insert_leaves_no_orphan_blob(
    seeded_documents: SeededDocuments, blobs: BlobStore, tmp_path: pathlib.Path
) -> None:
    """Purge is row-driven; a blob with no row would never be deleted."""
    conn = cast(Connection, _FailingConnection())
    with pytest.raises(RuntimeError, match="simulated"):
        store_document(conn, tenant_id=seeded_documents.tenant_id,
                       doc=_labelled(Origin.CORPUS), blobs=blobs, received_at=RECEIVED_AT)
    assert list((tmp_path / "blobs").iterdir()) == []


class _Spy:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def send(self, lane: Lane, payload: bytes) -> bytes:
        self.sent.append(payload)
        return b""


def test_a_stored_confidential_document_is_still_refused_after_reload(
    seeded_documents: SeededDocuments, blobs: BlobStore
) -> None:
    """The test that ties §4 to Never #1: persistence does not launder a label."""
    with store_session.app_engine().begin() as conn:
        document_id = store_document(conn, tenant_id=seeded_documents.tenant_id,
                                     doc=_labelled(Origin.UPLOAD), blobs=blobs,
                                     received_at=RECEIVED_AT)
    with store_session.app_engine().connect() as conn:
        loaded = load_document(conn, tenant_id=seeded_documents.tenant_id,
                               document_id=document_id, blobs=blobs)
    assert loaded.sensitivity is Sensitivity.CLIENT_CONFIDENTIAL

    spy = _Spy()
    with pytest.raises(SensitivityViolation):
        admit(Lane.EXTRACT_VISION, loaded,
              policy=ProviderPolicy(enabled=True, redacted_opt_in=True), transport=spy)
    assert spy.sent == []
