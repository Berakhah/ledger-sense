"""Continuation-sheet grid reconstruction.

The continuation sheet IS a table, so recover it as one: find the header row,
band the remaining words into rows by vertical proximity, assign each word to
the column whose header it sits under.

A missing column RAISES. Silently dropping the retainage column produces a
plausible bill that is wrong, which is worse than a loud failure.
"""

from typing import NamedTuple

# EXTRACTION OWNS ITS OWN SCHEMA — REVISED (F-14).
#
# This module originally did `from ledger_sense.corpus.layouts import
# CONTINUATION_COLUMNS`, so the extractor and the generator shared one source of
# truth. The twenty-seed test then proved that the generator agrees with the
# generator — which is not evidence about layout robustness, and spec §4.3's
# whole argument is that real documents vary enormously in geometry while being
# semantically identical.
#
# extract/ imports NOTHING from corpus/. A test asserts it.
from ledger_sense.extract.schema import COLUMN_SYNONYMS, REQUIRED_FIELDS
from ledger_sense.extract.textlayer import BBox, Word

HeaderMap = dict[str, "HeaderSpan"]
ROW_BAND_PT = 6.0
COLUMN_SLACK_PT = 4.0


class HeaderSpan(NamedTuple):
    """A header's box AND the horizontal span of the column beneath it.

    F-12: `_column_for` originally used only the header's LEFT edge, assigning a
    word to the rightmost header whose x0 it exceeded. Our renderer left-aligns
    every cell, so the tests passed. Real pay applications RIGHT-align money, and
    any layout whose columns are narrower than ours puts a right-aligned value
    under the next column's header — `Previous` read as `This Period` is a wrong
    bill that foots.
    """
    bbox: BBox
    x_left: float
    x_right: float


class AmbiguousColumnError(Exception):
    """A word fell inside two column spans. Guessing here is a wrong bill."""


class MissingColumnError(Exception):
    pass


class Cell(NamedTuple):
    field: str
    text: str
    bbox: BBox
    page: int


def _merge(words: list[Word]) -> tuple[str, BBox]:
    words = sorted(words, key=lambda w: w.bbox.x0)
    return (
        " ".join(w.text for w in words),
        BBox(words[0].bbox.x0, min(w.bbox.top for w in words),
             words[-1].bbox.x1, max(w.bbox.bottom for w in words)),
    )


def _band(words: list[Word]) -> dict[tuple[int, float], list[Word]]:
    """Band by (page, rounded top), not top alone.

    A layout that repeats the header on every page (`two_page`) puts page 2's
    header row, and every page's first data row, at the SAME `top` as page 1's
    -- banding by `top` only would merge them across pages. That merge is
    doubly wrong: it defeats the header phrase-matcher (each header word
    appears twice, adjacent, in the merged band, and a 3+ word phrase like
    "Total Completed and Stored" can never match a doubled sequence -- see
    `find_headers`), and it would splice two different pages' line items into
    one row.
    """
    bands: dict[tuple[int, float], list[Word]] = {}
    for word in words:
        key = (word.page, round(word.bbox.top / ROW_BAND_PT) * ROW_BAND_PT)
        bands.setdefault(key, []).append(word)
    return bands


def find_headers(words: list[Word]) -> HeaderMap:
    """Match each field's SYNONYM SET against consecutive words on one band, then
    derive each column's span from adjacent headers' LEFT edges.

    Synonyms come from extract/schema.py, not from corpus/layouts.py (F-14). Real
    documents say 'Sched. Value', 'Work in Place', 'Materials Presently Stored';
    matching only our own generator's wording is why the twenty-seed test proved
    nothing.
    """
    found: dict[str, BBox] = {}
    bands = _band(words)
    for field, phrases in COLUMN_SYNONYMS.items():
        for phrase in phrases:
            tokens = phrase.split()
            for band in bands.values():
                ordered = sorted(band, key=lambda w: w.bbox.x0)
                for i in range(len(ordered) - len(tokens) + 1):
                    window = ordered[i:i + len(tokens)]
                    if [w.text.lower() for w in window] == [t.lower() for t in tokens]:
                        _text, bbox = _merge(window)
                        found[field] = bbox
                        break
                if field in found:
                    break
            if field in found:
                break

    # Span = THIS header's own left edge to the NEXT header's left edge (F-16,
    # revising F-12). F-12's original span used the MIDPOINT between adjacent
    # header BBOXES, which assumes a header's text width is a fair proxy for
    # its column's width. That is false whenever a header is much narrower
    # than its column -- 'Item' (four letters) inside a 40pt numeric column --
    # and it is false for EVERY column once its DATA is right-aligned to the
    # column's far edge while its header stays left-anchored at the column's
    # near edge. `generate._render` always draws headers with `_insert_text`
    # at `column.x_pt`, left-aligned, REGARDLESS of layout -- only the DATA
    # moves for `right_aligned`. So a header's own left edge reliably marks
    # its column's left edge on every layout, and the NEXT header's left edge
    # reliably marks where that column ends, because columns are laid out
    # contiguously with no gap worth accounting for. (Verified empirically:
    # with this scheme every one of our own layout's true column boundaries
    # falls inside the derived span, comfortably clear of COLUMN_SLACK_PT.)
    #
    # The x1-midpoint scheme silently produced ZERO rows for every seed of the
    # `right_aligned` layout -- not an occasional miscall, total failure, and
    # `test_a_right_aligned_value_lands_in_its_own_column` never caught it
    # because `for row in rows: ...` is vacuously true over an empty list.
    ordered_fields = sorted(found, key=lambda f: found[f].x0)
    headers: HeaderMap = {}
    for index, field in enumerate(ordered_fields):
        bbox = found[field]
        left = 0.0 if index == 0 else bbox.x0
        right = (bbox.x1 + COLUMN_SLACK_PT * 4 if index == len(ordered_fields) - 1
                 else found[ordered_fields[index + 1]].x0)
        headers[field] = HeaderSpan(bbox=bbox, x_left=left, x_right=right)
    return headers


def _column_for(word: Word, headers: HeaderMap) -> str | None:
    """Assign by the word's CENTRE against the column's SPAN, not by its left
    edge against the header's left edge (F-12).

    Two columns matching is an error, not a coin flip: a misassigned money value
    is a bill that still foots and is wrong.
    """
    centre = (word.bbox.x0 + word.bbox.x1) / 2
    hits = [field for field, span in headers.items()
            if span.x_left - COLUMN_SLACK_PT <= centre <= span.x_right + COLUMN_SLACK_PT]
    if len(hits) > 1:
        raise AmbiguousColumnError(
            f"{word.text!r} at x={centre:.1f} falls inside columns {sorted(hits)}; "
            f"the column spans overlap and a guess here is a wrong bill")
    return hits[0] if hits else None


def reconstruct_rows(words: list[Word], headers: HeaderMap) -> list[dict[str, Cell]]:
    missing = REQUIRED_FIELDS - set(headers)
    if missing:
        raise MissingColumnError(
            f"continuation sheet is missing columns: {sorted(missing)}")

    header_bottom = max(span.bbox.bottom for span in headers.values())
    body = [w for w in words if w.bbox.top > header_bottom]

    # F-13: `_band(body)` was called twice inside the loop, so a 200-line sheet
    # (~1,800 words) performed ~720,000 dict operations instead of 1,800.
    bands = _band(body)

    rows: list[dict[str, Cell]] = []
    for band_top in sorted(bands):
        grouped: dict[str, list[Word]] = {}
        # A band below the grid (summary block, footer) can hold a stray word
        # that shares an x-position with a column boundary -- e.g. a font
        # falling back to a replacement glyph for a character the base-14 font
        # cannot render -- without that band being a data row at all. Whether
        # the ambiguity matters depends on whether this band turns out to be a
        # genuine row (checked below via REQUIRED_FIELDS); collect it first
        # rather than letting an off-grid word abort a document that has no
        # real ambiguity in its line items.
        ambiguous: AmbiguousColumnError | None = None
        for word in bands[band_top]:
            try:
                field = _column_for(word, headers)
            except AmbiguousColumnError as exc:
                ambiguous = ambiguous or exc
                continue
            if field is not None:
                grouped.setdefault(field, []).append(word)
        if not REQUIRED_FIELDS <= set(grouped):
            continue  # not a data row: footer, page number, continuation note
        if ambiguous is not None:
            raise ambiguous
        row: dict[str, Cell] = {}
        for field, group in grouped.items():
            text, bbox = _merge(group)
            row[field] = Cell(field=field, text=text, bbox=bbox, page=group[0].page)
        rows.append(row)
    return rows
