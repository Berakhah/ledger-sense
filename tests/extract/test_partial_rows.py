"""LS-07: a row with one unreadable cell must be reported, never silently dropped.

`reconstruct_rows` skips any band that lacks a required column, on the theory
that it is a footer or page number. A real item row with one missing cell looks
the same, so it vanished: the schedule shrank by a row, `_unextractable` stayed
empty, and the only symptom was an incidental footing mismatch. A subset of rows
must never masquerade as the complete schedule."""

import pytest

from ledger_sense.corpus.generate import generate
from ledger_sense.corpus.layouts import LAYOUTS
from ledger_sense.extract import assemble as assemble_mod
from ledger_sense.extract.assemble import UnextractableField, assemble_pay_application
from ledger_sense.extract.tables import (
    HeaderMap,
    find_headers,
    partial_rows,
    reconstruct_rows,
)
from ledger_sense.extract.textlayer import Word, extract_words
from ledger_sense.rules.engine import unverifiable_money_fields


def _without_cell(words: list[Word], headers: HeaderMap, row_index: int,
                  field: str) -> tuple[list[Word], str]:
    """`words` minus the one cell at (data row `row_index`, column `field`),
    plus that row's item number."""
    item_span = headers["item_no"]
    bottom = max(span.bbox.bottom for span in headers.values())
    item_words = sorted(
        (w for w in words
         if w.bbox.top > bottom
         and item_span.x_left <= (w.bbox.x0 + w.bbox.x1) / 2 <= item_span.x_right
         and w.text.isdigit()),
        key=lambda w: (w.page, w.bbox.top))
    anchor = item_words[row_index]
    span = headers[field]
    target = [w for w in words
              if w.page == anchor.page and abs(w.bbox.top - anchor.bbox.top) < 2.0
              and span.x_left <= (w.bbox.x0 + w.bbox.x1) / 2 <= span.x_right]
    assert target, "fixture error: no word found in that cell"
    return [w for w in words if not any(w is t for t in target)], anchor.text


def test_a_row_missing_one_cell_is_reported_as_partial_not_dropped() -> None:
    _contract, _app, pdf = generate(seed=1, error="clean")
    words = extract_words(pdf)
    headers = find_headers(words)
    full = reconstruct_rows(words, headers)
    damaged, item_no = _without_cell(words, headers, 1, "stored")

    assert len(reconstruct_rows(damaged, headers)) == len(full) - 1
    partials = partial_rows(damaged, headers)
    assert [(p.item_no, p.missing) for p in partials] == [(item_no, ("stored",))]


def test_a_blank_description_is_reported_too() -> None:
    _contract, _app, pdf = generate(seed=1, error="clean")
    words = extract_words(pdf)
    headers = find_headers(words)
    damaged, item_no = _without_cell(words, headers, 0, "description")
    assert [(p.item_no, p.missing) for p in partial_rows(damaged, headers)] == [
        (item_no, ("description",))]


def test_assembly_records_the_missing_cell_so_the_row_cannot_pass_as_complete(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The audit's probe: remove one stored-material cell from a clean
    multi-line PDF. Before the fix: one line fewer, `_unextractable == []`."""
    _contract, _app, pdf = generate(seed=1, error="clean")
    words = extract_words(pdf)
    damaged, item_no = _without_cell(words, find_headers(words), 1, "stored")
    monkeypatch.setattr(assemble_mod, "extract_words", lambda _pdf: damaged)

    built = assemble_pay_application(pdf, "corpus://seed-1")

    entries = built.provenance["_unextractable"]
    assert isinstance(entries, list)
    assert UnextractableField(f"line[{item_no}].stored", "cell not found in the row") in entries
    assert f"line[{item_no}].stored" in unverifiable_money_fields(built)


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_clean_documents_have_no_partial_rows_in_any_layout(layout: str, seed: int) -> None:
    """The control: summary lines, headers, footers and page numbers are not item
    rows, so a clean document of every layout reports nothing partial."""
    _contract, _app, pdf = generate(seed=seed, error="clean", layout=layout)
    words = extract_words(pdf)
    assert partial_rows(words, find_headers(words)) == []
