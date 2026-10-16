"""Continuation-sheet grid reconstruction.

The continuation sheet IS a table, so recover it as one: find the header row,
band the remaining words into rows by vertical proximity, assign each word to
the column whose header it sits under.

A missing column RAISES. Silently dropping the retainage column produces a
plausible bill that is wrong, which is worse than a loud failure.
"""

import re
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


class PartialRow(NamedTuple):
    """A band that looks like an item row but lacks one or more required cells
    (LS-07). Reported, never silently dropped: a missing cell is evidence, and a
    schedule with a row quietly removed must not read as complete."""
    item_no: str
    cells: dict[str, Cell]
    missing: tuple[str, ...]


# A probable item row: an all-digit item number AND at least two money-shaped
# cells. Summary lines, headers, page numbers and notes have neither, so they stay
# silently skipped; a real row with one unreadable cell has five or more.
_ITEM_NO_TOKEN = re.compile(r"\d+")
_MONEY_SHAPED = re.compile(r"\(?-?\$?\d[\d,]*(\.\d+)?\)?")
_MONEY_COLUMNS = frozenset({"scheduled_value", "previous", "this_period", "stored",
                            "total_completed_and_stored", "retainage"})
_MIN_MONEY_CELLS_FOR_AN_ITEM_ROW = 2


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


def _candidates(word: Word, headers: HeaderMap) -> list[str]:
    """Every field whose span the word's CENTRE falls inside, without raising.

    `_column_for` wraps this to enforce "ambiguous is an error"; `reconstruct_rows`
    calls this directly because it needs to know WHICH fields were candidates
    before deciding whether an ambiguity is fatal (F-17) -- a plain bool or a
    raised exception would already have thrown away the information it needs.
    """
    centre = (word.bbox.x0 + word.bbox.x1) / 2
    return [field for field, span in headers.items()
            if span.x_left - COLUMN_SLACK_PT <= centre <= span.x_right + COLUMN_SLACK_PT]


def _column_for(word: Word, headers: HeaderMap) -> str | None:
    """Assign by the word's CENTRE against the column's SPAN, not by its left
    edge against the header's left edge (F-12).

    Two columns matching is an error, not a coin flip: a misassigned money value
    is a bill that still foots and is wrong.
    """
    hits = _candidates(word, headers)
    if len(hits) > 1:
        centre = (word.bbox.x0 + word.bbox.x1) / 2
        raise AmbiguousColumnError(
            f"{word.text!r} at x={centre:.1f} falls inside columns {sorted(hits)}; "
            f"the column spans overlap and a guess here is a wrong bill")
    return hits[0] if hits else None


def reconstruct_rows(words: list[Word], headers: HeaderMap) -> list[dict[str, Cell]]:
    return _scan(words, headers)[0]


def partial_rows(words: list[Word], headers: HeaderMap) -> list[PartialRow]:
    """Item rows that `reconstruct_rows` could not return because a required
    cell is missing or unreadable (LS-07)."""
    return _scan(words, headers)[1]


def _partial_of(grouped: dict[str, list[Word]]) -> PartialRow | None:
    cells: dict[str, Cell] = {}
    for field, group in grouped.items():
        text, bbox = _merge(group)
        cells[field] = Cell(field=field, text=text, bbox=bbox, page=group[0].page)
    item = cells.get("item_no")
    if item is None or _ITEM_NO_TOKEN.fullmatch(item.text.strip()) is None:
        return None
    money_cells = sum(1 for field, cell in cells.items()
                      if field in _MONEY_COLUMNS
                      and _MONEY_SHAPED.fullmatch(cell.text.strip()) is not None)
    if money_cells < _MIN_MONEY_CELLS_FOR_AN_ITEM_ROW:
        return None
    return PartialRow(item_no=item.text.strip(), cells=cells,
                      missing=tuple(sorted(REQUIRED_FIELDS - set(cells))))


def _scan(words: list[Word], headers: HeaderMap) -> tuple[list[dict[str, Cell]], list[PartialRow]]:
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
    partials: list[PartialRow] = []
    for band_top in sorted(bands):
        grouped: dict[str, list[Word]] = {}
        # F-17 (revising the earlier, unsound version of this same guard). Two
        # ordinary column spans can overlap by COLUMN_SLACK_PT at their shared
        # boundary -- e.g. `previous` ends and `this_period` begins at the
        # same x -- and an everyday summary-label word ("Total", "Current",
        # "Original"...) lands there on every clean document, every layout,
        # at the six summary rows. That's not evidence the band is a real
        # data row.
        #
        # An earlier version excluded an ambiguous word from `grouped` and
        # only raised if `REQUIRED_FIELDS <= set(grouped)` was ALREADY true --
        # but an ambiguous word is BY DEFINITION never in `grouped`, so a
        # genuine row missing exactly one field to the ambiguity could never
        # satisfy that check, and the row vanished silently via `continue`
        # below before the raise was ever reached. Exactly the "coin flip"
        # class of bug this module exists to refuse.
        #
        # So: track which field(s) each ambiguous word COULD belong to, and
        # count those toward completeness OPTIMISTICALLY before deciding.
        # Best-case-still-incomplete means real noise -- skip silently, same
        # as any other non-data band. Best-case-complete means a genuine row
        # was corrupted by an unresolvable boundary call -- raise.
        ambiguous_words: list[Word] = []
        possible_fields: set[str] = set()
        for word in bands[band_top]:
            hits = _candidates(word, headers)
            if len(hits) > 1:
                ambiguous_words.append(word)
                possible_fields.update(hits)
            elif hits:
                grouped.setdefault(hits[0], []).append(word)
        if not REQUIRED_FIELDS <= (set(grouped) | possible_fields):
            # Not a complete row even best-case. Footer, page number and
            # continuation note stay skipped; a probable ITEM row is reported
            # (LS-07) instead of vanishing.
            partial = _partial_of(grouped)
            if partial is not None:
                partials.append(partial)
            continue
        if ambiguous_words:
            _column_for(ambiguous_words[0], headers)  # re-raises with the formatted message
        row: dict[str, Cell] = {}
        for field, group in grouped.items():
            text, bbox = _merge(group)
            row[field] = Cell(field=field, text=text, bbox=bbox, page=group[0].page)
        rows.append(row)
    return rows, partials
