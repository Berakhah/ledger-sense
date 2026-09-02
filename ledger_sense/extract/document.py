"""Single-parse pdfplumber pass, shared by everything downstream.

`pdfminer` layout analysis (which pdfplumber wraps) is the most expensive
step in the pipeline. Before this module existed, a single document could be
opened up to four separate times — once each for word extraction, producer
metadata, column fingerprinting (which itself called word extraction again)
and text-layer detection — with none of that work cached. `parse()` opens the
PDF exactly once; everything else downstream reads from the `ParsedDocument`
it returns.

COORDINATE CONTRACT: everything downstream of this module uses a TOP-LEFT
origin with `top` increasing downward. pdfplumber already reports this way,
so the conversion is a no-op today — the contract is stated so that any
future backend swap converts HERE and nowhere else.
"""

import io
from dataclasses import dataclass
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


@dataclass(frozen=True)
class ParsedDocument:
    words: tuple[Word, ...]
    metadata: dict[str, str]
    page_count: int
    has_text_layer: bool


def parse(pdf: bytes) -> ParsedDocument:
    words: list[Word] = []
    text_found = False
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        metadata = {str(key): str(value) for key, value in (doc.metadata or {}).items()}
        page_count = len(doc.pages)
        for page_no, page in enumerate(doc.pages):
            # use_text_flow=True: False merges adjacent header words (e.g., "Stored"+"Retainage" → "StoredRetainage"), breaking per-header positioning
            for raw in page.extract_words(use_text_flow=True, keep_blank_chars=False):
                words.append(Word(
                    text=raw["text"],
                    bbox=BBox(float(raw["x0"]), float(raw["top"]),
                              float(raw["x1"]), float(raw["bottom"])),
                    page=page_no))
            if not text_found:
                text_found = bool((page.extract_text() or "").strip())
    words.sort(key=lambda w: (w.page, round(w.bbox.top, 1), w.bbox.x0))
    return ParsedDocument(words=tuple(words), metadata=metadata,
                           page_count=page_count, has_text_layer=text_found)
