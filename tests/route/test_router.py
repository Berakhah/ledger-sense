import ast
import pathlib
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ledger_sense.route.calibration import (
    Basis,
    CalibrationCurve,
    CalibrationStore,
    Label,
    LabelBasis,
)
from ledger_sense.route.router import AUDIT_FRACTION_DEFAULT, TARGET_ACCURACY_DEFAULT, Router


def _curve(basis: Basis, pairs: Sequence[tuple[float, bool]]) -> CalibrationCurve:
    labels = [Label("retainage_rate", c, ok, datetime(2026, 8, 1, tzinfo=UTC),
                    f"c{i}", basis) for i, (c, ok) in enumerate(pairs)]
    return CalibrationCurve.fit(labels)


def _labels(pairs: Sequence[tuple[float, bool]], basis: LabelBasis = "synthetic") -> list[Label]:
    return [Label(field_type="retainage_rate", predicted_confidence=c, was_correct=ok,
                  observed_at=datetime(2026, 8, 1, tzinfo=UTC),
                  source_claim_id=f"c{i}", basis=basis)
            for i, (c, ok) in enumerate(pairs)]


@pytest.fixture
def store(tmp_path: Path) -> CalibrationStore:
    return CalibrationStore(tmp_path / "cal.db")


def test_the_target_accuracy_default_is_the_brief_figure() -> None:
    assert TARGET_ACCURACY_DEFAULT == 0.995


def test_with_no_curve_at_all_everything_routes_to_review(store: CalibrationStore) -> None:
    d = Router(store).decide("retainage_rate", confidence=0.999)
    assert d.route == "review"
    assert d.calibration_state == "uncalibrated"
    assert d.threshold is None
    assert "no calibration curve" in d.reason


def test_a_synthetic_curve_still_routes_everything_to_review(store: CalibrationStore) -> None:
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


def test_a_field_curve_routes_a_high_confidence_field_automatically(store: CalibrationStore) -> None:
    store.save_curve(_curve("field", [(0.99, True)] * _FIELD_CURVE_HIGH_N
                             + [(0.20, False)] * _FIELD_CURVE_LOW_N))
    d = Router(store).decide("retainage_rate", confidence=0.999)
    assert d.route == "auto"
    assert d.calibration_state == "calibrated:field"
    assert d.threshold is not None


def test_a_field_curve_still_reviews_a_low_confidence_field(store: CalibrationStore) -> None:
    store.save_curve(_curve("field", [(0.99, True)] * _FIELD_CURVE_HIGH_N
                             + [(0.20, False)] * _FIELD_CURVE_LOW_N))
    d = Router(store).decide("retainage_rate", confidence=0.25)
    assert d.route == "review"
    assert "below threshold" in d.reason


def test_the_decision_reports_a_calibrated_probability(store: CalibrationStore) -> None:
    store.save_curve(_curve("field", [(0.90, True)] * 200 + [(0.30, False)] * 110))
    d = Router(store).decide("retainage_rate", confidence=0.30)
    assert d.calibrated_confidence is not None
    assert 0.0 <= d.calibrated_confidence <= 1.0


def test_a_failed_rule_forces_review_even_at_a_perfect_confidence(store: CalibrationStore) -> None:
    store.save_curve(_curve("field", [(0.99, True)] * 250 + [(0.20, False)] * 60))
    d = Router(store).decide("retainage_rate", confidence=1.0, rule_failures=("C1",))
    assert d.route == "review"
    assert "C1" in d.reason


def test_a_model_sourced_claim_never_routes_automatically(store: CalibrationStore) -> None:
    store.save_curve(_curve("field", [(0.99, True)] * 250 + [(0.20, False)] * 60))
    d = Router(store).decide("retainage_rate", confidence=0.999, method="MODEL")
    assert d.route == "review"
    assert "MODEL" in d.reason


def test_the_decision_is_serialisable_for_the_audit_trail(store: CalibrationStore) -> None:
    d = Router(store).decide("retainage_rate", confidence=0.5)
    payload = d.as_dict()
    assert set(payload) >= {"field", "route", "calibration_state", "basis",
                            "threshold", "calibrated_confidence", "reason"}


def test_no_derived_threshold_is_hard_coded_anywhere_in_the_router() -> None:
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


# F-24. Reviewed exemptions to the blanket extract/ float-literal guard
# below. This allowlist is a REVIEW GATE, not a naming convention: each entry
# is one specific (file, line, value) the reviewer has actually looked at and
# confirmed is a PDF-geometry tolerance, never a confidence/quality cut-off. A
# new float literal anywhere in extract/ -- named CONFIDENCE, THRESHOLD, or
# anything else, e.g. a future OCR_FLOOR = 0.5 or MIN_QUALITY = 0.5 -- is NOT
# exempt just by not matching a name pattern; it must be individually added
# here, with a reason, or the test fails. That is the whole point of F-24.
_EXTRACT_FLOAT_ALLOWLIST: frozenset[tuple[str, int, float]] = frozenset({
    # anchors.py: point-distance tolerances for "is this value near that
    # label on the page", not a confidence score.
    ("ledger_sense/extract/anchors.py", 18, 6.0),   # SAME_BAND_PT
    ("ledger_sense/extract/anchors.py", 24, 12.0),  # BELOW_BAND_PT -- Task 17 review (Important-1)
    ("ledger_sense/extract/anchors.py", 27, 20.0),  # BELOW_X_SLACK_PT
    # tables.py: row/column banding tolerances for grid reconstruction, not
    # a confidence score.
    ("ledger_sense/extract/tables.py", 28, 6.0),    # ROW_BAND_PT
    ("ledger_sense/extract/tables.py", 29, 4.0),    # COLUMN_SLACK_PT
    # anchors.py: I3 (final whole-branch review) -- max inter-word gap for
    # _value_after_label_merged's same-band collection, not a confidence score.
    ("ledger_sense/extract/anchors.py", 37, 15.0),  # MAX_MERGE_GAP_PT
})


def test_the_same_guard_covers_extract_where_a_floor_was_hiding() -> None:
    """F-24. The principle was right and its SCOPE was wrong: extract/ocr.py
    carried MIN_CONFIDENCE = 0.5, an uncalibrated cut-off written by feel, on
    the noisiest path in the product, with no equivalent guard.

    The guard is BLANKET (any float literal in extract/ other than 0.0/1.0),
    exactly like the router.py guard above -- narrowing it to only
    CONFIDENCE/THRESHOLD-named constants would let a future uncalibrated
    cut-off through silently under any other name (OCR_FLOOR, MIN_QUALITY,
    ...), which defeats the reason this guard exists. The five PDF-geometry
    tolerances in anchors.py/tables.py are real, already-reviewed exceptions
    -- they are named individually in _EXTRACT_FLOAT_ALLOWLIST above, by
    file/line/value, so a future reviewer can see exactly what is exempted
    and why. Anything not on that list still trips the test, whatever it is
    named.
    """
    import ast
    import pathlib
    offenders = []
    for path in pathlib.Path("ledger_sense/extract").rglob("*.py"):
        rel = path.as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                if node.value in (0.0, 1.0):
                    continue
                if (rel, node.lineno, node.value) in _EXTRACT_FLOAT_ALLOWLIST:
                    continue
                offenders.append(f"{rel}:{node.lineno}: {node.value}")
    assert offenders == [], f"literal thresholds in extract/: {offenders}"


# ---- Hold-back audit: F-49 ----

def test_a_fraction_of_auto_eligible_fields_is_held_back_for_audit(store: CalibrationStore) -> None:
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


def test_a_held_back_decision_says_why() -> None:
    """It is not a low-confidence review and must not read like one to the
    reviewer, or they will 'fix' an extraction that was right."""
    store = CalibrationStore(pathlib.Path(":memory:"))
    store.save_curve(_curve("field", [(0.99, True)] * 2200))
    router = Router(store, audit_fraction=1.0)
    assert "audit" in router.decide("retainage_rate", confidence=0.999).reason


def test_a_drifted_field_has_its_threshold_withdrawn_automatically(store: CalibrationStore) -> None:
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


def test_audit_labels_never_promote_a_synthetic_curve(store: CalibrationStore) -> None:
    """basis='audit' is field evidence. basis='synthetic' contaminates. Neither
    rule may leak into the other.

    2200, not 900, for the same holdout-sizing reason as the tests above.
    """
    labels = _labels([(0.99, True)] * 2200, basis="audit")
    assert CalibrationCurve.fit(labels).state == "calibrated:field"
