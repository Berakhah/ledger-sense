"""confirm-bank-change is the only way to clear an I3 hard stop (LS-10)."""

import io
from datetime import UTC, datetime

import pypdfium2 as pdfium

from ledger_sense.boundary import Origin
from ledger_sense.corpus.generate import _insert_text, generate
from ledger_sense.corpus.layouts import REMIT_LABEL_X_PT, REMIT_TOP_PT
from ledger_sense.corpus.remit import remit_for
from ledger_sense.ingest.intake import accept_document
from ledger_sense.run.document import run_document
from ledger_sense.store.blobs import blob_store_from_environment
from ledger_sense.store.documents import store_document
from ledger_sense.store.session import app_engine
from tests.api.conftest import TenantClient

ATTEST: dict[str, object] = {"channel": "phone", "contact": "J. Vendor", "note": "called the number on file"}


def _stored_and_run(tenant: TenantClient, seed: int, variant: str) -> str:
    remit = remit_for(seed, variant)  # type: ignore[arg-type]
    _c, _a, pdf = generate(seed=seed, error="clean", remit=remit)
    return _store_and_run(tenant, pdf)


def _store_and_run(tenant: TenantClient, pdf: bytes) -> str:
    doc = accept_document(pdf, origin=Origin.CORPUS, provenance="tests/api/test_bank_change.py")
    with app_engine().begin() as conn:
        document_id = store_document(
            conn, tenant_id=tenant.tenant_id, doc=doc, blobs=blob_store_from_environment(),
            received_at=datetime.now(UTC), filename="payapp.pdf")
    run_document(app_engine(), document_id)
    return document_id


def _exception(tenant: TenantClient, document_id: str, rule_id: str) -> dict[str, object]:
    rows = tenant.get(f"/api/review/documents/{document_id}/exceptions").json()
    (row,) = [r for r in rows if r["rule_id"] == rule_id]
    assert isinstance(row, dict)
    return row


def _confirm_new_vendor(tenant: TenantClient, document_id: str) -> str:
    i2 = _exception(tenant, document_id, "I2")
    response = tenant.post_json(
        f"/api/review/exceptions/{i2['id']}/confirm-vendor",
        {"new_vendor_name": "Acme Steel Company"})
    assert response.status_code == 200, response.text
    return str(response.json()["vendor_id"])


def test_generic_accept_edit_and_reject_cannot_clear_the_hard_stop(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=2, variant="base")
    i3 = _exception(a, document_id, "I3")
    base = f"/api/review/exceptions/{i3['id']}"
    assert a.post(f"{base}/accept").status_code == 422
    assert a.post_json(f"{base}/edit", {"value": "123456789"}).status_code == 422
    assert a.post_json(f"{base}/reject", {"reason": "looks fine"}).status_code == 422
    assert _exception(a, document_id, "I3")["state"] == "open"


def test_confirm_needs_a_confirmed_vendor_first(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=2, variant="base")
    i3 = _exception(a, document_id, "I3")
    response = a.post_json(f"/api/review/exceptions/{i3['id']}/confirm-bank-change", ATTEST)
    assert response.status_code == 409
    assert _exception(a, document_id, "I3")["state"] == "open"


def test_an_attestation_without_a_named_contact_is_refused(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=2, variant="base")
    _confirm_new_vendor(a, document_id)
    i3 = _exception(a, document_id, "I3")
    response = a.post_json(f"/api/review/exceptions/{i3['id']}/confirm-bank-change",
                           {"channel": "phone", "contact": "  "})
    assert response.status_code == 422


def test_confirming_records_the_detail_with_its_evidence_and_never_the_hmac(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=3, variant="base")
    vendor_id = _confirm_new_vendor(a, document_id)
    i3 = _exception(a, document_id, "I3")
    response = a.post_json(f"/api/review/exceptions/{i3['id']}/confirm-bank-change", ATTEST)
    assert response.status_code == 200, response.text
    assert _exception(a, document_id, "I3")["state"] == "confirmed"
    ledger = a.get(f"/api/vendors/{vendor_id}/ledger").json()
    (detail,) = ledger["payment_history"]
    assert detail["account_last4"] == remit_for(3).account[-4:]
    assert detail["confirmed_by"]
    assert (detail["confirmation_channel"], detail["confirmation_contact"]) == (
        "phone", "J. Vendor")
    assert "account_hmac" not in detail


def test_a_changed_account_on_the_next_invoice_stops_again_and_undo_reopens_it(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    first = _stored_and_run(a, seed=4, variant="base")
    vendor_id = _confirm_new_vendor(a, first)
    first_i3 = _exception(a, first, "I3")
    assert a.post_json(f"/api/review/exceptions/{first_i3['id']}/confirm-bank-change",
                       ATTEST).status_code == 200

    second = _stored_and_run(a, seed=4, variant="changed")
    changed = _exception(a, second, "I3")
    assert changed["expected"] == f"....{remit_for(4).account[-4:]}"
    assert changed["actual"] == f"....{remit_for(4, 'changed').account[-4:]}"
    response = a.post_json(f"/api/review/exceptions/{changed['id']}/confirm-bank-change", ATTEST)
    assert response.status_code == 200, response.text
    assert len(a.get(f"/api/vendors/{vendor_id}/ledger").json()["payment_history"]) == 2

    undo = a.post(f"/api/review/exceptions/{changed['id']}/undo-confirm-bank-change")
    assert undo.status_code == 200
    assert _exception(a, second, "I3")["state"] == "open"
    assert len(a.get(f"/api/vendors/{vendor_id}/ledger").json()["payment_history"]) == 1


def test_another_tenant_cannot_confirm_or_undo(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, b = two_tenants
    document_id = _stored_and_run(a, seed=5, variant="base")
    _confirm_new_vendor(a, document_id)
    i3 = _exception(a, document_id, "I3")
    base = f"/api/review/exceptions/{i3['id']}"
    assert b.post_json(f"{base}/confirm-bank-change", ATTEST).status_code == 404
    assert b.post(f"{base}/undo-confirm-bank-change").status_code == 404


def test_confirm_only_applies_to_an_i3_exception(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    exc_id = a.seed_exception()  # an A2 finding
    assert a.post_json(f"/api/review/exceptions/{exc_id}/confirm-bank-change",
                       ATTEST).status_code == 422


def test_undo_confirm_vendor_cannot_reopen_an_i3_finding(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=3, variant="base")
    vendor_id = _confirm_new_vendor(a, document_id)
    i3 = _exception(a, document_id, "I3")
    assert a.post_json(f"/api/review/exceptions/{i3['id']}/confirm-bank-change",
                       ATTEST).status_code == 200
    response = a.post(f"/api/review/exceptions/{i3['id']}/undo-confirm-vendor")
    assert response.status_code == 422
    assert _exception(a, document_id, "I3")["state"] == "confirmed"
    assert len(a.get(f"/api/vendors/{vendor_id}/ledger").json()["payment_history"]) == 1


def test_an_unreadable_bank_block_x0_cannot_be_accepted_or_edited_but_can_be_rejected(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    _c, _a, plain = generate(seed=6, error="clean")
    doc = pdfium.PdfDocument(plain)
    page = doc[0]
    _insert_text(page, doc, "Account Number", REMIT_LABEL_X_PT, REMIT_TOP_PT, 9.0)
    page.gen_content()
    buf = io.BytesIO()
    doc.save(buf)
    document_id = _store_and_run(a, buf.getvalue())
    rows = a.get(f"/api/review/documents/{document_id}/exceptions").json()
    (x0,) = [r for r in rows if r["rule_id"] == "X0" and r["field"] == "payment_account"]
    base = f"/api/review/exceptions/{x0['id']}"
    assert a.post(f"{base}/accept").status_code == 422
    assert a.post_json(f"{base}/edit", {"value": "123456789"}).status_code == 422
    assert a.post_json(f"{base}/reject", {"reason": "unreadable, ask vendor"}).status_code == 200
