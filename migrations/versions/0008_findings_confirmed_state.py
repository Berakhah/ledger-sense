"""findings: allow the "confirmed" state

Revision ID: 0008_findings_confirmed_state
Revises: 0007_vendor_persistence
Create Date: 2026-09-23

Task 7's `/api/review/exceptions/{exc_id}/confirm-vendor` closes an I2
exception to a new `"confirmed"` state (distinct from `"accepted"` /
`"edited"` — a vendor confirmation writes no HUMAN claim; it links or creates
a vendor and sets `documents.vendor_id`). 0005 created `ck_findings_state`
with only the four original states; this widens it rather than reusing
`"accepted"`, which would conflate "a human accepted the flagged value
as-is" with "a human resolved this to a specific vendor" — two different
facts the calibration/audit trail must be able to tell apart.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_findings_confirmed_state"
down_revision: str | None = "0007_vendor_persistence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors ledger_sense.store.retention.FINDING_STATES. Written out on
# purpose, as 0005 does for the states it introduced.
_OLD_STATES = ("open", "accepted", "edited", "rejected")
_NEW_STATES = ("open", "accepted", "edited", "rejected", "confirmed")


def _one_of(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    with op.batch_alter_table("findings") as batch:
        batch.drop_constraint("ck_findings_state", type_="check")
        batch.create_check_constraint("ck_findings_state", _one_of("state", _NEW_STATES))


def downgrade() -> None:
    with op.batch_alter_table("findings") as batch:
        batch.drop_constraint("ck_findings_state", type_="check")
        batch.create_check_constraint("ck_findings_state", _one_of("state", _OLD_STATES))
