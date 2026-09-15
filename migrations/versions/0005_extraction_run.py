"""extraction run: documents.status & friends, claims value columns, findings

Revision ID: 0005_extraction_run
Revises: 0004_filename
Create Date: 2026-09-15

Spec: docs/superpowers/specs/2026-09-15-extraction-run-design.md §1.

`documents.status` keeps its server default ('received'): a row that exists
is, by definition, received. `run_attempts` keeps its default (0) for the
same reason. Every other new column is nullable — it is written by the run,
and NULL honestly means "no run has said anything yet".

`claims` had no value columns and no writer before this revision, so the
table is empty in every deployment; the NOT NULL columns are added with a
throwaway server default and the default is then dropped (0003's two-block
pattern — on SQLite one block that adds NOT NULL and drops the default would
copy rows with nothing to fill).

`ix_claims_document_field` is deliberately NOT unique: Phase 4 writes a
HUMAN-method claim on the same field for every accept/edit.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_extraction_run"
down_revision: str | None = "0004_filename"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors ledger_sense.store.retention.DOCUMENT_STATUSES / DOC_TYPES /
# FINDING_STATES / CLAIM_VALUE_TYPES. Written out on purpose, as 0003 does.
_STATUSES = ("received", "extracted", "cannot_extract", "duplicate", "failed")
_DOC_TYPES = ("pay_application", "continuation_sheet", "invoice", "lien_waiver", "unknown")
_STATES = ("open", "accepted", "edited", "rejected")
_VALUE_TYPES = ("money", "date", "int", "str")


def _one_of(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("status", sa.Text(), nullable=False,
                                   server_default="received"))
        batch.add_column(sa.Column("content_sha256", sa.Text(), nullable=True))
        batch.add_column(sa.Column("doc_type", sa.Text(), nullable=True))
        batch.add_column(sa.Column("run_note", sa.Text(), nullable=True))
        batch.add_column(sa.Column("rounding_policy_source", sa.Text(), nullable=True))
        batch.add_column(sa.Column("run_attempts", sa.Integer(), nullable=False,
                                   server_default="0"))
        batch.create_check_constraint("ck_documents_status", _one_of("status", _STATUSES))
        batch.create_check_constraint(
            "ck_documents_doc_type",
            f"doc_type IS NULL OR {_one_of('doc_type', _DOC_TYPES)}")
        batch.create_index(
            "ix_documents_tenant_sha", ["tenant_id", "content_sha256"],
            sqlite_where=sa.text("content_sha256 IS NOT NULL"),
            postgresql_where=sa.text("content_sha256 IS NOT NULL"))

    with op.batch_alter_table("claims") as batch:
        batch.add_column(sa.Column("field", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("value", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("value_type", sa.Text(), nullable=False,
                                   server_default="str"))
        batch.add_column(sa.Column("method", sa.Text(), nullable=False,
                                   server_default="deterministic"))
        batch.add_column(sa.Column("confidence", sa.Float(), nullable=True))
        batch.add_column(sa.Column("match_quality", sa.Float(), nullable=True))
        batch.add_column(sa.Column("page", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("x0", sa.Float(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("top", sa.Float(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("x1", sa.Float(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("bottom", sa.Float(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("caveat", sa.Text(), nullable=True))
        batch.add_column(sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False,
                                   server_default="1970-01-01 00:00:00"))
        batch.create_check_constraint("ck_claims_value_type",
                                      _one_of("value_type", _VALUE_TYPES))
        batch.create_index("ix_claims_document_field", ["document_id", "field"])

    with op.batch_alter_table("claims") as batch:
        for column in ("field", "value", "value_type", "method", "page",
                       "x0", "top", "x1", "bottom", "observed_at"):
            batch.alter_column(column, server_default=None)

    op.create_table(
        "findings",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("document_id", sa.Text(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("expected", sa.Text(), nullable=True),
        sa.Column("actual", sa.Text(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("x0", sa.Float(), nullable=True),
        sa.Column("top", sa.Float(), nullable=True),
        sa.Column("x1", sa.Float(), nullable=True),
        sa.Column("bottom", sa.Float(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_one_of("state", _STATES), name="ck_findings_state"),
    )
    op.create_index("ix_findings_document_order", "findings",
                    ["document_id", "page", "top", "x0"])
    op.create_index("ix_findings_tenant_id_id", "findings", ["tenant_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_findings_tenant_id_id", table_name="findings")
    op.drop_index("ix_findings_document_order", table_name="findings")
    op.drop_table("findings")

    with op.batch_alter_table("claims") as batch:
        batch.drop_index("ix_claims_document_field")
        batch.drop_constraint("ck_claims_value_type", type_="check")
        for column in ("observed_at", "caveat", "bottom", "x1", "top", "x0", "page",
                       "match_quality", "confidence", "method", "value_type", "value",
                       "field"):
            batch.drop_column(column)

    with op.batch_alter_table("documents") as batch:
        batch.drop_index("ix_documents_tenant_sha")
        batch.drop_constraint("ck_documents_doc_type", type_="check")
        batch.drop_constraint("ck_documents_status", type_="check")
        for column in ("run_attempts", "rounding_policy_source", "run_note", "doc_type",
                       "content_sha256", "status"):
            batch.drop_column(column)
