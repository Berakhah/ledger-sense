"""OAuth 2.0 token storage, encrypted at rest, with rotation.

Token compromise means access to a client's financials. Encrypt, rotate, and
never log a token value. Uses the shared per-thread WAL connection factory
(F-19) instead of opening sqlite3 directly, matching every other store in
this codebase (enforced by
tests/test_platform_skeleton.py::test_no_module_opens_a_database_connection_directly).
"""

from datetime import datetime

from cryptography.fernet import Fernet
from pydantic import BaseModel

from ledger_sense.store.sqlite import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    realm_id   TEXT PRIMARY KEY,
    access     BLOB NOT NULL,
    refresh    BLOB NOT NULL,
    expires_at TEXT NOT NULL
)
"""


class Tokens(BaseModel):
    access: str
    refresh: str
    expires_at: datetime


class TokenStore:
    """A SQLite-backed, encrypted-at-rest OAuth token store, keyed by realm.

    `rotate` is a separate method from `save` (not a bare alias) so a caller
    reading the call site sees the intent — replacing a token pair after a
    refresh — rather than an ordinary first write.
    """

    def __init__(self, db_path: str, fernet_key: bytes) -> None:
        self._path = db_path
        connect(db_path).execute(_SCHEMA)
        connect(db_path).commit()
        self._fernet = Fernet(fernet_key)

    def save(self, realm_id: str, access: str, refresh: str,
             expires_at: datetime) -> None:
        conn = connect(self._path)
        conn.execute(
            "INSERT INTO tokens (realm_id, access, refresh, expires_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (realm_id) DO UPDATE SET "
            "access = excluded.access, refresh = excluded.refresh, "
            "expires_at = excluded.expires_at",
            (realm_id, self._fernet.encrypt(access.encode()),
             self._fernet.encrypt(refresh.encode()), expires_at.isoformat()))
        conn.commit()

    def rotate(self, realm_id: str, access: str, refresh: str,
               expires_at: datetime) -> None:
        """Replace both tokens after a refresh. Documents the intent at the
        call site; mechanically identical to `save`."""
        self.save(realm_id, access=access, refresh=refresh, expires_at=expires_at)

    def load(self, realm_id: str) -> Tokens | None:
        row = connect(self._path).execute(
            "SELECT access, refresh, expires_at FROM tokens WHERE realm_id = ?",
            (realm_id,)).fetchone()
        if row is None:
            return None
        return Tokens(access=self._fernet.decrypt(row[0]).decode(),
                      refresh=self._fernet.decrypt(row[1]).decode(),
                      expires_at=datetime.fromisoformat(row[2]))

    def close(self) -> None:
        """Checkpoint WAL so committed writes land in the main db file (this
        is what a test reading the raw file for leaked plaintext observes).
        The connection itself is owned and cached by the shared factory
        (per-thread, keyed by path), not by this instance, so it is not
        closed here."""
        connect(self._path).execute("PRAGMA wal_checkpoint(FULL)")
