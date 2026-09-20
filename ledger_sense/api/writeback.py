"""Writeback API — the human-triggered seam from a cleared review queue to a
draft bill in the client's accounting system.

Never-rule #3: posting is a separate, later, human-triggered action, never a
step ledger_sense.run.document takes automatically. This router is the only
door a bill reaches an accounting system through.

The idempotency key (writeback.assemble.derive_idempotency_key) is
deliberately independent of vendor identity (Ruling 5, Phase 6 spec) — it
comes from the document alone. So "is this document already posted, and
under what vendor" is answered from the audit log, the one place both
QBOAdapter and ExportAdapter write through uniformly (AuditedAdapter),
rather than from IdempotencyLedger, which ExportAdapter never touches.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StrictStr

from ledger_sense.api.deps import owned, require_session
from ledger_sense.auth.models import users
from ledger_sense.auth.session import Session
from ledger_sense.store.retention import documents, findings
from ledger_sense.store.session import app_engine
from ledger_sense.writeback.assemble import (
    MissingClaimError,
    build_write_request,
    derive_idempotency_key,
)
from ledger_sense.writeback.audit import (
    AuditedAdapter,
    AuditEntry,
    AuditLog,
    audit_log_from_environment,
)
from ledger_sense.writeback.export import export_adapter_from_environment
from ledger_sense.writeback.idempotency import ledger_from_environment
from ledger_sense.writeback.interface import WritebackPort, WriteResult
from ledger_sense.writeback.qbo import qbo_adapter_from_environment

router = APIRouter(prefix="/api/writeback")

CurrentSession = Annotated[Session, Depends(require_session)]

AdapterName = Literal["qbo", "export"]


class StatusResponse(BaseModel):
    state: Literal["not_posted", "posted"]
    remote_id: str | None = None


def _get_document(document_id: str, session: Session, conn: sa.Connection) -> sa.Row[Any]:
    row = conn.execute(
        sa.select(documents.c.id, documents.c.tenant_id)
        .where(documents.c.id == document_id)).one_or_none()
    owned(session, None if row is None else str(row.tenant_id))
    assert row is not None  # owned() raised otherwise; for the type checker
    return row


def _live_write_entries(audit: AuditLog, doc_id: str) -> dict[str, AuditEntry]:
    """The currently-live 'write' entry per adapter for this document — one
    that has not since been reversed.

    Adapter-agnostic on purpose: ExportAdapter never touches
    IdempotencyLedger at all (its own CSV/IIF key-scan is its state), but
    both adapters write through AuditedAdapter uniformly, so the audit log
    is the one place "is this posted" can be answered without knowing which
    adapter posted it. Mirrors IdempotencyLedger.begin()'s own "a reversed
    record is treated as absent" rule (F-39).
    """
    live: dict[str, AuditEntry] = {}
    for entry in audit.for_document(doc_id):
        if entry.action == "write":
            live[entry.adapter] = entry
        elif entry.action == "reversal":
            current = live.get(entry.adapter)
            if current is not None and current.remote_id == entry.remote_id:
                del live[entry.adapter]
    return live


@router.get("/documents/{doc_id}/status")
def status(doc_id: str, session: CurrentSession) -> StatusResponse:
    with app_engine().connect() as conn:
        _get_document(doc_id, session, conn)
        try:
            derive_idempotency_key(conn, document_id=doc_id)
        except MissingClaimError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"cannot check status: no claim for {exc.field!r}") from exc

    audit = audit_log_from_environment()
    live = _live_write_entries(audit, doc_id)
    if not live:
        return StatusResponse(state="not_posted", remote_id=None)
    entry = next(iter(live.values()))
    return StatusResponse(state="posted", remote_id=entry.remote_id)


class PostBody(BaseModel):
    adapter: AdapterName
    vendor_ref: StrictStr
    vendor_invoice_number: StrictStr | None = None


class PostResponse(BaseModel):
    remote_id: str
    idempotency_key: str
    adapter: str
    was_replay: bool


def _require_cleared(conn: sa.Connection, document_id: str, tenant_id: str) -> None:
    rows = conn.execute(
        sa.select(findings.c.state)
        .where(findings.c.document_id == document_id, findings.c.tenant_id == tenant_id)
    ).all()
    if not rows:
        raise HTTPException(status_code=422,
                            detail="document has never been run: no exceptions recorded")
    remaining = sum(1 for row in rows if row.state == "open")
    if remaining:
        raise HTTPException(status_code=422, detail=f"{remaining} exceptions still open")


def _approved_by(conn: sa.Connection, session: Session) -> str:
    row = conn.execute(sa.select(users.c.email).where(users.c.id == session.user_id)).one()
    return str(row.email)


def _inner_adapter(name: AdapterName, ledger: Any) -> WritebackPort:
    if name == "qbo":
        return qbo_adapter_from_environment(ledger)
    return export_adapter_from_environment()


@router.post("/documents/{doc_id}/post")
def post(doc_id: str, body: PostBody, session: CurrentSession) -> PostResponse:
    with app_engine().connect() as conn:
        _get_document(doc_id, session, conn)
        _require_cleared(conn, doc_id, session.tenant_id)
        approved_by = _approved_by(conn, session)
        try:
            request = build_write_request(
                conn, tenant_id=session.tenant_id, document_id=doc_id,
                vendor_ref=body.vendor_ref, vendor_invoice_number=body.vendor_invoice_number)
        except MissingClaimError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"cannot post: no claim for {exc.field!r}") from exc

    audit = audit_log_from_environment()
    ledger = ledger_from_environment()
    adapter = AuditedAdapter(_inner_adapter(body.adapter, ledger), audit, approved_by)
    result: WriteResult = adapter.create_unapproved_bill(request)
    return PostResponse(remote_id=result.remote_id, idempotency_key=result.idempotency_key,
                        adapter=result.adapter, was_replay=result.was_replay)
