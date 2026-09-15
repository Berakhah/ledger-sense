"""Retention and deletion (`02` B9, `00` B7).

A design partner's data agreement names a period. Until there is code that
honours one, that clause is a promise with nothing behind it — so this is the
code, and it exists before the first document arrives rather than after.

`purge_expired` runs from a compose-scheduled job. For each document whose
tenant's retention term has run out it deletes, in this order:

    the encrypted original  ->  the claims derived from it  ->  the row itself

then writes one `retention_audit_log` entry recording what went and why. The
blob goes first deliberately: if the run dies halfway, the ciphertext is
already gone and the surviving row is purged on the next pass. The opposite
order would leave an unreferenced encrypted document on disk that nothing
knows to delete — an undeleted-client-data breach that no later run can find.

Deleting the document but keeping the claims would be worse than useless:
the claims are where the extracted bank details live.

**Schema note.** The document, claim and audit tables are defined here but
registered on `auth.models.metadata` — the one project MetaData, which
migrations/env.py autogenerates against and tests/store/test_migrations.py
diffs against 0002_retention. A second MetaData would need its own copy of
`tenants` for the join and the foreign keys, which is exactly the drift those
tests exist to catch. The store-imports-auth direction is a wart; the fix,
when a third owner appears, is to move the MetaData to a neutral module
rather than to fork it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from ledger_sense.auth.models import metadata, tenants
from ledger_sense.boundary import Origin, Sensitivity
from ledger_sense.store.blobs import BlobStore
from ledger_sense.store.session import app_engine

documents = sa.Table(
    "documents",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("tenant_id", sa.Text, sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("blob_id", sa.Text, nullable=True),
    sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    # 0003: the label, persisted. NOT NULL with no default — a row without a
    # label is refused by the schema, and store/documents.py is the one writer.
    sa.Column("sensitivity", sa.Text, nullable=False),
    sa.Column("origin", sa.Text, nullable=False),
    sa.Column("provenance", sa.Text, nullable=False),
    # 0004: the name the client gave the file. Display only — never a path,
    # never provenance. NOT NULL, no default, same discipline as the label.
    sa.Column("filename", sa.Text, nullable=False),
    # Named to match 0003 exactly: SQLite batch recreate keeps named CHECKs.
    sa.CheckConstraint(
        "sensitivity IN (" + ", ".join(f"'{s.value}'" for s in Sensitivity) + ")",
        name="ck_documents_sensitivity"),
    sa.CheckConstraint(
        "origin IN (" + ", ".join(f"'{o.value}'" for o in Origin) + ")",
        name="ck_documents_origin"),
    sa.Index("ix_documents_tenant_id_id", "tenant_id", "id"),
)

claims = sa.Table(
    "claims",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("tenant_id", sa.Text, sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("document_id", sa.Text, sa.ForeignKey("documents.id"), nullable=False),
    sa.Index("ix_claims_tenant_id_id", "tenant_id", "id"),
    sa.Index("ix_claims_document_id", "document_id"),
)

# NOT `audit`: Phase 5's writeback log owns that name and a completely
# different set of columns. See 0002_retention's docstring.
retention_audit_log = sa.Table(
    "retention_audit_log",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("tenant_id", sa.Text, sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("document_id", sa.Text, nullable=False),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("actor", sa.Text, nullable=False),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("claims_purged", sa.Integer, nullable=False),
    sa.Column("blob_deleted", sa.Boolean, nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index("ix_retention_audit_log_tenant_id_id", "tenant_id", "id"),
    sa.Index("ix_retention_audit_log_document_id", "document_id"),
)

SCHEDULED_JOB = "retention-job"


@dataclass(frozen=True)
class PurgeReport:
    """What one run actually removed. Reported, not inferred — `00`'s standing
    objection to claiming an outcome nobody measured."""

    documents_purged: int = 0
    claims_purged: int = 0
    blobs_deleted: int = 0


def _aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes where Postgres hands back aware ones;
    comparing the two raises TypeError. Everything written is UTC.

    (Twin of auth/session.py's `_aware`. Two copies of one line is cheaper
    than a store/auth cross-import; a third caller earns it a shared home.)
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def retention_days(tenant_id: str) -> int:
    """How long this tenant's documents may be held.

    Per tenant, never a global constant: it is a term in a signed data
    agreement, transcribed onto the tenant row at onboarding. An unknown
    tenant raises rather than defaulting — guessing a retention period on
    behalf of a client we have no agreement with is the failure mode.
    """
    with app_engine().connect() as conn:
        row = conn.execute(
            sa.select(tenants.c.retention_days).where(tenants.c.id == tenant_id)
        ).one_or_none()
    if row is None:
        raise LookupError(f"no such tenant: {tenant_id!r}")
    return int(row.retention_days)


def purge_expired(
    now: datetime,
    blobs: BlobStore | None = None,
    actor: str = SCHEDULED_JOB,
) -> PurgeReport:
    """Delete every document whose tenant's retention term has run out.

    `now` is the run's evaluation time, passed in rather than read from the
    clock so the job is testable and so a dry run at a chosen date is possible.
    `blobs` is the store holding the encrypted originals; `actor` names who
    ordered the deletion — the schedule, or an operator acting on a client's
    erasure request.

    Idempotent: a document already gone is not re-purged, and a re-run after a
    partial failure completes the job rather than double-counting it.
    """
    now = _aware(now)
    purged = claims_purged = blobs_deleted = 0

    with app_engine().begin() as conn:
        # ponytail: whole-table scan with the date arithmetic done in Python,
        # because SQLite has no INTERVAL and this stays one dialect-neutral
        # query. Push the comparison into SQL behind a received_at index when
        # a tenant's document count makes a nightly scan hurt.
        rows = conn.execute(
            sa.select(
                documents.c.id,
                documents.c.tenant_id,
                documents.c.blob_id,
                documents.c.received_at,
                tenants.c.retention_days,
            ).join_from(documents, tenants, documents.c.tenant_id == tenants.c.id)
        ).all()

        expired = []
        for row in rows:
            expires_at = _aware(row.received_at) + timedelta(days=int(row.retention_days))
            if expires_at <= now:
                expired.append((row, expires_at))

        # Refuse the whole run rather than delete the rows and strand the
        # ciphertext: a document nobody can find is a document nobody can
        # delete. Checked before anything is removed, so this changes nothing.
        if blobs is None and any(row.blob_id for row, _ in expired):
            raise RuntimeError(
                "purge_expired needs the BlobStore: expired documents hold "
                "encrypted originals, and deleting their rows without deleting "
                "the ciphertext is not a deletion."
            )

        for row, expires_at in expired:
            blob_deleted = bool(
                blobs is not None
                and row.blob_id
                and blobs.delete(str(row.tenant_id), str(row.blob_id))
            )
            deleted_claims = conn.execute(
                claims.delete().where(claims.c.document_id == row.id)
            ).rowcount
            conn.execute(documents.delete().where(documents.c.id == row.id))
            conn.execute(retention_audit_log.insert().values(
                id=uuid.uuid4().hex,
                tenant_id=row.tenant_id,
                document_id=row.id,
                action="purge",
                actor=actor,
                reason=(
                    f"retention term of {int(row.retention_days)} days expired "
                    f"{expires_at.isoformat()} (received {_aware(row.received_at).isoformat()})"
                ),
                claims_purged=deleted_claims,
                blob_deleted=blob_deleted,
                occurred_at=now,
            ))
            purged += 1
            claims_purged += deleted_claims
            blobs_deleted += int(blob_deleted)

    return PurgeReport(documents_purged=purged, claims_purged=claims_purged,
                       blobs_deleted=blobs_deleted)
