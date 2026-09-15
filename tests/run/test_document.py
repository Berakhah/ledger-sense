"""run_document: the guard. Extraction behaviour arrives in later tests."""

import io
from datetime import UTC, datetime

import pypdfium2 as pdfium
import pytest
import sqlalchemy as sa

from ledger_sense.corpus.generate import generate
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
