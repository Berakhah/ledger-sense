import pytest

from ledger_sense.corpus.generate import generate, ground_truth
from ledger_sense.domain.models import PayApplication
from ledger_sense.extract.tables import (
    Cell,
    HeaderMap,
    MissingColumnError,
    find_headers,
    reconstruct_rows,
)
from ledger_sense.extract.textlayer import extract_words


def _rows(seed: int, layout: str = "baseline") -> tuple[PayApplication, list[dict[str, Cell]]]:
    _contract, app, pdf = generate(seed=seed, error="clean", layout=layout)
    words = extract_words(pdf)
    return app, reconstruct_rows(words, find_headers(words))


def test_headers_map_to_semantic_field_names() -> None:
    _c, _a, pdf = generate(seed=1, error="clean")
    headers = find_headers(extract_words(pdf))
    assert {"item_no", "scheduled_value", "previous", "this_period",
            "stored", "total_completed_and_stored", "retainage"} <= set(headers)


def test_row_count_matches_the_generated_line_count() -> None:
    app, rows = _rows(1)
    assert len(rows) == len(app.lines)


@pytest.mark.parametrize("seed", list(range(1, 21)))
@pytest.mark.parametrize("layout", ["baseline", "right_aligned", "alternate_headers",
                                    "wrapped_descriptions", "two_page"])
def test_every_cell_matches_ground_truth(seed: int, layout: str) -> None:
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


def test_extract_imports_nothing_from_corpus() -> None:
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


def test_a_right_aligned_value_lands_in_its_own_column() -> None:
    """F-12. `_column_for` used the header's LEFT edge only, so a right-aligned
    money value could sit under the NEXT column's header — `Previous` read as
    `This Period` a wrong bill that foots. Our renderer left-aligns, which is
    why every original test passed."""
    _app, rows = _rows(1, layout="right_aligned")
    truth = ground_truth(seed=1, error="clean")
    for row in rows:
        assert row["previous"].text == truth[f"line[{row['item_no'].text}].previous"]


def test_overlapping_columns_raise_rather_than_guess() -> None:
    from ledger_sense.extract.tables import AmbiguousColumnError, HeaderSpan, _column_for
    from ledger_sense.extract.textlayer import BBox, Word
    headers = {"a": HeaderSpan(BBox(0, 0, 50, 10), 0, 100),
               "b": HeaderSpan(BBox(60, 0, 110, 10), 50, 160)}
    with pytest.raises(AmbiguousColumnError):
        _column_for(Word("1.00", BBox(70, 20, 90, 30), 0), headers)


def _hand_built_headers() -> HeaderMap:
    """Eight non-overlapping-except-at-shared-boundaries columns, ten points
    wide each, header bboxes irrelevant to the checks below (bottom=10 so any
    word with top>10 counts as body). Every REQUIRED_FIELDS name is covered.
    """
    from ledger_sense.extract.tables import HeaderSpan
    from ledger_sense.extract.textlayer import BBox
    fields = ["item_no", "description", "scheduled_value", "previous", "this_period",
              "stored", "total_completed_and_stored", "retainage"]
    return {field: HeaderSpan(BBox(i * 10, 0, i * 10 + 5, 10), float(i * 10), float(i * 10 + 10))
            for i, field in enumerate(fields)}


def test_ambiguous_word_in_an_otherwise_complete_row_raises() -> None:
    """F-17. A word landing in the COLUMN_SLACK_PT overlap at a shared column
    boundary (here: previous/this_period, at x=40) must not let the row it
    belongs to vanish silently just because the ambiguous word itself can't
    be filed anywhere. Demonstrated with a hand-built HeaderMap so it does
    not depend on any particular PDF rendering producing the ambiguity --
    reconstruct_rows's OWN collect-and-decide logic is what's under test,
    not _column_for in isolation (that's the test above)."""
    from ledger_sense.extract.tables import AmbiguousColumnError, reconstruct_rows
    from ledger_sense.extract.textlayer import BBox, Word
    headers = _hand_built_headers()
    words = [
        Word("010", BBox(1, 100, 3, 110), 0),        # item_no, unambiguous
        Word("Desc", BBox(14, 100, 16, 110), 0),      # description, unambiguous
        Word("100.00", BBox(24, 100, 26, 110), 0),    # scheduled_value, unambiguous
        Word("20.00", BBox(34, 100, 36, 110), 0),     # previous, unambiguous
        Word("5.00", BBox(39, 100, 41, 110), 0),      # centre=40: previous OR this_period
        Word("0.00", BBox(54, 100, 56, 110), 0),      # stored, unambiguous
        Word("120.00", BBox(64, 100, 66, 110), 0),    # total_completed_and_stored, unambiguous
        Word("0.00", BBox(76, 100, 78, 110), 0),      # retainage, unambiguous
    ]
    with pytest.raises(AmbiguousColumnError):
        reconstruct_rows(words, headers)


def test_ambiguous_word_in_a_genuinely_incomplete_band_is_silently_skipped() -> None:
    """The flip side of F-17: an ambiguous word does not, by itself, turn a
    genuinely incomplete band (footer, page number, summary block) into a
    raise. Same overlap as above, but nothing else in the band -- even
    crediting the ambiguous word to BOTH candidate fields still leaves six
    required fields missing, so this is noise, not a corrupted row."""
    from ledger_sense.extract.tables import reconstruct_rows
    from ledger_sense.extract.textlayer import BBox, Word
    headers = _hand_built_headers()
    words = [
        Word("20.00", BBox(34, 100, 36, 110), 0),  # previous, unambiguous
        Word("5.00", BBox(39, 100, 41, 110), 0),   # centre=40: previous OR this_period
    ]
    assert reconstruct_rows(words, headers) == []


def test_every_cell_carries_a_bbox_inside_its_column() -> None:
    _c, _a, pdf = generate(seed=1, error="clean")
    words = extract_words(pdf)
    headers = find_headers(words)
    for row in reconstruct_rows(words, headers):
        for field, cell in row.items():
            assert cell.bbox.x0 >= headers[field].bbox.x0 - 4.0


def test_a_missing_column_raises_rather_than_silently_dropping() -> None:
    """A dropped retainage column is a wrong bill, not a missing field."""
    _c, _a, pdf = generate(seed=1, error="clean")
    words = [w for w in extract_words(pdf) if w.text != "Retainage"]
    with pytest.raises(MissingColumnError, match="retainage"):
        reconstruct_rows(words, find_headers(words))
