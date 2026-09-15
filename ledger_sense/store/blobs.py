"""Encryption at rest for client documents (`02` B9, `00` B7).

We become a data processor on the first document. A pay application carries a
subcontractor's bank details; a stolen disk, a copied backup, or a stray
`docker cp` must not hand those over in the clear. Every stored document is
encrypted before it touches the filesystem.

**Envelope encryption.** Each blob gets its own freshly generated Fernet data
key. The data key encrypts the payload; the KEK (one per deployment, from
`LEDGER_SENSE_KEK` — see docs/security.md) encrypts the data key. Two
consequences that a single-key scheme does not give us:

- Identical documents produce unrelated ciphertext, so the store leaks no
  "these two clients uploaded the same file" signal.
- A KEK rotation rewrites one short wrapped key per blob, not gigabytes of
  re-encrypted ciphertext.

**On-disk layout.** One file per blob at `<root>/<blob_id>`, holding

    <wrapped data key>\\n<envelope ciphertext>

Both halves are Fernet tokens — URL-safe base64, which never contains a
newline — so the split is exact. Keeping the wrapped key in the same file as
the ciphertext it opens means `delete()` cannot half-succeed and leave a
readable blob behind or an orphaned key file: one `unlink` removes both.

**Tenant binding.** The envelope plaintext is `sha256(tenant_id) || payload`.
The tenant is therefore *inside* Fernet's authenticated ciphertext, not
implied by a directory name — moving a file between directories, or guessing
another tenant's blob id, does not grant a read. Fixed 32-byte digest rather
than a delimiter, so no tenant id can be crafted to confuse the parse.

Filesystem-rooted, so this is the one store that does not go through
store/session.py: there is no database connection here to centralise.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import uuid
from pathlib import Path

from cryptography.fernet import Fernet

# uuid4().hex — what put() generates, and the only shape any method will look
# up. A blob id reaches this module from a database row, and one day from a
# URL; validating it before it becomes a path component keeps "../../.env"
# from ever being opened.
_BLOB_ID = re.compile(r"\A[0-9a-f]{32}\Z")

_SEPARATOR = b"\n"
_TENANT_DIGEST_BYTES = 32


class BlobStore:
    """Envelope-encrypted document storage rooted at a directory."""

    def __init__(self, root: str, kek: bytes) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        # Fails here, at construction, on a malformed or truncated KEK rather
        # than on the first document upload.
        self._kek = Fernet(kek)

    # -- internals ---------------------------------------------------------

    def _path(self, blob_id: str) -> Path | None:
        return self._root / blob_id if _BLOB_ID.match(blob_id) else None

    @staticmethod
    def _tenant_digest(tenant_id: str) -> bytes:
        return hashlib.sha256(tenant_id.encode("utf-8")).digest()

    def _open_envelope(self, tenant_id: str, blob_id: str) -> bytes | None:
        """Decrypt and tenant-check, or None if there is nothing this tenant
        may read. Raises InvalidToken if a blob exists but will not open —
        wrong KEK or tampering, which must never look like "not found"."""
        stored = self.raw(blob_id)
        if stored is None:
            return None
        wrapped, _, ciphertext = stored.partition(_SEPARATOR)
        plaintext = Fernet(self._kek.decrypt(wrapped)).decrypt(ciphertext)
        digest, payload = (plaintext[:_TENANT_DIGEST_BYTES],
                           plaintext[_TENANT_DIGEST_BYTES:])
        if not hmac.compare_digest(digest, self._tenant_digest(tenant_id)):
            return None
        return payload

    # -- api ---------------------------------------------------------------

    def put(self, tenant_id: str, payload: bytes) -> str:
        """Encrypt and store `payload`, returning its blob id."""
        blob_id = uuid.uuid4().hex
        data_key = Fernet.generate_key()
        envelope = Fernet(data_key).encrypt(self._tenant_digest(tenant_id) + payload)
        record = self._kek.encrypt(data_key) + _SEPARATOR + envelope

        path = self._root / blob_id
        # O_EXCL: a blob id collision must fail loudly, never overwrite a
        # client's document. 0o600 keeps the file off other local accounts
        # (advisory on Windows, enforced on the Linux container we ship).
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(path, flags, 0o600), "wb") as handle:
            handle.write(record)
            handle.flush()
            # The database row that points here is committed separately; a
            # crash between the two must not leave a row pointing at a file
            # the page cache never wrote.
            os.fsync(handle.fileno())
        return blob_id

    def get(self, tenant_id: str, blob_id: str) -> bytes | None:
        """The decrypted payload, or None if it is absent or another
        tenant's."""
        return self._open_envelope(tenant_id, blob_id)

    def delete(self, tenant_id: str, blob_id: str) -> bool:
        """Erase the ciphertext and its wrapped key. True if something was
        deleted. A tenant cannot delete a blob it cannot read, and deleting
        an absent blob is a no-op — the retention job re-runs after a partial
        failure and must not fail on what it already removed."""
        if self._open_envelope(tenant_id, blob_id) is None:
            return False
        path = self._path(blob_id)
        if path is None:
            return False
        path.unlink(missing_ok=True)
        return True

    def raw(self, blob_id: str) -> bytes | None:
        """The bytes on disk, unopened — None if there is no such blob.

        A test seam (proving the payload is unreadable at rest and that two
        identical documents differ on disk) and the deletion check: after a
        purge this must be None, whatever the database says.
        """
        path = self._path(blob_id)
        if path is None or not path.is_file():
            return None
        return path.read_bytes()


def kek_from_environment() -> bytes:
    """The deployment's key-encryption key, from `LEDGER_SENSE_KEK`.

    Deliberately not a module-level singleton BlobStore: the key is read where
    the application wires up storage, so nothing imports this module into a
    process that has no business holding the KEK. Raises if unset — a
    misconfigured deployment must refuse to start, never fall back to writing
    client documents in the clear.
    """
    key = os.environ.get("LEDGER_SENSE_KEK")
    if not key:
        raise RuntimeError(
            "LEDGER_SENSE_KEK is unset: refusing to store client documents "
            "unencrypted. Generate one with Fernet.generate_key() and supply "
            "it through the environment (see docs/security.md)."
        )
    return key.encode("utf-8")


def blob_store_from_environment() -> BlobStore:
    """The deployment's blob store: `LEDGER_SENSE_BLOB_ROOT` + the KEK.

    Built where storage is wired (the inbox route, the demo seeder), not as a
    module singleton, for the reason kek_from_environment gives. Raises if the
    root is unset: a document written to a guessed directory is a document
    nobody backs up.
    """
    root = os.environ.get("LEDGER_SENSE_BLOB_ROOT")
    if not root:
        raise RuntimeError(
            "LEDGER_SENSE_BLOB_ROOT is unset: refusing to guess where client "
            "documents live (see docs/security.md)."
        )
    return BlobStore(root=root, kek=kek_from_environment())
