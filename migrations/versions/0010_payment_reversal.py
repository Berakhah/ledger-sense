"""payment_details: reversal stamp, so undo keeps the bank-detail evidence

Revision ID: 0010_payment_reversal
Revises: 0009_payment_attestation
Create Date: 2026-10-20

Undoing a bank-detail confirmation used to delete the row, and the attestation
with it. It now stamps who reversed the entry and when and keeps everything
else; a reversed entry is no baseline but still counts as an account that was
on file. Both columns nullable and unbackfilled: every existing row is, truthfully,
not reversed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_payment_reversal"
down_revision: str | None = "0009_payment_attestation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("payment_details") as batch:
        batch.add_column(sa.Column("reversed_by", sa.Text(), nullable=True))
        batch.add_column(sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("payment_details") as batch:
        batch.drop_column("reversed_at")
        batch.drop_column("reversed_by")
