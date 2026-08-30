"""Session lifecycle: opaque tokens, sliding expiry, and the tenant a request
is allowed to see.

A Session is the only thing api.deps.owned() compares a resource against, so
tenant_id here is load-bearing: it comes from the sessions row, never from
anything the caller sends.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from ledger_sense.auth.models import sessions
from ledger_sense.store.session import app_engine

TOKEN_BYTES = 32  # 256 bits of entropy, opaque — the token carries no claims
SESSION_TTL = timedelta(hours=8)


@dataclass(frozen=True)
class Session:
    user_id: str
    tenant_id: str
    expires_at: datetime


def _digest(token: str) -> str:
    """What actually goes in the database. Plain SHA-256, deliberately: the
    input is 256 random bits, so there is no guess space for a slow KDF to
    protect and this runs on every authenticated request."""
    return hashlib.sha256(token.encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes where Postgres hands back aware ones;
    comparing the two raises TypeError. Everything written is UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def issue_session(user_id: str, tenant_id: str) -> str:
    """Create a session and return its raw token — the only time it exists
    outside the caller's cookie."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    with app_engine().begin() as conn:
        conn.execute(sessions.insert().values(
            token_hash=_digest(token),
            user_id=user_id,
            tenant_id=tenant_id,
            expires_at=datetime.now(UTC) + SESSION_TTL,
        ))
    return token


def load_session(token: str | None) -> Session | None:
    """Return the live session for `token`, sliding its expiry forward, or
    None if there is no such session or it has expired."""
    if not token:
        return None
    now = datetime.now(UTC)
    token_hash = _digest(token)
    with app_engine().begin() as conn:
        row = conn.execute(
            sa.select(sessions.c.user_id, sessions.c.tenant_id, sessions.c.expires_at)
            .where(sessions.c.token_hash == token_hash)
        ).one_or_none()
        if row is None:
            return None
        if _aware(row.expires_at) <= now:
            conn.execute(sessions.delete().where(sessions.c.token_hash == token_hash))
            return None
        expires_at = now + SESSION_TTL
        # ponytail: one UPDATE per authenticated request. Refresh only past a
        # few minutes of drift if session writes ever show up in a profile.
        conn.execute(
            sessions.update()
            .where(sessions.c.token_hash == token_hash)
            .values(expires_at=expires_at)
        )
    return Session(user_id=str(row.user_id), tenant_id=str(row.tenant_id),
                   expires_at=expires_at)


def expire_now(token: str) -> None:
    """End a session immediately (logout, or a session revoked under
    suspicion). Idempotent: an unknown token is a no-op."""
    with app_engine().begin() as conn:
        conn.execute(
            sessions.update()
            .where(sessions.c.token_hash == _digest(token))
            .values(expires_at=datetime.now(UTC))
        )
