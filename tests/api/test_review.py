"""tests/api/test_review.py

Bare FastAPI() + include_router, same pattern as tests/api/test_baseline.py:
this suite is the router's business logic in isolation. api.deps.register()
(wiring it into the real app with require_session) is covered by
tests/api/test_auth.py, not here.

Findings and claims are seeded by direct SQL against the real tables — the
same tables ledger_sense.run.document writes in production — rather than
through a test seam, because none exists here: unlike the stale Task 28 plan
sketch, this router has no in-memory store to seed.
"""

import inspect
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ledger_sense.api import review
from ledger_sense.api.review import router
from ledger_sense.auth.models import create_tenant, create_user
from ledger_sense.auth.session import issue_session
from ledger_sense.boundary import Origin, Sensitivity
from ledger_sense.route.deps import calibration_store_from_environment
from ledger_sense.store.retention import claims, documents, findings
from ledger_sense.store.session import app_engine

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def _session_cookie() -> tuple[dict[str, str], str]:
    suffix = uuid.uuid4().hex[:8]
    tenant_id = create_tenant(f"review-test-{suffix}")
    user_id = create_user(tenant_id, f"review-test-{suffix}@example.test", "irrelevant")
    return {"session": issue_session(user_id, tenant_id)}, tenant_id


def _document(tenant_id: str, *, sensitivity: Sensitivity = Sensitivity.SYNTHETIC) -> str:
    document_id = uuid.uuid4().hex
    with app_engine().begin() as conn:
        conn.execute(documents.insert().values(
            id=document_id, tenant_id=tenant_id, blob_id=None,
            received_at=datetime.now(UTC), sensitivity=sensitivity.value,
            origin=Origin.CORPUS.value, provenance="tests/api/test_review.py",
            filename="fixture.pdf"))
    return document_id


def _prior_claim(tenant_id: str, document_id: str, field: str, *,
                 match_quality: float = 0.87) -> None:
    """A prior DETERMINISTIC claim for the field a finding flags — the
    extraction's own quality score that a reviewer's accept/edit judges."""
    with app_engine().begin() as conn:
        conn.execute(claims.insert().values(
            id=uuid.uuid4().hex, tenant_id=tenant_id, document_id=document_id,
            field=field, value="1000.00", value_type="money", method="deterministic",
            confidence=None, match_quality=match_quality, page=0, x0=288.0, top=100.0,
            x1=368.0, bottom=112.0, caveat=None, observed_at=datetime.now(UTC)))


def _finding(tenant_id: str, document_id: str, *, index: int = 0,
            bbox: tuple[float, float, float, float] | None = (288.0, 100.0, 368.0, 112.0),
            page: int | None = 0, actual: str | None = "1005.00") -> str:
    exc_id = uuid.uuid4().hex
    x0, top, x1, bottom = bbox if bbox is not None else (None, None, None, None)
    with app_engine().begin() as conn:
        conn.execute(findings.insert().values(
            id=exc_id, tenant_id=tenant_id, document_id=document_id,
            rule_id="A2", field=f"line[{index:03d}].total_completed_and_stored",
            message="previous + this period + stored does not equal the total",
            expected="1000.00", actual=actual, page=page,
            x0=x0, top=top, x1=x1, bottom=bottom,
            state="open", created_at=datetime.now(UTC)))
    return exc_id


def _exceptions(cookies: dict[str, str], doc_id: str) -> list[dict[str, Any]]:
    resp = client.get(f"/api/review/documents/{doc_id}/exceptions", cookies=cookies)
    assert resp.status_code == 200, resp.text
    return list(resp.json())


def _claim(claim_id: str) -> sa.Row[Any]:
    with app_engine().connect() as conn:
        return conn.execute(
            sa.select(claims).where(claims.c.id == claim_id)).one()


def test_exceptions_list_is_ordered_by_page_then_position() -> None:
    cookies, tenant_id = _session_cookie()
    doc_id = _document(tenant_id)
    _finding(tenant_id, doc_id, index=2, bbox=(0.0, 40.0, 10.0, 50.0))
    _finding(tenant_id, doc_id, index=0, bbox=(0.0, 10.0, 10.0, 20.0))
    _finding(tenant_id, doc_id, index=1, bbox=(0.0, 25.0, 10.0, 35.0))
    keys = [(e["page"], e["bbox"][1]) for e in _exceptions(cookies, doc_id)]
    assert keys == sorted(keys)


def test_every_exception_carries_the_box_that_locates_it() -> None:
    cookies, tenant_id = _session_cookie()
    doc_id = _document(tenant_id)
    _finding(tenant_id, doc_id)
    exc = _exceptions(cookies, doc_id)[0]
    x0, top, x1, bottom = exc["bbox"]
    assert x1 > x0 and bottom > top
    assert exc["page"] == 0


def test_a_finding_with_no_geometry_carries_a_null_bbox() -> None:
    cookies, tenant_id = _session_cookie()
    doc_id = _document(tenant_id)
    _finding(tenant_id, doc_id, bbox=None, page=None)
    exc = _exceptions(cookies, doc_id)[0]
    assert exc["bbox"] is None
    assert exc["page"] is None


def test_accepting_writes_a_human_claim() -> None:
    cookies, tenant_id = _session_cookie()
    doc_id = _document(tenant_id)
    exc_id = _finding(tenant_id, doc_id)
    resp = client.post(f"/api/review/exceptions/{exc_id}/accept", cookies=cookies)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "accepted"
    claim = _claim(body["claim_id"])
    assert claim.method == "human"
    assert claim.value == "1005.00"


def test_accepting_without_geometry_is_refused() -> None:
    cookies, tenant_id = _session_cookie()
    doc_id = _document(tenant_id)
    exc_id = _finding(tenant_id, doc_id, bbox=None, page=None)
    resp = client.post(f"/api/review/exceptions/{exc_id}/accept", cookies=cookies)
    assert resp.status_code == 422


def test_accepting_with_no_extracted_value_is_refused() -> None:
    """A finding can flag a discrepancy with no candidate value at all (e.g.
    a missing continuation-sheet line) — nothing to accept as-is."""
    cookies, tenant_id = _session_cookie()
    doc_id = _document(tenant_id)
    exc_id = _finding(tenant_id, doc_id, actual=None)
    resp = client.post(f"/api/review/exceptions/{exc_id}/accept", cookies=cookies)
    assert resp.status_code == 422


def test_editing_records_the_new_value_and_a_human_claim() -> None:
    cookies, tenant_id = _session_cookie()
    doc_id = _document(tenant_id)
    exc_id = _finding(tenant_id, doc_id)
    resp = client.post(f"/api/review/exceptions/{exc_id}/edit",
                       json={"value": "1234.56"}, cookies=cookies)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "edited"
    claim = _claim(body["claim_id"])
    assert claim.method == "human"
    assert claim.value == "1234.56"


def test_edit_rejects_a_json_float_rather_than_coercing_it() -> None:
    cookies, tenant_id = _session_cookie()
    doc_id = _document(tenant_id)
    exc_id = _finding(tenant_id, doc_id)
    resp = client.post(f"/api/review/exceptions/{exc_id}/edit",
                       json={"value": 1234.56}, cookies=cookies)
    assert resp.status_code == 422


def test_editing_without_geometry_is_refused() -> None:
    cookies, tenant_id = _session_cookie()
    doc_id = _document(tenant_id)
    exc_id = _finding(tenant_id, doc_id, bbox=None, page=None)
    resp = client.post(f"/api/review/exceptions/{exc_id}/edit",
                       json={"value": "1234.56"}, cookies=cookies)
    assert resp.status_code == 422


def test_editing_inherits_value_type_from_the_claim_it_judges() -> None:
    cookies, tenant_id = _session_cookie()
    doc_id = _document(tenant_id)
    exc_id = _finding(tenant_id, doc_id)
    field = "line[000].total_completed_and_stored"
    with app_engine().begin() as conn:
        conn.execute(claims.insert().values(
            id=uuid.uuid4().hex, tenant_id=tenant_id, document_id=doc_id,
            field=field, value="1000.00", value_type="money", method="deterministic",
            confidence=0.9, match_quality=0.9, page=0, x0=288.0, top=100.0,
            x1=368.0, bottom=112.0, caveat=None, observed_at=datetime.now(UTC)))
    resp = client.post(f"/api/review/exceptions/{exc_id}/edit",
                       json={"value": "1234.56"}, cookies=cookies)
    claim = _claim(resp.json()["claim_id"])
    assert claim.value_type == "money"


def test_rejecting_requires_a_reason() -> None:
    cookies, tenant_id = _session_cookie()
    doc_id = _document(tenant_id)
    exc_id = _finding(tenant_id, doc_id)
    resp = client.post(f"/api/review/exceptions/{exc_id}/reject",
                       json={"reason": "   "}, cookies=cookies)
    assert resp.status_code == 422


def test_rejecting_records_the_reason_and_writes_no_claim() -> None:
    cookies, tenant_id = _session_cookie()
    doc_id = _document(tenant_id)
    exc_id = _finding(tenant_id, doc_id)
    resp = client.post(f"/api/review/exceptions/{exc_id}/reject",
                       json={"reason": "retainage step is intentional at 50%"},
                       cookies=cookies)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "rejected"
    assert body["claim_id"] is None
    with app_engine().connect() as conn:
        row = conn.execute(
            sa.select(findings.c.state, findings.c.reason)
            .where(findings.c.id == exc_id)).one()
    assert row.state == "rejected"
    assert row.reason == "retainage step is intentional at 50%"


def test_an_action_on_an_already_cleared_exception_is_refused() -> None:
    cookies, tenant_id = _session_cookie()
    doc_id = _document(tenant_id)
    exc_id = _finding(tenant_id, doc_id)
    client.post(f"/api/review/exceptions/{exc_id}/accept", cookies=cookies)
    resp = client.post(f"/api/review/exceptions/{exc_id}/accept", cookies=cookies)
    assert resp.status_code == 409


def test_close_is_a_compare_and_swap_not_a_read_then_write() -> None:
    """The router's own _require_open reads state before either call reaches
    _close, so it cannot by itself prove two concurrent accept/edit calls on
    the same finding can't both write a claim — that guarantee comes from
    _close's UPDATE ... WHERE state='open' matching at most one caller. This
    calls _close directly, twice, bypassing _require_open entirely, to prove
    the second call is refused by the UPDATE itself, not by the earlier read."""
    from fastapi import HTTPException

    from ledger_sense.api.review import _close

    _cookies, tenant_id = _session_cookie()
    doc_id = _document(tenant_id)
    exc_id = _finding(tenant_id, doc_id)

    with app_engine().begin() as conn:
        _close(conn, exc_id, "accepted")

    with pytest.raises(HTTPException) as exc_info, app_engine().begin() as conn:
        _close(conn, exc_id, "accepted")
    assert exc_info.value.status_code == 409

    with app_engine().connect() as conn:
        state = conn.execute(
            sa.select(findings.c.state).where(findings.c.id == exc_id)).scalar_one()
    assert state == "accepted"


def test_progress_counts_cleared_against_total() -> None:
    cookies, tenant_id = _session_cookie()
    doc_id = _document(tenant_id)
    first = _finding(tenant_id, doc_id, index=0)
    _finding(tenant_id, doc_id, index=1)
    _finding(tenant_id, doc_id, index=2)
    client.post(f"/api/review/exceptions/{first}/accept", cookies=cookies)
    resp = client.get(f"/api/review/documents/{doc_id}/progress", cookies=cookies)
    assert resp.json() == {"total": 3, "cleared": 1, "remaining": 2}


def test_a_cleared_exception_stays_cleared_in_the_list() -> None:
    cookies, tenant_id = _session_cookie()
    doc_id = _document(tenant_id)
    first = _finding(tenant_id, doc_id, index=0)
    _finding(tenant_id, doc_id, index=1, bbox=(0.0, 200.0, 10.0, 210.0))
    client.post(f"/api/review/exceptions/{first}/accept", cookies=cookies)
    open_states = [e for e in _exceptions(cookies, doc_id) if e["state"] == "open"]
    assert len(open_states) == 1


def test_an_exception_from_another_tenants_document_is_not_found() -> None:
    owner_cookies, owner_tenant = _session_cookie()
    other_cookies, _other_tenant = _session_cookie()
    doc_id = _document(owner_tenant)
    exc_id = _finding(owner_tenant, doc_id)
    resp = client.post(f"/api/review/exceptions/{exc_id}/accept", cookies=other_cookies)
    assert resp.status_code == 404
    # The owner's own action still succeeds — the check is tenant-scoped, not broken.
    assert client.post(f"/api/review/exceptions/{exc_id}/accept",
                       cookies=owner_cookies).status_code == 200


def test_an_unknown_exception_id_is_not_found() -> None:
    cookies, _tenant_id = _session_cookie()
    resp = client.post(f"/api/review/exceptions/{uuid.uuid4().hex}/accept", cookies=cookies)
    assert resp.status_code == 404


def test_accepting_a_synthetic_documents_exception_writes_a_synthetic_label() -> None:
    cookies, tenant_id = _session_cookie()
    doc_id = _document(tenant_id, sensitivity=Sensitivity.SYNTHETIC)
    field = "line[000].total_completed_and_stored"
    _prior_claim(tenant_id, doc_id, field, match_quality=0.91)
    exc_id = _finding(tenant_id, doc_id)
    resp = client.post(f"/api/review/exceptions/{exc_id}/accept", cookies=cookies)
    assert resp.status_code == 200, resp.text
    claim_id = resp.json()["claim_id"]

    store = calibration_store_from_environment()
    labels = store.labels_for(field, basis="synthetic")
    assert any(lab.source_claim_id == claim_id and lab.was_correct is True
              and lab.predicted_confidence == 0.91 for lab in labels)
    # No route promotes a synthetic session's evidence to field-grade.
    assert store.labels_for(field, basis="field") == []


def test_editing_a_client_confidential_documents_exception_writes_a_field_label() -> None:
    cookies, tenant_id = _session_cookie()
    doc_id = _document(tenant_id, sensitivity=Sensitivity.CLIENT_CONFIDENTIAL)
    field = "line[000].total_completed_and_stored"
    _prior_claim(tenant_id, doc_id, field, match_quality=0.42)
    exc_id = _finding(tenant_id, doc_id)
    resp = client.post(f"/api/review/exceptions/{exc_id}/edit",
                       json={"value": "1234.56"}, cookies=cookies)
    assert resp.status_code == 200, resp.text
    claim_id = resp.json()["claim_id"]

    store = calibration_store_from_environment()
    labels = store.labels_for(field, basis="field")
    assert any(lab.source_claim_id == claim_id and lab.was_correct is False
              and lab.predicted_confidence == 0.42 for lab in labels)


def test_rejecting_writes_no_calibration_label() -> None:
    cookies, tenant_id = _session_cookie()
    doc_id = _document(tenant_id)
    field = "line[000].total_completed_and_stored"
    _prior_claim(tenant_id, doc_id, field)
    exc_id = _finding(tenant_id, doc_id)
    before = calibration_store_from_environment().count(field)
    resp = client.post(f"/api/review/exceptions/{exc_id}/reject",
                       json={"reason": "false positive"}, cookies=cookies)
    assert resp.status_code == 200, resp.text
    after = calibration_store_from_environment().count(field)
    assert after == before


def test_accepting_with_no_prior_claim_writes_no_calibration_label() -> None:
    """A finding whose field has no prior extracted claim carries no
    predicted confidence to score -- nothing is fabricated."""
    cookies, tenant_id = _session_cookie()
    doc_id = _document(tenant_id)
    field = "line[999].never_extracted"
    exc_id = _finding(tenant_id, doc_id, index=999)
    with app_engine().begin() as conn:
        conn.execute(findings.update().where(findings.c.id == exc_id).values(field=field))
    before = calibration_store_from_environment().count(field)
    resp = client.post(f"/api/review/exceptions/{exc_id}/accept", cookies=cookies)
    assert resp.status_code == 200, resp.text
    after = calibration_store_from_environment().count(field)
    assert after == before


def test_no_review_route_accepts_a_caller_supplied_basis() -> None:
    """No route may exist by which a synthetic session is labelled as field
    evidence: none of accept/edit/reject take a `basis` the caller controls.

    The brief's own integration test names a single `review.record_action`
    function; this codebase has no such function (accept/edit/reject are
    separate FastAPI route handlers -- see task-42-report.md), so this checks
    the same property against each of them instead."""
    for handler in (review.accept, review.edit, review.reject):
        sig = inspect.signature(handler)
        assert "basis" not in sig.parameters
