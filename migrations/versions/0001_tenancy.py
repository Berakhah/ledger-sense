"""tenancy: tenants, users, sessions

Revision ID: 0001_tenancy
Revises:
Create Date: 2026-08-30

The first migration, and the shape every later one follows: **every table
carries tenant_id NOT NULL with an index on (tenant_id, id)**. `tenants` is
the sole exception — its own id is the tenant.

This creates only what Phase 0 owns: tenant, user and session identity. The
document, claim, exception, vendor, write-record, audit, label, curve and
profile tables are each created by the phase that owns them, and each inherits
the tenant_id rule — a table without one cannot be ownership-checked, and
api.deps.owned() is the whole boundary.

Applied by the suite against SQLite (see tests/store/test_migrations.py) and
by `alembic upgrade head` against Postgres in a deployment. Nothing here is
dialect-specific, so those are the same schema.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_tenancy"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_tenant_id_id", "users", ["tenant_id", "id"])
    op.create_table(
        "sessions",
        # SHA-256 of the token, never the token: see auth/models.py.
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index("ix_sessions_tenant_id_token_hash", "sessions",
                    ["tenant_id", "token_hash"])


def downgrade() -> None:
    op.drop_index("ix_sessions_tenant_id_token_hash", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("ix_users_tenant_id_id", table_name="users")
    op.drop_table("users")
    op.drop_table("tenants")
