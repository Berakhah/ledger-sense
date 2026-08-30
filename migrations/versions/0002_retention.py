"""retention: tenants.retention_days, documents, claims, retention_audit_log

Revision ID: 0002_retention
Revises: 0001_tenancy
Create Date: 2026-08-30

`02` B9 / `00` B7: encryption at rest, retention and deletion, all of it
required *before the first document arrives*. This is the schema half — the
document and claim rows a purge deletes, the tenant term that decides when,
and the audit trail that survives the deletion.

Three deliberate decisions, each of which a later phase can get wrong quietly:

1. **`documents` and `claims` are minimal on purpose.** Enough to answer "when
   did this arrive, whose is it, what ciphertext does it point at, what did we
   derive from it" and nothing else. Phase 2 (extraction) and Phase 4 (review)
   extend these same tables with ALTER TABLE — vendor fields, amounts, field
   values, method and confidence. They do not create rival tables. Adding the
   domain model here would freeze guesses about a schema those phases own.

2. **The purge audit trail is `retention_audit_log`, NOT `audit`.** Phase 5's
   writeback plan already claims the name `audit` for a different log — one
   row per remote write, with `adapter`, `remote_id`, `idempotency_key` and a
   hash chain. None of those columns describe "we deleted a document because
   its retention term expired", and none of these describe a write to
   QuickBooks. Two concepts, two tables, and the distinct name means Phase 5's
   `CREATE TABLE IF NOT EXISTS audit` cannot silently adopt this schema or
   collide with it. Do not merge them.

3. **`retention_audit_log.document_id` carries no foreign key.** The row it
   names is deleted by design — that is the event being recorded. An FK would
   either block the purge or cascade away the evidence of it.

`tenants.retention_days` arrives NOT NULL with a server default because
ALTER TABLE ADD COLUMN NOT NULL needs one for the rows already there. See
DEFAULT_RETENTION_DAYS in auth/models.py for why the backstop errs short.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_retention"
down_revision: str | None = "0001_tenancy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors ledger_sense.auth.models.DEFAULT_RETENTION_DAYS. Written out rather
# than imported: a migration is a frozen historical record, and must keep
# emitting the DDL it emitted the day it ran even after the constant moves.
_DEFAULT_RETENTION_DAYS = "365"


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("retention_days", sa.Integer(), nullable=False,
                  server_default=_DEFAULT_RETENTION_DAYS),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        # The BlobStore id of the encrypted original (store/blobs.py).
        # Nullable: a row can exist before its ciphertext lands, and a purged
        # blob leaves nothing to point at.
        sa.Column("blob_id", sa.Text(), nullable=True),
        # The retention clock starts when we take custody, not when the
        # document was written or signed.
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_documents_tenant_id_id", "documents", ["tenant_id", "id"])
    op.create_table(
        "claims",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_claims_tenant_id_id", "claims", ["tenant_id", "id"])
    op.create_index("ix_claims_document_id", "claims", ["document_id"])
    op.create_table(
        "retention_audit_log",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        # No FK: the document this names has just been deleted (see 3 above).
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        # Who: the job, or the named operator behind a manual deletion request.
        sa.Column("actor", sa.Text(), nullable=False),
        # Why, in enough detail to answer a client asking what happened to a
        # specific document: the term applied and the date it ran out.
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("claims_purged", sa.Integer(), nullable=False),
        sa.Column("blob_deleted", sa.Boolean(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_retention_audit_log_tenant_id_id", "retention_audit_log",
                    ["tenant_id", "id"])
    op.create_index("ix_retention_audit_log_document_id", "retention_audit_log",
                    ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_retention_audit_log_document_id",
                  table_name="retention_audit_log")
    op.drop_index("ix_retention_audit_log_tenant_id_id",
                  table_name="retention_audit_log")
    op.drop_table("retention_audit_log")
    op.drop_index("ix_claims_document_id", table_name="claims")
    op.drop_index("ix_claims_tenant_id_id", table_name="claims")
    op.drop_table("claims")
    op.drop_index("ix_documents_tenant_id_id", table_name="documents")
    op.drop_table("documents")
    op.drop_column("tenants", "retention_days")
