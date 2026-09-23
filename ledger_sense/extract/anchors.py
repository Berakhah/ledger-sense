"""Summary-certificate extraction by label anchor plus geometry.

Find the label, take the nearest value-shaped token to its right on the same
band, or directly below if the row is empty to the right.

If the label is absent, the field is ABSENT. Never take the nearest number and
hope — a guessed contract sum is a wrong bill with a confident number on it.
"""

import re
from collections.abc import Callable
from typing import NamedTuple

from ledger_sense.extract.schema import SUMMARY_LABELS
from ledger_sense.extract.textlayer import BBox, Word

MONEY_TOKEN = re.compile(r"\(?\$?-?\d{1,3}(,\d{3})*\.\d{2}\)?|\(?\$?-?\d+\.\d{2}\)?")
SAME_BAND_PT = 6.0
# Tightened (Important-1, Task 17 review): 20.0 was wider than the corpus's own
# summary row spacing (16.0pt), so the below-band search could always reach the
# NEXT summary row. 12.0 comfortably covers a genuine below-label value (one
# body-font line height below the label, ~9-10pt) while never reaching a row
# one full spacing below.
BELOW_BAND_PT = 12.0
# The below-band value must sit roughly under the LABEL'S OWN text, not
# anywhere else on the page -- see the Important-1 fix in `_value_near`.
BELOW_X_SLACK_PT = 20.0
# I3 (final whole-branch review): `_value_after_label_merged`'s same-band
# collection originally had no horizontal bound between consecutive words, so
# it would happily merge a genuine second label/value pair sharing the band
# into one corrupted "value". The corpus's own inter-word gap within a single
# value (e.g. a two-word vendor name at BODY_FONT_PT=9.0) measures ~2.5pt --
# the width of one space glyph. 15.0pt gives ~6x that headroom (comfortable
# for a wider font or looser kerning) while staying far short of any genuine
# second column: the document header's own label-to-value offset alone is
# ~100pt+. A gap past this bound ends the merge rather than crossing it.
MAX_MERGE_GAP_PT = 15.0


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


# Public alias (Ruling 5, Task 18): `assemble.extract_document_header` needs
# the same label-anchor phrase matching for the document header block, but the
# VALUE shapes there (an integer, a date, a bare id string) aren't
# money-shaped, so it brings its own value matcher rather than reusing
# `_value_near`. Re-exporting `_find_labels` under a public name avoids a
# second, drifting copy of this matching logic.
find_labels = _find_labels


def _value_after_label(words: list[Word], label_words: list[Word],
                        predicate: Callable[[Word], bool]) -> Word | None:
    """Nearest value-shaped token to the label's right on the same band, else
    directly below — generalised over a value-shape PREDICATE instead of a
    fixed money regex, so `assemble.py`'s document-header extraction
    (`application_no` an integer, `period_to` a date, `contract_id` a bare id
    string — none of those match `MONEY_TOKEN`) can share this geometry
    instead of hand-copying it. See `_value_near` immediately below for the
    money-specific caller.

    Was two near-identical copies (Task 18's deliberate-at-the-time scope
    decision) until the "should fix" upgrade in the Phase-2 final review:
    Task 17's horizontal-proximity fix below already had to be hand-copied
    into both once — proven drift risk, not hypothetical."""
    label_left = min(w.bbox.x0 for w in label_words)
    label_right = max(w.bbox.x1 for w in label_words)
    label_top = min(w.bbox.top for w in label_words)
    page = label_words[0].page

    same_band = [w for w in words
                 if w.page == page and w.bbox.x0 >= label_right
                 and abs(w.bbox.top - label_top) <= SAME_BAND_PT
                 and predicate(w)]
    if same_band:
        return min(same_band, key=lambda w: w.bbox.x0)

    # F-XX (Important-1, Task 17 review), VERIFIED with a live repro: this
    # search originally had NO horizontal constraint, so it would grab any
    # value-shaped token within BELOW_BAND_PT of the label ANYWHERE on the
    # page. Every summary row shares one value column, so whenever a label's
    # own same-band value was missing, this silently returned the NEXT row's
    # value instead -- a wrong bill with a confident bounding box, the exact
    # failure this module's docstring exists to prevent. A genuine
    # directly-below value sits roughly under the label's OWN text, not at
    # some other row's value column, so require horizontal proximity too.
    below = [w for w in words
             if w.page == page and 0 < w.bbox.top - label_top <= BELOW_BAND_PT
             and label_left - BELOW_X_SLACK_PT <= (w.bbox.x0 + w.bbox.x1) / 2
             <= label_right + BELOW_X_SLACK_PT
             and predicate(w)]
    if below:
        return min(below, key=lambda w: (w.bbox.top, w.bbox.x0))
    return None


# Public alias, same convention as `find_labels` above: `assemble.py` needs
# this exact geometry for non-money value shapes and re-exports it under a
# public name rather than keeping a second, drifting copy.
value_after_label = _value_after_label


def _value_after_label_merged(words: list[Word], label_words: list[Word],
                              predicate: Callable[[Word], bool]) -> Word | None:
    """Like `_value_after_label`, but merges every same-band value-shaped word
    to the label's right into one Word, sorted left to right — a multi-word
    value (e.g. a vendor name spanning two tokens) is not silently truncated
    to its first token. Falls back to `_value_after_label`'s existing
    below-band single-word search unchanged when no same-band words match —
    same merge convention `assemble._value_in_box` already uses for a
    reviewer-correction replay box.

    I3 (final whole-branch review): the merge STOPS at the first gap wider
    than `MAX_MERGE_GAP_PT`, same horizontal-proximity principle as the
    Important-1 fix above (`_value_after_label`'s below-band search) — a
    genuine second label/value pair sharing this band must not be swept into
    one corrupted value just because both are value-shaped and to the right."""
    label_right = max(w.bbox.x1 for w in label_words)
    label_top = min(w.bbox.top for w in label_words)
    page = label_words[0].page

    same_band = sorted(
        (w for w in words
         if w.page == page and w.bbox.x0 >= label_right
         and abs(w.bbox.top - label_top) <= SAME_BAND_PT
         and predicate(w)),
        key=lambda w: w.bbox.x0)
    if same_band:
        merged = [same_band[0]]
        for w in same_band[1:]:
            if w.bbox.x0 - merged[-1].bbox.x1 > MAX_MERGE_GAP_PT:
                break
            merged.append(w)
        merged_bbox = BBox(merged[0].bbox.x0, min(w.bbox.top for w in merged),
                           merged[-1].bbox.x1, max(w.bbox.bottom for w in merged))
        return Word(text=" ".join(w.text for w in merged), bbox=merged_bbox, page=page)
    return _value_after_label(words, label_words, predicate)


# Public alias, same convention as `find_labels`/`value_after_label` above.
value_after_label_merged = _value_after_label_merged


def _value_near(words: list[Word], label_words: list[Word]) -> Word | None:
    return _value_after_label(
        words, label_words, lambda w: bool(MONEY_TOKEN.fullmatch(w.text)))


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

    `found` and `ambiguous` are NOT mutually exclusive per field (Important-2,
    Task 17 review) — deliberately. `SUMMARY_LABELS` can carry more than one
    entry for the same field (a prefixed one and a bare, real-document one);
    one entry can resolve cleanly while a DIFFERENT entry for that same field
    finds a second occurrence elsewhere on the page and calls it ambiguous.
    When that happens the field lands in both. `found[field]` is still the
    value the CLEAN entry resolved (never a guess), but a caller must check
    `ambiguous` regardless of whether the field is already in `found` — its
    presence in `found` is not, on its own, proof that nothing else on the
    page could have meant something different.
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
