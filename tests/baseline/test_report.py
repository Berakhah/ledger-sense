import html
from datetime import date
from decimal import Decimal

from ledger_sense.baseline.report import render_report
from ledger_sense.baseline.study import BaselineStudy, LoadedRate, TimedSample
from ledger_sense.domain.money import money


def _study():
    return BaselineStudy(
        samples=[TimedSample(document_id=str(i), doc_type="pay_application",
                             touch_seconds=s, exceptions=0)
                 for i, s in enumerate([300, 360, 420, 380, 340, 400, 320, 360])],
        loaded_rate=LoadedRate(hourly=money("30.00"),
                               overhead_multiplier=Decimal("1.4")),
        population_size=500, sampling_frame="all AP documents received Aug 2026",
        collection_period=(date(2026, 8, 1), date(2026, 8, 31)))


def test_report_never_shows_a_point_estimate_without_its_interval():
    html = render_report(_study())
    assert "95% confidence interval" in html


def test_report_embeds_the_method_statement_verbatim():
    """Code review (Task 13b fix round): method_statement() carries
    sampling_frame — free text from an API caller — into this report, so
    render_report() HTML-escapes it before embedding (an unescaped
    "</pre><script>" in that field would otherwise execute). What lands in
    the report is the escaped form, not the literal method_statement()
    return value; "Student's" alone (the interval-method line's apostrophe)
    is enough to make the two differ even for an injection-free study."""
    study = _study()
    assert html.escape(study.method_statement()) in render_report(study)


def test_report_carries_a_print_stylesheet():
    assert "@media print" in render_report(_study())


def test_report_makes_no_automation_claim():
    html = render_report(_study()).lower()
    for forbidden in ["fully automated", "100% accurate", "eliminates"]:
        assert forbidden not in html
