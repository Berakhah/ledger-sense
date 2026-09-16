"""review: findings.reason

Revision ID: 0006_review_reject_reason
Revises: 0005_extraction_run
Create Date: 2026-09-16

Phase 4 Task 28's `/api/review/.../reject` records why a reviewer judged a
flagged discrepancy a false positive. Nothing else on `findings` holds text a
human wrote for this document — `message` is the rule's own text and a reject
must not overwrite it. Nullable, no backfill: every existing row predates
review actions entirely, and NULL honestly means "never rejected".
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_review_reject_reason"
down_revision: str | None = "0005_extraction_run"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("findings") as batch:
        batch.add_column(sa.Column("reason", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("findings") as batch:
        batch.drop_column("reason")
