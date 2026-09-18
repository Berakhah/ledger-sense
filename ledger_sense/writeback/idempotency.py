"""Idempotency ledger. Records intent BEFORE the remote call.

The dual-write problem: call the remote system, then record what you did, and a
crash between the two produces a duplicate bill on retry. So the order inverts —
record `pending`, make the call, record `committed`.

A `pending` record found on retry means WE DO NOT KNOW whether the write landed.
The adapter must query the remote system for the stamped key before creating
anything. That reconciliation is Task 36's job; this module's job is to make the
uncertainty visible instead of silently duplicating.
"""

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

    def lookup(self, key: str, adapter: str = "qbo") -> WriteRecord | None:
        row = connect(self._path).execute(
            "SELECT key, adapter, state, remote_id, reason, attempted_at, "
            "committed_at FROM writes WHERE key = ? AND adapter = ?",
            (key, adapter)).fetchone()
        return _to_record(row) if row else None

    def begin(self, key: str, adapter: str) -> WriteRecord:
        """Return the existing record if there is one; otherwise open a pending.

        A `reversed` record is treated as absent (F-39): the remote object is
        gone, so this is a genuinely new write, not a replay of a dead one.
        """
        conn = connect(self._path)
        existing = self.lookup(key, adapter)
        if existing is not None and existing.state != "reversed":
            return existing
        if existing is not None:
            conn.execute(
                "DELETE FROM writes WHERE key = ? AND adapter = ?", (key, adapter))
        conn.execute(
            "INSERT INTO writes (key, adapter, state, attempted_at) "
            "VALUES (?, ?, 'pending', ?)",
            (key, adapter, datetime.now(UTC).isoformat()))
        conn.commit()
        record = self.lookup(key, adapter)
        assert record is not None
        return record

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
