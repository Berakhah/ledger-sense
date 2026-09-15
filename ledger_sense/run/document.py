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
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import NamedTuple

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from ledger_sense.domain.models import Contract, PayApplication, RetainageTerms
from ledger_sense.domain.money import RoundingPolicy, money
from ledger_sense.extract.assemble import assemble_pay_application
from ledger_sense.extract.textlayer import has_text_layer
from ledger_sense.ingest.classify import classify
from ledger_sense.provenance import Claim, FileSource
from ledger_sense.store.blobs import BlobStore, blob_store_from_environment
from ledger_sense.store.documents import load_document
from ledger_sense.store.retention import claims, documents, findings

log = logging.getLogger(__name__)

RUNNABLE = frozenset({"received", "failed"})
HUMAN_DECISIONS_NOTE = "human decisions present; refusing to overwrite"
ROUNDING_POLICY_SOURCE = "tenant_default"
NO_TEXT_LAYER_NOTE = "no text layer; scanned documents route to review until OCR routing ships"


class _RunFailure(Exception):
    """Carries what was learned before the failure (the doc_type) out of the
    transaction that is about to roll back."""

    def __init__(self, cause: Exception, doc_type: str | None) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.doc_type = doc_type


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
    try:
        with engine.begin() as conn:
            taken = _take_row(conn, document_id)
            if isinstance(taken, RunOutcome):
                return taken
            return _execute(conn, taken, store)
    except _RunFailure as failure:
        return _mark_failed(engine, document_id, failure.cause, failure.doc_type)


def _mark_failed(engine: Engine, document_id: str, cause: Exception,
                 doc_type: str | None) -> RunOutcome:
    # The transaction above has rolled back: no partial claims survive, and
    # the attempt increment rolled back with it — re-applied here. The
    # message may carry page text, so it goes to the log; the row gets the
    # class only. If the guarded UPDATE matches nothing the row changed
    # under us, which means the lock did not hold — re-raise.
    note = f"failed: {type(cause).__name__}"
    with engine.begin() as conn:
        tenant = conn.execute(sa.select(documents.c.tenant_id)
                              .where(documents.c.id == document_id)).scalar_one_or_none()
        log.warning("run failed document_id=%s tenant_id=%s: %r", document_id, tenant, cause)
        updated = conn.execute(
            documents.update()
            .where(documents.c.id == document_id, documents.c.status.in_(RUNNABLE))
            .values(status="failed", run_note=note, doc_type=doc_type,
                    run_attempts=documents.c.run_attempts + 1)
        ).rowcount
    if updated == 0:
        raise cause
    return RunOutcome(document_id, "failed", note, 0, 0)


def _refuse(conn: Connection, row: sa.Row[tuple[object, ...]], note: str,
            doc_type: str | None) -> RunOutcome:
    conn.execute(documents.update().where(documents.c.id == row.id).values(
        status="cannot_extract", run_note=note, doc_type=doc_type))
    return RunOutcome(str(row.id), "cannot_extract", note, 0, 0)


def _value_of(value: object) -> tuple[str, str]:
    """(text, value_type). Money first — a Decimal is never a float on the way out."""
    if isinstance(value, Decimal):
        return str(value), "money"
    if isinstance(value, date):
        return value.isoformat(), "date"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value), "int"
    return str(value), "str"


def _claim_rows(tenant_id: str, document_id: str, app: PayApplication) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for field, claim in app.provenance.items():
        if field.startswith("_") or not isinstance(claim, Claim):
            continue
        source = claim.source
        if not isinstance(source, FileSource):
            continue  # the run only ever produces file-sourced claims
        text, value_type = _value_of(claim.value)
        x0, top, x1, bottom = source.bbox
        rows.append({
            "id": uuid.uuid4().hex, "tenant_id": tenant_id, "document_id": document_id,
            "field": field, "value": text, "value_type": value_type,
            "method": claim.method.value, "confidence": claim.confidence,
            "match_quality": claim.match_quality, "page": source.page,
            "x0": x0, "top": top, "x1": x1, "bottom": bottom,
            "caveat": claim.caveat, "observed_at": claim.observed_at,
        })
    return rows


def _x0_rows(tenant_id: str, document_id: str, app: PayApplication,
             now: datetime) -> list[dict[str, object]]:
    entries = app.provenance.get("_unextractable", [])
    if not isinstance(entries, list):
        return []
    return [{
        "id": uuid.uuid4().hex, "tenant_id": tenant_id, "document_id": document_id,
        "rule_id": "X0", "field": entry.field,
        "message": f"could not extract: {entry.reason}",
        "expected": None, "actual": None,
        "page": None, "x0": None, "top": None, "x1": None, "bottom": None,
        "state": "open", "created_at": now,
    } for entry in entries]


def _write(conn: Connection, row: sa.Row[tuple[object, ...]],
           claim_rows: list[dict[str, object]],
           finding_rows: list[dict[str, object]]) -> RunOutcome:
    document_id = str(row.id)
    # Replace in place: everything here is `open` (the guard checked), and a
    # re-run's answer supersedes the previous one entirely.
    conn.execute(findings.delete().where(findings.c.document_id == document_id))
    conn.execute(claims.delete().where(claims.c.document_id == document_id))
    if claim_rows:
        conn.execute(claims.insert(), claim_rows)
    if finding_rows:
        conn.execute(findings.insert(), finding_rows)
    updated = conn.execute(
        documents.update()
        .where(documents.c.id == document_id, documents.c.status.in_(RUNNABLE))
        .values(status="extracted", run_note=None,
                rounding_policy_source=ROUNDING_POLICY_SOURCE)
    ).rowcount
    if updated == 0:
        raise RuntimeError(f"document {document_id} changed status during the run")
    return RunOutcome(document_id, "extracted", None, len(claim_rows), len(finding_rows))


def _execute(conn: Connection, row: sa.Row[tuple[object, ...]], store: BlobStore) -> RunOutcome:
    document_id, tenant_id = str(row.id), str(row.tenant_id)
    # `load_document` stays outside the try: DocumentNotFound / blob / KEK
    # failures are infrastructure, not a document-level problem, and must
    # propagate rather than becoming an honest `failed` row.
    pdf = load_document(conn, tenant_id=tenant_id, document_id=document_id, blobs=store).payload
    doc_type: str | None = None
    try:
        if not has_text_layer(pdf):
            return _refuse(conn, row, NO_TEXT_LAYER_NOTE, None)
        doc_type = classify(pdf)
        if doc_type != "pay_application":
            return _refuse(conn, row, f"doc_type={doc_type}: only pay applications are "
                                      "extracted in v1", doc_type)
        conn.execute(documents.update().where(documents.c.id == document_id)
                     .values(doc_type=doc_type))
        app = assemble_pay_application(pdf, document_id)
        now = datetime.now(UTC)
        claim_rows = _claim_rows(tenant_id, document_id, app)
        finding_rows = _x0_rows(tenant_id, document_id, app, now)
    except Exception as exc:  # every document-level error becomes `failed`
        raise _RunFailure(exc, doc_type) from exc
    # Outside the try on purpose (spec §2 step 7's catch-all covers steps 3-5
    # only): `_write`'s guarded UPDATE matching zero rows is a lock-integrity
    # violation — infrastructure, not an extraction failure — so its
    # RuntimeError must propagate and roll back the transaction rather than
    # become an ordinary `failed` row indistinguishable from a real crash.
    return _write(conn, row, claim_rows, finding_rows)
