"""run_document: the guard. Extraction behaviour arrives in later tests."""

import io
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pypdfium2 as pdfium
import pytest
import sqlalchemy as sa

from ledger_sense.corpus.errors import ErrorClass
from ledger_sense.corpus.generate import generate
from ledger_sense.domain.money import money
from ledger_sense.extract.assemble import UnextractableField, assemble_pay_application
from ledger_sense.provenance import Claim
from ledger_sense.run.document import RunOutcome, run_document
from ledger_sense.store.retention import documents, findings
from tests.run.conftest import RunDb


def _clean_pdf(seed: int = 1) -> bytes:
    return generate(seed=seed, error="clean")[2]


def test_a_missing_document_is_reported_not_raised(db: RunDb) -> None:
    outcome = run_document(db.engine, "nope")
    assert outcome == RunOutcome("nope", "missing", None, 0, 0)


def test_a_document_that_is_not_runnable_is_left_alone(db: RunDb) -> None:
    document_id = db.store_pdf(_clean_pdf())
    with db.engine.begin() as conn:
        conn.execute(documents.update().where(documents.c.id == document_id)
                     .values(status="extracted", run_attempts=3))
    outcome = run_document(db.engine, document_id)
    assert outcome.status == "extracted"
    assert outcome.note == "already extracted"
    assert db.document(document_id).run_attempts == 3


def test_every_run_increments_the_attempt_counter(db: RunDb) -> None:
    document_id = db.store_pdf(_clean_pdf())
    run_document(db.engine, document_id)
    assert db.document(document_id).run_attempts == 1


def test_a_document_with_a_human_decision_is_never_overwritten(db: RunDb) -> None:
    document_id = db.store_pdf(_clean_pdf())
    with db.engine.begin() as conn:
        conn.execute(documents.update().where(documents.c.id == document_id)
                     .values(status="failed"))
        conn.execute(findings.insert().values(
            id="f1", tenant_id=db.tenant_id, document_id=document_id, rule_id="A1",
            field="total_completed_and_stored", message="m", state="accepted",
            created_at=datetime.now(UTC)))
    outcome = run_document(db.engine, document_id)
    assert outcome.status == "failed"
    assert outcome.note == "human decisions present; refusing to overwrite"
    with db.engine.connect() as conn:
        assert conn.execute(sa.select(findings.c.state)
                            .where(findings.c.document_id == document_id)).scalar() == "accepted"


def _blank_pdf() -> bytes:
    doc = pdfium.PdfDocument.new()
    doc.new_page(612, 792)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _invoice_pdf() -> bytes:
    # The corpus generator's private text helpers, used the way generate.py
    # itself uses them (ledger_sense/corpus/generate.py:63-107). Check the
    # positional order of _insert_text there before running.
    from ledger_sense.corpus.generate import _insert_text
    doc = pdfium.PdfDocument.new()
    page = doc.new_page(612, 792)
    _insert_text(page, doc, "INVOICE 1042", 72, 700, 12)
    _insert_text(page, doc, "Remit to: Acme Concrete", 72, 680, 12)
    page.gen_content()
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def test_a_pdf_with_no_text_layer_is_refused_honestly(db: RunDb) -> None:
    document_id = db.store_pdf(_blank_pdf())
    outcome = run_document(db.engine, document_id)
    assert outcome.status == "cannot_extract"
    row = db.document(document_id)
    assert row.status == "cannot_extract"
    assert row.doc_type is None
    assert row.run_note.startswith("no text layer")
    assert db.claims_for(document_id) == []


def test_a_document_that_is_not_a_pay_application_is_refused_with_its_type(db: RunDb) -> None:
    document_id = db.store_pdf(_invoice_pdf())
    outcome = run_document(db.engine, document_id)
    assert outcome.status == "cannot_extract"
    row = db.document(document_id)
    assert row.doc_type == "invoice"
    assert row.run_note == "doc_type=invoice: only pay applications are extracted in v1"
    assert db.claims_for(document_id) == []


def test_an_exception_inside_extraction_marks_failed_with_the_class_only(
    db: RunDb, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ledger_sense.run.document as run_module

    def _boom(pdf: bytes, source_path: str) -> object:
        raise ValueError("page text: Retainage 10% of $1,234.56")
    monkeypatch.setattr(run_module, "assemble_pay_application", _boom)
    document_id = db.store_pdf(_clean_pdf())
    outcome = run_document(db.engine, document_id)
    assert outcome.status == "failed"
    row = db.document(document_id)
    assert row.status == "failed"
    assert row.run_note == "failed: ValueError"
    assert "1,234.56" not in row.run_note
    assert row.doc_type == "pay_application"  # classified before the failure
    assert db.claims_for(document_id) == []
    assert row.run_attempts == 1


def test_a_failed_document_is_runnable_again(db: RunDb, monkeypatch: pytest.MonkeyPatch) -> None:
    import ledger_sense.run.document as run_module
    document_id = db.store_pdf(_clean_pdf())

    def _boom(pdf: bytes, source_path: str) -> object:
        raise RuntimeError("x")
    monkeypatch.setattr(run_module, "assemble_pay_application", _boom)
    assert run_document(db.engine, document_id).status == "failed"
    monkeypatch.undo()
    outcome = run_document(db.engine, document_id)
    assert outcome.status != "failed"
    assert db.document(document_id).run_attempts == 2


def test_a_parse_crash_before_classification_is_recorded_as_failed(
    db: RunDb, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ledger_sense.run.document as run_module

    def _boom(pdf: bytes) -> bool:
        raise ValueError("page text: $1,234.56")
    monkeypatch.setattr(run_module, "has_text_layer", _boom)
    document_id = db.store_pdf(_clean_pdf())
    outcome = run_document(db.engine, document_id)
    assert outcome.status == "failed"
    row = db.document(document_id)
    assert row.status == "failed"
    assert row.run_note == "failed: ValueError"
    assert "1,234.56" not in row.run_note
    assert row.doc_type is None
    assert db.claims_for(document_id) == []
    assert row.run_attempts == 1


def test_a_clean_document_is_extracted_with_one_claim_per_provenance_entry(db: RunDb) -> None:
    pdf = _clean_pdf()
    document_id = db.store_pdf(pdf)
    outcome = run_document(db.engine, document_id)
    assert outcome.status == "extracted"
    row = db.document(document_id)
    assert row.status == "extracted"
    assert row.doc_type == "pay_application"
    assert row.run_note is None
    assert row.rounding_policy_source == "tenant_default"

    expected = {k: v for k, v in assemble_pay_application(pdf, document_id).provenance.items()
                if not k.startswith("_")}
    stored = {c.field: c for c in db.claims_for(document_id)}
    assert set(stored) == set(expected)
    assert outcome.claims_written == len(expected)
    for field, claim in expected.items():
        assert isinstance(claim, Claim)
        s = stored[field]
        assert s.tenant_id == db.tenant_id
        assert s.method == "deterministic"
        assert s.page == claim.source.page
        assert (s.x0, s.top, s.x1, s.bottom) == claim.source.bbox
        if isinstance(claim.value, Decimal):
            assert s.value_type == "money"
            assert Decimal(s.value) == claim.value


def test_claims_keep_money_as_text_never_float(db: RunDb) -> None:
    document_id = db.store_pdf(_clean_pdf())
    run_document(db.engine, document_id)
    money_claims = [c for c in db.claims_for(document_id) if c.value_type == "money"]
    assert money_claims
    assert all(isinstance(c.value, str) for c in money_claims)


def test_an_unextractable_field_becomes_an_x0_finding_without_a_box(
    db: RunDb, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ledger_sense.run.document as run_module

    real = run_module.assemble_pay_application

    def _drop_previous_payments(pdf: bytes, source_path: str) -> object:
        app = real(pdf, source_path)
        app.provenance.pop("previous_payments")
        app.provenance["_unextractable"].append(
            UnextractableField("previous_payments", "label not found on page"))
        return app
    monkeypatch.setattr(run_module, "assemble_pay_application", _drop_previous_payments)

    document_id = db.store_pdf(_clean_pdf())
    outcome = run_document(db.engine, document_id)
    assert outcome.status == "extracted"
    x0 = [f for f in db.findings_for(document_id) if f.rule_id == "X0"]
    assert [f.field for f in x0] == ["previous_payments"]
    assert x0[0].message == "could not extract: label not found on page"
    assert x0[0].page is None and x0[0].x0 is None
    assert x0[0].state == "open"
    assert "previous_payments" not in {c.field for c in db.claims_for(document_id)}


def test_a_row_that_changes_status_during_the_write_raises_instead_of_failing(
    db: RunDb, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ledger_sense.run.document as run_module

    def _boom(conn: object, row: object, claim_rows: object, finding_rows: object) -> object:
        raise RuntimeError("document x changed status during the run")
    monkeypatch.setattr(run_module, "_write", _boom)

    document_id = db.store_pdf(_clean_pdf())
    with pytest.raises(RuntimeError):
        run_document(db.engine, document_id)

    row = db.document(document_id)
    assert row.status == "received"
    assert row.run_attempts == 0
    assert db.claims_for(document_id) == []


def test_a_clean_document_produces_no_findings(db: RunDb) -> None:
    document_id = db.store_pdf(_clean_pdf())
    outcome = run_document(db.engine, document_id)
    assert outcome.findings_written == 0
    assert db.findings_for(document_id) == []


@pytest.mark.parametrize("error,rule_id", [
    ("A1_footing", "A1"), ("A2_line_sum", "A2"), ("A8_retainage", "A8")])
def test_an_injected_error_becomes_a_finding_boxed_at_its_claim(
    db: RunDb, error: ErrorClass, rule_id: str,
) -> None:
    document_id = db.store_pdf(generate(seed=1, error=error)[2])
    run_document(db.engine, document_id)
    hits = [f for f in db.findings_for(document_id) if f.rule_id == rule_id]
    assert hits, f"{rule_id} did not fire"
    claims_by_field = {c.field: c for c in db.claims_for(document_id)}
    for f in hits:
        assert f.state == "open"
        assert f.expected is not None and f.actual is not None
        assert Decimal(f.expected) != Decimal(f.actual)
        if f.field in claims_by_field:
            c = claims_by_field[f.field]
            assert (f.page, f.x0, f.top, f.x1, f.bottom) == (c.page, c.x0, c.top, c.x1, c.bottom)
        else:
            assert f.page is None


def test_arithmetic_is_not_run_on_a_field_that_could_not_be_extracted(
    db: RunDb, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """assemble defaults an unextractable total to 0.00 so the model validates;
    a footing rule run on that zero is a confident lie. X0 says the truth."""
    import ledger_sense.run.document as run_module

    real = run_module.assemble_pay_application

    def _scrub_total(pdf: bytes, source_path: str) -> object:
        app = real(pdf, source_path)
        app.provenance.pop("total_completed_and_stored")
        app.provenance["_unextractable"].append(
            UnextractableField("total_completed_and_stored", "label not found on page"))
        return app.model_copy(update={"total_completed_and_stored": money("0.00")})
    monkeypatch.setattr(run_module, "assemble_pay_application", _scrub_total)

    document_id = db.store_pdf(_clean_pdf())
    run_document(db.engine, document_id)
    rules_fired = {f.rule_id for f in db.findings_for(document_id)}
    assert rules_fired == {"X0"}, rules_fired


def _pdf_with_one_unparseable_cell() -> tuple[bytes, str]:
    """A clean corpus document whose second line's `this_period` cell reads
    "n/a". Rendered through the generator's own `_render` (the way generate.py
    does it) so the document is otherwise indistinguishable from the corpus;
    per-line retainage is made real so A8 has something to sum. Returns the
    PDF and the provenance key `assemble` records for the lost cell."""
    from types import SimpleNamespace

    from ledger_sense.corpus.generate import _clean as clean_app
    from ledger_sense.corpus.generate import _render

    contract, app = clean_app(1)
    lines = [ln.model_copy(update={"retainage": money(ln.total_completed_and_stored / 10)})
             for ln in app.lines]
    app = app.model_copy(update={
        "lines": lines,
        "retainage_total": money(sum((ln.retainage for ln in lines), money("0")))})
    broken = SimpleNamespace(**{k: getattr(lines[1], k) for k in type(lines[1]).model_fields})
    broken.this_period = "n/a"
    rendered = app.model_copy(update={"lines": [lines[0], broken, *lines[2:]]})
    return _render(rendered, contract, "baseline"), f"line[{lines[1].item_no}].this_period"


def test_a_dropped_line_suppresses_the_rules_that_sum_over_lines(db: RunDb) -> None:
    """assemble drops a whole continuation row when one cell is not money.
    A1/A8 sum over the surviving rows, so an incomplete sum would read as a
    confident footing error that is really a missing row. X0 says the truth."""
    pdf, lost_field = _pdf_with_one_unparseable_cell()
    document_id = db.store_pdf(pdf)
    outcome = run_document(db.engine, document_id)
    assert outcome.status == "extracted"
    found = db.findings_for(document_id)
    assert [f.field for f in found if f.rule_id == "X0"] == [lost_field]
    assert lost_field not in {c.field for c in db.claims_for(document_id)}
    rules_fired = {f.rule_id for f in found}
    assert "A1" not in rules_fired, rules_fired
    assert "A8" not in rules_fired, rules_fired


def test_a7_never_fires_because_no_uploaded_document_has_a_contract(
    db: RunDb, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ledger_sense.run.document as run_module

    real = run_module.assemble_pay_application

    def _negative_change_orders(pdf: bytes, source_path: str) -> object:
        return real(pdf, source_path).model_copy(update={"change_orders_net": money("-1.00")})
    monkeypatch.setattr(run_module, "assemble_pay_application", _negative_change_orders)
    document_id = db.store_pdf(_clean_pdf())
    run_document(db.engine, document_id)
    assert "A7" not in {f.rule_id for f in db.findings_for(document_id)}


def test_a_byte_identical_resubmission_is_flagged_not_re_extracted(db: RunDb) -> None:
    pdf = _clean_pdf()
    t0 = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)
    first = db.store_pdf(pdf, received_at=t0)
    second = db.store_pdf(pdf, received_at=t0 + timedelta(minutes=5))
    assert run_document(db.engine, first).status == "extracted"
    outcome = run_document(db.engine, second)
    assert outcome.status == "duplicate"
    assert outcome.findings_written == 1
    row = db.document(second)
    assert row.run_note == f"duplicate of {first}"
    assert db.claims_for(second) == []
    d0 = db.findings_for(second)
    assert [(f.rule_id, f.field) for f in d0] == [("D0", "document")]
    assert first in d0[0].message
    assert d0[0].page is None


def test_the_original_of_a_pair_is_decided_by_received_at_then_id(db: RunDb) -> None:
    pdf = _clean_pdf()
    same_instant = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)
    a = db.store_pdf(pdf, received_at=same_instant)
    b = db.store_pdf(pdf, received_at=same_instant)
    original, twin = sorted([a, b])
    assert run_document(db.engine, twin).status == "duplicate"
    assert run_document(db.engine, original).status == "extracted"


def test_the_same_bytes_from_another_tenant_are_not_a_duplicate(db: RunDb) -> None:
    pdf = _clean_pdf()
    other = db.new_tenant()
    mine = db.store_pdf(pdf)
    theirs = db.store_pdf(pdf, tenant_id=other)
    assert run_document(db.engine, mine).status == "extracted"
    assert run_document(db.engine, theirs).status == "extracted"


def test_a_row_without_a_hash_is_never_matched(db: RunDb) -> None:
    pdf = _clean_pdf()
    legacy = db.store_pdf(pdf, received_at=datetime(2020, 1, 1, tzinfo=UTC))
    with db.engine.begin() as conn:
        conn.execute(documents.update().where(documents.c.id == legacy)
                     .values(content_sha256=None))
    later = db.store_pdf(pdf)
    assert run_document(db.engine, later).status == "extracted"
