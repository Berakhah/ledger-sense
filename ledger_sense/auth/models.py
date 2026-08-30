"""Tenants, users, and the password hash.

The schema lives here as SQLAlchemy MetaData: migrations/env.py autogenerates
against it, and every store builds dialect-neutral SQL from the same Table
objects rather than hand-writing strings per database. The DDL that actually
runs is migrations/versions/0001_tenancy.py — the suite applies that migration
to its SQLite database, so a drift between the two fails a test rather than
surfacing in production.

Every table carries tenant_id, indexed on (tenant_id, id). `tenants` is the
one exception: its own id *is* the tenant. That rule holds for every table any
later phase adds — documents, claims, exceptions, vendors, write records,
audit entries, labels, curves, profiles — because a table without tenant_id
cannot be ownership-checked, and api.deps.owned() is the only thing standing
between one client's pay application and another's.
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from ledger_sense.store.session import app_engine

metadata = sa.MetaData()

tenants = sa.Table(
    "tenants",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

users = sa.Table(
    "users",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("tenant_id", sa.Text, sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("email", sa.Text, nullable=False, unique=True),
    sa.Column("password_hash", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index("ix_users_tenant_id_id", "tenant_id", "id"),
)

sessions = sa.Table(
    "sessions",
    metadata,
    # The raw token is never stored: a database read (backup, dump, SQL
    # injection in some later query) must not hand over live sessions. 256
    # bits of entropy makes the digest unguessable without a slow KDF, which
    # would otherwise run on every authenticated request.
    sa.Column("token_hash", sa.Text, primary_key=True),
    sa.Column("user_id", sa.Text, sa.ForeignKey("users.id"), nullable=False),
    # Denormalised from users so the per-request lookup is one indexed read
    # with no join.
    sa.Column("tenant_id", sa.Text, sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index("ix_sessions_tenant_id_token_hash", "tenant_id", "token_hash"),
)

_hasher = PasswordHasher()

# Verifying a real hash against the wrong password costs the same as verifying
# the right one; skipping verification entirely for an unknown email does not.
# authenticate() burns this hash instead, so "no such user" and "wrong
# password" take the same time and stop being distinguishable from outside.
_DUMMY_HASH = _hasher.hash("dummy-password-for-constant-time-rejection")


def hash_password(password: str) -> str:
    """Argon2id digest, self-describing ($argon2id$v=19$m=...$...)."""
    return _hasher.hash(password)


def verify_password(password: str, digest: str) -> bool:
    try:
        return _hasher.verify(digest, password)
    except (VerificationError, InvalidHashError):
        return False


def create_tenant(name: str) -> str:
    tenant_id = uuid.uuid4().hex
    with app_engine().begin() as conn:
        conn.execute(tenants.insert().values(
            id=tenant_id, name=name, created_at=datetime.now(UTC)))
    return tenant_id


def create_user(tenant_id: str, email: str, password: str) -> str:
    user_id = uuid.uuid4().hex
    with app_engine().begin() as conn:
        conn.execute(users.insert().values(
            id=user_id,
            tenant_id=tenant_id,
            email=email,
            password_hash=hash_password(password),
            created_at=datetime.now(UTC),
        ))
    return user_id


def authenticate(email: str, password: str) -> tuple[str, str] | None:
    """Return (user_id, tenant_id) on a correct password, else None."""
    with app_engine().connect() as conn:
        row = conn.execute(
            sa.select(users.c.id, users.c.tenant_id, users.c.password_hash)
            .where(users.c.email == email)
        ).one_or_none()
    if row is None:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, str(row.password_hash)):
        return None
    return str(row.id), str(row.tenant_id)
