import io

import pypdfium2 as pdfium

from ledger_sense.corpus.generate import generate
from ledger_sense.ingest.classify import classify


def test_a_generated_document_classifies_as_a_pay_application() -> None:
    """The corpus's clean document carries its continuation grid on the same
    page as the summary certification, so it is a complete pay application —
    not a standalone continuation sheet (that classification is for a
    document submitted without the summary block)."""
    _c, _a, pdf = generate(seed=1, error="clean")
    assert classify(pdf) == "pay_application"


def test_an_empty_pdf_classifies_as_unknown_not_as_a_guess() -> None:
    """Unknown routes to review. A confident wrong type is worse than a shrug."""
    blank = pdfium.PdfDocument.new()
    blank.new_page(612, 792)
    buf = io.BytesIO()
    blank.save(buf)
    assert classify(buf.getvalue()) == "unknown"
