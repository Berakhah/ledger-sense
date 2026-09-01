from datetime import date
from decimal import Decimal
import pytest
from ledger_sense.baseline.study import BaselineStudy, LoadedRate, TimedSample
from ledger_sense.domain.money import money


def _study(seconds_list):
    return BaselineStudy(
        samples=[TimedSample(document_id=str(i), doc_type="pay_application",
                             touch_seconds=s, exceptions=0)
                 for i, s in enumerate(seconds_list)],
        loaded_rate=LoadedRate(hourly=money("30.00"),
                               overhead_multiplier=Decimal("1.4")),
        population_size=500, sampling_frame="all AP documents received Aug 2026",
        collection_period=(date(2026, 8, 1), date(2026, 8, 31)))


def test_effective_hourly_applies_the_overhead_multiplier():
    assert LoadedRate(hourly=money("30.00"),
                      overhead_multiplier=Decimal("1.4")).effective_hourly() == money("42.00")


def test_cost_per_document_uses_mean_touch_time_and_loaded_rate():
    # mean 360s = 0.1h; 0.1 * 42.00 = 4.20
    assert _study([300, 360, 420]).cost_per_document() == money("4.20")


def test_confidence_interval_widens_with_variance():
    tight_lo, tight_hi = _study([360] * 8).confidence_interval()
    wide_lo, wide_hi = _study([60, 360, 660, 120, 600, 90, 540, 300]).confidence_interval()
    assert (wide_hi - wide_lo) > (tight_hi - tight_lo)


def test_too_few_samples_refuses_to_report_an_interval():
    """F-54. Three observations of a right-skewed quantity has no defensible
    t-interval. Say so rather than invent one — B13 is the whole differentiator."""
    with pytest.raises(ValueError, match="at least 8"):
        _study([360, 300, 420]).confidence_interval()


def test_the_t_value_does_not_collapse_to_the_normal_approximation():
    """F-52, VERIFIED. The original table stopped at df=9 and used 1.96 above it,
    understating the margin by 15.4% at df=9 and 6.8% at df=19 — always in the
    direction that flatters us."""
    from ledger_sense.baseline.study import _t_95
    assert _t_95(9) == Decimal("2.262")
    assert _t_95(19) == Decimal("2.093")
    assert _t_95(49) >= Decimal("2.009")
    assert _t_95(500) == Decimal("1.960")


def test_an_uncertain_loaded_rate_widens_the_interval():
    """F-53. The overhead multiplier is a figure the client gives off the top of
    their head. Treating it as exact makes the published interval too narrow."""
    seconds = [300, 360, 420, 380, 340, 400, 320, 360]
    tight = _study(seconds)
    wide = _study(seconds)
    wide.loaded_rate = LoadedRate(hourly=money("30.00"),
                                  overhead_multiplier=Decimal("1.4"),
                                  hourly_low=money("26.00"),
                                  hourly_high=money("34.00"))
    t_lo, t_hi = tight.confidence_interval()
    w_lo, w_hi = wide.confidence_interval()
    assert (w_hi - w_lo) > (t_hi - t_lo)


def test_the_method_statement_states_what_the_interval_does_not_cover():
    statement = _study([300, 360, 420, 380, 340, 400, 320, 360]).method_statement()
    assert "holds the loaded rate fixed" in statement
    assert "median" in statement.lower()


def test_method_statement_names_every_element_the_number_depends_on():
    statement = _study([300, 360, 420]).method_statement().lower()
    for required in ["sample size", "sampling frame", "touch time",
                     "loaded rate", "collection period"]:
        assert required in statement
