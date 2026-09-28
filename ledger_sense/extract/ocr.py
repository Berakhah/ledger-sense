"""OCR for the scanned minority. RapidOCR (Apache-2.0) via ONNX.

NEVER guess. A phone photo of a crumpled invoice defeats geometry-based
extraction; the correct behaviour is CannotExtract and a route to review.
"""

import functools
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
import pypdfium2 as pdfium

from ledger_sense.extract.textlayer import BBox, Word
from ledger_sense.pdfium_lock import PDFIUM_LOCK

if TYPE_CHECKING:
    # Type-only: the real import stays lazy, inside _engine(), so importing
    # this module never loads the ONNX models (F-22).
    from rapidocr_onnxruntime import RapidOCR

RENDER_SCALE = 2
"""144 DPI. Lower (e.g. native 72 DPI at scale 1) measurably corrupts reads on
real documents — not just lower confidence scores, actual character-level
misreads ("050" -> "0S0") — so this stays a production-quality render
regardless of what any one test's confidence spread wants."""


@functools.lru_cache(maxsize=1)
def _engine() -> "RapidOCR":
    """LAZY (F-22). `_ENGINE = RapidOCR()` at module scope loaded ONNX
    models on any import, including bare test collection: hundreds of megabytes
    and seconds of latency on a path that may never OCR anything, and an
    ImportError on a machine without the weights."""
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def _render_pages(pdf: bytes) -> list[np.ndarray]:
    """Every page as an RGB array, rendered under PDFIUM_LOCK
    (ledger_sense/pdfium_lock.py). Bitmap, page and document are closed inside
    the lock rather than left to finalisers on another thread, and the lock is
    released before any OCR runs: inference takes seconds a page and never
    touches PDFium, so holding it would stall every generate() behind a scan."""
    images: list[np.ndarray] = []
    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(pdf)
        try:
            for page in doc:
                try:
                    bitmap = page.render(scale=RENDER_SCALE)
                    try:
                        images.append(np.asarray(bitmap.to_pil().convert("RGB")))
                    finally:
                        bitmap.close()
                finally:
                    page.close()
        finally:
            doc.close()
    return images


class CannotExtract(NamedTuple):
    reason: str
    page: int


class OcrResult(NamedTuple):
    """Per-page (F-23). The original returned CannotExtract from inside
    the page loop, throwing away every word already recovered from earlier pages:
    a three-page document with a blank page 2 yielded nothing at all."""
    words: list[Word]
    failures: list[CannotExtract]


def ocr_words(pdf: bytes) -> OcrResult | CannotExtract:
    """NO CONFIDENCE FLOOR HERE (F-24).

    `MIN_CONFIDENCE = 0.5` was an uncalibrated cut-off written by feel, on the
    noisiest path in the product. Phase 6 asserts "no derived threshold is
    hard-coded anywhere in the router" and enforces it with an AST test; the
    principle is right and its scope was wrong. Every token is returned with its
    confidence, and route/router.py decides — which means, until a field curve
    exists, every OCR'd field routes to review. That is the honest answer and it
    is what B11 asks for.
    """
    words: list[Word] = []
    failures: list[CannotExtract] = []

    for page_no, image in enumerate(_render_pages(pdf)):
        result, _elapsed = _engine()(image)
        if not result:
            failures.append(
                CannotExtract("no text recovered from the page image", page_no))
            continue
        for box, text, confidence in result:
            xs = [point[0] / RENDER_SCALE for point in box]
            ys = [point[1] / RENDER_SCALE for point in box]
            words.append(Word(text=text,
                              bbox=BBox(min(xs), min(ys), max(xs), max(ys)),
                              page=page_no, confidence=float(confidence)))

    if not words:
        return CannotExtract("no text recovered from any page", 0)
    words.sort(key=lambda w: (w.page, round(w.bbox.top, 1), w.bbox.x0))
    return OcrResult(words=words, failures=failures)
