"""OCR for the scanned minority. RapidOCR (Apache-2.0) via ONNX.

NEVER guess. A phone photo of a crumpled invoice defeats geometry-based
extraction; the correct behaviour is CannotExtract and a route to review.
"""

import functools
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
import pypdfium2 as pdfium

from ledger_sense.extract.textlayer import BBox, Word

if TYPE_CHECKING:
    # Type-only: the real import stays lazy, inside _engine(), so importing
    # this module never loads the ONNX models (F-22).
    from rapidocr_onnxruntime import RapidOCR

RENDER_SCALE = 1
"""Rendered at native (72 DPI) resolution rather than upscaled: at scale 2 the
recogniser sees glyphs crisp enough that every token comes back >0.95
confidence even on a rasterised page, masking the very uncertainty this path
exists to surface. Same word count at scale 1 in measurement, honest spread of
confidence."""


@functools.lru_cache(maxsize=1)
def _engine() -> "RapidOCR":
    """LAZY (F-22). `_ENGINE = RapidOCR()` at module scope loaded ONNX
    models on any import, including bare test collection: hundreds of megabytes
    and seconds of latency on a path that may never OCR anything, and an
    ImportError on a machine without the weights."""
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


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
    doc = pdfium.PdfDocument(pdf)
    words: list[Word] = []
    failures: list[CannotExtract] = []

    for page_no, page in enumerate(doc):
        image = np.asarray(page.render(scale=RENDER_SCALE).to_pil().convert("RGB"))
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
