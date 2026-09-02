from ledger_sense.corpus.generate import generate
from ledger_sense.extract.anchors import MONEY_TOKEN, _find_labels, _value_near, extract_summary
from ledger_sense.extract.tables import find_headers, reconstruct_rows
from ledger_sense.extract.textlayer import BBox, Word, extract_words


def _grid_bottom(words):
    headers = find_headers(words)
    return max(c.bbox.bottom for row in reconstruct_rows(words, headers)
               for c in row.values())


def _summary(seed=1):
    _contract, app, pdf = generate(seed=seed, error="clean")
    found, _ambiguous = extract_summary(extract_words(pdf))
    return app, found


def test_money_token_accepts_plain_and_formatted_amounts():
    for good in ["1234.56", "1,234.56", "$1,234.56", "0.00", "(1,234.56)"]:
        assert MONEY_TOKEN.fullmatch(good), good


def test_money_token_rejects_prose_and_bare_integers():
    for bad in ["Sitework", "Item", "003", "N/A"]:
        assert not MONEY_TOKEN.fullmatch(bad), bad


def test_summary_total_matches_the_generated_application():
    app, summary = _summary()
    cleaned = summary["total_completed_and_stored"].text.replace(",", "").lstrip("$")
    assert cleaned == str(app.total_completed_and_stored)


def test_each_anchored_value_carries_a_non_degenerate_bbox():
    _app, summary = _summary()
    for anchored in summary.values():
        assert anchored.bbox.x1 > anchored.bbox.x0
        assert anchored.bbox.bottom > anchored.bbox.top


def test_an_absent_label_is_omitted_rather_than_guessed():
    """No label, no value. Never take the nearest number and hope."""
    _c, _a, pdf = generate(seed=1, error="clean")
    words = [w for w in extract_words(pdf) if w.text != "Retainage"]
    found, _ambiguous = extract_summary(words)
    assert "retainage_total" not in found


def test_a_label_appearing_twice_is_refused_rather_than_resolved():
    """Targets `total_completed_and_stored`'s bare entry, not `retainage_total`'s
    (contra the brief's own version of this test) — VERIFIED (task-17-report.md).
    A bare 'Retainage' search is unconditionally ambiguous against the summary
    block's OWN 'Total Earned Less Retainage' row, with or without a second
    label anywhere, so it can never carry this test's intent (a label refused
    ONLY because it genuinely appears twice). 'Total Completed and Stored' has
    no such cross-field collision, so it is the field that actually isolates
    the guarantee this test exists to prove: an identical label appearing
    twice is refused, not silently resolved onto whichever copy comes first.
    """
    _c, _a, pdf = generate(seed=1, error="clean")
    words = extract_words(pdf)
    grid_bottom = _grid_bottom(words)
    candidates = [w for w in words if w.bbox.top > grid_bottom]
    [genuine_match] = _find_labels(candidates, "Total Completed and Stored")
    # Duplicate the matched label's own words AS A UNIT, still mutually
    # adjacent, so the duplicate is a second COMPLETE match — not an isolated
    # fragment `_find_labels` could never complete on its own.
    duplicate = [w._replace(bbox=w.bbox._replace(top=w.bbox.top + 400))
                for w in genuine_match]
    _found, ambiguous = extract_summary(words + duplicate, grid_bottom=grid_bottom)
    assert "total_completed_and_stored" in ambiguous


def test_the_summary_is_searched_below_the_grid_when_the_extent_is_known():
    _c, app, pdf = generate(seed=1, error="clean")
    words = extract_words(pdf)
    found, ambiguous = extract_summary(words, grid_bottom=_grid_bottom(words))
    assert ambiguous == []
    cleaned = found["total_completed_and_stored"].text.replace(",", "").lstrip("$")
    assert cleaned == str(app.total_completed_and_stored)


def test_a_field_can_be_found_and_flagged_ambiguous_at_once():
    """Deliberate, documented on `extract_summary` (Important-2, Task 17 review).
    Without `grid_bottom`, the bare 'Total Completed and Stored' entry collides
    with the grid header (the original F-09 collision) and is flagged
    ambiguous, while the prefixed entry still resolves the field cleanly — so
    the field lands in BOTH `found` and `ambiguous`. A caller must check
    `ambiguous` even for a field already present in `found`."""
    _c, app, pdf = generate(seed=1, error="clean")
    found, ambiguous = extract_summary(extract_words(pdf))
    assert "total_completed_and_stored" in ambiguous
    cleaned = found["total_completed_and_stored"].text.replace(",", "").lstrip("$")
    assert cleaned == str(app.total_completed_and_stored)


def test_value_near_finds_a_directly_below_value_when_none_is_beside_it():
    """Important-1, Task 17 review. A hand-built row, no corpus needed (same
    style as `tables.py`'s hand-built `HeaderMap` tests): the value sits one
    line below the label, roughly under its own text — the below-band path's
    intended, genuine case."""
    label = [Word("Label", BBox(100.0, 100.0, 140.0, 110.0), 0)]
    value = Word("500.00", BBox(105.0, 108.0, 140.0, 118.0), 0)
    assert _value_near([*label, value], label) == value


def test_value_near_rejects_a_below_value_in_the_wrong_column():
    """Important-1, Task 17 review, VERIFIED with a live repro: the original
    below-band search had no horizontal constraint, so a money-shaped token
    below the label but in a completely different column (e.g. the NEXT row's
    own value, sharing a value column with this one) was picked anyway — a
    wrong bill with a confident bounding box. Vertically in range, horizontally
    nowhere near the label, must not be picked."""
    label = [Word("Label", BBox(100.0, 100.0, 140.0, 110.0), 0)]
    wrong_column = Word("999.00", BBox(400.0, 108.0, 440.0, 118.0), 0)
    assert _value_near([*label, wrong_column], label) is None
