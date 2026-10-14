"""Isotonic calibration of extraction confidences.

The whole point of this module is the refusal in ``threshold_for``: a curve that
was not fitted from field labels returns ``None``, and a ``None`` threshold routes
everything to human review.  There is no override.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from math import exp, lgamma, log
from pathlib import Path
from typing import Literal, NamedTuple

from ledger_sense.store.sqlite import connect as _connect

Basis = Literal["synthetic", "field"]
# What a Label may carry. "audit" is field evidence sampled from the auto-accepted
# stream (see router.py); a fitted curve collapses it to "field", hence two types.
LabelBasis = Literal["synthetic", "field", "audit"]
CalibrationState = Literal["uncalibrated", "calibrated:synthetic", "calibrated:field"]

# MIN_LABELS_FOR_FIELD IS GONE — REVISED (F-47).
#
# It was 200, asserted by a test, and traced to nothing. VERIFIED: a 95% lower
# bound at 99.5% accuracy needs n >= 3/0.005 = 600 by the rule of three, and with
# 200 labels the finest accuracy resolvable is 1/200 = 0.5% — the entire error
# budget. The phase that exists to stop the code emitting a threshold it has not
# earned was emitting one it had not earned.
#
# The requirement is now DERIVED from the target: threshold_for() returns a value
# only where a Clopper-Pearson lower bound meets it. Too few labels yields None
# automatically, and the code can say why.
_VALID_BASES: frozenset[str] = frozenset({"synthetic", "field", "audit"})
HOLDOUT_FRACTION = 0.30          # F-48: fit on 70%, judge on the held-out 30%
CONFIDENCE_LEVEL = 0.95
TARGET_ACCURACY_DEFAULT = 0.995


def clopper_pearson_lower(successes: int, trials: int,
                          level: float = CONFIDENCE_LEVEL) -> float:
    """Exact one-sided lower bound on a binomial proportion (F-47).

    Stdlib only — the inverse Beta CDF via bisection on the regularised
    incomplete beta, ~20 lines. Same argument as PAVA: a buyer auditing the
    reliability claim can read the algorithm that produced it.

        clopper_pearson_lower(200, 200) ~ 0.9851   -> cannot support 0.995
        clopper_pearson_lower(600, 600) ~ 0.9950   -> can
    """
    if trials == 0:
        return 0.0
    if successes == trials:
        return float((1.0 - level) ** (1.0 / trials))
    lo, hi = 0.0, successes / trials
    for _ in range(200):
        mid = (lo + hi) / 2
        if _binom_sf(successes - 1, trials, mid) > 1.0 - level:
            hi = mid
        else:
            lo = mid
    return lo


def _binom_sf(k: int, n: int, p: float) -> float:
    """Survival function of binomial distribution: P(X > k) where X ~ Binomial(n, p)."""
    if p == 0.0:
        return 0.0 if k >= 0 else 1.0
    if p == 1.0:
        return 1.0 if k < n else 0.0
    return _beta_reg(k + 1, n - k, p)


def _beta_reg(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta function I_x(a, b) via continued fractions.

    Uses log-space computation to avoid numerical underflow with large a, b.
    """
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    # Use symmetry: I_x(a,b) = 1 - I_{1-x}(b,a)
    if x > (a + 1) / (a + b + 2):
        return 1.0 - _beta_reg(b, a, 1.0 - x)

    # Compute in log-space to avoid underflow:
    # front = (x^a * (1-x)^b) / (a * Beta(a,b))
    #       = exp(a*log(x) + b*log(1-x) - log(a) - lgamma(a) - lgamma(b) + lgamma(a+b))
    log_front = a * log(x) + b * log(1.0 - x) - log(a) - lgamma(a) - lgamma(b) + lgamma(a + b)
    front = exp(log_front)

    return float(front * _beta_cf(a, b, x))


def _beta_cf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (modified Lentz's method)."""
    max_iter = 200
    eps = 3.0e-16
    fpmin = 1.0e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


class UnknownBasisError(ValueError):
    """A label carried a basis that is neither 'synthetic' nor 'field'."""


class Label(NamedTuple):
    field_type: str
    predicted_confidence: float
    was_correct: bool
    observed_at: datetime
    source_claim_id: str
    basis: LabelBasis


def fit_isotonic(xs: Sequence[float], ys: Sequence[float]) -> list[float]:
    """Pool-adjacent-violators.  Returns a non-decreasing fit of ``ys`` ordered by ``xs``.

    Fifteen lines instead of a scikit-learn dependency, so that a buyer auditing the
    reliability claim can read the algorithm that produced it.
    """
    if not xs:
        return []
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    blocks: list[list[float]] = []          # each block is [sum, count]
    for i in order:
        blocks.append([float(ys[i]), 1.0])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            s, n = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += n
    fitted_sorted: list[float] = []
    for s, n in blocks:
        fitted_sorted.extend([s / n] * int(n))
    out = [0.0] * len(xs)
    for pos, i in enumerate(order):
        out[i] = fitted_sorted[pos]
    return out


@dataclass(frozen=True)
class CalibrationCurve:
    field_type: str
    basis: Basis
    n: int
    xs: tuple[float, ...]
    knots: tuple[float, ...]
    fit_n: int
    holdout_xs: tuple[float, ...]
    holdout_ys: tuple[float, ...]

    @property
    def holdout_n(self) -> int:
        return len(self.holdout_xs)

    @property
    def state(self) -> CalibrationState:
        # REVISED (F-47). This was `if self.n < MIN_LABELS_FOR_FIELD` — a
        # constant of 200 traced to nothing. "Enough" is a function of the TARGET:
        # a curve is uncalibrated when no score in it can meet the target at the
        # 95% lower bound, which is checked, not assumed.
        if not self._can_reach(TARGET_ACCURACY_DEFAULT):
            return "uncalibrated"
        return "calibrated:field" if self.basis == "field" else "calibrated:synthetic"

    def _can_reach(self, target: float) -> bool:
        """Is there ANY x where held-out accuracy's Clopper-Pearson bound meets target?
        Used to determine if the curve state is 'calibrated' or 'uncalibrated'."""
        if not self.holdout_xs:
            return False
        # Check each unique confidence threshold in the holdout set
        for x in sorted(set(self.holdout_xs)):
            at_or_above = [(hx, hy) for hx, hy in zip(self.holdout_xs, self.holdout_ys)
                           if hx >= x]
            successes = sum(1 for _hx, hy in at_or_above if hy >= 0.5)
            if clopper_pearson_lower(successes, len(at_or_above)) >= target:
                return True
        return False

    @classmethod
    def fit(cls, labels: Sequence[Label]) -> CalibrationCurve:
        if not labels:
            raise ValueError("cannot fit a curve from zero labels")
        bases = {lab.basis for lab in labels}
        unknown = bases - _VALID_BASES
        if unknown:
            raise UnknownBasisError(f"unknown label basis: {sorted(unknown)!r}")
        # A single synthetic label contaminates the whole curve.  Weakest link
        # wins. 'audit' labels are FIELD evidence -- a held-back sample of the
        # same production population, just routed to review on purpose (F-49)
        # -- so a basis set of {"field"}, {"audit"}, or {"field", "audit"} is
        # all field-grade. Only the presence of "synthetic" contaminates.
        basis: Basis = "synthetic" if "synthetic" in bases else "field"
        field_type = labels[0].field_type
        xs_raw = [lab.predicted_confidence for lab in labels]
        ys = [1.0 if lab.was_correct else 0.0 for lab in labels]

        # F-50: Validate confidences strictly - ANY value outside [0, 1] raises ValueError
        xs = []
        for conf in xs_raw:
            if not (0.0 <= conf <= 1.0):
                raise ValueError(f"confidence {conf} outside [0, 1]")
            xs.append(conf)

        # Split into fit and holdout sets
        split_idx = int(len(labels) * (1 - HOLDOUT_FRACTION))
        fit_indices = list(range(split_idx))
        holdout_indices = list(range(split_idx, len(labels)))

        fit_xs = [xs[i] for i in fit_indices]
        fit_ys = [ys[i] for i in fit_indices]
        holdout_xs = [xs[i] for i in holdout_indices]
        holdout_ys = [ys[i] for i in holdout_indices]

        # Fit isotonic on 70% (fit_xs, fit_ys), not the full dataset
        knots = fit_isotonic(fit_xs, fit_ys)
        pairs = sorted(zip(fit_xs, knots))

        # Collapse to unique blocks: PAVA output has many repeated values
        # but we only need to store unique (x, knot) pairs for storage/interpolation
        unique_pairs: list[tuple[float, float]] = []
        for x, k in pairs:
            if not unique_pairs or unique_pairs[-1][1] != k:
                unique_pairs.append((x, k))

        return cls(field_type=field_type, basis=basis, n=len(labels),
                   xs=tuple(x for x, _ in unique_pairs),
                   knots=tuple(k for _, k in unique_pairs),
                   fit_n=len(fit_indices), holdout_xs=tuple(holdout_xs),
                   holdout_ys=tuple(holdout_ys))

    def calibrated(self, confidence: float) -> float:
        """Linear interpolation between knots; clamped outside the observed range."""
        if confidence <= self.xs[0]:
            return self.knots[0]
        if confidence >= self.xs[-1]:
            return self.knots[-1]
        for i in range(1, len(self.xs)):
            if confidence <= self.xs[i]:
                x0, x1 = self.xs[i - 1], self.xs[i]
                y0, y1 = self.knots[i - 1], self.knots[i]
                if x1 == x0:
                    return y1
                return y0 + (y1 - y0) * (confidence - x0) / (x1 - x0)
        return self.knots[-1]

    def threshold_for(self, target_accuracy: float) -> float | None:
        """The lowest score whose HELD-OUT accuracy has a lower bound meeting the target.

        REVISED (F-47, F-48). Evaluates held-out data only (F-48) to avoid optimism bias.
        Takes Clopper-Pearson lower bound (F-47) on held-out accuracy.

        Returns None unless the curve was fitted from field labels. That refusal
        was always right: synthetic evidence licenses nothing.
        """
        if self.state != "calibrated:field":
            return None
        # Check HELD-OUT data only for each confidence threshold
        for x in sorted(set(self.holdout_xs)):
            at_or_above = [(hx, hy) for hx, hy in zip(self.holdout_xs, self.holdout_ys)
                           if hx >= x]
            if not at_or_above:
                continue
            successes = sum(1 for _hx, hy in at_or_above if hy >= 0.5)
            if clopper_pearson_lower(successes, len(at_or_above)) >= target_accuracy:
                return x
        return None

    def refusal_reason(self, target_accuracy: float) -> str | None:
        """Why there is no threshold. The UI renders this verbatim — a greyed-out
        meter with no explanation is the thing this programme exists to correct."""
        if self.state == "uncalibrated":
            import math
            needed = math.ceil(3 / (1 - target_accuracy))
            return (f"{self.n} labels is not enough to demonstrate "
                    f"{target_accuracy:.1%} accuracy with 95% confidence; "
                    f"roughly {needed} are needed")
        if self.state == "calibrated:synthetic":
            return ("this curve was fitted on generated documents; synthetic "
                    "evidence licenses no automation on your documents")
        if self.threshold_for(target_accuracy) is None:
            return (f"no score in this population reaches {target_accuracy:.1%} "
                    f"accuracy at the 95% lower bound")
        return None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS labels (
  field_type TEXT NOT NULL,
  predicted_confidence REAL NOT NULL,
  was_correct INTEGER NOT NULL,
  observed_at TEXT NOT NULL,
  source_claim_id TEXT NOT NULL,
  basis TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS curves (
  field_type TEXT PRIMARY KEY,
  basis TEXT NOT NULL,
  n INTEGER NOT NULL,
  xs TEXT NOT NULL,
  knots TEXT NOT NULL,
  fit_n INTEGER NOT NULL,
  holdout_xs TEXT NOT NULL,
  holdout_ys TEXT NOT NULL
);
"""


class CalibrationStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._conn = _connect(str(self._path))
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def add_labels(self, labels: Sequence[Label]) -> None:
        self._conn.executemany(
            "INSERT INTO labels VALUES (?,?,?,?,?,?)",
            [(lab.field_type, lab.predicted_confidence, int(lab.was_correct),
              lab.observed_at.isoformat(), lab.source_claim_id, lab.basis)
             for lab in labels])
        self._conn.commit()

    def labels_for(self, field_type: str, *, basis: str | None = None) -> list[Label]:
        if basis is None:
            rows = self._conn.execute(
                "SELECT field_type, predicted_confidence, was_correct, observed_at,"
                " source_claim_id, basis FROM labels WHERE field_type = ?",
                (field_type,)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT field_type, predicted_confidence, was_correct, observed_at,"
                " source_claim_id, basis FROM labels WHERE field_type = ? AND basis = ?",
                (field_type, basis)).fetchall()
        return [Label(r[0], r[1], bool(r[2]), datetime.fromisoformat(r[3]), r[4], r[5])
                for r in rows]

    def count(self, field_type: str) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT basis, COUNT(*) FROM labels WHERE field_type = ? GROUP BY basis",
            (field_type,)).fetchall()
        return {basis: n for basis, n in rows}

    def save_curve(self, curve: CalibrationCurve) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO curves VALUES (?,?,?,?,?,?,?,?)",
            (curve.field_type, curve.basis, curve.n,
             json.dumps(list(curve.xs)), json.dumps(list(curve.knots)),
             curve.fit_n, json.dumps(list(curve.holdout_xs)), json.dumps(list(curve.holdout_ys))))
        self._conn.commit()

    def load_curve(self, field_type: str) -> CalibrationCurve | None:
        row = self._conn.execute(
            "SELECT field_type, basis, n, xs, knots, fit_n, holdout_xs, holdout_ys FROM curves WHERE field_type = ?",
            (field_type,)).fetchone()
        if row is None:
            return None
        return CalibrationCurve(row[0], row[1], row[2],
                                tuple(json.loads(row[3])), tuple(json.loads(row[4])),
                                row[5], tuple(json.loads(row[6])), tuple(json.loads(row[7])))

    def withdraw_curve(self, field_type: str, *, reason: str) -> None:
        """Remove a field type's stored curve so ``load_curve`` afterward
        returns ``None`` and the router falls back to review (F-49: automatic
        withdrawal on detected drift). ``reason`` is accepted for the audit
        trail / future logging even though this simple store does not
        currently persist it anywhere other than the caller's own records."""
        del reason
        self._conn.execute("DELETE FROM curves WHERE field_type = ?", (field_type,))
        self._conn.commit()

    def all_curves(self) -> list[CalibrationCurve]:
        rows = self._conn.execute("SELECT field_type FROM curves").fetchall()
        return [c for c in (self.load_curve(r[0]) for r in rows) if c is not None]
