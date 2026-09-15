"""The documents table's one writer and one reader (§4).

`store_document` takes a Labelled[bytes], never bytes: a row cannot exist
without a label because the function cannot be called without one. It is the
only `documents.insert()` under ledger_sense/ — tests/boundary/test_structure.py
enforces that — so the only way into the table is through the door.

`load_document` rebuilds the same Labelled from the row. That is what makes the
label survive a restart: a stored document reaches the gateway as the value it
came in as, never re-labelled by hand.

Every miss — wrong tenant, unknown id, a row whose blob is NULL, a blob the
retention job has purged — is DocumentNotFound. 404, never 403: the row's
existence is not this tenant's to learn.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Connection

from ledger_sense.boundary import Labelled, Origin, Sensitivity
from ledger_sense.store.blobs import BlobStore
from ledger_sense.store.retention import documents

__all__ = ["DocumentNotFound", "load_document", "store_document"]


class DocumentNotFound(LookupError):
    """No document by that id is visible to this tenant."""


def store_document(
    conn: Connection,
    *,
    tenant_id: str,
    doc: Labelled[bytes],
    blobs: BlobStore,
    received_at: datetime,
    filename: str,
) -> str:
    """Encrypt the payload, insert the row with its label and filename, return the new id."""
    document_id = uuid.uuid4().hex
    # The blob lands first (blobs.py fsyncs before returning) so the row never
    # points at ciphertext that was not written. If the row then fails, the
    # blob is removed: purge is row-driven and would never find an orphan. If
    # that cleanup itself fails, the insert error is still what propagates —
    # not the cleanup error — with a note recording that the blob is now an
    # orphan needing manual deletion, so the caller learns why the insert
    # failed instead of why the cleanup failed. The blob write happens outside
    # the caller's own transaction, so a caller that rolls back its
    # transaction after store_document returns leaves an orphaned blob that
    # the row-driven purge will never reclaim (there is no row to drive it);
    # that case is the caller's to own.
    blob_id = blobs.put(tenant_id, doc.payload)
    try:
        conn.execute(documents.insert().values(
            id=document_id,
            tenant_id=tenant_id,
            blob_id=blob_id,
            received_at=received_at,
            sensitivity=doc.sensitivity.value,
            origin=doc.origin.value,
            provenance=doc.provenance,
            filename=filename,
        ))
    except Exception as insert_error:
        try:
            blobs.delete(tenant_id, blob_id)
        except Exception as cleanup_error:  # noqa: BLE001 — the insert error is the one that matters
            insert_error.add_note(
                f"blob {blob_id} could not be removed after the insert failed "
                f"({cleanup_error!r}); it is an orphan until deleted by hand")
        raise
    return document_id


def load_document(
    conn: Connection,
    *,
    tenant_id: str,
    document_id: str,
    blobs: BlobStore,
) -> Labelled[bytes]:
    """The document as it was accepted: same payload, same label."""
    row = conn.execute(
        sa.select(documents)
        .where(documents.c.tenant_id == tenant_id, documents.c.id == document_id)
    ).one_or_none()
    if row is None or row.blob_id is None:
        raise DocumentNotFound(document_id)
    payload = blobs.get(tenant_id, row.blob_id)
    if payload is None:
        raise DocumentNotFound(document_id)
    return Labelled(
        payload=payload,
        sensitivity=Sensitivity(row.sensitivity),
        origin=Origin(row.origin),
        provenance=row.provenance,
    )
