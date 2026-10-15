"""Retention and deletion (`02` B9, `00` B7).

A design partner's data agreement names a period. Until there is code that
honours one, that clause is a promise with nothing behind it — so this is the
code, and it exists before the first document arrives rather than after.

`purge_expired` runs nightly from the compose `retention` service
(`python -m ledger_sense purge`). For each document whose
tenant's retention term has run out it deletes, in this order:

    the encrypted original  ->  the claims and findings derived from it
        ->  the vendor-ledger history it contributed  ->  the row itself

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

DOCUMENT_STATUSES = ("received", "extracted", "cannot_extract", "duplicate", "failed")
DOC_TYPES = ("pay_application", "continuation_sheet", "invoice", "lien_waiver", "unknown")
FINDING_STATES = ("open", "accepted", "edited", "rejected", "confirmed")
CLAIM_VALUE_TYPES = ("money", "date", "int", "str")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


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
    # 0005: the run. `status` and `run_attempts` keep server defaults (a row
    # that exists is received, zero times tried); the rest is written by
    # ledger_sense.run.document and NULL until it has something to say.
    sa.Column("status", sa.Text, nullable=False, server_default="received"),
    sa.Column("content_sha256", sa.Text, nullable=True),
    sa.Column("doc_type", sa.Text, nullable=True),
    sa.Column("run_note", sa.Text, nullable=True),
    sa.Column("rounding_policy_source", sa.Text, nullable=True),
    sa.Column("run_attempts", sa.Integer, nullable=False, server_default="0"),
    # 0007: set only when a reviewer confirms a vendor match via the
    # review-queue's confirm-vendor action — never inferred, never defaulted.
    # deferrable/initially="DEFERRED": store/vendors.py's save_vendor_ledger
    # deletes and reinserts a tenant's whole vendors table (same ids) inside
    # one transaction. A non-deferrable FK has Postgres check the reference
    # the instant DELETE FROM vendors runs, which fails even though the same
    # vendor id exists again by commit. Deferred, the check runs at commit,
    # by which point the reinsert has restored the reference.
    # A single column: this document has at most one confirmed vendor, which
    # is only true because check_vendor_resolution fires against exactly one
    # vendor_name field per document today. api/review.py's confirm-vendor
    # and undo-confirm-vendor both key off this column and off "the I2
    # finding for this document" interchangeably — a second vendor-bearing
    # field would need this column (and both routes) to become per-field.
    sa.Column("vendor_id", sa.Text,
              sa.ForeignKey("vendors.id", name="fk_documents_vendor_id_vendors",
                            deferrable=True, initially="DEFERRED"),
              nullable=True),
    # Named to match the migrations exactly: SQLite batch recreate keeps named CHECKs.
    sa.CheckConstraint(
        "sensitivity IN (" + ", ".join(f"'{s.value}'" for s in Sensitivity) + ")",
        name="ck_documents_sensitivity"),
    sa.CheckConstraint(
        "origin IN (" + ", ".join(f"'{o.value}'" for o in Origin) + ")",
        name="ck_documents_origin"),
    sa.CheckConstraint(_one_of("status", DOCUMENT_STATUSES), name="ck_documents_status"),
    sa.CheckConstraint(f"doc_type IS NULL OR {_one_of('doc_type', DOC_TYPES)}",
                       name="ck_documents_doc_type"),
    sa.Index("ix_documents_tenant_id_id", "tenant_id", "id"),
    sa.Index("ix_documents_tenant_sha", "tenant_id", "content_sha256",
             sqlite_where=sa.text("content_sha256 IS NOT NULL"),
             postgresql_where=sa.text("content_sha256 IS NOT NULL")),
)

claims = sa.Table(
    "claims",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("tenant_id", sa.Text, sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("document_id", sa.Text, sa.ForeignKey("documents.id"), nullable=False),
    # 0005: one row per Claim the run produced. `value` is TEXT on purpose —
    # Money never leaves as a float. confidence/match_quality/bbox are scores
    # and geometry, the only REAL columns in the store.
    sa.Column("field", sa.Text, nullable=False),
    sa.Column("value", sa.Text, nullable=False),
    sa.Column("value_type", sa.Text, nullable=False),
    sa.Column("method", sa.Text, nullable=False),
    sa.Column("confidence", sa.Float, nullable=True),
    sa.Column("match_quality", sa.Float, nullable=True),
    sa.Column("page", sa.Integer, nullable=False),
    sa.Column("x0", sa.Float, nullable=False),
    sa.Column("top", sa.Float, nullable=False),
    sa.Column("x1", sa.Float, nullable=False),
    sa.Column("bottom", sa.Float, nullable=False),
    sa.Column("caveat", sa.Text, nullable=True),
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(_one_of("value_type", CLAIM_VALUE_TYPES), name="ck_claims_value_type"),
    sa.Index("ix_claims_tenant_id_id", "tenant_id", "id"),
    sa.Index("ix_claims_document_id", "document_id"),
    # NOT unique: Phase 4 writes a HUMAN claim on the same field per accept/edit.
    sa.Index("ix_claims_document_field", "document_id", "field"),
)

findings = sa.Table(
    "findings",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("tenant_id", sa.Text, sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("document_id", sa.Text, sa.ForeignKey("documents.id"), nullable=False),
    sa.Column("rule_id", sa.Text, nullable=False),
    sa.Column("field", sa.Text, nullable=False),
    sa.Column("message", sa.Text, nullable=False),
    sa.Column("expected", sa.Text, nullable=True),
    sa.Column("actual", sa.Text, nullable=True),
    sa.Column("page", sa.Integer, nullable=True),
    sa.Column("x0", sa.Float, nullable=True),
    sa.Column("top", sa.Float, nullable=True),
    sa.Column("x1", sa.Float, nullable=True),
    sa.Column("bottom", sa.Float, nullable=True),
    sa.Column("state", sa.Text, nullable=False, server_default="open"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    # 0006: why a reviewer rejected this finding as a false positive. NULL for
    # every finding never rejected, and never for the rule's own `message`.
    sa.Column("reason", sa.Text, nullable=True),
    sa.CheckConstraint(_one_of("state", FINDING_STATES), name="ck_findings_state"),
    sa.Index("ix_findings_document_order", "document_id", "page", "top", "x0"),
    sa.Index("ix_findings_tenant_id_id", "tenant_id", "id"),
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

# 0007: persistence for the vendor ledger (I2 vendor resolution, I3 bank-detail
# hard stop) — previously in-memory only, per-process, for one test's duration.
vendors = sa.Table(
    "vendors", metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("tenant_id", sa.Text, sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("name", sa.Text, nullable=False),
)

vendor_aliases = sa.Table(
    "vendor_aliases", metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("tenant_id", sa.Text, sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("vendor_id", sa.Text,
              sa.ForeignKey("vendors.id", name="fk_vendor_aliases_vendor_id_vendors"),
              nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
    sa.Column("source_document", sa.Text, nullable=False),
    sa.Column("confirmed_by", sa.Text, nullable=False),
    sa.Column("merged_from", sa.Text, nullable=True),
)

payment_details = sa.Table(
    "payment_details", metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("tenant_id", sa.Text, sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("vendor_id", sa.Text,
              sa.ForeignKey("vendors.id", name="fk_payment_details_vendor_id_vendors"),
              nullable=False),
    sa.Column("account_last4", sa.Text, nullable=False),
    sa.Column("routing_last4", sa.Text, nullable=False),
    # HMAC of the full account+routing under LEDGER_SENSE_PAYMENT_HMAC_KEY
    # (domain/vendor.py:payment_hmac) — never the plaintext account number.
    sa.Column("account_hmac", sa.Text, nullable=False),
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("source_document", sa.Text, nullable=False),
    sa.Column("confirmed_by", sa.Text, nullable=True),
    sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
)

SCHEDULED_JOB = "retention-job"


@dataclass(frozen=True)
class PurgeReport:
    """What one run actually removed. Reported, not inferred — `00`'s standing
    objection to claiming an outcome nobody measured."""

    documents_purged: int = 0
    claims_purged: int = 0
    blobs_deleted: int = 0
    findings_purged: int = 0
    payment_details_purged: int = 0
    vendors_purged: int = 0


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
    purged = claims_purged = blobs_deleted = findings_purged = 0
    details_purged = 0
    # (tenant, vendor) pairs a purged document touched: the only vendors that
    # can have just lost their last reason to exist.
    touched_vendors: set[tuple[str, str]] = set()

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
                documents.c.vendor_id,
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
            deleted_findings = conn.execute(
                findings.delete().where(findings.c.document_id == row.id)
            ).rowcount
            deleted_claims = conn.execute(
                claims.delete().where(claims.c.document_id == row.id)
            ).rowcount
            # The vendor ledger's bank-detail history is derived from the
            # document too. Scoped by tenant as well as source: a document id
            # is never trusted to be unique across tenants.
            for ledger in (payment_details, vendor_aliases):
                scope = sa.and_(ledger.c.tenant_id == row.tenant_id,
                                ledger.c.source_document == row.id)
                touched_vendors.update(
                    (str(row.tenant_id), str(v))
                    for v in conn.execute(sa.select(ledger.c.vendor_id).where(scope)).scalars())
                removed = conn.execute(ledger.delete().where(scope)).rowcount
                if ledger is payment_details:
                    details_purged += removed
            if row.vendor_id:
                touched_vendors.add((str(row.tenant_id), str(row.vendor_id)))
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
            findings_purged += deleted_findings
            blobs_deleted += int(blob_deleted)

        # Last, once every expired document is gone (documents.vendor_id is a
        # foreign key): a vendor with no remaining bank-detail history, no
        # alias, and no surviving document pointing at it has nothing left to
        # be a vendor of. One that still has any is kept whole.
        vendors_purged = 0
        for tenant_id, vendor_id in touched_vendors:
            still_used = any(
                conn.execute(sa.select(sa.exists().where(sa.and_(
                    table.c.tenant_id == tenant_id, table.c.vendor_id == vendor_id)))).scalar()
                for table in (payment_details, vendor_aliases, documents))
            if not still_used:
                vendors_purged += conn.execute(vendors.delete().where(sa.and_(
                    vendors.c.tenant_id == tenant_id, vendors.c.id == vendor_id))).rowcount

    return PurgeReport(documents_purged=purged, claims_purged=claims_purged,
                       blobs_deleted=blobs_deleted, findings_purged=findings_purged,
                       payment_details_purged=details_purged, vendors_purged=vendors_purged)
