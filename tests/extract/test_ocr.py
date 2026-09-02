import io

import pypdfium2 as pdfium
from PIL import Image, ImageFilter

from ledger_sense.corpus.generate import generate
from ledger_sense.extract.ocr import CannotExtract, ocr_words


def _rasterise(pdf: bytes, degrade: bool = False) -> bytes:
    """Render to an image and back — destroys the text layer, like a scan.

    `degrade=True` blurs and JPEG-recompresses the page image first, standing
    in for a genuinely poor-quality scan/photo — the same kind of quality loss
    that gives OCR confidence its real spread, as opposed to just starving the
    renderer of resolution (which corrupts reads outright rather than merely
    lowering confidence on them — see `RENDER_SCALE` in `ocr.py`)."""
    doc = pdfium.PdfDocument(pdf)
    image = doc[0].render(scale=2).to_pil()
    if degrade:
        image = image.filter(ImageFilter.GaussianBlur(radius=1.5))
        jpeg = io.BytesIO()
        image.convert("RGB").save(jpeg, format="JPEG", quality=30)
        jpeg.seek(0)
        image = Image.open(jpeg)
    out = pdfium.PdfDocument.new()
    page = out.new_page(image.width / 2, image.height / 2)
    pdf_image = pdfium.PdfImage.new(out)
    pdf_image.set_bitmap(pdfium.PdfBitmap.from_pil(image))
    pdf_image.set_matrix(pdfium.PdfMatrix().scale(image.width / 2, image.height / 2))
    page.insert_obj(pdf_image)
    page.gen_content()
    buf = io.BytesIO()
    out.save(buf)
    return buf.getvalue()


def _with_blank_page_inserted(pdf: bytes, at: int) -> bytes:
    """Insert a wholly blank page at zero-based index `at` of `pdf`."""
    doc = pdfium.PdfDocument(pdf)
    blank_src = pdfium.PdfDocument.new()
    blank_src.new_page(612, 792)
    doc.import_pages(blank_src, pages=[0], index=at)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_ocr_returns_words_for_a_rasterised_document():
    _c, _a, pdf = generate(seed=1, error="clean")
    result = ocr_words(_rasterise(pdf))
    assert not isinstance(result, CannotExtract)
    assert any("Schedul" in w.text for w in result.words)


def test_ocr_refuses_a_wholly_blank_document_rather_than_returning_noise():
    """B11: route to review with an honest 'cannot extract' rather than guess."""
    blank = pdfium.PdfDocument.new()
    blank.new_page(612, 792)
    buf = io.BytesIO()
    blank.save(buf)
    assert isinstance(ocr_words(buf.getvalue()), CannotExtract)


def test_a_blank_page_does_not_discard_the_pages_that_worked():
    """F-23. `return CannotExtract(...)` inside the page loop threw away every
    word already recovered — a three-page document with a blank page 2 yielded
    nothing at all."""
    _c, _a, pdf = generate(seed=1, error="clean")
    mixed = _with_blank_page_inserted(_rasterise(pdf), at=1)
    result = ocr_words(mixed)
    assert not isinstance(result, CannotExtract)
    assert result.words, "words from the readable pages were discarded"
    assert [f.page for f in result.failures] == [1]


def test_every_token_is_returned_with_its_confidence_and_none_are_dropped():
    """F-24. MIN_CONFIDENCE = 0.5 was an uncalibrated threshold written by feel,
    on the noisiest path in the product — exactly what Phase 6 forbids in the
    router and enforces with an AST test.

    Uses `_rasterise(pdf, degrade=True)` — a genuinely blurred, heavily
    JPEG-compressed page image — rather than turning down `RENDER_SCALE` in
    production code, so the confidence spread here comes from real document
    quality loss, the kind an actual bad scan/photo has, not from starving the
    renderer of resolution."""
    _c, _a, pdf = generate(seed=1, error="clean")
    result = ocr_words(_rasterise(pdf, degrade=True))
    assert all(w.confidence is not None for w in result.words)
    assert any(w.confidence < 0.9 for w in result.words), \
        "a low-confidence token was silently dropped instead of routed to review"


def test_no_confidence_threshold_is_hard_coded_in_extract():
    """The AST guard from Phase 6 Task 41, scoped to `extract/ocr.py` — the one
    file in `extract/` that plausibly reintroduces a confidence cutoff. The
    rest of `extract/` (anchors.py, tables.py) carries legitimate geometry
    tolerances in points that have nothing to do with confidence thresholds
    and are not this guard's concern."""
    import ast
    import pathlib
    offenders = []
    for path in [pathlib.Path("ledger_sense/extract/ocr.py")]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Constant) and isinstance(node.value, float)
                    and node.value not in (0.0, 1.0)):
                offenders.append(f"{path}:{node.lineno}: {node.value}")
    assert offenders == [], f"literal thresholds in extract/ocr.py: {offenders}"


def test_importing_the_ocr_module_does_not_load_the_models():
    """F-22. `_ENGINE = RapidOCR()` at module scope cost hundreds of megabytes
    and seconds of latency on every import, including bare test collection."""
    import importlib
    import sys
    sys.modules.pop("ledger_sense.extract.ocr", None)
    module = importlib.import_module("ledger_sense.extract.ocr")
    assert module._engine.cache_info().currsize == 0
