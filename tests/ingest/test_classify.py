import io

import pypdfium2 as pdfium

from ledger_sense.corpus.generate import generate
from ledger_sense.ingest.classify import classify


def test_a_generated_document_classifies_as_a_continuation_sheet() -> None:
    _c, _a, pdf = generate(seed=1, error="clean")
    assert classify(pdf) == "continuation_sheet"


def test_an_empty_pdf_classifies_as_unknown_not_as_a_guess() -> None:
    """Unknown routes to review. A confident wrong type is worse than a shrug."""
    blank = pdfium.PdfDocument.new()
    blank.new_page(612, 792)
    buf = io.BytesIO()
    blank.save(buf)
    assert classify(buf.getvalue()) == "unknown"
