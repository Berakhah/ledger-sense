"""vendor persistence: vendors, vendor_aliases, payment_details, documents.vendor_id

Revision ID: 0007_vendor_persistence
Revises: 0006_review_reject_reason
Create Date: 2026-09-23

I2 (vendor resolution) and I3 (bank-detail hard stop) have been written and
tested since the initial vendor module but never had a table to persist
against — VendorLedger lived only in-memory, per-process, for the duration
of a single test. This gives the ledger real storage: one row per canonical
vendor, one per confirmed alias (provenance: who confirmed it and from what
document, per B6), and one per payment-detail observation (never the full
account number — account_hmac only, per B5's "only last four digits are
ever stored" rule in domain/vendor.py).

documents.vendor_id is nullable and set only when a reviewer confirms a
vendor match via the review-queue's confirm-vendor action — never inferred,
never defaulted.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_vendor_persistence"
down_revision: str | None = "0006_review_reject_reason"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vendors",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
    )
    op.create_table(
        "vendor_aliases",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("vendor_id", sa.Text(), sa.ForeignKey("vendors.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_document", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.Text(), nullable=False),
        sa.Column("merged_from", sa.Text(), nullable=True),
    )
    op.create_table(
        "payment_details",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("vendor_id", sa.Text(), sa.ForeignKey("vendors.id"), nullable=False),
        sa.Column("account_last4", sa.Text(), nullable=False),
        sa.Column("routing_last4", sa.Text(), nullable=False),
        sa.Column("account_hmac", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_document", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.Text(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("documents") as batch:
        batch.add_column(
            sa.Column("vendor_id", sa.Text(),
                      sa.ForeignKey("vendors.id", name="fk_documents_vendor_id_vendors"),
                      nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("vendor_id")
    op.drop_table("payment_details")
    op.drop_table("vendor_aliases")
    op.drop_table("vendors")
