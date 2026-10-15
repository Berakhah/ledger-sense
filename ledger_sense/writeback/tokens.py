"""OAuth 2.0 token storage, encrypted at rest, with rotation.

Token compromise means access to a client's financials. Encrypt, rotate, and
never log a token value. Uses the shared per-thread WAL connection factory
(F-19) instead of opening sqlite3 directly, matching every other store in
this codebase (enforced by
tests/test_platform_skeleton.py::test_no_module_opens_a_database_connection_directly).
"""

import base64
import os
from datetime import datetime

from cryptography.fernet import Fernet
from pydantic import BaseModel

from ledger_sense.store.sqlite import connect

_TOKEN_KEY_ENV = "LEDGER_SENSE_TOKEN_KEY"


def _decoded(key: str) -> bytes:
    """The raw bytes a Fernet key stands for, decoded the way Fernet decodes it."""
    try:
        return base64.urlsafe_b64decode(key.encode("utf-8"))
    except ValueError:  # binascii.Error is a ValueError: a malformed KEK
        return key.encode("utf-8")


def token_key_from_environment() -> bytes:
    """The key the OAuth token store is encrypted under, from `LEDGER_SENSE_TOKEN_KEY`.

    Its own key, never the document KEK: one key over both would make the loss
    or exposure of either compromise the other. Raises when unset (no fallback
    to the KEK), when it is not a valid Fernet key (the `.env.example`
    placeholder), or when it equals the KEK — the separation has to be a fact,
    not a label.
    """
    key = os.environ.get(_TOKEN_KEY_ENV)
    if not key:
        raise RuntimeError(
            f"{_TOKEN_KEY_ENV} is unset: refusing to store accounting OAuth "
            f"tokens under the document KEK. Generate one with "
            f"Fernet.generate_key() (see docs/security.md).")
    try:
        Fernet(key.encode("utf-8"))
    except ValueError as error:
        raise RuntimeError(
            f"{_TOKEN_KEY_ENV} is not a valid Fernet key; generate one with "
            f"Fernet.generate_key().") from error
    # Compare the decoded keys, not the strings: Fernet's decoder ignores
    # whitespace and accepts +/ for -_, so a pasted KEK with a trailing newline
    # is textually different and cryptographically identical.
    kek = os.environ.get("LEDGER_SENSE_KEK", "")
    if kek and _decoded(kek) == _decoded(key):
        raise RuntimeError(
            f"{_TOKEN_KEY_ENV} must differ from LEDGER_SENSE_KEK: a shared key "
            f"is the coupling this variable exists to remove.")
    return key.encode("utf-8")

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
