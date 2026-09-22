import ast
import pathlib
from datetime import datetime, timezone

import pytest

from ledger_sense.route.calibration import CalibrationCurve, CalibrationStore, Label
from ledger_sense.route.router import AUDIT_FRACTION_DEFAULT, Router, TARGET_ACCURACY_DEFAULT


def _curve(basis, pairs):
    labels = [Label("retainage_rate", c, ok, datetime(2026, 8, 1, tzinfo=timezone.utc),
                    f"c{i}", basis) for i, (c, ok) in enumerate(pairs)]
    return CalibrationCurve.fit(labels)


def _labels(pairs, basis="synthetic"):
    return [Label(field_type="retainage_rate", predicted_confidence=c, was_correct=ok,
                  observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
                  source_claim_id=f"c{i}", basis=basis)
            for i, (c, ok) in enumerate(pairs)]


@pytest.fixture
def store(tmp_path):
    return CalibrationStore(tmp_path / "cal.db")


def test_the_target_accuracy_default_is_the_brief_figure():
    assert TARGET_ACCURACY_DEFAULT == 0.995


def test_with_no_curve_at_all_everything_routes_to_review(store):
    d = Router(store).decide("retainage_rate", confidence=0.999)
    assert d.route == "review"
    assert d.calibration_state == "uncalibrated"
    assert d.threshold is None
    assert "no calibration curve" in d.reason


def test_a_synthetic_curve_still_routes_everything_to_review(store):
    """The single most important test in the phase.

    2200 labels, not the brief's original 300: F-47/F-48 (already merged,
    Task 40) evaluate the Clopper-Pearson lower bound on the 30% HELD-OUT
    split only, so reaching the 0.995 target needs a holdout of ~600+ (see
    tests/route/test_calibration.py::test_six_hundred_clean_labels_do_support_it,
    which uses the same 2200 figure for the identical reason). At 300, the
    holdout is 90 and the curve is 'uncalibrated' regardless of basis --
    which would make this test pass for the wrong reason (an uncalibrated
    curve also routes to review) instead of the reason it names.
    """
    store.save_curve(_curve("synthetic", [(0.99, True)] * 2200))
    d = Router(store).decide("retainage_rate", confidence=0.999)
    assert d.route == "review"
    assert d.calibration_state == "calibrated:synthetic"
    assert d.threshold is None
    assert "synthetic" in d.reason


# Same scale-up as above (F-47/F-48: threshold is judged on the 30% holdout
# only). 8000 high-confidence + 2000 low-confidence labels, same ~4:1 ratio
# as the brief's 250:60, leaves ~1000 pure-success 0.99 labels in the
# holdout after the 70/30 split -- comfortably past the ~600 the rule of
# three requires at a 0.995 target -- so the curve actually reaches
# 'calibrated:field' with threshold 0.99, which is the scenario these two
# tests are named for.
_FIELD_CURVE_HIGH_N = 8000
_FIELD_CURVE_LOW_N = 2000


def test_a_field_curve_routes_a_high_confidence_field_automatically(store):
    store.save_curve(_curve("field", [(0.99, True)] * _FIELD_CURVE_HIGH_N
                             + [(0.20, False)] * _FIELD_CURVE_LOW_N))
    d = Router(store).decide("retainage_rate", confidence=0.999)
    assert d.route == "auto"
    assert d.calibration_state == "calibrated:field"
    assert d.threshold is not None


def test_a_field_curve_still_reviews_a_low_confidence_field(store):
    store.save_curve(_curve("field", [(0.99, True)] * _FIELD_CURVE_HIGH_N
                             + [(0.20, False)] * _FIELD_CURVE_LOW_N))
    d = Router(store).decide("retainage_rate", confidence=0.25)
    assert d.route == "review"
    assert "below threshold" in d.reason


def test_the_decision_reports_a_calibrated_probability(store):
    store.save_curve(_curve("field", [(0.90, True)] * 200 + [(0.30, False)] * 110))
    d = Router(store).decide("retainage_rate", confidence=0.30)
    assert d.calibrated_confidence is not None
    assert 0.0 <= d.calibrated_confidence <= 1.0


def test_a_failed_rule_forces_review_even_at_a_perfect_confidence(store):
    store.save_curve(_curve("field", [(0.99, True)] * 250 + [(0.20, False)] * 60))
    d = Router(store).decide("retainage_rate", confidence=1.0, rule_failures=("C1",))
    assert d.route == "review"
    assert "C1" in d.reason


def test_a_model_sourced_claim_never_routes_automatically(store):
    store.save_curve(_curve("field", [(0.99, True)] * 250 + [(0.20, False)] * 60))
    d = Router(store).decide("retainage_rate", confidence=0.999, method="MODEL")
    assert d.route == "review"
    assert "MODEL" in d.reason


def test_the_decision_is_serialisable_for_the_audit_trail(store):
    d = Router(store).decide("retainage_rate", confidence=0.5)
    payload = d.as_dict()
    assert set(payload) >= {"field", "route", "calibration_state", "basis",
                            "threshold", "calibrated_confidence", "reason"}


def test_no_derived_threshold_is_hard_coded_anywhere_in_the_router():
    """A stray 0.9 in this file would silently undo the whole phase.

    TARGET_ACCURACY_DEFAULT is permitted: a *target* is a policy figure that the
    product states out loud.  A *threshold* must be derived from a fitted curve,
    never written down.
    """
    src = pathlib.Path("ledger_sense/route/router.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    permitted = {0.0, 1.0, TARGET_ACCURACY_DEFAULT, AUDIT_FRACTION_DEFAULT}
    offenders = [n.value for n in ast.walk(tree)
                 if isinstance(n, ast.Constant) and isinstance(n.value, float)
                 and n.value not in permitted]
    assert offenders == [], f"literal thresholds found in router.py: {offenders}"


def test_the_same_guard_covers_extract_where_a_floor_was_hiding():
    """F-24. The principle was right and its SCOPE was wrong: extract/ocr.py
    carried MIN_CONFIDENCE = 0.5, an uncalibrated cut-off written by feel, on the
    noisiest path in the product, with no equivalent guard.

    Scoped to CONFIDENCE/THRESHOLD-named constants specifically (not every
    float in extract/): anchors.py and tables.py carry reviewed PDF-geometry
    tolerances in points (SAME_BAND_PT, BELOW_BAND_PT, BELOW_X_SLACK_PT,
    table row/column spacing) that are not confidence cut-offs and are out of
    this task's scope to touch. A blanket "no float but 0.0/1.0 anywhere in
    extract/" guard would flag those legitimate constants alongside a real
    F-24 regression, which defeats the guard by making it too noisy to trust.
    """
    import ast
    import pathlib
    offenders = []
    for path in pathlib.Path("ledger_sense/extract").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            value = node.value
            if not (isinstance(value, ast.Constant) and isinstance(value.value, float)):
                continue
            if value.value in (0.0, 1.0):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and (
                        "CONFIDENCE" in target.id or "THRESHOLD" in target.id):
                    offenders.append(f"{path}:{node.lineno}: {target.id} = {value.value}")
    assert offenders == [], f"uncalibrated confidence/threshold constants in extract/: {offenders}"


# ---- Hold-back audit: F-49 ----

def test_a_fraction_of_auto_eligible_fields_is_held_back_for_audit(store):
    """Without it, a field type that crosses its threshold stops producing labels
    entirely, and the curve can never see that extraction has degraded.

    2200 labels, not the brief's 900, for the same reason as above: a pure
    900-label success curve never reaches 'calibrated:field' (holdout of 270
    falls short of the ~600 the rule of three needs at 0.995), so
    decide() would always route to review anyway and this test would pass
    without ever exercising the hold-back sampler at all.
    """
    store.save_curve(_curve("field", [(0.99, True)] * 2200))
    router = Router(store, audit_fraction=0.05)
    routes = [router.decide("retainage_rate", confidence=0.999).route
              for _ in range(400)]
    held = routes.count("review")
    assert 5 <= held <= 45, f"held back {held} of 400 at a 5% fraction"


def test_a_held_back_decision_says_why():
    """It is not a low-confidence review and must not read like one to the
    reviewer, or they will 'fix' an extraction that was right."""
    store = CalibrationStore(":memory:")
    store.save_curve(_curve("field", [(0.99, True)] * 2200))
    router = Router(store, audit_fraction=1.0)
    assert "audit" in router.decide("retainage_rate", confidence=0.999).reason


def test_a_drifted_field_has_its_threshold_withdrawn_automatically(store):
    """Seeded degradation. A human who has to notice is a human who will not.

    2200, not 900, for the field curve so it starts genuinely
    'calibrated:field' with a real threshold -- otherwise decide() already
    routes to review before any drift check runs, and withdraw_if_drifted
    returning True would not prove anything was actually withdrawn.
    """
    store.save_curve(_curve("field", [(0.99, True)] * 2200))
    store.add_labels(_labels([(0.99, False)] * 60, basis="audit"))
    assert Router(store).withdraw_if_drifted("retainage_rate") is True
    assert Router(store).decide("retainage_rate", confidence=0.999).route == "review"


def test_audit_labels_never_promote_a_synthetic_curve(store):
    """basis='audit' is field evidence. basis='synthetic' contaminates. Neither
    rule may leak into the other.

    2200, not 900, for the same holdout-sizing reason as the tests above.
    """
    labels = _labels([(0.99, True)] * 2200, basis="audit")
    assert CalibrationCurve.fit(labels).state == "calibrated:field"
