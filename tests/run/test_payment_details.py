"""The runner turns a document's bank details into an I3 hard stop (LS-10)."""

import io
import logging
from datetime import UTC, datetime

import pypdfium2 as pdfium
import pytest
import sqlalchemy as sa

from ledger_sense.auth.models import metadata
from ledger_sense.corpus.generate import _insert_text, generate
from ledger_sense.corpus.layouts import REMIT_LABEL_X_PT, REMIT_TOP_PT
from ledger_sense.corpus.remit import Remit, remit_for
from ledger_sense.domain.models import PayApplication
from ledger_sense.domain.vendor import PaymentDetail, VendorLedger, payment_hmac
from ledger_sense.run.document import run_document
from ledger_sense.store.retention import documents
from ledger_sense.store.vendors import save_vendor_ledger
from tests.run.conftest import RunDb


def _doc(db: RunDb, seed: int, variant: str = "base") -> tuple[PayApplication, Remit, str]:
    remit = remit_for(seed, variant)  # type: ignore[arg-type]
    _c, app, pdf = generate(seed=seed, error="clean", remit=remit)
    return app, remit, db.store_pdf(pdf)


def _confirm_baseline(db: RunDb, app: PayApplication, remit: Remit) -> None:
    assert app.vendor_name
    ledger = VendorLedger()
    vendor_id = ledger.create_confirmed(app.vendor_name, app.vendor_name,
                                        source_document="seed", confirmed_by="A. Bookkeeper")
    ledger.record_payment_detail(
        vendor_id,
        PaymentDetail(account_last4=remit.account[-4:], routing_last4=remit.routing[-4:],
                      account_hmac=payment_hmac(remit.account, remit.routing),
                      observed_at=datetime.now(UTC), source_document="seed"),
        confirmed_by="A. Bookkeeper")
    with db.engine.begin() as conn:
        save_vendor_ledger(conn, db.tenant_id, ledger)


def _i3(db: RunDb, document_id: str) -> list[sa.Row[tuple[object, ...]]]:
    return [f for f in db.findings_for(document_id) if f.rule_id == "I3"]


def test_a_document_without_a_bank_block_raises_nothing(db: RunDb) -> None:
    _c, _a, pdf = generate(seed=1, error="clean")
    document_id = db.store_pdf(pdf)
    run_document(db.engine, document_id)
    assert _i3(db, document_id) == []
    assert not [f for f in db.findings_for(document_id) if f.field == "payment_account"]


def test_first_seen_bank_details_are_a_hard_stop(db: RunDb) -> None:
    _app, remit, document_id = _doc(db, seed=2)
    run_document(db.engine, document_id)
    (finding,) = _i3(db, document_id)
    assert finding.field == "payment_account"
    assert finding.state == "open"
    assert finding.expected is None
    assert finding.actual == f"....{remit.account[-4:]}"
    assert finding.x0 is not None  # located: the reviewer can see the box


def test_the_same_account_as_the_confirmed_baseline_is_clean(db: RunDb) -> None:
    app, remit, document_id = _doc(db, seed=3)
    _confirm_baseline(db, app, remit)
    run_document(db.engine, document_id)
    assert _i3(db, document_id) == []


def test_a_changed_account_is_a_hard_stop(db: RunDb) -> None:
    app, remit, _ = _doc(db, seed=4)
    _confirm_baseline(db, app, remit)
    _a, changed, document_id = _doc(db, seed=4, variant="changed")
    run_document(db.engine, document_id)
    (finding,) = _i3(db, document_id)
    assert finding.expected == f"....{remit.account[-4:]}"
    assert finding.actual == f"....{changed.account[-4:]}"


def test_a_rerun_replaces_the_finding_instead_of_duplicating_it(db: RunDb) -> None:
    _app, _remit, document_id = _doc(db, seed=5)
    run_document(db.engine, document_id)
    with db.engine.begin() as conn:
        conn.execute(documents.update().where(documents.c.id == document_id)
                     .values(status="received"))
    run_document(db.engine, document_id)
    assert len(_i3(db, document_id)) == 1


def test_a_bank_block_that_cannot_be_read_is_recorded_not_guessed(db: RunDb) -> None:
    # A label with no value beside it: append a bare "Account Number" to a plain
    # corpus PDF, which has no bank block of its own.
    _c, _a, plain = generate(seed=6, error="clean")
    doc = pdfium.PdfDocument(plain)
    page = doc[0]
    _insert_text(page, doc, "Account Number", REMIT_LABEL_X_PT, REMIT_TOP_PT, 9.0)
    page.gen_content()
    buf = io.BytesIO()
    doc.save(buf)
    document_id = db.store_pdf(buf.getvalue())
    run_document(db.engine, document_id)
    assert _i3(db, document_id) == []
    (x0,) = [f for f in db.findings_for(document_id)
             if f.rule_id == "X0" and f.field == "payment_account"]
    assert "could not extract" in x0.message


def test_no_plaintext_account_or_routing_is_stored_or_logged(
        db: RunDb, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    _app, remit, document_id = _doc(db, seed=7)
    run_document(db.engine, document_id)
    dump: list[str] = []
    with db.engine.connect() as conn:
        for table in metadata.tables.values():
            dump.extend(str(row) for row in conn.execute(sa.select(table)).all())
    haystack = "\n".join(dump) + caplog.text
    assert remit.account not in haystack
    assert remit.routing not in haystack


def test_a_fuzzy_vendor_match_cannot_borrow_that_vendors_baseline(db: RunDb) -> None:
    app, remit, document_id = _doc(db, seed=3)
    assert app.vendor_name
    ledger = VendorLedger()
    near = f"{app.vendor_name} Co"
    vendor_id = ledger.create_confirmed(near, near, source_document="seed",
                                        confirmed_by="A. Bookkeeper")
    ledger.record_payment_detail(
        vendor_id,
        PaymentDetail(account_last4=remit.account[-4:], routing_last4=remit.routing[-4:],
                      account_hmac=payment_hmac(remit.account, remit.routing),
                      observed_at=datetime.now(UTC), source_document="seed"),
        confirmed_by="A. Bookkeeper")
    match = ledger.resolve(app.vendor_name)
    assert match.vendor_id == vendor_id and match.needs_confirmation  # fuzzy, not exact
    with db.engine.begin() as conn:
        save_vendor_ledger(conn, db.tenant_id, ledger)
    run_document(db.engine, document_id)
    (finding,) = _i3(db, document_id)
    assert finding.expected is None  # first-seen: no vendor is confirmed yet
