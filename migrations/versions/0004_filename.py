"""filename: documents.filename

Revision ID: 0004_filename
Revises: 0003_sensitivity
Create Date: 2026-09-15

The inbox lists documents by the name the client gave them. That name is a
display attribute and nothing more: it is never provenance (who put the file
through the door — see 0003) and never a path (store/documents.py's caller
strips it to a basename before it gets here).

Same two decisions as 0003, for the same reasons:

1. Server default, then dropped. A row that predates this column is
   backfilled 'unknown:pre-0004' — named, not blank, so the inbox shows what
   it is rather than an empty cell. The default is then dropped so no raw
   INSERT can omit the name and have one invented.

2. Two batch blocks. On SQLite a single block that adds NOT NULL and drops
   the default would copy existing rows with nothing to fill.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_filename"
down_revision: str | None = "0003_sensitivity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("filename", sa.Text(), nullable=False,
                                   server_default="unknown:pre-0004"))
    with op.batch_alter_table("documents") as batch:
        batch.alter_column("filename", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("filename")
