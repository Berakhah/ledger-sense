"""run_document: one stored document → Claims + Findings + an honest status.

Spec: docs/superpowers/specs/2026-09-15-extraction-run-design.md §2.

The runner owns its transactions (it needs two on failure), so it takes an
Engine. Its first statement is a WRITE — `run_attempts = run_attempts + 1
WHERE status IN (runnable)` — because that is what takes the row lock on
Postgres AND the database write lock on SQLite; `SELECT ... FOR UPDATE` is a
no-op on SQLite and two runners could both read `received`.

Nothing here raises for a document-level problem: the document ends up
`cannot_extract`, `duplicate` or `failed` with a note. Infrastructure
failures (DB, blob store, KEK) propagate.
"""

import logging
from typing import NamedTuple

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from ledger_sense.domain.models import Contract, RetainageTerms
from ledger_sense.domain.money import RoundingPolicy, money
from ledger_sense.store.blobs import BlobStore, blob_store_from_environment
from ledger_sense.store.retention import documents, findings

log = logging.getLogger(__name__)

RUNNABLE = frozenset({"received", "failed"})
HUMAN_DECISIONS_NOTE = "human decisions present; refusing to overwrite"
ROUNDING_POLICY_SOURCE = "tenant_default"


class RunOutcome(NamedTuple):
    document_id: str
    status: str
    """The row's status after the call — or "missing", which is an outcome, not a status."""
    note: str | None
    claims_written: int
    findings_written: int


def _tenant_default_contract() -> Contract:
    """The Contract the arithmetic rules are run against until a contracts
    table exists. Only `rounding` is real; every rule the run executes reads
    nothing else (tests/rules/test_contract_surface.py), and A7 — which does —
    is excluded. Never persisted."""
    return Contract(
        id="tenant-default",
        original_sum=money("0.00"),
        rounding=RoundingPolicy(money_places=2, percent_places=2),
        retainage=RetainageTerms(base_rate=money("0.00")),
    )


def _take_row(conn: Connection, document_id: str) -> RunOutcome | sa.Row[tuple[object, ...]]:
    taken = conn.execute(
        documents.update()
        .where(documents.c.id == document_id, documents.c.status.in_(RUNNABLE))
        .values(run_attempts=documents.c.run_attempts + 1)
    ).rowcount
    row = conn.execute(
        sa.select(documents).where(documents.c.id == document_id)).one_or_none()
    if row is None:
        return RunOutcome(document_id, "missing", None, 0, 0)
    if taken == 0:
        return RunOutcome(document_id, str(row.status), f"already {row.status}", 0, 0)
    decided = conn.execute(
        sa.select(sa.func.count()).select_from(findings)
        .where(findings.c.document_id == document_id, findings.c.state != "open")
    ).scalar_one()
    if decided:
        return RunOutcome(document_id, str(row.status), HUMAN_DECISIONS_NOTE, 0, 0)
    return row


def run_document(engine: Engine, document_id: str, *,
                 blobs: BlobStore | None = None) -> RunOutcome:
    """Run one document. Safe to call again: a row that is not `received` or
    `failed`, or that carries a human decision, is left exactly as it was."""
    store = blobs if blobs is not None else blob_store_from_environment()
    with engine.begin() as conn:
        taken = _take_row(conn, document_id)
        if isinstance(taken, RunOutcome):
            return taken
        return _execute(conn, taken, store)


def _execute(conn: Connection, row: sa.Row[tuple[object, ...]], store: BlobStore) -> RunOutcome:
    # Filled in by the extraction tasks. Until then every runnable document is
    # honestly "not extracted" rather than silently "extracted".
    conn.execute(documents.update().where(documents.c.id == row.id).values(
        status="cannot_extract", run_note="runner not yet implemented"))
    return RunOutcome(str(row.id), "cannot_extract", "runner not yet implemented", 0, 0)
