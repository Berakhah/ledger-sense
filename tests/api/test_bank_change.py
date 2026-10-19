"""confirm-bank-change is the only way to clear an I3 hard stop (LS-10)."""

import io
from datetime import UTC, datetime

import pypdfium2 as pdfium
import pytest
from httpx import Response

from ledger_sense.boundary import Origin
from ledger_sense.corpus.generate import _insert_text, generate
from ledger_sense.corpus.layouts import REMIT_LABEL_X_PT, REMIT_TOP_PT
from ledger_sense.corpus.remit import remit_for
from ledger_sense.ingest.intake import accept_document
from ledger_sense.run.document import run_document
from ledger_sense.store.blobs import blob_store_from_environment
from ledger_sense.store.documents import DocumentNotFound, store_document
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

def _run_with_remit(tenant: TenantClient, seed: int, remit_seed: int) -> str:
    _c, _a, pdf = generate(seed=seed, error="clean", remit=remit_for(remit_seed))
    return _store_and_run(tenant, pdf)

def _open_count(detail: str) -> int:
    return int(detail.split(" ", 1)[0])


def _confirm_i2_existing(tenant: TenantClient, document_id: str, vendor_id: str) -> None:
    i2 = _exception(tenant, document_id, "I2")
    response = tenant.post_json(
        f"/api/review/exceptions/{i2['id']}/confirm-vendor", {"vendor_id": vendor_id})
    assert response.status_code == 200, response.text


def test_undo_confirm_vendor_is_refused_while_a_confirmed_bank_detail_hangs_off_it(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=11, variant="base")
    vendor_id = _confirm_new_vendor(a, document_id)
    i2 = _exception(a, document_id, "I2")
    i3 = _exception(a, document_id, "I3")
    assert a.post_json(f"/api/review/exceptions/{i3['id']}/confirm-bank-change",
                       ATTEST).status_code == 200
    response = a.post(f"/api/review/exceptions/{i2['id']}/undo-confirm-vendor")
    assert response.status_code == 409
    assert "bank" in response.json()["detail"]
    assert _exception(a, document_id, "I2")["state"] == "confirmed"
    assert len(a.get(f"/api/vendors/{vendor_id}/ledger").json()["payment_history"]) == 1

    assert a.post(f"/api/review/exceptions/{i3['id']}/undo-confirm-bank-change"
                  ).status_code == 200
    assert a.post(f"/api/review/exceptions/{i2['id']}/undo-confirm-vendor"
                  ).status_code == 200


def test_confirming_a_first_sighting_against_a_vendor_with_a_different_baseline_is_refused(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    first = _run_with_remit(a, seed=12, remit_seed=12)
    second = _run_with_remit(a, seed=13, remit_seed=13)  # also first_seen: ledger still empty
    vendor_id = _confirm_new_vendor(a, first)
    first_i3 = _exception(a, first, "I3")
    assert a.post_json(f"/api/review/exceptions/{first_i3['id']}/confirm-bank-change",
                       ATTEST).status_code == 200
    _confirm_i2_existing(a, second, vendor_id)
    second_i3 = _exception(a, second, "I3")
    response = a.post_json(
        f"/api/review/exceptions/{second_i3['id']}/confirm-bank-change", ATTEST)
    assert response.status_code == 409
    assert "change, not a first sighting" in response.json()["detail"]
    assert _exception(a, second, "I3")["state"] == "open"
    assert len(a.get(f"/api/vendors/{vendor_id}/ledger").json()["payment_history"]) == 1


def test_the_panel_view_of_a_first_sighting_refused_by_confirm_is_refused_too(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    first = _run_with_remit(a, seed=12, remit_seed=12)
    second = _run_with_remit(a, seed=13, remit_seed=13)
    vendor_id = _confirm_new_vendor(a, first)
    first_i3 = _exception(a, first, "I3")
    assert a.post_json(f"/api/review/exceptions/{first_i3['id']}/confirm-bank-change",
                       ATTEST).status_code == 200
    _confirm_i2_existing(a, second, vendor_id)
    second_i3 = _exception(a, second, "I3")
    confirm = a.post_json(
        f"/api/review/exceptions/{second_i3['id']}/confirm-bank-change", ATTEST)
    view = a.get(f"/api/review/exceptions/{second_i3['id']}/bank-change")
    assert view.status_code == 409
    assert view.json()["detail"] == confirm.json()["detail"]


def test_confirming_a_first_sighting_equal_to_the_baseline_is_accepted(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    first = _run_with_remit(a, seed=14, remit_seed=14)
    second = _run_with_remit(a, seed=15, remit_seed=14)  # same account, different invoice
    vendor_id = _confirm_new_vendor(a, first)
    first_i3 = _exception(a, first, "I3")
    assert a.post_json(f"/api/review/exceptions/{first_i3['id']}/confirm-bank-change",
                       ATTEST).status_code == 200
    _confirm_i2_existing(a, second, vendor_id)
    second_i3 = _exception(a, second, "I3")
    response = a.post_json(
        f"/api/review/exceptions/{second_i3['id']}/confirm-bank-change", ATTEST)
    assert response.status_code == 200, response.text


def test_an_open_bank_hard_stop_blocks_writeback_until_it_is_confirmed(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=16, variant="base")
    post = f"/api/writeback/documents/{document_id}/post"
    body: dict[str, object] = {"adapter": "export", "vendor_ref": "V-9"}
    refused = a.post_json(post, body)
    assert refused.status_code == 422
    assert "exceptions still open" in refused.json()["detail"]

    _confirm_new_vendor(a, document_id)
    i3 = _exception(a, document_id, "I3")
    assert a.post_json(f"/api/review/exceptions/{i3['id']}/confirm-bank-change",
                       ATTEST).status_code == 200
    after = a.post_json(post, body)
    # The fixture document carries other findings this slice does not clear, so
    # the gate still refuses -- but for exactly two fewer open exceptions (the
    # I2 and I3 just confirmed): the bank hard stop no longer holds it.
    assert after.status_code == 422
    assert _open_count(after.json()["detail"]) == _open_count(refused.json()["detail"]) - 2


def test_a_purged_document_blob_makes_confirm_a_409_not_a_500(
        two_tenants: tuple[TenantClient, TenantClient],
        monkeypatch: pytest.MonkeyPatch) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=17, variant="base")
    _confirm_new_vendor(a, document_id)
    i3 = _exception(a, document_id, "I3")

    def _gone(*_args: object, **_kwargs: object) -> object:
        raise DocumentNotFound(document_id)

    monkeypatch.setattr("ledger_sense.api.review.load_document", _gone)
    response = a.post_json(f"/api/review/exceptions/{i3['id']}/confirm-bank-change", ATTEST)
    assert response.status_code == 409
    assert "no longer available" in response.json()["detail"]


def test_the_attestation_fields_are_length_bounded(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=18, variant="base")
    _confirm_new_vendor(a, document_id)
    i3 = _exception(a, document_id, "I3")
    url = f"/api/review/exceptions/{i3['id']}/confirm-bank-change"
    assert a.post_json(url, {**ATTEST, "contact": "c" * 201}).status_code == 422
    assert a.post_json(url, {**ATTEST, "note": "n" * 2001}).status_code == 422
    ok = a.post_json(url, {**ATTEST, "contact": "c" * 200, "note": "n" * 2000})
    assert ok.status_code == 200, ok.text


def test_neither_the_full_numbers_nor_the_hmac_appear_in_any_response(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=19, variant="base")
    vendor_id = _confirm_new_vendor(a, document_id)
    i3 = _exception(a, document_id, "I3")
    confirm = a.post_json(f"/api/review/exceptions/{i3['id']}/confirm-bank-change", ATTEST)
    remit = remit_for(19)
    texts = [confirm.text,
             a.get(f"/api/review/documents/{document_id}/exceptions").text,
             a.get(f"/api/vendors/{vendor_id}/ledger").text]
    for text in texts:
        assert remit.account not in text
        assert remit.routing not in text
        assert "account_hmac" not in text


def _bank_view(tenant: TenantClient, exc_id: object) -> Response:
    return tenant.get(f"/api/review/exceptions/{exc_id}/bank-change")


def test_bank_change_view_of_a_first_sighting(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=20, variant="base")
    _confirm_new_vendor(a, document_id)
    i3 = _exception(a, document_id, "I3")
    response = _bank_view(a, i3["id"])
    assert response.status_code == 200, response.text
    assert response.json() == {
        "vendor_name": "Acme Steel Company", "kind": "first_seen",
        "previous_last4": None, "incoming_last4": remit_for(20).account[-4:]}


def test_bank_change_view_of_a_changed_account(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    first = _stored_and_run(a, seed=21, variant="base")
    _confirm_new_vendor(a, first)
    first_i3 = _exception(a, first, "I3")
    assert a.post_json(f"/api/review/exceptions/{first_i3['id']}/confirm-bank-change",
                       ATTEST).status_code == 200

    second = _stored_and_run(a, seed=21, variant="changed")
    changed = _exception(a, second, "I3")
    body = _bank_view(a, changed["id"]).json()
    assert body["kind"] == "changed"
    assert body["previous_last4"] == remit_for(21).account[-4:]
    assert body["incoming_last4"] == remit_for(21, "changed").account[-4:]


def test_bank_change_view_is_read_only(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=22, variant="base")
    vendor_id = _confirm_new_vendor(a, document_id)
    i3 = _exception(a, document_id, "I3")
    before = a.get(f"/api/vendors/{vendor_id}/ledger").json()
    assert _bank_view(a, i3["id"]).status_code == 200
    assert a.get(f"/api/vendors/{vendor_id}/ledger").json() == before
    assert _exception(a, document_id, "I3")["state"] == "open"


def test_bank_change_view_refuses_a_non_i3_exception(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=23, variant="base")
    i2 = _exception(a, document_id, "I2")
    assert _bank_view(a, i2["id"]).status_code == 422


def test_bank_change_view_refuses_a_closed_exception(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=24, variant="base")
    _confirm_new_vendor(a, document_id)
    i3 = _exception(a, document_id, "I3")
    assert a.post_json(f"/api/review/exceptions/{i3['id']}/confirm-bank-change",
                       ATTEST).status_code == 200
    assert _bank_view(a, i3["id"]).status_code == 409


def test_bank_change_view_is_tenant_scoped(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, b = two_tenants
    document_id = _stored_and_run(a, seed=25, variant="base")
    _confirm_new_vendor(a, document_id)
    i3 = _exception(a, document_id, "I3")
    assert _bank_view(b, i3["id"]).status_code == 404


def test_bank_change_view_needs_a_confirmed_vendor_first(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=26, variant="base")
    i3 = _exception(a, document_id, "I3")
    assert _bank_view(a, i3["id"]).status_code == 409


def test_bank_change_view_leaks_no_full_number_and_no_hmac(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=27, variant="base")
    _confirm_new_vendor(a, document_id)
    i3 = _exception(a, document_id, "I3")
    text = _bank_view(a, i3["id"]).text
    remit = remit_for(27)
    assert remit.account not in text and remit.routing not in text
    assert "hmac" not in text.lower()


def test_bank_change_view_is_a_409_when_the_baseline_has_caught_up(
        two_tenants: tuple[TenantClient, TenantClient],
        monkeypatch: pytest.MonkeyPatch) -> None:
    """assess_payment_detail returns None when the detail equals the confirmed
    baseline: the finding is stale and the reviewer must re-run the document."""
    a, _ = two_tenants
    document_id = _stored_and_run(a, seed=28, variant="base")
    _confirm_new_vendor(a, document_id)
    i3 = _exception(a, document_id, "I3")
    monkeypatch.setattr("ledger_sense.api.review.assess_payment_detail",
                        lambda ledger, vendor_id, detail: None)
    response = _bank_view(a, i3["id"])
    assert response.status_code == 409
    assert "re-run" in response.json()["detail"]
