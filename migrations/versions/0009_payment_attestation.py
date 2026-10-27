"""payment_details: attestation evidence for the I3 bank-change confirmation

Revision ID: 0009_payment_attestation
Revises: 0008_findings_confirmed_state
Create Date: 2026-10-18

A bank-detail confirmation is a human out-of-band verification (LS-10, B5).
The channel, the person spoken to and a note go on the record beside
`confirmed_by` / `confirmed_at` so the vendor ledger can show them and an audit
can read them. All nullable: rows written before this carry no evidence, and
the code treats an unconfirmed or evidence-less row as exactly that.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_payment_attestation"
down_revision: str | None = "0008_findings_confirmed_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("confirmation_channel", "confirmation_contact", "confirmation_note")


def upgrade() -> None:
    with op.batch_alter_table("payment_details") as batch:
        for name in _COLUMNS:
            batch.add_column(sa.Column(name, sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("payment_details") as batch:
        for name in _COLUMNS:
            batch.drop_column(name)
