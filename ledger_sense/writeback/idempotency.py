"""Idempotency ledger. Records intent BEFORE the remote call.

The dual-write problem: call the remote system, then record what you did, and a
crash between the two produces a duplicate bill on retry. So the order inverts —
record `pending`, make the call, record `committed`.

A `pending` record found on retry means WE DO NOT KNOW whether the write landed.
The adapter must query the remote system for the stamped key before creating
anything. That reconciliation is Task 36's job; this module's job is to make the
uncertainty visible instead of silently duplicating.

CONCURRENCY (C-1, fixed after the final whole-branch review): `begin()`'s
read-then-insert runs inside one `BEGIN IMMEDIATE` transaction (see its
docstring), closing the race where two concurrent callers with the SAME key
could both read "nothing exists yet" before either had inserted. That alone is
NOT sufficient to stop two concurrent callers from both posting a bill,
though: once `begin()` returns a `pending` record, whether freshly created or
found, both callers can still reach `QBOAdapter.create_unapproved_bill`'s
reconcile-then-POST branch and both find nothing posted yet — proven by a
concurrency test with a realistic (non-instant) fake `post_bill`, mirroring a
real HTTP round trip to QBO, which reliably produced duplicate bills even
with this transaction in place. `begin()` cannot close that second window by
itself: from the ledger's stored data alone, a `pending` record this call did
NOT create is indistinguishable between "a truly concurrent sibling call
still in-flight" and "this same logical caller, retrying sequentially after
its OWN prior crash" (the case
`test_force_failing_mid_write_then_retrying_creates_exactly_one_bill` exists
for) — there is no cross-process signal to tell them apart. `BeginResult.created`
lets a caller see which case applies FOR THIS PROCESS's own bookkeeping; the
actual refusal of a genuinely concurrent sibling is `QBOAdapter`'s
in-process `ConcurrentWriteInProgressError` guard (qbo.py), which closes the
window `begin()` structurally cannot.
"""

import sqlite3
from datetime import UTC, datetime
from typing import Literal, cast

from pydantic import BaseModel

from ledger_sense.store.sqlite import connect

# `reversed` is NEW — REVISED (F-39). Without it, `reverse` deleted the remote
# bill and left the ledger row saying `committed` with the now-dead remote_id,
# so the next write with the same key hit the committed branch and returned
# WriteResult(remote_id=<deleted id>, was_replay=True) — a success response
# pointing at an object that no longer exists in QuickBooks, and an audit entry
# recording a write that did not happen. Write -> reverse -> write was impossible.
WriteState = Literal["pending", "committed", "failed", "reversed"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS writes (
    key          TEXT NOT NULL,
    adapter      TEXT NOT NULL,
    state        TEXT NOT NULL,
    remote_id    TEXT,
    reason       TEXT,
    attempted_at TEXT NOT NULL,
    committed_at TEXT,
    PRIMARY KEY (key, adapter)
)
"""


class WriteRecord(BaseModel):
    key: str
    adapter: str
    state: WriteState
    remote_id: str | None
    reason: str | None
    attempted_at: datetime
    committed_at: datetime | None


class BeginResult(BaseModel):
    """The outcome of `IdempotencyLedger.begin()`.

    `created` is what lets a caller (see `QBOAdapter.create_unapproved_bill`)
    distinguish "I just opened this pending row myself" from "a pending row
    was already here" — the second case is ambiguous: it could be this same
    caller retrying after ITS OWN prior crash (the case
    `test_force_failing_mid_write_then_retrying_creates_exactly_one_bill`
    exists for), or it could be a truly concurrent sibling call still
    in-flight (C-1). See the module docstring for how that ambiguity is
    resolved.
    """
    record: WriteRecord
    created: bool


def _to_record(row: tuple[str, str, str, str | None, str | None, str, str | None]) -> WriteRecord:
    return WriteRecord(
        key=row[0], adapter=row[1], state=cast(WriteState, row[2]),
        remote_id=row[3], reason=row[4],
        attempted_at=datetime.fromisoformat(row[5]),
        committed_at=datetime.fromisoformat(row[6]) if row[6] else None)


class IdempotencyLedger:
    """A SQLite-backed store recording write intent, keyed by (key, adapter).

    Uses the shared per-thread WAL connection factory (F-19) — this class holds
    only the db path, not a connection, so it is safe to use from FastAPI's
    sync-handler threadpool.
    """

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        connect(db_path).execute(_SCHEMA)
        connect(db_path).commit()

    @property
    def path(self) -> str:
        """The underlying db file path. Used by `QBOAdapter`'s in-process
        concurrent-write guard (C-1) to key its in-flight marker on the
        actual resource being written to, rather than on this Python
        object's identity — two separate `IdempotencyLedger` instances
        opened against the same path (as happens after a simulated process
        restart in tests) still refer to the same underlying ledger."""
        return self._path

    def lookup(self, key: str, adapter: str = "qbo") -> WriteRecord | None:
        row = connect(self._path).execute(
            "SELECT key, adapter, state, remote_id, reason, attempted_at, "
            "committed_at FROM writes WHERE key = ? AND adapter = ?",
            (key, adapter)).fetchone()
        return _to_record(row) if row else None

    def begin(self, key: str, adapter: str) -> BeginResult:
        """Return the existing record if there is one; otherwise open a pending.

        A `reversed` record is treated as absent (F-39): the remote object is
        gone, so this is a genuinely new write, not a replay of a dead one.

        CONCURRENCY (C-1, fixed after review): the read-then-insert below runs
        inside one `BEGIN IMMEDIATE` transaction, matching `AuditLog._append`
        (see that class's docstring for why a Python-level lock cannot
        substitute for SQLite's own transaction locking here). Without it,
        two concurrent callers with the SAME key could both read "no existing
        record" before either had inserted, and both proceed to create/find a
        pending row — from there both would reach the
        reconcile-then-POST branch in `QBOAdapter.create_unapproved_bill` and
        find nothing posted yet, so both would post, creating two bills under
        one idempotency key. `BEGIN IMMEDIATE` acquires SQLite's write lock
        before the read, so only one caller can complete the read-then-insert
        at a time; every other concurrent caller blocks until that commits,
        then sees the row the first one created — it never independently
        decides "nothing exists yet" once a sibling has already started.
        """
        conn = connect(self._path)
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = self._lookup_locked(conn, key, adapter)
            if existing is not None and existing.state != "reversed":
                result = BeginResult(record=existing, created=False)
            else:
                if existing is not None:
                    conn.execute(
                        "DELETE FROM writes WHERE key = ? AND adapter = ?",
                        (key, adapter))
                conn.execute(
                    "INSERT INTO writes (key, adapter, state, attempted_at) "
                    "VALUES (?, ?, 'pending', ?)",
                    (key, adapter, datetime.now(UTC).isoformat()))
                record = self._lookup_locked(conn, key, adapter)
                assert record is not None
                result = BeginResult(record=record, created=True)
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()
        return result

    def _lookup_locked(self, conn: sqlite3.Connection, key: str,
                        adapter: str) -> WriteRecord | None:
        """Same query as `lookup`, but explicit about running on the caller's
        own connection/transaction (must be called from inside the `BEGIN
        IMMEDIATE` block in `begin()` — see that method)."""
        row = conn.execute(
            "SELECT key, adapter, state, remote_id, reason, attempted_at, "
            "committed_at FROM writes WHERE key = ? AND adapter = ?",
            (key, adapter)).fetchone()
        return _to_record(row) if row else None

    def commit(self, key: str, remote_id: str, adapter: str = "qbo") -> None:
        if self.lookup(key, adapter) is None:
            raise KeyError(f"no pending write for key {key!r} on adapter {adapter!r}")
        conn = connect(self._path)
        conn.execute(
            "UPDATE writes SET state = 'committed', remote_id = ?, committed_at = ? "
            "WHERE key = ? AND adapter = ?",
            (remote_id, datetime.now(UTC).isoformat(), key, adapter))
        conn.commit()

    def fail(self, key: str, reason: str, adapter: str = "qbo") -> None:
        if self.lookup(key, adapter) is None:
            raise KeyError(f"no pending write for key {key!r} on adapter {adapter!r}")
        conn = connect(self._path)
        conn.execute(
            "UPDATE writes SET state = 'failed', reason = ? "
            "WHERE key = ? AND adapter = ?", (reason, key, adapter))
        conn.commit()

    def mark_reversed(self, key: str, adapter: str = "qbo") -> None:
        """NEW (F-39). After a reversal the remote object is gone, so a replay
        would return a dead id. A `reversed` record lets `begin` open a fresh
        `pending` — write, reverse, write again produces a NEW bill."""
        if self.lookup(key, adapter) is None:
            raise KeyError(f"no write for key {key!r} on adapter {adapter!r}")
        conn = connect(self._path)
        conn.execute(
            "UPDATE writes SET state = 'reversed', remote_id = NULL "
            "WHERE key = ? AND adapter = ?", (key, adapter))
        conn.commit()

    def close(self) -> None:
        """No-op: connections are owned and cached by the shared factory
        (per-thread, keyed by path), not by this instance."""
