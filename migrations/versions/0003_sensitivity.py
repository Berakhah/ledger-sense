"""sensitivity: documents.sensitivity, origin, provenance

Revision ID: 0003_sensitivity
Revises: 0002_retention
Create Date: 2026-09-15

`02` Never #1 / `00` R4: a document row cannot exist without its label, and the
label must survive a restart as the same value it came in as. Three columns,
all NOT NULL, two of them CHECKed against the vocabulary.

Four decisions:

1. **Values are literals, not imports.** Same rule as 0002: a migration is a
   frozen record and must emit the DDL it emitted the day it ran, even after
   the enum moves or grows. If a fifth Sensitivity value ever exists, that is
   migration 0004, not an edit here.

2. **Server defaults, then dropped.** ALTER TABLE ADD COLUMN NOT NULL needs a
   default for rows already there. Every real deployment has an empty
   documents table today — but a migration that fails on a populated table is
   a migration that fails in front of a client. A row that predates the label
   is backfilled CLIENT_CONFIDENTIAL / UPLOAD / 'unknown:pre-0003': treating
   it as a client's upload is the only honest guess. The defaults are then
   dropped so that no future raw INSERT can omit the label and get one
   invented for it — the schema refuses, which is the point.

3. **Two batch blocks, not one.** On SQLite, batch_alter_table reflects the
   table, applies every op in the block, recreates the table once and copies
   the rows in. A single block that adds the columns AND drops their defaults
   would copy existing rows into a NOT NULL column with nothing to fill it.
   Block one adds with defaults (the copy fills them); block two drops the
   defaults (a second recreate, on a table whose rows now carry values). On
   Postgres both blocks are plain ALTERs and the split costs nothing.

4. **The CHECKs are named.** SQLite batch recreate silently drops UNNAMED check
   constraints, and 0002 promises Phase 2 and Phase 4 will ALTER this table
   again. Named, they are reflected and carried across every future recreate.
   store/retention.py mirrors the names on the Table so metadata and schema
   agree.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_sensitivity"
down_revision: str | None = "0002_retention"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors ledger_sense.boundary.Sensitivity / Origin. Written out on purpose.
_SENSITIVITIES = ("PUBLIC", "SYNTHETIC", "REDACTED", "CLIENT_CONFIDENTIAL")
_ORIGINS = ("UPLOAD", "CORPUS", "REDACTED_UPLOAD")


def _one_of(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("sensitivity", sa.Text(), nullable=False,
                                   server_default="CLIENT_CONFIDENTIAL"))
        batch.add_column(sa.Column("origin", sa.Text(), nullable=False,
                                   server_default="UPLOAD"))
        batch.add_column(sa.Column("provenance", sa.Text(), nullable=False,
                                   server_default="unknown:pre-0003"))
        batch.create_check_constraint("ck_documents_sensitivity",
                                      _one_of("sensitivity", _SENSITIVITIES))
        batch.create_check_constraint("ck_documents_origin", _one_of("origin", _ORIGINS))

    with op.batch_alter_table("documents") as batch:
        batch.alter_column("sensitivity", server_default=None)
        batch.alter_column("origin", server_default=None)
        batch.alter_column("provenance", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_constraint("ck_documents_origin", type_="check")
        batch.drop_constraint("ck_documents_sensitivity", type_="check")
        batch.drop_column("provenance")
        batch.drop_column("origin")
        batch.drop_column("sensitivity")
