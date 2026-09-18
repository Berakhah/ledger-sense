"""Audit trail for everything written to a client's books.

What was written, from which document, on whose approval, with a reversal path.

Append-only: there is deliberately no delete or update method. An audit log you
can edit is not an audit log — but a Python class missing four method names
proves nothing about the SQLite file underneath it, which is writable by the
same process. What a client hears — "full audit trail" — implies tamper
evidence, so every entry also carries a `prev_hash` / `entry_hash` chain
(F-43): `entry_hash = sha256(prev_hash || canonical_payload)`. `verify_chain`
walks the chain in insertion order and names every entry whose stored hash no
longer matches what its payload and predecessor would produce.

TENANCY: there is no established multi-tenant plumbing reaching into the
writeback module yet (`IdempotencyLedger` and `TokenStore` are both
single-tenant, keyed by key/adapter or realm_id only). `tenant_id` is
populated with a fixed literal, `DEFAULT_TENANT_ID`, so the schema column and
the hash chain are both real today; wiring an actual tenant identifier through
`AuditedAdapter`'s constructor is out of this task's scope.
"""

import hashlib
import json
import sqlite3
import uuid
from datetime import date, datetime
from typing import Literal, cast

from pydantic import BaseModel

from ledger_sense.store.sqlite import connect
from ledger_sense.writeback.interface import (
    ReversalResult,
    WritebackPort,
    WriteRequest,
    WriteResult,
)

Action = Literal["write", "reversal"]

# No tenant plumbing reaches writeback yet (see module docstring) — a fixed
# literal keeps the column populated without inventing multi-tenancy here.
DEFAULT_TENANT_ID = "default"

# The hash chain's starting point. Not a real hash of anything; a fixed,
# well-known genesis value so the first entry's `prev_hash` is not NULL and
# `verify_chain` has a value to check it against.
_GENESIS_HASH = "0" * 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    doc_id          TEXT NOT NULL,
    adapter         TEXT NOT NULL,
    action          TEXT NOT NULL,
    remote_id       TEXT NOT NULL,
    idempotency_key TEXT,
    approved_by     TEXT NOT NULL,
    occurred_at     TEXT NOT NULL,
    detail          TEXT NOT NULL,
    prev_hash       TEXT NOT NULL,
    entry_hash      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_by_remote ON audit (tenant_id, remote_id);
"""

# Column order used by every INSERT/SELECT touching the chain fields, so the
# canonical payload and the row layout can never silently drift apart.
_PAYLOAD_FIELDS = (
    "id", "tenant_id", "doc_id", "adapter", "action", "remote_id",
    "idempotency_key", "approved_by", "occurred_at", "detail",
)


class AuditEntry(BaseModel):
    id: str
    doc_id: str
    adapter: str
    action: Action
    remote_id: str
    idempotency_key: str | None
    approved_by: str
    occurred_at: datetime
    detail: str


def _canonical_payload(*, id: str, tenant_id: str, doc_id: str, adapter: str,
                       action: str, remote_id: str,
                       idempotency_key: str | None, approved_by: str,
                       occurred_at: str, detail: str) -> str:
    """A deterministic serialization of one entry's fields.

    `json.dumps(..., sort_keys=True)` over a plain dict of strings sidesteps
    Python's dict-iteration-order ambiguity (not a real risk in 3.7+, but the
    explicit sort makes the format self-documenting and immune to a future
    field reordering silently changing every hash already on disk).
    """
    payload = {
        "id": id, "tenant_id": tenant_id, "doc_id": doc_id, "adapter": adapter,
        "action": action, "remote_id": remote_id,
        "idempotency_key": idempotency_key, "approved_by": approved_by,
        "occurred_at": occurred_at, "detail": detail,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _compute_entry_hash(prev_hash: str, canonical_payload: str) -> str:
    return hashlib.sha256((prev_hash + canonical_payload).encode()).hexdigest()


class AuditLog:
    """Append-only, tamper-evident record of everything written to a client's
    accounting system.

    Uses the shared per-thread WAL connection factory (`store/sqlite.py`),
    matching `IdempotencyLedger` and `TokenStore` — this class holds only the
    db path, not a connection, and is safe to use from FastAPI's sync-handler
    threadpool. Enforced by
    `tests/test_platform_skeleton.py::test_no_module_opens_a_database_connection_directly`.
    """

    def __init__(self, db_path: str, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self._path = db_path
        self._tenant_id = tenant_id
        connect(db_path).executescript(_SCHEMA)
        connect(db_path).commit()

    def _last_hash(self) -> str:
        row = connect(self._path).execute(
            "SELECT entry_hash FROM audit WHERE tenant_id = ? "
            "ORDER BY rowid DESC LIMIT 1", (self._tenant_id,)).fetchone()
        return cast(str, row[0]) if row else _GENESIS_HASH

    def _append(self, entry: AuditEntry) -> None:
        conn = connect(self._path)
        occurred_at = entry.occurred_at.isoformat()
        prev_hash = self._last_hash()
        payload = _canonical_payload(
            id=entry.id, tenant_id=self._tenant_id, doc_id=entry.doc_id,
            adapter=entry.adapter, action=entry.action,
            remote_id=entry.remote_id, idempotency_key=entry.idempotency_key,
            approved_by=entry.approved_by, occurred_at=occurred_at,
            detail=entry.detail)
        entry_hash = _compute_entry_hash(prev_hash, payload)
        conn.execute(
            "INSERT INTO audit (id, tenant_id, doc_id, adapter, action, "
            "remote_id, idempotency_key, approved_by, occurred_at, detail, "
            "prev_hash, entry_hash) VALUES "
            "(:id, :tenant_id, :doc_id, :adapter, :action, :remote_id, "
            ":idempotency_key, :approved_by, :occurred_at, :detail, "
            ":prev_hash, :entry_hash)",
            {"id": entry.id, "tenant_id": self._tenant_id, "doc_id": entry.doc_id,
             "adapter": entry.adapter, "action": entry.action,
             "remote_id": entry.remote_id,
             "idempotency_key": entry.idempotency_key,
             "approved_by": entry.approved_by, "occurred_at": occurred_at,
             "detail": entry.detail, "prev_hash": prev_hash,
             "entry_hash": entry_hash})
        conn.commit()

    def record_write(self, doc_id: str, adapter: str, result: WriteResult,
                     approved_by: str) -> None:
        self._append(AuditEntry(
            id=str(uuid.uuid4()), doc_id=doc_id, adapter=adapter, action="write",
            remote_id=result.remote_id, idempotency_key=result.idempotency_key,
            approved_by=approved_by, occurred_at=result.created_at,
            detail="replay of an earlier write" if result.was_replay
                   else "unapproved bill created, awaiting review"))

    def record_reversal(self, doc_id: str, adapter: str, result: ReversalResult,
                        approved_by: str) -> None:
        self._append(AuditEntry(
            id=str(uuid.uuid4()), doc_id=doc_id, adapter=adapter, action="reversal",
            remote_id=result.remote_id, idempotency_key=None,
            approved_by=approved_by, occurred_at=result.reversed_at,
            detail=f"reversed by {result.method}"))

    def for_document(self, doc_id: str) -> list[AuditEntry]:
        rows = self._conn.execute(
            "SELECT id, doc_id, adapter, action, remote_id, idempotency_key, "
            "approved_by, occurred_at, detail FROM audit "
            "WHERE tenant_id = ? AND doc_id = ? ORDER BY rowid",
            (self._tenant_id, doc_id)).fetchall()
        return [AuditEntry(
            id=r[0], doc_id=r[1], adapter=r[2], action=r[3], remote_id=r[4],
            idempotency_key=r[5], approved_by=r[6],
            occurred_at=datetime.fromisoformat(r[7]), detail=r[8]) for r in rows]

    def document_for_remote(self, remote_id: str) -> str | None:
        """The doc_id of the audited *write* that created `remote_id`.

        Needed by `AuditedAdapter.reverse` (F-42): a reversal call carries only
        a remote id and an idempotency key, neither of which names the source
        document, and that resolution must survive a process restart — an
        in-memory cache does not. Looks up the `write` action specifically
        (not the most recent entry for the id) so a reversal that itself gets
        audited under the same remote_id never becomes its own answer.
        """
        row = self._conn.execute(
            "SELECT doc_id FROM audit WHERE tenant_id = ? AND remote_id = ? "
            "AND action = 'write' ORDER BY rowid DESC LIMIT 1",
            (self._tenant_id, remote_id)).fetchone()
        return cast(str, row[0]) if row else None

    def verify_chain(self) -> list[str]:
        """Walk every entry in insertion order (rowid, not occurred_at — two
        entries can share a timestamp) and recompute each expected hash from
        the previous entry's *stored* entry_hash and this row's own payload.

        Returns a human-readable description of every entry whose stored hash
        does not match, naming its remote_id and rowid so a human (or a test)
        can identify exactly what was tampered with. An unmodified chain
        returns an empty list.
        """
        rows = self._conn.execute(
            "SELECT rowid, id, tenant_id, doc_id, adapter, action, remote_id, "
            "idempotency_key, approved_by, occurred_at, detail, prev_hash, "
            "entry_hash FROM audit WHERE tenant_id = ? ORDER BY rowid",
            (self._tenant_id,)).fetchall()
        broken: list[str] = []
        expected_prev = _GENESIS_HASH
        for row in rows:
            (rowid, id_, tenant_id, doc_id, adapter, action, remote_id,
             idempotency_key, approved_by, occurred_at, detail, prev_hash,
             stored_hash) = row
            payload = _canonical_payload(
                id=id_, tenant_id=tenant_id, doc_id=doc_id, adapter=adapter,
                action=action, remote_id=remote_id,
                idempotency_key=idempotency_key, approved_by=approved_by,
                occurred_at=occurred_at, detail=detail)
            expected_hash = _compute_entry_hash(expected_prev, payload)
            if prev_hash != expected_prev or stored_hash != expected_hash:
                broken.append(
                    f"audit entry rowid={rowid} id={id_} remote_id={remote_id!r} "
                    f"action={action!r}: stored hash does not match its "
                    f"recomputed payload/predecessor — the row was modified "
                    f"after it was written")
            # Chain forward from the STORED hash, not the expected one, so a
            # single tampered entry is named once rather than cascading into
            # a false positive for every entry after it.
            expected_prev = stored_hash
        return broken

    @property
    def _conn(self) -> sqlite3.Connection:
        return connect(self._path)

    def close(self) -> None:
        """Checkpoint WAL so committed writes land in the main db file. The
        connection itself is owned and cached by the shared factory
        (per-thread, keyed by path), not by this instance, so it is not
        closed here — matching TokenStore's convention."""
        connect(self._path).execute("PRAGMA wal_checkpoint(FULL)")


class AuditedAdapter:
    """Wraps any WritebackPort so nothing reaches a client's books unaudited."""

    name: str

    def __init__(self, inner: WritebackPort, audit: AuditLog,
                 approved_by: str) -> None:
        self._inner = inner
        self._audit = audit
        self._approved_by = approved_by
        self.name = inner.name
        # F-42: resolving the source document for a reversal goes through the
        # durable audit log (`document_for_remote`), not an in-memory dict —
        # a dict dies with the process, and a reversal is exactly the
        # operation most likely to happen a day later.

    def is_period_closed(self, txn_date: date) -> bool:
        return self._inner.is_period_closed(txn_date)

    def create_unapproved_bill(self, request: WriteRequest) -> WriteResult:
        if not self._approved_by:
            raise ValueError(
                "a write requires approved_by — nothing reaches a client's books "
                "without a named human on the record")
        result = self._inner.create_unapproved_bill(request)
        self._audit.record_write(request.doc_id, self.name, result, self._approved_by)
        return result

    def reverse(self, remote_id: str, idempotency_key: str) -> ReversalResult:
        doc_id = self._audit.document_for_remote(remote_id)
        if doc_id is None:
            raise ValueError(
                f"no audited write for remote id {remote_id!r}; refusing to "
                f"reverse an object this system has no record of creating")
        result = self._inner.reverse(remote_id, idempotency_key)
        self._audit.record_reversal(doc_id, self.name, result, self._approved_by)
        return result
