from typing import Any

import pdfplumber
import pytest
from pdfplumber.pdf import PDF

from ledger_sense.corpus.generate import generate
from ledger_sense.extract.document import ParsedPDF, parse
from ledger_sense.ingest.fingerprint import fingerprint


def _pdf(seed: int = 1) -> bytes:
    _contract, _app, pdf = generate(seed=seed, error="clean")
    return pdf


def test_parse_returns_words_metadata_page_count_and_text_layer_flag() -> None:
    doc = parse(_pdf())
    assert isinstance(doc, ParsedPDF)
    assert doc.words
    assert all(w.text for w in doc.words)
    assert doc.page_count == 1
    assert doc.has_text_layer is True


def test_parse_metadata_reflects_pdfplumber_doc_metadata() -> None:
    """Generated PDFs are pypdfium2 output, which stamps Creator but not
    Producer — `parse()` must carry through whatever pdfplumber reports,
    not invent or drop fields."""
    doc = parse(_pdf())
    assert isinstance(doc.metadata, dict)
    assert doc.metadata.get("Creator") == "PDFium"


def test_parse_words_are_the_same_as_the_old_extract_words_would_return() -> None:
    from ledger_sense.extract.textlayer import extract_words
    assert list(parse(_pdf()).words) == extract_words(_pdf())


def test_parse_numbers_pages_and_sorts_words_within_each_page() -> None:
    """Multi-page coverage for the page_no/sort-key logic this refactor
    centralized (`two_page` splits the continuation rows across two pages,
    header repeated on each)."""
    _contract, _app, pdf = generate(seed=1, error="clean", layout="two_page")
    doc = parse(pdf)
    assert doc.page_count == 2
    pages_seen = {w.page for w in doc.words}
    assert pages_seen == {0, 1}
    for page_no in pages_seen:
        tops = [round(w.bbox.top, 1) for w in doc.words if w.page == page_no]
        assert tops == sorted(tops)


def test_fingerprint_opens_the_pdf_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """F-21. Before the shared ParsedDocument, a single call to `fingerprint`
    opened the PDF up to three times (once for the producer, once inside
    `_column_hash`'s `extract_words` call, and `extract_words` opens it
    again). `fingerprint` must now call `parse()` exactly once."""
    calls = {"n": 0}
    real_open = pdfplumber.open

    def counting_open(*args: Any, **kwargs: Any) -> PDF:
        calls["n"] += 1
        return real_open(*args, **kwargs)

    monkeypatch.setattr(pdfplumber, "open", counting_open)
    fingerprint(_pdf())
    assert calls["n"] == 1
