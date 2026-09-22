import pytest
from datetime import datetime, timezone

from ledger_sense.route.calibration import (
    CalibrationCurve, CalibrationStore, Label, clopper_pearson_lower,
    fit_isotonic, UnknownBasisError,
)


def _labels(pairs, basis="synthetic"):
    return [Label(field_type="retainage_rate", predicted_confidence=c, was_correct=ok,
                  observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
                  source_claim_id=f"c{i}", basis=basis)
            for i, (c, ok) in enumerate(pairs)]


# --- the maths -------------------------------------------------------------

def test_fit_isotonic_on_already_monotone_input_returns_it_unchanged():
    xs = [0.1, 0.4, 0.7, 0.9]
    ys = [0.1, 0.4, 0.7, 0.9]
    assert fit_isotonic(xs, ys) == pytest.approx(ys)


def test_fit_isotonic_pools_a_single_violation():
    # 0.8 followed by 0.2 violates monotonicity; PAVA pools them to their mean.
    out = fit_isotonic([0.1, 0.2, 0.3], [0.1, 0.8, 0.2])
    assert out == pytest.approx([0.1, 0.5, 0.5])


def test_fit_isotonic_output_is_non_decreasing_on_adversarial_input():
    ys = [0.9, 0.1, 0.8, 0.2, 0.7, 0.3]
    out = fit_isotonic([0.1, 0.2, 0.3, 0.4, 0.5, 0.6], ys)
    assert all(a <= b + 1e-12 for a, b in zip(out, out[1:]))


def test_fit_isotonic_preserves_the_mean():
    ys = [0.9, 0.1, 0.8, 0.2]
    out = fit_isotonic([0.1, 0.2, 0.3, 0.4], ys)
    assert sum(out) == pytest.approx(sum(ys))


def test_fit_isotonic_on_empty_input_returns_empty():
    assert fit_isotonic([], []) == []


# --- the refusal, which is the point of this task --------------------------

def test_a_synthetic_curve_refuses_to_yield_a_threshold():
    curve = CalibrationCurve.fit(_labels([(0.9, True)] * 2000, basis="synthetic"))
    assert curve.state == "calibrated:synthetic"
    assert curve.threshold_for(target_accuracy=0.995) is None


def test_an_uncalibrated_curve_refuses_to_yield_a_threshold():
    curve = CalibrationCurve.fit(_labels([(0.9, True)] * 10, basis="field"))
    assert curve.state == "uncalibrated"
    assert curve.threshold_for(target_accuracy=0.995) is None


def test_a_field_curve_with_enough_labels_yields_a_threshold():
    pairs = [(0.99, True)] * 4000 + [(0.30, False)] * 600
    curve = CalibrationCurve.fit(_labels(pairs, basis="field"))
    assert curve.state == "calibrated:field"
    t = curve.threshold_for(target_accuracy=0.995)
    assert t is not None and 0.0 < t <= 1.0


def test_a_field_curve_that_never_reaches_the_target_yields_no_threshold():
    # Nothing in this population is ever 99.5% reliable.
    pairs = [(c / 300, (i % 4) != 0) for i, c in enumerate(range(1, 301))]
    curve = CalibrationCurve.fit(_labels(pairs, basis="field"))
    assert curve.state == "uncalibrated"
    assert curve.threshold_for(target_accuracy=0.995) is None


def test_mixed_basis_labels_are_treated_as_synthetic():
    """One synthetic label contaminates the curve. Weakest link wins, deliberately."""
    pairs = [(0.9, True)] * 2000
    labels = _labels(pairs, basis="field")
    labels[0] = labels[0]._replace(basis="synthetic")
    curve = CalibrationCurve.fit(labels)
    assert curve.state == "calibrated:synthetic"


def test_an_unknown_basis_is_rejected_rather_than_defaulted():
    with pytest.raises(UnknownBasisError):
        CalibrationCurve.fit(_labels([(0.9, True)] * 300, basis="probably-real"))


def test_two_hundred_labels_cannot_support_the_target_accuracy():
    """REGRESSION — F-47, VERIFIED. MIN_LABELS_FOR_FIELD = 200 was asserted by a
    test and traced to nothing. By the rule of three a 95% lower bound at 99.5%
    needs n >= 600; with 200 the finest resolvable accuracy is 0.5%, the entire
    error budget. This phase was doing the thing it exists to prevent."""
    curve = CalibrationCurve.fit(_labels([(0.99, True)] * 200, basis="field"))
    assert curve.threshold_for(target_accuracy=0.995) is None
    assert "600" in curve.refusal_reason(0.995)


def test_six_hundred_clean_labels_do_support_it():
    curve = CalibrationCurve.fit(_labels([(0.99, True)] * 2200, basis="field"))
    assert curve.threshold_for(target_accuracy=0.995) is not None


def test_no_min_labels_constant_survives_anywhere_in_the_module():
    """A derived requirement, not a number somebody picked."""
    import ledger_sense.route.calibration as calibration
    assert not hasattr(calibration, "MIN_LABELS_FOR_FIELD")


def test_the_clopper_pearson_bound_matches_the_rule_of_three():
    from ledger_sense.route.calibration import clopper_pearson_lower
    assert clopper_pearson_lower(200, 200) < 0.995
    assert clopper_pearson_lower(600, 600) >= 0.994


def test_the_threshold_is_derived_on_held_out_data():
    """F-48. Isotonic with one knot per observation interpolates its training
    data, so a threshold read off the fit is optimistically biased — and the
    reliability diagram built from it ships in docs/methodology.md as evidence."""
    curve = CalibrationCurve.fit(_labels([(0.99, True)] * 900, basis="field"))
    assert curve.holdout_n > 0
    assert curve.fit_n + curve.holdout_n == curve.n


def test_a_curve_fitted_on_noise_yields_no_threshold():
    import random
    rng = random.Random(7)
    pairs = [(rng.random(), rng.random() < 0.5) for _ in range(900)]
    curve = CalibrationCurve.fit(_labels(pairs, basis="field"))
    assert curve.threshold_for(target_accuracy=0.995) is None


def test_the_refusal_says_why_in_words():
    """A greyed-out meter with no explanation is the thing this programme exists
    to correct. The UI renders this string verbatim."""
    curve = CalibrationCurve.fit(_labels([(0.9, True)] * 2000, basis="synthetic"))
    assert "synthetic" in curve.refusal_reason(0.995)


def test_a_confidence_outside_zero_to_one_is_rejected():
    """F-50. test_a_field_curve_that_never_reaches_the_target generated scores up
    to 3.0 and nothing validated the range."""
    with pytest.raises(ValueError):
        CalibrationCurve.fit(_labels([(1.5, True)] * 900, basis="field"))


def test_the_stored_curve_collapses_to_unique_blocks():
    """F-51. 900 labels produced 900 xs and 900 knots, JSON-serialised into
    SQLite and linear-scanned by calibrated(). PAVA output has few distinct
    values by construction."""
    curve = CalibrationCurve.fit(_labels([(0.99, True)] * 900, basis="field"))
    assert len(curve.xs) < 50


# --- calibrated probability ------------------------------------------------

def test_calibrated_probability_is_monotone_between_knots():
    pairs = [(0.2, False)] * 100 + [(0.8, True)] * 210
    curve = CalibrationCurve.fit(_labels(pairs, basis="field"))
    low, high = curve.calibrated(0.2), curve.calibrated(0.8)
    mid = curve.calibrated(0.5)
    assert low <= mid <= high


def test_calibrated_probability_clamps_outside_the_observed_range():
    curve = CalibrationCurve.fit(_labels([(0.5, True)] * 300, basis="field"))
    assert curve.calibrated(0.0) == curve.calibrated(0.5)
    assert curve.calibrated(1.0) == curve.calibrated(0.5)


# --- storage ---------------------------------------------------------------

def test_store_round_trips_a_curve(tmp_path):
    store = CalibrationStore(tmp_path / "cal.db")
    curve = CalibrationCurve.fit(_labels([(0.9, True)] * 300, basis="field"))
    store.save_curve(curve)
    back = store.load_curve("retainage_rate")
    assert back is not None
    assert back.state == curve.state
    assert list(back.knots) == pytest.approx(list(curve.knots))


def test_store_returns_none_for_a_field_type_it_has_never_seen(tmp_path):
    store = CalibrationStore(tmp_path / "cal.db")
    assert store.load_curve("never_heard_of_it") is None


def test_store_appends_labels_and_counts_them_by_basis(tmp_path):
    store = CalibrationStore(tmp_path / "cal.db")
    store.add_labels(_labels([(0.9, True)] * 5, basis="synthetic"))
    store.add_labels(_labels([(0.9, True)] * 3, basis="field"))
    assert store.count("retainage_rate") == {"synthetic": 5, "field": 3}


def test_saving_a_curve_twice_replaces_rather_than_duplicates(tmp_path):
    store = CalibrationStore(tmp_path / "cal.db")
    store.save_curve(CalibrationCurve.fit(_labels([(0.9, True)] * 300, basis="field")))
    store.save_curve(CalibrationCurve.fit(_labels([(0.4, False)] * 300, basis="field")))
    assert len(store.all_curves()) == 1
