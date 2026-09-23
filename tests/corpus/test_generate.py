import pypdfium2 as pdfium
import pytest

from ledger_sense.corpus.errors import generate_history
from ledger_sense.corpus.generate import generate, ground_truth
from ledger_sense.corpus.layouts import CONTINUATION_COLUMNS, SUMMARY_FIELDS, columns_for
from ledger_sense.extract.textlayer import extract_words
from ledger_sense.rules.arithmetic.foot import rule_a1
from ledger_sense.rules.arithmetic.payment import rule_a8


def test_clean_document_trips_no_arithmetic_rule() -> None:
    contract, app, _pdf = generate(seed=1, error="clean")
    assert rule_a1(app, contract) == []
    assert rule_a8(app, contract) == []


def test_footing_error_class_trips_exactly_a1() -> None:
    contract, app, _pdf = generate(seed=1, error="A1_footing")
    assert [f.rule_id for f in rule_a1(app, contract)] == ["A1"]


def test_retainage_error_class_trips_exactly_a8() -> None:
    contract, app, _pdf = generate(seed=1, error="A8_retainage")
    assert [f.rule_id for f in rule_a8(app, contract)] == ["A8"]


def test_generation_is_deterministic_for_a_seed() -> None:
    _c1, app1, _p1 = generate(seed=7, error="clean")
    _c2, app2, _p2 = generate(seed=7, error="clean")
    assert app1.model_dump_json() == app2.model_dump_json()


def test_ground_truth_values_match_the_generated_model() -> None:
    _contract, app, _pdf = generate(seed=3, error="clean")
    truth = ground_truth(seed=3, error="clean")
    assert truth["total_completed_and_stored"] == str(app.total_completed_and_stored)


def test_rendered_pdf_carries_a_text_layer() -> None:
    """Deterministic extraction depends on this; a scanned-only corpus is useless."""
    _contract, _app, pdf = generate(seed=1, error="clean")
    doc = pdfium.PdfDocument(pdf)
    text = doc[0].get_textpage().get_text_range()
    assert "Scheduled Value" in text


def test_the_summary_block_is_actually_rendered() -> None:
    """F-10. SUMMARY_FIELDS was defined and never drawn, so Task 17's summary
    test could not pass and Task 21's `_unextractable == []` could not hold. The
    original plan discovered this three tasks later, in the task proving DoD 2."""
    _contract, app, pdf = generate(seed=1, error="clean")
    text = pdfium.PdfDocument(pdf)[0].get_textpage().get_text_range()
    assert "Summary — Current Payment Due" in text
    assert str(app.total_completed_and_stored) in text


def test_no_summary_label_collides_with_a_column_header() -> None:
    """F-09, VERIFIED. The original set shared 'Retainage' and 'Total Completed
    and Stored' with the grid headers, so extract_summary's first-match anchor
    returned a LINE ITEM cell as the summary figure — with a bbox pointing at the
    wrong cell, and A1 passing on a document that does not foot."""
    headers = {c.header for c in CONTINUATION_COLUMNS}
    labels = {label for label, _ in SUMMARY_FIELDS}
    assert headers & labels == set()


def test_the_document_header_carries_a_vendor_name() -> None:
    _, app, pdf = generate(seed=1, error="clean")
    assert app.vendor_name is not None
    assert app.vendor_name.strip() != ""
    # F-09's lesson, extended: the vendor name must not collide with any
    # continuation-grid header, summary label, or the other three header
    # labels, or a label-anchor test could bind to the wrong text.
    words = {w.text for w in extract_words(pdf)}
    assert "Contractor" in words


def test_the_duplicate_injector_produces_a_second_document() -> None:
    """F-62. `inject` returned the application unchanged for I1 and C1, so Task
    47's demo could not catch either and DoD 7 could not be met."""
    history = generate_history(seed=1)
    assert len(history) == 2
    assert history[0][1].period_to == history[1][1].period_to


def test_the_retainage_injector_disagrees_with_the_contract_term() -> None:
    contract, app, _pdf = generate(seed=1, error="C1_retainage_rate")
    expected = contract.retainage.base_rate * app.total_completed_and_stored
    assert app.retainage_total != expected


@pytest.mark.parametrize("layout", ["baseline", "right_aligned", "alternate_headers",
                                    "wrapped_descriptions", "two_page"])
def test_every_layout_variant_renders_with_a_text_layer(layout: str) -> None:
    """F-14. Five layouts so the extraction tests have something to disagree with,
    instead of the generator agreeing with itself."""
    _c, _a, pdf = generate(seed=1, error="clean", layout=layout)
    assert pdfium.PdfDocument(pdf)[0].get_textpage().get_text_range().strip()


def test_alternate_headers_do_not_reuse_our_own_wording() -> None:
    """The synonym variant is only adversarial if the words actually differ: the
    extractor must own a synonym table rather than our header strings."""
    baseline = {c.header for c in CONTINUATION_COLUMNS}
    alternate = {c.header for c in columns_for("alternate_headers")}
    assert alternate - baseline == {"Sched. Value", "Work in Place",
                                    "Materials Presently Stored"}


def test_right_aligned_money_shares_a_right_edge_not_a_left_edge() -> None:
    """F-12. Column assignment must use the span, not the left edge: under this
    layout the Scheduled Value cells share a right edge and no left edge."""
    _c, _a, pdf = generate(seed=1, error="clean", layout="right_aligned")
    page = pdfium.PdfDocument(pdf)[0]
    bounds = [obj.get_bounds() for obj in page.get_objects()]
    # The Scheduled Value column spans 288..368; the header sits above y=540.
    cells = [b for b in bounds if b[0] >= 288.0 and b[2] <= 368.5 and b[3] < 540.0]
    assert len(cells) > 1
    assert {round(b[2]) for b in cells} == {368}
    assert {round(b[0]) for b in cells} != {288}


def test_two_page_repeats_the_header_row_on_the_second_page() -> None:
    """The header row must not be mistaken for a data row on page 2."""
    _c, _a, pdf = generate(seed=1, error="clean", layout="two_page")
    doc = pdfium.PdfDocument(pdf)
    assert len(doc) > 1
    assert "Scheduled Value" in doc[1].get_textpage().get_text_range()


def test_wrapped_descriptions_split_a_cell_over_two_lines() -> None:
    """Row banding has to survive a wrapped cell, so one must exist."""
    _c, app, pdf = generate(seed=1, error="clean", layout="wrapped_descriptions")
    text = pdfium.PdfDocument(pdf)[0].get_textpage().get_text_range()
    assert app.lines[0].description not in text
    assert app.lines[0].description.split(" ")[0] in text
