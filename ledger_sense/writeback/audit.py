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
from typing import TYPE_CHECKING, Literal, cast

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
    id                    TEXT PRIMARY KEY,
    tenant_id             TEXT NOT NULL,
    doc_id                TEXT NOT NULL,
    adapter               TEXT NOT NULL,
    action                TEXT NOT NULL,
    remote_id             TEXT NOT NULL,
    idempotency_key       TEXT,
    vendor_ref            TEXT,
    vendor_invoice_number TEXT,
    approved_by           TEXT NOT NULL,
    occurred_at           TEXT NOT NULL,
    detail                TEXT NOT NULL,
    prev_hash             TEXT NOT NULL,
    entry_hash            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_by_remote ON audit (tenant_id, remote_id);
"""

# The fields that make up a hash-chained entry's canonical payload. This
# tuple is not just documentation: `_canonical_payload`, the INSERT in
# `_append`, and the SELECT in `verify_chain` are all built FROM it (see
# `_CHAIN_FIELDS`, `_ALL_COLUMNS`, `_INSERT_SQL`, `_VERIFY_COLUMNS` below), so
# adding, removing, or reordering a field here changes all three together —
# they cannot drift apart the way a comment merely asserting the same thing
# could.
_PAYLOAD_FIELDS = (
    "id", "tenant_id", "doc_id", "adapter", "action", "remote_id",
    "idempotency_key", "vendor_ref", "vendor_invoice_number",
    "approved_by", "occurred_at", "detail",
)

# The two chain columns, stored alongside the payload fields but excluded
# from the hash itself (a row's own hash cannot include its own hash).
_CHAIN_FIELDS = ("prev_hash", "entry_hash")

_ALL_COLUMNS = _PAYLOAD_FIELDS + _CHAIN_FIELDS

_INSERT_SQL = (
    f"INSERT INTO audit ({', '.join(_ALL_COLUMNS)}) VALUES "
    f"({', '.join(f':{column}' for column in _ALL_COLUMNS)})"
)

# rowid first (SQLite's true insertion order, used by verify_chain), then
# every payload field, then the two chain columns — same source tuple as the
# INSERT above, so the SELECT can never silently stop covering a column the
# hash actually depends on.
_VERIFY_COLUMNS = ("rowid", *_PAYLOAD_FIELDS, *_CHAIN_FIELDS)


class AuditEntry(BaseModel):
    id: str
    doc_id: str
    adapter: str
    action: Action
    remote_id: str
    idempotency_key: str | None
    vendor_ref: str | None
    vendor_invoice_number: str | None
    approved_by: str
    occurred_at: datetime
    detail: str


def _canonical_payload(**fields: str | None) -> str:
    """A deterministic serialization of one entry's fields.

    Only the names in `_PAYLOAD_FIELDS` are included (extras in `fields` are
    ignored, e.g. a caller passing `prev_hash`/`entry_hash` alongside the rest
    of a row) — this is what ties the hash to that single source-of-truth
    tuple rather than to whatever happens to be in scope at each call site.

    `json.dumps(..., sort_keys=True)` over a plain dict of strings sidesteps
    Python's dict-iteration-order ambiguity (not a real risk in 3.7+, but the
    explicit sort makes the format self-documenting and immune to a future
    field reordering silently changing every hash already on disk).
    """
    payload = {name: fields[name] for name in _PAYLOAD_FIELDS}
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

    CONCURRENCY (fixed after review, I-1): `_append` reads the chain tip and
    inserts the new entry inside one `BEGIN IMMEDIATE` transaction. Without
    that, "read the tip, then insert" is two separate implicit transactions —
    WAL mode's single-writer guarantee serializes each COMMIT, but says
    nothing about two writers interleaving between a SELECT and the INSERT
    that follows it, so two concurrent honest appends could read the same
    tip and both chain from it, corrupting the chain and making
    `verify_chain` report a FALSE tamper accusation against an entry nobody
    touched. A Python `threading.Lock` cannot fix this either, because
    `store/sqlite.py::connect()` hands each THREAD (and each process) its own
    connection to the same file — the race is between separate SQLite
    connections, so only SQLite's own transaction locking (which `BEGIN
    IMMEDIATE` acquires eagerly, before the read) can serialize it.

    WHAT `verify_chain()` DOES NOT PROVE (I-2, documented not fixed): the
    chain has no anchor outside the `audit` table itself. Deleting the most
    recent row, or forging its contents, before any further entry is
    appended leaves `verify_chain() == []` — there is nothing after that row
    to notice its hash no longer chains forward correctly. Only edits to an
    entry that already has a successor are caught. Closing this gap needs an
    external anchor (e.g. persisting the tip hash + row count somewhere
    outside this table, or an append-only remote log) and is a candidate
    follow-up task, not attempted here.
    """

    def __init__(self, db_path: str, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self._path = db_path
        self._tenant_id = tenant_id
        connect(db_path).executescript(_SCHEMA)
        connect(db_path).commit()

    def _last_hash(self, conn: sqlite3.Connection) -> str:
        """Must be called from inside the same `BEGIN IMMEDIATE` transaction
        that will perform the following INSERT (see `_append`) — reading the
        tip on its own connection/transaction is exactly the race I-1 fixed.
        """
        row = conn.execute(
            "SELECT entry_hash FROM audit WHERE tenant_id = ? "
            "ORDER BY rowid DESC LIMIT 1", (self._tenant_id,)).fetchone()
        return cast(str, row[0]) if row else _GENESIS_HASH

    def _append(self, entry: AuditEntry) -> None:
        conn = connect(self._path)
        occurred_at = entry.occurred_at.isoformat()
        # I-1: BEGIN IMMEDIATE acquires SQLite's write lock up front, before
        # the tip is even read, so no other connection (other thread, other
        # process) can insert between our read of the tip and our own
        # insert. See the class docstring for why a Python-level lock cannot
        # substitute for this.
        conn.execute("BEGIN IMMEDIATE")
        try:
            prev_hash = self._last_hash(conn)
            row: dict[str, str | None] = {
                "id": entry.id, "tenant_id": self._tenant_id,
                "doc_id": entry.doc_id, "adapter": entry.adapter,
                "action": entry.action, "remote_id": entry.remote_id,
                "idempotency_key": entry.idempotency_key,
                "vendor_ref": entry.vendor_ref,
                "vendor_invoice_number": entry.vendor_invoice_number,
                "approved_by": entry.approved_by, "occurred_at": occurred_at,
                "detail": entry.detail,
            }
            payload = _canonical_payload(**row)
            row["prev_hash"] = prev_hash
            row["entry_hash"] = _compute_entry_hash(prev_hash, payload)
            conn.execute(_INSERT_SQL, row)
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()

    def record_write(self, doc_id: str, adapter: str, result: WriteResult,
                     approved_by: str, *, vendor_ref: str | None = None,
                     vendor_invoice_number: str | None = None) -> None:
        self._append(AuditEntry(
            id=str(uuid.uuid4()), doc_id=doc_id, adapter=adapter, action="write",
            remote_id=result.remote_id, idempotency_key=result.idempotency_key,
            vendor_ref=vendor_ref, vendor_invoice_number=vendor_invoice_number,
            approved_by=approved_by, occurred_at=result.created_at,
            detail="replay of an earlier write" if result.was_replay
                   else "unapproved bill created, awaiting review"))

    def record_reversal(self, doc_id: str, adapter: str, result: ReversalResult,
                        approved_by: str, idempotency_key: str) -> None:
        """M-C (final whole-branch review): `idempotency_key` used to be
        hardcoded `None` here, even though `AuditedAdapter.reverse` already
        has the key on hand — dropping it from the reversal's own audit row
        is an unnecessary omission in a phase about reversal<->key linkage."""
        self._append(AuditEntry(
            id=str(uuid.uuid4()), doc_id=doc_id, adapter=adapter, action="reversal",
            remote_id=result.remote_id, idempotency_key=idempotency_key,
            vendor_ref=None, vendor_invoice_number=None,
            approved_by=approved_by, occurred_at=result.reversed_at,
            detail=f"reversed by {result.method}"))

    def for_document(self, doc_id: str) -> list[AuditEntry]:
        rows = self._conn.execute(
            "SELECT id, doc_id, adapter, action, remote_id, idempotency_key, "
            "vendor_ref, vendor_invoice_number, approved_by, occurred_at, detail "
            "FROM audit WHERE tenant_id = ? AND doc_id = ? ORDER BY rowid",
            (self._tenant_id, doc_id)).fetchall()
        return [AuditEntry(
            id=r[0], doc_id=r[1], adapter=r[2], action=r[3], remote_id=r[4],
            idempotency_key=r[5], vendor_ref=r[6], vendor_invoice_number=r[7],
            approved_by=r[8], occurred_at=datetime.fromisoformat(r[9]), detail=r[10])
            for r in rows]

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

        LIMITATION (I-2): this only catches tampering with an entry that
        already has a successor in the chain. Deleting, or rewriting the
        contents of, the single most recent row — before anything else is
        appended — leaves this method reporting a clean chain, because
        nothing downstream exists yet to disagree with it. Treat
        `verify_chain() == []` as "no tampering was detected among entries
        that have since been chained over", not as an unconditional "nothing
        was ever touched". Closing that gap needs an anchor outside this
        table (see the class docstring) and is a candidate follow-up, not
        implemented here.
        """
        query = (f"SELECT {', '.join(_VERIFY_COLUMNS)} FROM audit "
                 f"WHERE tenant_id = ? ORDER BY rowid")
        rows = self._conn.execute(query, (self._tenant_id,)).fetchall()
        broken: list[str] = []
        expected_prev = _GENESIS_HASH
        for raw_row in rows:
            rowid = cast(int, raw_row[0])
            values = dict(zip(_VERIFY_COLUMNS[1:], raw_row[1:], strict=True))
            prev_hash = cast(str, values["prev_hash"])
            stored_hash = cast(str, values["entry_hash"])
            payload_fields = {
                name: cast(str | None, values[name]) for name in _PAYLOAD_FIELDS}
            payload = _canonical_payload(**payload_fields)
            expected_hash = _compute_entry_hash(expected_prev, payload)
            if prev_hash != expected_prev or stored_hash != expected_hash:
                broken.append(
                    f"audit entry rowid={rowid} id={values['id']} "
                    f"remote_id={values['remote_id']!r} "
                    f"action={values['action']!r}: stored hash does not match "
                    f"its recomputed payload/predecessor — the row was "
                    f"modified after it was written")
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
        self._audit.record_write(
            request.doc_id, self.name, result, self._approved_by,
            vendor_ref=request.vendor_ref,
            vendor_invoice_number=request.vendor_invoice_number)
        return result

    def reverse(self, remote_id: str, idempotency_key: str) -> ReversalResult:
        doc_id = self._audit.document_for_remote(remote_id)
        if doc_id is None:
            raise ValueError(
                f"no audited write for remote id {remote_id!r}; refusing to "
                f"reverse an object this system has no record of creating")
        result = self._inner.reverse(remote_id, idempotency_key)
        self._audit.record_reversal(doc_id, self.name, result, self._approved_by,
                                    idempotency_key=idempotency_key)
        return result


if TYPE_CHECKING:
    # Static conformance check (I-1, final whole-branch review), matching
    # the pattern QBOAdapter and ExportAdapter already establish: this
    # assignment never executes (TYPE_CHECKING is False at runtime) but
    # mypy --strict fails this module the moment AuditedAdapter's shape
    # drifts from the protocol. AuditedAdapter is the composition root
    # production code would actually inject, so it is the one adapter this
    # check matters most for — its absence here (while both wrapped
    # adapters had it) was the gap this fixes. The dynamic counterpart —
    # isinstance(adapter, WritebackPort) — lives in
    # tests/writeback/test_audit.py.
    def _audited_adapter_satisfies_writebackport(adapter: AuditedAdapter) -> None:
        _conforms: WritebackPort = adapter
