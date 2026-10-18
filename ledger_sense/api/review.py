"""Review-queue API.

Every accept and edit writes a HUMAN-method Claim against the real `claims`
table Phase 2 already built — these are the labels Phase 6 fits a calibration
curve on, and why the product can honestly say it improves with use (F-46;
see docs/superpowers/plans/2026-08-29-REVISIONS.md's follow-up). Reject writes
no claim: it asserts nothing about the field's value, only that a human
judged the flagged discrepancy a false positive, and the reason goes on the
finding itself (migration 0006).

`claims.page/x0/top/x1/bottom` are NOT NULL — every claim is located. A
`findings` row can lack geometry (it is nullable there), and until the
profile-learning slice gives a reviewer a way to place or move the box (F-17,
deferred — see the same follow-up), this router cannot invent one. Accept and
edit on a finding with no geometry are refused with 422 rather than writing a
claim whose location was never confirmed.

Values arrive as STRINGS. A JSON float in an edit body is rejected, not
coerced — the money path (Never-rule #2) never sees one.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StrictStr, field_validator, model_validator

from ledger_sense.api.deps import owned, require_session
from ledger_sense.auth.session import Session
from ledger_sense.boundary import Sensitivity
from ledger_sense.domain.vendor import Attestation, PaymentDetail, VendorLedger, payment_hmac
from ledger_sense.extract.remit import extract_remit
from ledger_sense.extract.textlayer import extract_words
from ledger_sense.provenance import Method
from ledger_sense.review.actions import ReviewAction
from ledger_sense.route.calibration import LabelBasis
from ledger_sense.route.deps import calibration_store_from_environment
from ledger_sense.route.labels import labels_from_action
from ledger_sense.rules.integrity.bank_details import assess_payment_detail
from ledger_sense.store.blobs import blob_store_from_environment
from ledger_sense.store.documents import DocumentNotFound, load_document
from ledger_sense.store.retention import claims, documents, findings
from ledger_sense.store.session import app_engine
from ledger_sense.store.vendors import (
    load_vendor_ledger,
    load_vendor_ledger_for_update,
    save_vendor_ledger,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/review")

CurrentSession = Annotated[Session, Depends(require_session)]

ExceptionState = Literal["open", "accepted", "edited", "rejected", "confirmed"]

_BANK_FIELD = "payment_account"


def _refuse_bank_clearing(row: sa.Row[Any], *, action: str) -> None:
    """Never-rule 6. The I3 hard stop is cleared by ONE action, confirm-bank-change,
    which records who verified the details and how. accept/edit would write a
    claim carrying an account number; reject would dismiss the stop with a
    sentence. An X0 on the bank field (details present but unreadable) may still
    be rejected with a stated reason — there is no detail to attest."""
    if row.rule_id == "I3" or (row.field == _BANK_FIELD and action != "reject"):
        raise HTTPException(
            status_code=422,
            detail="bank-detail exceptions are cleared only by confirm-bank-change")


class ConfirmBankChangeBody(BaseModel):
    channel: Literal["phone", "in_person", "video_call", "other"]
    contact: StrictStr = Field(max_length=200)
    note: StrictStr | None = Field(default=None, max_length=2000)

    @field_validator("contact")
    @classmethod
    def _named(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("an attestation must name who was spoken to")
        return value


def _vendor_for_bank_confirmation(conn: sa.Connection, ledger: VendorLedger,
                                  row: sa.Row[Any]) -> str:
    """The vendor whose bank details this document asserts: the one a reviewer
    confirmed (documents.vendor_id), else an EXACT, unambiguous ledger match.
    Never a fuzzy guess — confirming bank details against the wrong vendor is the
    attack this control exists to stop."""
    confirmed = conn.execute(
        sa.select(documents.c.vendor_id).where(documents.c.id == row.document_id)
    ).scalar_one()
    if confirmed is not None:
        return str(confirmed)
    observed = _observed_vendor_name(conn, row.document_id)
    if observed is not None:
        match = ledger.resolve(observed)
        if match.vendor_id is not None and not match.needs_confirmation:
            return match.vendor_id
    raise HTTPException(status_code=409,
                        detail="confirm the vendor (I2) before confirming its bank details")


def _reextract_detail(conn: sa.Connection, row: sa.Row[Any], session: Session) -> PaymentDetail:
    """Re-read the detail from the stored document instead of keeping a pending
    one anywhere: there is then no unconfirmed row to leak into the baseline, and
    what is confirmed is provably what the document says (its last four must
    match the finding the reviewer is looking at)."""
    try:
        pdf = load_document(conn, tenant_id=session.tenant_id, document_id=row.document_id,
                            blobs=blob_store_from_environment()).payload
    except DocumentNotFound:
        raise HTTPException(
            status_code=409,
            detail="the stored document is no longer available; the bank details cannot be re-read",
        ) from None
    hit = extract_remit(extract_words(pdf)).hit
    if hit is None or f"....{hit.account[-4:]}" != row.actual:
        raise HTTPException(status_code=409,
                            detail="the document no longer matches this exception; re-run it")
    return PaymentDetail(
        account_last4=hit.account[-4:], routing_last4=hit.routing[-4:],
        account_hmac=payment_hmac(hit.account, hit.routing),
        observed_at=datetime.now(UTC), source_document=row.document_id)


_NO_GEOMETRY_DETAIL = (
    "this exception has no located geometry; accept/edit needs a confirmed "
    "box, which lands with the profile-learning slice")


class ReviewException(BaseModel):
    id: str
    doc_id: str
    field: str
    rule_id: str
    message: str
    expected: str | None
    actual: str | None
    page: int | None
    bbox: tuple[float, float, float, float] | None
    state: ExceptionState
    # The document's confirmed vendor — present only once an I2 exception is
    # confirmed, never derived from confirm-vendor's own response alone: a
    # page reload (or a different reviewer opening the same document later)
    # has no such response to remember, and "undo" has to keep working then.
    vendor_id: str | None = None


class EditBody(BaseModel):
    value: StrictStr


class RejectBody(BaseModel):
    reason: StrictStr

    @field_validator("reason")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a rejection must state a reason")
        return value


class VendorCandidate(BaseModel):
    vendor_id: str
    name: str
    score: float


class VendorMatch(BaseModel):
    observed_name: str
    candidate: VendorCandidate | None


class ConfirmVendorBody(BaseModel):
    vendor_id: str | None = None
    new_vendor_name: StrictStr | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> ConfirmVendorBody:
        if (self.vendor_id is None) == (self.new_vendor_name is None):
            raise ValueError("exactly one of vendor_id or new_vendor_name is required")
        return self


def _bbox(row: sa.Row[Any]) -> tuple[float, float, float, float] | None:
    if row.x0 is None or row.top is None or row.x1 is None or row.bottom is None:
        return None
    return (row.x0, row.top, row.x1, row.bottom)


def _exception_of(row: sa.Row[Any]) -> ReviewException:
    return ReviewException(
        id=row.id, doc_id=row.document_id, field=row.field, rule_id=row.rule_id,
        message=row.message, expected=row.expected, actual=row.actual,
        page=row.page, bbox=_bbox(row), state=row.state,
        vendor_id=row.vendor_id if row.rule_id == "I2" and row.state == "confirmed" else None)


def _sort_key(row: sa.Row[Any]) -> tuple[bool, int, bool, float, bool, float]:
    # Nulls sort last on every axis rather than raising a TypeError against a
    # None, which Python's default comparison would.
    return (row.page is None, row.page or 0,
            row.top is None, row.top or 0.0,
            row.x0 is None, row.x0 or 0.0)


def _get_finding(exc_id: str, session: Session, conn: sa.Connection) -> sa.Row[Any]:
    row = conn.execute(sa.select(findings).where(findings.c.id == exc_id)).one_or_none()
    owned(session, None if row is None else str(row.tenant_id))
    assert row is not None  # owned() raised otherwise; for the type checker
    return row


def _require_open(row: sa.Row[Any]) -> None:
    """Fast-path check only — not the concurrency guard. Two concurrent
    accept/edit calls on the same finding can both pass this (it reads a row
    fetched before either transaction's write), so it exists purely to skip
    unnecessary work in the common, non-racing case. `_transition` below is
    what actually prevents a double write."""
    if row.state != "open":
        raise HTTPException(status_code=409, detail=f"exception already {row.state}")


def _require_confirmed(row: sa.Row[Any]) -> None:
    """`_require_open`'s mirror for undo-confirm-vendor: a fast-path check
    only, since `_transition` below is what actually prevents a double
    undo."""
    if row.state != "confirmed":
        raise HTTPException(status_code=409,
                            detail=f"exception is {row.state}, not confirmed")


def _transition(conn: sa.Connection, exc_id: str, from_state: ExceptionState,
                new_state: ExceptionState, **extra: object) -> None:
    """The compare-and-swap: the UPDATE's own WHERE state=from_state is the
    concurrency guard. Two concurrent requests against the same finding race
    to this UPDATE; the loser's WHERE clause matches zero rows once the
    winner commits, so it gets 409 here rather than both committing a claim
    for one finding — which would silently corrupt the calibration labels
    this router exists to produce."""
    result = conn.execute(
        findings.update()
        .where(findings.c.id == exc_id, findings.c.state == from_state)
        .values(state=new_state, **extra))
    if result.rowcount == 0:
        raise HTTPException(status_code=409, detail="exception already actioned")


def _close(conn: sa.Connection, exc_id: str, new_state: ExceptionState,
          **extra: object) -> None:
    _transition(conn, exc_id, "open", new_state, **extra)


def _value_type_for(conn: sa.Connection, document_id: str, field: str) -> str:
    """The type of the claim this action judges, inherited rather than
    reparsed from a reviewer's raw string — a wrong guess here is a money-path
    correctness question (Never-rule #2), not a display detail.

    Falls back to "str" when no prior claim exists for this field — reachable
    from `edit` on a finding whose `actual` is None (extraction produced no
    candidate at all, so nothing was ever typed). A reviewer-supplied money
    value can land mistyped in that case. Same "no way to confirm a
    never-before-seen field" gap F-17/F-46's follow-up already tracks; not a
    Never-rule #2 violation on its own since the stored value stays a
    Decimal-safe string either way, but worth closing alongside that slice."""
    row = conn.execute(
        sa.select(claims.c.value_type)
        .where(claims.c.document_id == document_id, claims.c.field == field)
        .order_by(claims.c.observed_at.desc())
        .limit(1)
    ).one_or_none()
    return row.value_type if row is not None else "str"


_MONEY_EDIT = re.compile(r"-?\d{1,15}(\.\d{1,2})?")
_INT_EDIT = re.compile(r"-?\d{1,15}")


def _validate_edit_value(value_type: str, value: str) -> None:
    """LS-05: an edit is typed by the claim it judges, and must parse as that
    type BEFORE the finding is closed. Otherwise a reviewer's `not money` is
    stored as the effective value and writeback later fails on
    InvalidOperation. Money is plain decimal notation, at most two places:
    `money()` itself accepts NaN, Infinity and exponents, so it cannot be the
    gate. `str` claims are free text and unchanged."""
    ok = True
    if value_type == "money":
        ok = _MONEY_EDIT.fullmatch(value) is not None
    elif value_type == "int":
        ok = _INT_EDIT.fullmatch(value) is not None
    elif value_type == "date":
        try:
            date.fromisoformat(value)
        except ValueError:
            ok = False
    if not ok:
        raise HTTPException(status_code=422,
                            detail=f"{value!r} is not a valid {value_type} value")


def _match_quality_for(conn: sa.Connection, document_id: str, field: str) -> float | None:
    """The extraction's own quality score for the value a reviewer is now
    judging — the prior DETERMINISTIC claim for this (document_id, field),
    found the same way `_value_type_for` finds the prior claim's type.

    Labels carry `match_quality`, not `confidence` (F-46): `Claim.confidence`
    is `None` for every deterministic extraction and always was. Returns None
    when no prior claim exists (mirrors `_value_type_for`'s "str" fallback
    gap — reachable from `edit` on a finding whose `actual` was never a typed
    claim); callers must skip labelling rather than inventing a confidence
    that was never predicted."""
    row = conn.execute(
        sa.select(claims.c.match_quality)
        .where(claims.c.document_id == document_id, claims.c.field == field)
        .order_by(claims.c.observed_at.desc())
        .limit(1)
    ).one_or_none()
    return None if row is None else row.match_quality


def _observed_vendor_name(conn: sa.Connection, document_id: str) -> str | None:
    row = conn.execute(
        sa.select(claims.c.value)
        .where(claims.c.document_id == document_id, claims.c.field == "vendor_name")
        .order_by(claims.c.observed_at.desc())
        .limit(1)
    ).one_or_none()
    return None if row is None else row.value


def _basis_for(conn: sa.Connection, document_id: str, rule_id: str) -> LabelBasis:
    """Derived from the source document's own sensitivity label, never from a
    caller-supplied value — F-49/Never-rule #1's spirit applied to labels: no
    route may exist by which a synthetic review session gets recorded as
    field evidence. An R2 finding is an audit sample of a field the Router
    would have auto-accepted: on a real document its verdict is `audit`
    evidence, the only signal that can reveal drift (route/router.py)."""
    sensitivity = conn.execute(
        sa.select(documents.c.sensitivity).where(documents.c.id == document_id)
    ).scalar_one()
    if sensitivity == Sensitivity.SYNTHETIC.value:
        return "synthetic"
    return "audit" if rule_id == "R2" else "field"


def _record_label(conn: sa.Connection, row: sa.Row[Any], *, kind: str, claim_id: str,
                  session: Session, match_quality: float | None) -> None:
    """Turn an accept/edit into a calibration label and persist it — the
    review queue is the training signal (route/labels.py). A finding whose
    field has no prior extracted claim carries no predicted confidence to
    score, so it produces no label rather than a fabricated one."""
    if match_quality is None:
        log.warning(
            "no calibration label recorded: field=%s document_id=%s kind=%s claim_id=%s "
            "-- no prior claim carried a match_quality to score",
            row.field, row.document_id, kind, claim_id)
        return
    action = ReviewAction(kind=kind, field=row.field, claim_id=claim_id,
                          predicted_confidence=match_quality, actor=session.user_id,
                          at=datetime.now(UTC))
    labels = labels_from_action(action, basis=_basis_for(conn, row.document_id, row.rule_id))
    if labels:
        calibration_store_from_environment().add_labels(labels)


def _write_claim(conn: sa.Connection, row: sa.Row[Any], *, value: str,
                 value_type: str, session: Session) -> str:
    claim_id = uuid.uuid4().hex
    conn.execute(claims.insert().values(
        id=claim_id, tenant_id=session.tenant_id, document_id=row.document_id,
        field=row.field, value=value, value_type=value_type,
        method=Method.HUMAN.value, confidence=None, match_quality=None,
        page=row.page, x0=row.x0, top=row.top, x1=row.x1, bottom=row.bottom,
        caveat=None, observed_at=datetime.now(UTC)))
    return claim_id


@router.get("/documents/{doc_id}/exceptions")
def list_exceptions(doc_id: str, session: CurrentSession) -> list[ReviewException]:
    # Tenant-filtered in the query, not owned()+404: a cross-tenant doc_id
    # returns 200 with an empty list rather than 404, unlike the single-
    # resource endpoints below. Deliberate for a list-shaped endpoint (an
    # empty result reads the same either way) — not a data leak, but a real
    # difference from accept/edit/reject's 404-not-403 discipline; progress()
    # makes the same choice for the same reason.
    with app_engine().connect() as conn:
        rows = conn.execute(
            sa.select(findings, documents.c.vendor_id)
            .select_from(findings.join(documents, findings.c.document_id == documents.c.id))
            .where(findings.c.document_id == doc_id, findings.c.tenant_id == session.tenant_id)
        ).all()
    return [_exception_of(row) for row in sorted(rows, key=_sort_key)]


@router.post("/exceptions/{exc_id}/accept")
def accept(exc_id: str, session: CurrentSession) -> dict[str, str | None]:
    with app_engine().begin() as conn:
        row = _get_finding(exc_id, session, conn)
        _require_open(row)
        _refuse_bank_clearing(row, action="accept")
        if _bbox(row) is None:
            raise HTTPException(status_code=422, detail=_NO_GEOMETRY_DETAIL)
        if row.actual is None:
            raise HTTPException(status_code=422, detail="no extracted value to accept")
        _close(conn, exc_id, "accepted")
        value_type = _value_type_for(conn, row.document_id, row.field)
        match_quality = _match_quality_for(conn, row.document_id, row.field)
        claim_id = _write_claim(conn, row, value=row.actual, value_type=value_type,
                                session=session)
        _record_label(conn, row, kind="accept", claim_id=claim_id, session=session,
                      match_quality=match_quality)
    return {"status": "accepted", "claim_id": claim_id}


@router.post("/exceptions/{exc_id}/edit")
def edit(exc_id: str, body: EditBody, session: CurrentSession) -> dict[str, str | None]:
    with app_engine().begin() as conn:
        row = _get_finding(exc_id, session, conn)
        _require_open(row)
        _refuse_bank_clearing(row, action="edit")
        if _bbox(row) is None:
            raise HTTPException(status_code=422, detail=_NO_GEOMETRY_DETAIL)
        value_type = _value_type_for(conn, row.document_id, row.field)
        _validate_edit_value(value_type, body.value)
        _close(conn, exc_id, "edited")
        match_quality = _match_quality_for(conn, row.document_id, row.field)
        claim_id = _write_claim(conn, row, value=body.value, value_type=value_type,
                                session=session)
        _record_label(conn, row, kind="edit", claim_id=claim_id, session=session,
                      match_quality=match_quality)
    return {"status": "edited", "claim_id": claim_id}


@router.post("/exceptions/{exc_id}/reject")
def reject(exc_id: str, body: RejectBody, session: CurrentSession) -> dict[str, str | None]:
    with app_engine().begin() as conn:
        row = _get_finding(exc_id, session, conn)
        _require_open(row)
        _refuse_bank_clearing(row, action="reject")
        _close(conn, exc_id, "rejected", reason=body.reason)
    return {"status": "rejected", "claim_id": None}


@router.get("/exceptions/{exc_id}/vendor-match")
def vendor_match(exc_id: str, session: CurrentSession) -> VendorMatch:
    """The observed vendor name behind an I2 exception, plus the ledger's own
    fuzzy-match candidate (domain/vendor.py's `resolve`) — what confirm-vendor
    would act on, so a reviewer sees the same candidate confirm-vendor itself
    would use instead of re-deriving it from I2's free-text message.

    Read-only: no `_require_open`, since viewing this carries no consequence
    for an already-closed exception the way accept/edit/reject/confirm-vendor
    do."""
    with app_engine().connect() as conn:
        row = _get_finding(exc_id, session, conn)
        if row.rule_id != "I2":
            raise HTTPException(status_code=422,
                                detail="vendor-match only applies to an I2 exception")
        observed_name = _observed_vendor_name(conn, row.document_id)
        if observed_name is None:
            raise HTTPException(status_code=422, detail="no extracted vendor name to match")

        ledger = load_vendor_ledger(conn, session.tenant_id)
        match = ledger.resolve(observed_name)
        candidate = None
        if match.vendor_id is not None:
            canonical = ledger.get(match.vendor_id)
            assert canonical is not None  # resolve() only returns ids it found
            candidate = VendorCandidate(vendor_id=match.vendor_id, name=canonical.name,
                                        score=match.score)
    return VendorMatch(observed_name=observed_name, candidate=candidate)


@router.post("/exceptions/{exc_id}/confirm-vendor")
def confirm_vendor(exc_id: str, body: ConfirmVendorBody,
                   session: CurrentSession) -> dict[str, str | None]:
    with app_engine().begin() as conn:
        row = _get_finding(exc_id, session, conn)
        _require_open(row)
        if row.rule_id != "I2":
            raise HTTPException(status_code=422,
                                detail="confirm-vendor only applies to an I2 exception")
        observed_name = _observed_vendor_name(conn, row.document_id)
        if observed_name is None:
            raise HTTPException(status_code=422, detail="no extracted vendor name to confirm")

        ledger = load_vendor_ledger_for_update(conn, session.tenant_id)
        if body.vendor_id is not None:
            if ledger.get(body.vendor_id) is None:
                raise HTTPException(status_code=404, detail="not found")
            vendor_id = body.vendor_id
            ledger.confirm_alias(vendor_id, observed_name, source_document=row.document_id,
                                 confirmed_by=session.user_id)
        else:
            assert body.new_vendor_name is not None  # the validator guarantees one is set
            # create_confirmed, never bare create(): a vendor this route
            # creates must never exist with zero evidence, since
            # undo-confirm-vendor deletes exactly that state on undo.
            vendor_id = ledger.create_confirmed(body.new_vendor_name, observed_name,
                                                source_document=row.document_id,
                                                confirmed_by=session.user_id)

        save_vendor_ledger(conn, session.tenant_id, ledger)
        conn.execute(documents.update().where(documents.c.id == row.document_id)
                     .values(vendor_id=vendor_id))
        _close(conn, exc_id, "confirmed")
    return {"status": "confirmed", "vendor_id": vendor_id}


@router.post("/exceptions/{exc_id}/undo-confirm-vendor")
def undo_confirm_vendor(exc_id: str, session: CurrentSession) -> dict[str, str | None]:
    """Reverses confirm_vendor: removes the alias it added (by identity,
    deleting the vendor entirely if this was its only evidence — B6's
    "merges are reversible" spirit applied to a single confirmation) and
    reopens the exception so a wrong confirmation can be corrected, however
    long after the fact it is caught. No time limit: the mistake may surface
    on a later audit, not just in the moment.
    """
    with app_engine().begin() as conn:
        row = _get_finding(exc_id, session, conn)
        _require_confirmed(row)
        if row.rule_id != "I2":
            raise HTTPException(status_code=422,
                                detail="undo-confirm-vendor only applies to an I2 exception")

        # The lock must be acquired BEFORE anything this transaction reads is
        # trusted: two concurrent undo calls can both pass _require_confirmed
        # above (a fast-path check against a row read before either writes,
        # same caveat as _require_open's), so the vendor_id and alias reads
        # below are re-derived fresh once this transaction actually holds the
        # tenant's lock, not from any state read before it.
        ledger = load_vendor_ledger_for_update(conn, session.tenant_id)

        bank_confirmed = conn.execute(
            sa.select(findings.c.id).where(
                findings.c.document_id == row.document_id,
                findings.c.tenant_id == session.tenant_id,
                findings.c.rule_id == "I3", findings.c.state == "confirmed")
        ).first()
        if bank_confirmed is not None:
            raise HTTPException(
                status_code=409,
                detail="undo the bank-detail confirmation (I3) for this document first")

        doc_row = conn.execute(
            sa.select(documents.c.vendor_id).where(documents.c.id == row.document_id)
        ).one()
        vendor_id = doc_row.vendor_id
        if vendor_id is None:
            raise HTTPException(status_code=409, detail="exception already actioned")

        alias = ledger.alias_confirmed_from(vendor_id, row.document_id)
        if alias is None:
            raise HTTPException(status_code=409, detail="exception already actioned")

        ledger.undo_confirmation(vendor_id, alias.id)
        save_vendor_ledger(conn, session.tenant_id, ledger)

        conn.execute(documents.update().where(documents.c.id == row.document_id)
                     .values(vendor_id=None))
        _transition(conn, exc_id, "confirmed", "open")
    return {"status": "open"}


@router.post("/exceptions/{exc_id}/confirm-bank-change")
def confirm_bank_change(exc_id: str, body: ConfirmBankChangeBody,
                        session: CurrentSession) -> dict[str, str | None]:
    """The ONLY way to clear I3 (Never-rule 6). Records the detail the document
    carries, with the reviewer as confirmer and their attestation as evidence."""
    with app_engine().begin() as conn:
        row = _get_finding(exc_id, session, conn)
        _require_open(row)
        if row.rule_id != "I3":
            raise HTTPException(status_code=422,
                                detail="confirm-bank-change only applies to an I3 exception")
        ledger = load_vendor_ledger_for_update(conn, session.tenant_id)
        vendor_id = _vendor_for_bank_confirmation(conn, ledger, row)
        detail = _reextract_detail(conn, row, session)
        # The finding was assessed when the document ran; the baseline may have
        # moved since. Re-assess under the lock: a detail that now differs from
        # the vendor's confirmed account is a change, not the first sighting the
        # reviewer was shown, and must not silently replace or join the baseline.
        assessment = assess_payment_detail(ledger, vendor_id, detail)
        shown = (row.expected or "").removeprefix("....") or None
        if (assessment is not None and assessment.kind != "first_seen"
                and assessment.expected_last4 != shown):
            raise HTTPException(
                status_code=409,
                detail=("bank details differ from the vendor's confirmed account; this is a "
                        "change, not a first sighting - re-ingest the document so it is "
                        "assessed against that vendor"))
        ledger.record_payment_detail(
            vendor_id, detail, confirmed_by=session.user_id,
            attestation=Attestation(channel=body.channel, contact=body.contact,
                                    note=body.note))
        save_vendor_ledger(conn, session.tenant_id, ledger)
        _close(conn, exc_id, "confirmed")
    return {"status": "confirmed", "vendor_id": vendor_id}


@router.post("/exceptions/{exc_id}/undo-confirm-bank-change")
def undo_confirm_bank_change(exc_id: str, session: CurrentSession) -> dict[str, str | None]:
    """Reverses confirm_bank_change: removes the recorded detail by identity,
    restoring the previous baseline, and reopens the hard stop. No time limit — a
    wrong confirmation may only surface on a later audit."""
    with app_engine().begin() as conn:
        row = _get_finding(exc_id, session, conn)
        _require_confirmed(row)
        if row.rule_id != "I3":
            raise HTTPException(status_code=422,
                                detail="undo-confirm-bank-change only applies to an I3 exception")
        ledger = load_vendor_ledger_for_update(conn, session.tenant_id)
        found = ledger.find_payment_detail(row.document_id)
        if found is None:
            raise HTTPException(status_code=409, detail="exception already actioned")
        ledger.undo_payment_detail(found[0], found[1].id)
        save_vendor_ledger(conn, session.tenant_id, ledger)
        _transition(conn, exc_id, "confirmed", "open")
    return {"status": "open"}


@router.get("/documents/{doc_id}/progress")
def progress(doc_id: str, session: CurrentSession) -> dict[str, int]:
    with app_engine().connect() as conn:
        rows = conn.execute(
            sa.select(findings.c.state)
            .where(findings.c.document_id == doc_id, findings.c.tenant_id == session.tenant_id)
        ).all()
    total = len(rows)
    cleared = sum(1 for row in rows if row.state != "open")
    return {"total": total, "cleared": cleared, "remaining": total - cleared}
