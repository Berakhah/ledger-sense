"""Confidence routing.

Auto-post requires five things to be simultaneously true:
  1. a curve exists for this field type,
  2. that curve was fitted from FIELD labels,
  3. the curve yields a threshold at the target accuracy,
  4. this field's confidence is at or above it,
  5. the claim is DETERMINISTIC and every rule passed.

Any one of those failing routes to review.  There is no override.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Literal

from ledger_sense.route.calibration import CalibrationCurve, CalibrationStore

Route = Literal["auto", "review"]

# Policy figures the product STATES OUT LOUD. A THRESHOLD is derived from a
# curve, never written here -- that distinction is the whole phase.
TARGET_ACCURACY_DEFAULT = 0.995
# F-49, and 2026-08-29-REVISIONS.md Sec 11 Q4. Goes in docs/methodology.md,
# because it is a real trade between straight-through rate and drift
# detection.
AUDIT_FRACTION_DEFAULT = 0.05
# F-49: below this many audit labels, a refit is noise, not evidence -- the
# same rule-of-three reasoning as calibration.py's threshold_for, applied to
# the decision of whether to trust a drift check at all.
_MIN_AUDIT_LABELS_FOR_DRIFT_CHECK = 30


@dataclass(frozen=True)
class RoutingDecision:
    field: str
    route: Route
    calibration_state: str
    basis: str | None
    threshold: float | None
    calibrated_confidence: float | None
    reason: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class _AuditHoldBackSampler:
    """Deterministic per-field hold-back at a stated fraction (F-49).

    A field type that crosses its auto threshold would otherwise stop
    producing labels entirely, freezing the curve on the evidence that
    authorised it. This samples every Nth auto-eligible decision back to
    review instead, where N = round(1 / fraction), so the curve keeps
    seeing fresh evidence and can detect drift.

    Deterministic rather than randomised so behaviour at fraction=1.0 is
    exact (the very first call is held back) rather than merely likely.
    """

    def __init__(self, fraction: float, store: CalibrationStore) -> None:
        self._fraction = fraction
        self._store = store

    def should_hold_back(self, field: str) -> bool:
        if self._fraction <= 0.0:
            return False
        if self._fraction >= 1.0:
            return True
        n = self._store.next_holdback_count(field)
        interval = round(1.0 / self._fraction)
        return n % interval == 0


class Router:
    def __init__(self, store: CalibrationStore,
                 target_accuracy: float = TARGET_ACCURACY_DEFAULT,
                 audit_fraction: float = AUDIT_FRACTION_DEFAULT) -> None:
        self._store = store
        self._target = target_accuracy
        self._audit_fraction = audit_fraction
        self._audit_sampler = _AuditHoldBackSampler(audit_fraction, store)

    def decide(self, field: str, confidence: float, *,
               method: str = "DETERMINISTIC",
               rule_failures: Sequence[str] = ()) -> RoutingDecision:
        curve = self._store.load_curve(field)

        if curve is None:
            return RoutingDecision(field, "review", "uncalibrated", None, None, None,
                                    f"no calibration curve exists for {field}")

        threshold = curve.threshold_for(self._target)
        calibrated = curve.calibrated(confidence)

        if rule_failures:
            return RoutingDecision(field, "review", curve.state, curve.basis, threshold,
                                    calibrated,
                                    "rule failures block auto-posting: "
                                    f"{', '.join(rule_failures)}")

        if method.upper() != "DETERMINISTIC":
            return RoutingDecision(field, "review", curve.state, curve.basis, threshold,
                                    calibrated,
                                    f"claim method is {method}; only DETERMINISTIC "
                                    "may auto-post")

        if threshold is None:
            why = ("curve is synthetic; synthetic evidence licenses no automation"
                   if curve.state == "calibrated:synthetic"
                   else f"curve does not reach the {self._target} target accuracy")
            return RoutingDecision(field, "review", curve.state, curve.basis, None,
                                    calibrated, why)

        if confidence < threshold:
            return RoutingDecision(field, "review", curve.state, curve.basis, threshold,
                                    calibrated,
                                    f"confidence {confidence:.3f} below threshold "
                                    f"{threshold:.3f}")

        # HOLD-BACK AUDIT (F-49).
        #
        # Labels come only from fields routed to review. The moment a field
        # type crosses its threshold, its high-confidence population stops
        # producing labels -- so the curve freezes on the evidence that
        # authorised it and can NEVER detect that extraction has degraded. A
        # producer changing its PDF template is the most likely real-world
        # failure and it would be invisible.
        #
        # A feedback loop failing silently in the optimistic direction, in
        # the product whose thesis is that optimistic silence is the
        # industry's sin.
        if self._audit_sampler.should_hold_back(field):
            return RoutingDecision(field, "review", curve.state, curve.basis, threshold,
                                    calibrated,
                                    f"held back for audit ({self._audit_fraction:.0%} "
                                    "of auto-eligible fields) so drift stays visible")

        return RoutingDecision(field, "auto", curve.state, curve.basis, threshold,
                                calibrated,
                                f"confidence {confidence:.3f} at or above "
                                f"field-calibrated threshold {threshold:.3f}")

    def decide_claim(self, field: str, confidence: object, *,
                     method: str = "DETERMINISTIC",
                     rule_failures: Sequence[str] = ()) -> RoutingDecision:
        """`decide` for a stored claim, whose confidence column may be NULL.
        A missing or non-numeric confidence is the lowest confidence, so it
        can never clear a threshold."""
        score = float(confidence) if isinstance(confidence, (int, float)) else 0.0
        return self.decide(field, score, method=method, rule_failures=rule_failures)

    def withdraw_if_drifted(self, field: str) -> bool:
        """Refit on audit labels; withdraw the threshold if it can no longer
        meet the target. Automatic, because a human who has to notice is a
        human who will not."""
        audit_labels = self._store.labels_for(field, basis="audit")
        if len(audit_labels) < _MIN_AUDIT_LABELS_FOR_DRIFT_CHECK:
            return False
        curve = CalibrationCurve.fit(audit_labels)
        if curve.threshold_for(self._target) is None:
            self._store.withdraw_curve(field, reason="audit accuracy fell below target")
            return True
        return False
