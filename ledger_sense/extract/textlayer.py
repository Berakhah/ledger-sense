"""Word-level extraction with coordinates.

Thin wrappers over `ledger_sense.extract.document.parse` (F-21): a single
document is opened exactly once by `parse()`. Callers that need more than one
of words / has_text_layer / metadata / page_count should call `parse(pdf)`
directly rather than combining these wrappers, so the document is still only
opened once.

`BBox` and `Word` are defined in `document.py` now (the shared parse owns the
primitives) and re-exported here so existing imports of
`ledger_sense.extract.textlayer.{BBox,Word}` keep working unchanged.
"""

from ledger_sense.extract.document import BBox, Word, parse

__all__ = ["BBox", "Word", "extract_words", "has_text_layer"]


def extract_words(pdf: bytes) -> list[Word]:
    return list(parse(pdf).words)


def has_text_layer(pdf: bytes) -> bool:
    return parse(pdf).has_text_layer
