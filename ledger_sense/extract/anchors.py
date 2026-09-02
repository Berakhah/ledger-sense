"""Summary-certificate extraction by label anchor plus geometry.

Find the label, take the nearest value-shaped token to its right on the same
band, or directly below if the row is empty to the right.

If the label is absent, the field is ABSENT. Never take the nearest number and
hope — a guessed contract sum is a wrong bill with a confident number on it.
"""

import re
from typing import NamedTuple

from ledger_sense.extract.schema import SUMMARY_LABELS
from ledger_sense.extract.textlayer import BBox, Word

MONEY_TOKEN = re.compile(r"\(?\$?-?\d{1,3}(,\d{3})*\.\d{2}\)?|\(?\$?-?\d+\.\d{2}\)?")
SAME_BAND_PT = 6.0
BELOW_BAND_PT = 20.0


class Anchored(NamedTuple):
    field: str
    text: str
    bbox: BBox
    page: int


class AmbiguousLabelError(Exception):
    """The label appears more than once. Taking the first is how F-09 happened."""


def _find_labels(words: list[Word], label: str) -> list[list[Word]]:
    """Return EVERY match, not the first — REVISED (F-09).

    VERIFIED: the original returned the first match in reading order. The
    continuation grid header row sits above the summary block and shares the
    strings 'Retainage' and 'Total Completed and Stored' with it, so the header
    won, `_value_near` took the adjacent data cell, and
    summary['total_completed_and_stored'] held LINE ITEM 010's total — with a
    FileSource bbox pointing at the wrong cell. A1 would then pass on a document
    that does not foot.

    This is not a corpus artifact. Real G702-format documents carry the same
    words in both places for the same reason.
    """
    tokens = label.split()
    matches: list[list[Word]] = []
    for i in range(len(words) - len(tokens) + 1):
        window = words[i:i + len(tokens)]
        if [w.text.lower() for w in window] != [t.lower() for t in tokens]:
            continue
        spread = max(w.bbox.top for w in window) - min(w.bbox.top for w in window)
        if spread > SAME_BAND_PT:
            continue
        matches.append(window)
    return matches


def _value_near(words: list[Word], label_words: list[Word]) -> Word | None:
    label_right = max(w.bbox.x1 for w in label_words)
    label_top = min(w.bbox.top for w in label_words)
    page = label_words[0].page

    same_band = [w for w in words
                 if w.page == page and w.bbox.x0 >= label_right
                 and abs(w.bbox.top - label_top) <= SAME_BAND_PT
                 and MONEY_TOKEN.fullmatch(w.text)]
    if same_band:
        return min(same_band, key=lambda w: w.bbox.x0)

    below = [w for w in words
             if w.page == page and 0 < w.bbox.top - label_top <= BELOW_BAND_PT
             and MONEY_TOKEN.fullmatch(w.text)]
    if below:
        return min(below, key=lambda w: (w.bbox.top, w.bbox.x0))
    return None


def extract_summary(
    words: list[Word], grid_bottom: float | None = None
) -> tuple[dict[str, Anchored], list[str]]:
    """Returns (found, ambiguous). REVISED (F-09).

    Two defences, because the first is a heuristic and the second is not:

    1. `grid_bottom` — when the continuation grid was reconstructed, search only
       BELOW its last data row. The summary block is never inside the grid.
    2. An ambiguous label is REFUSED, not resolved. It is recorded and routed to
       review. Picking one of two identical labels is guessing with a confident
       bounding box, which is the failure mode this whole product exists to fix.
    """
    candidates = ([w for w in words if grid_bottom is None or w.bbox.top > grid_bottom]
                  or words)
    found: dict[str, Anchored] = {}
    ambiguous: list[str] = []

    for label, field in SUMMARY_LABELS:
        matches = _find_labels(candidates, label)
        if not matches:
            continue
        if len(matches) > 1:
            ambiguous.append(field)
            continue
        value = _value_near(candidates, matches[0])
        if value is None:
            continue
        found[field] = Anchored(field=field, text=value.text,
                                bbox=value.bbox, page=value.page)
    return found, ambiguous
