import pytest

from ledger_sense.corpus.generate import generate, ground_truth
from ledger_sense.extract.tables import MissingColumnError, find_headers, reconstruct_rows
from ledger_sense.extract.textlayer import extract_words


def _rows(seed, layout="baseline"):
    _contract, app, pdf = generate(seed=seed, error="clean", layout=layout)
    words = extract_words(pdf)
    return app, reconstruct_rows(words, find_headers(words))


def test_headers_map_to_semantic_field_names():
    _c, _a, pdf = generate(seed=1, error="clean")
    headers = find_headers(extract_words(pdf))
    assert {"item_no", "scheduled_value", "previous", "this_period",
            "stored", "total_completed_and_stored", "retainage"} <= set(headers)


def test_row_count_matches_the_generated_line_count():
    app, rows = _rows(1)
    assert len(rows) == len(app.lines)


@pytest.mark.parametrize("seed", list(range(1, 21)))
@pytest.mark.parametrize("layout", ["baseline", "right_aligned", "alternate_headers",
                                    "wrapped_descriptions", "two_page"])
def test_every_cell_matches_ground_truth(seed, layout):
    """REVISED (F-14). Twenty seeds against ONE layout — our own — proved that the
    generator agrees with the generator, because extract/ imported its column
    definitions from corpus/. Five layouts, and extract/ now owns its schema."""
    _app, rows = _rows(seed, layout=layout)
    # Without this, a reconstruction bug that drops every row (rather than
    # misreading one) makes the loop below vacuously pass -- exactly the shape
    # of the page-banding bug this test exists to catch (F-16, `two_page`).
    assert len(rows) == len(_app.lines)
    truth = ground_truth(seed=seed, error="clean")
    for row in rows:
        item_no = row["item_no"].text
        for field in ("scheduled_value", "previous", "this_period", "stored",
                      "total_completed_and_stored"):
            assert row[field].text == truth[f"line[{item_no}].{field}"]


def test_extract_imports_nothing_from_corpus():
    """The structural guarantee behind F-14. A test suite where the generator and
    the extractor share a source of truth is a suite that cannot fail for the
    reason it exists to catch."""
    import ast
    import pathlib
    offenders = []
    for path in pathlib.Path("ledger_sense/extract").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and "corpus" in (node.module or ""):
                offenders.append(f"{path}:{node.lineno}")
    assert offenders == [], f"extract/ imports from corpus/: {offenders}"


def test_a_right_aligned_value_lands_in_its_own_column():
    """F-12. `_column_for` used the header's LEFT edge only, so a right-aligned
    money value could sit under the NEXT column's header — `Previous` read as
    `This Period` a wrong bill that foots. Our renderer left-aligns, which is
    why every original test passed."""
    _app, rows = _rows(1, layout="right_aligned")
    truth = ground_truth(seed=1, error="clean")
    for row in rows:
        assert row["previous"].text == truth[f"line[{row['item_no'].text}].previous"]


def test_overlapping_columns_raise_rather_than_guess():
    from ledger_sense.extract.tables import AmbiguousColumnError, HeaderSpan, _column_for
    from ledger_sense.extract.textlayer import BBox, Word
    headers = {"a": HeaderSpan(BBox(0, 0, 50, 10), 0, 100),
               "b": HeaderSpan(BBox(60, 0, 110, 10), 50, 160)}
    with pytest.raises(AmbiguousColumnError):
        _column_for(Word("1.00", BBox(70, 20, 90, 30), 0), headers)


def test_every_cell_carries_a_bbox_inside_its_column():
    _c, _a, pdf = generate(seed=1, error="clean")
    words = extract_words(pdf)
    headers = find_headers(words)
    for row in reconstruct_rows(words, headers):
        for field, cell in row.items():
            assert cell.bbox.x0 >= headers[field].bbox.x0 - 4.0


def test_a_missing_column_raises_rather_than_silently_dropping():
    """A dropped retainage column is a wrong bill, not a missing field."""
    _c, _a, pdf = generate(seed=1, error="clean")
    words = [w for w in extract_words(pdf) if w.text != "Retainage"]
    with pytest.raises(MissingColumnError, match="retainage"):
        reconstruct_rows(words, find_headers(words))
