"""Word-level extraction with coordinates.

COORDINATE CONTRACT: everything downstream of this module uses a TOP-LEFT
origin with `top` increasing downward. pdfplumber already reports this way, so
the conversion is a no-op today — the contract is stated so that any future
backend swap converts HERE and nowhere else.
"""

import io
from typing import NamedTuple

import pdfplumber


class BBox(NamedTuple):
    x0: float
    top: float
    x1: float
    bottom: float


class Word(NamedTuple):
    text: str
    bbox: BBox
    page: int


def extract_words(pdf: bytes) -> list[Word]:
    words: list[Word] = []
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        for page_no, page in enumerate(doc.pages):
            # use_text_flow=True: False merges adjacent header words (e.g., "Stored"+"Retainage" → "StoredRetainage"), breaking per-header positioning
            for raw in page.extract_words(use_text_flow=True, keep_blank_chars=False):
                words.append(Word(
                    text=raw["text"],
                    bbox=BBox(float(raw["x0"]), float(raw["top"]),
                              float(raw["x1"]), float(raw["bottom"])),
                    page=page_no))
    words.sort(key=lambda w: (w.page, round(w.bbox.top, 1), w.bbox.x0))
    return words


def has_text_layer(pdf: bytes) -> bool:
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        return any((page.extract_text() or "").strip() for page in doc.pages)
