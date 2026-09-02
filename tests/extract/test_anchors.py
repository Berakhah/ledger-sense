from ledger_sense.corpus.generate import generate
from ledger_sense.extract.anchors import MONEY_TOKEN, extract_summary
from ledger_sense.extract.tables import find_headers, reconstruct_rows
from ledger_sense.extract.textlayer import extract_words


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
    """F-09, VERIFIED. 'Retainage' and 'Total Completed and Stored' appear as BOTH
    a grid header and a summary label. Taking the first match returned line item
    010's cell as the summary figure, with a bbox pointing at the wrong cell —
    and A1 then passes on a document that does not foot."""
    _c, _a, pdf = generate(seed=1, error="clean")
    words = extract_words(pdf)
    duplicated = words + [w._replace(bbox=w.bbox._replace(top=w.bbox.top + 400))
                          for w in words if w.text == "Retainage"]
    _found, ambiguous = extract_summary(duplicated)
    assert "retainage_total" in ambiguous


def test_the_summary_is_searched_below_the_grid_when_the_extent_is_known():
    _c, app, pdf = generate(seed=1, error="clean")
    words = extract_words(pdf)
    headers = find_headers(words)
    grid_bottom = max(c.bbox.bottom for row in reconstruct_rows(words, headers)
                      for c in row.values())
    found, ambiguous = extract_summary(words, grid_bottom=grid_bottom)
    assert ambiguous == []
    cleaned = found["total_completed_and_stored"].text.replace(",", "").lstrip("$")
    assert cleaned == str(app.total_completed_and_stored)
