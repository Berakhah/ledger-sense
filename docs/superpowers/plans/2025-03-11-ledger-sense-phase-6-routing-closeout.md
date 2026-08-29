# Ledger Sense — Phase 6: Routing, Calibration and Close-Out (Weeks 11–12)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the confidence-routing machinery honestly — calibrated where there is evidence, refusing to auto-post where there is not — then close weeks 5–12 with the three remaining screens, the methodology document, the sixty-second demo and a repository someone else can run.

**Architecture:** Isotonic calibration fitted per field type from reviewer labels, with three explicit states (`uncalibrated`, `calibrated:synthetic`, `calibrated:field`). Only `calibrated:field` yields a routing threshold. Everything else routes to review. The router has no derived-from-nothing constant anywhere in it, and a test proves that.

**Tech Stack:** Python stdlib only for the calibration maths (PAVA, ~15 lines), SQLite for labels and curves, FastAPI, React, Playwright.

**Spec:** `docs/superpowers/specs/2025-03-11-ledger-sense-design.md` §7, §8, §12
**Parent plan:** `2025-03-11-ledger-sense-weeks-5-12.md` — its Global Constraints and File Structure apply here in full.
**Audit:** `2026-08-29-REVISIONS.md` §6 — read it before Task 40. Nine defects in this phase were fixed in place.
Three of them (F-46, F-47, F-49) meant the phase built to refuse unearned automation would have granted it.

---

## Read this before Task 40

### This phase decides what the product is allowed to claim

Everything before this phase produced numbers. This phase decides which of those numbers a buyer is
allowed to hear without a caveat attached. Get it wrong in the optimistic direction and the first
real customer call ends the conversation. Get it wrong in the pessimistic direction and you have
built a review queue with extra steps.

The resolution is mechanical, not rhetorical: **the code refuses to emit a threshold it has not
earned.** `CalibrationCurve.threshold_for()` returns `None` unless the curve was fitted from field
labels. A `None` threshold routes everything to review. There is no override flag, no
`--trust-me`, no configuration key that turns it off. If the product auto-posts, it is because a
human corrected two hundred extractions and the curve says so.

### Why no scikit-learn

`sklearn.isotonic.IsotonicRegression` would do this in one line. It is also a large dependency
carrying NumPy and SciPy, for one function, in a repository whose whole differentiator is that a
buyer can read the validation logic. Pool-adjacent-violators is fifteen readable lines. A
reliability curve a buyer might question is better as fifteen lines they can check than an opaque
call into a package they will not read. `scripts/licence_audit.py` (Task 1) is also happier with a
smaller dependency tree.

### Confidences are floats — deliberately

The no-float rule from the parent plan guards `domain/`, `rules/` and `writeback/`. It does **not**
apply here. A confidence is a model score, not money. Storing it as a `Decimal` would imply a
precision it does not have. The AST guard in Task 2 is already scoped to the three money packages;
do not widen it to `route/`, and do not "fix" the floats you see in this phase.

### The input this phase consumes, which nothing produced — REVISED (F-46)

`Router.decide(field, confidence)`, `labels_from_action(action)` and `CalibrationCurve.fit(labels)` all require a
per-field score. `extract/assemble.py` sets `confidence=None` on every claim and **Task 18's test asserts it**.
Nothing else produced one. **DoD 4 rested on a value the product never computed.**

Phase 2 Task 18 now emits `Claim.match_quality: float` — a documented, bounded function of the evidence
extraction actually had (label found exactly vs fuzzily; value token uniquely in the expected geometry vs one of
several; column assignment unambiguous vs within slack; profile-cache hit vs cold anchor). `Claim.confidence`
stays `None` for `DETERMINISTIC`, which was always correct.

**Everything in this phase reads `match_quality`, never `confidence`.** That the score is explicitly not a
probability is precisely why it must be calibrated before it routes anything — the argument this phase already
makes, now with something to make it about.

### 200 labels cannot support a 99.5% claim — REVISED (F-47)

**Verified.** A 95% lower confidence bound at 99.5% accuracy requires, by the rule of three,
`n ≥ 3 / 0.005 = 600` consecutive correct observations. With 200 labels the finest accuracy the data can resolve
is 1/200 = 0.5% — the entire error budget.

`MIN_LABELS_FOR_FIELD = 200` was asserted by a test and traced to nothing. **The phase whose stated purpose is
"the code refuses to emit a threshold it has not earned" emitted one it had not earned.** That is the same sin
one level up, and it is the finding this phase should be most embarrassed by.

The constant is **deleted**. `threshold_for(target)` derives its own requirement: a **Clopper–Pearson lower
bound** on observed accuracy at and above each candidate threshold, returning the lowest candidate whose *lower
bound* meets the target. A curve with too few labels then yields `None` automatically, for a reason the code can
state — which is what the phase wanted all along.

### The curve must not be fitted and judged on the same labels — REVISED (F-48)

Isotonic regression with one knot per observation interpolates its training data, so `threshold_for` read a
fitted value that was optimistically biased — and `docs/methodology.md` shipped the resulting reliability diagram
as evidence. Fit on a seeded random 70%; derive and check the threshold on the held-out 30%; record both n's on
the curve and both in the methodology.

### The sample censors itself the moment auto-posting starts — REVISED (F-49)

Labels come only from fields routed to review. When a field type crosses its threshold, its high-confidence
population stops producing labels — so the curve freezes on the evidence that authorised it and **can never
detect that extraction has degraded**, which is the single most likely real-world failure (a producer changes its
PDF template). A feedback loop that fails silently in the optimistic direction, in the product whose whole thesis
is that optimistic silence is the industry's defining sin.

**Hold-back audit sample:** a fixed fraction of above-threshold fields routes to review anyway, labelled
`audit`. The curve refits on audit labels. If audit accuracy falls below the target's lower bound, **the
threshold is withdrawn automatically** and the field type returns to `review`. The fraction is
`2026-08-29-REVISIONS.md` §11 **Q4** — 5% proposed — and it goes in `docs/methodology.md`, because it is a real
trade between straight-through rate and drift detection.

### The three states, and what each is worth

| State | How it arises | `threshold_for()` | What may be said to a buyer |
|---|---|---|---|
| `uncalibrated` | too few labels for the target to be **provable** — derived, never a constant | `None` | "Confidence is displayed, not acted on." |
| `calibrated:synthetic` | enough labels, any of them from the generated corpus | `None` | "The machinery is built and exercised. It has never seen a real document." |
| `calibrated:field` | enough real labels that the **lower bound** on accuracy meets the target, on held-out data | a float | "At the threshold shown, this field type was correct at least N% of the time with 95% confidence, over M held-out observations." |

Note what the third row now says. The original promised *"correct N times out of M"* — a point estimate read off
a curve fitted on its own training data. The revised sentence is one a controller can check.

Week 12 will almost certainly end at `calibrated:synthetic`. **That is the planned outcome, not a
failure.** The deliverable is the machinery plus the honesty, and the honesty is the harder half.

---

### Task 40: Calibration machinery

**Files:**
- Create: `ledger_sense/route/__init__.py`
- Create: `ledger_sense/route/calibration.py`
- Test: `tests/route/test_calibration.py`

- [ ] **Step 1: Write the tests (RED)**

```python
# tests/route/test_calibration.py
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
    curve = CalibrationCurve.fit(_labels([(0.9, True)] * 300, basis="synthetic"))
    assert curve.state == "calibrated:synthetic"
    assert curve.threshold_for(target_accuracy=0.995) is None


def test_an_uncalibrated_curve_refuses_to_yield_a_threshold():
    curve = CalibrationCurve.fit(_labels([(0.9, True)] * 10, basis="field"))
    assert curve.state == "uncalibrated"
    assert curve.threshold_for(target_accuracy=0.995) is None


def test_a_field_curve_with_enough_labels_yields_a_threshold():
    pairs = [(0.99, True)] * 250 + [(0.30, False)] * 60
    curve = CalibrationCurve.fit(_labels(pairs, basis="field"))
    assert curve.state == "calibrated:field"
    t = curve.threshold_for(target_accuracy=0.995)
    assert t is not None and 0.0 < t <= 1.0


def test_a_field_curve_that_never_reaches_the_target_yields_no_threshold():
    # Nothing in this population is ever 99.5% reliable.
    pairs = [(c / 100, (i % 4) != 0) for i, c in enumerate(range(1, 301))]
    curve = CalibrationCurve.fit(_labels(pairs, basis="field"))
    assert curve.state == "calibrated:field"
    assert curve.threshold_for(target_accuracy=0.995) is None


def test_mixed_basis_labels_are_treated_as_synthetic():
    """One synthetic label contaminates the curve. Weakest link wins, deliberately."""
    pairs = [(0.9, True)] * 300
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
    curve = CalibrationCurve.fit(_labels([(0.99, True)] * 900, basis="field"))
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
    curve = CalibrationCurve.fit(_labels([(0.9, True)] * 900, basis="synthetic"))
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
```

Run: `uv run pytest tests/route/test_calibration.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.route'`.

- [ ] **Step 2: Implement (GREEN)**

```python
# ledger_sense/route/calibration.py
"""Isotonic calibration of extraction confidences.

The whole point of this module is the refusal in ``threshold_for``: a curve that
was not fitted from field labels returns ``None``, and a ``None`` threshold routes
everything to human review.  There is no override.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, NamedTuple, Sequence

Basis = Literal["synthetic", "field"]
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
        return (1.0 - level) ** (1.0 / trials)
    lo, hi = 0.0, successes / trials
    for _ in range(200):
        mid = (lo + hi) / 2
        if _binom_sf(successes - 1, trials, mid) > 1.0 - level:
            hi = mid
        else:
            lo = mid
    return lo


class UnknownBasisError(ValueError):
    """A label carried a basis that is neither 'synthetic' nor 'field'."""


class Label(NamedTuple):
    field_type: str
    predicted_confidence: float
    was_correct: bool
    observed_at: datetime
    source_claim_id: str
    basis: Basis


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
        """Is there ANY score whose held-out lower bound meets the target? With
        too few labels the answer is no, for a reason the code can state."""
        for x in sorted(set(self.holdout_xs)):
            at_or_above = [(hx, hy) for hx, hy in zip(self.holdout_xs, self.holdout_ys)
                           if hx >= x]
            successes = sum(1 for _hx, hy in at_or_above if hy >= 0.5)
            if clopper_pearson_lower(successes, len(at_or_above)) >= target:
                return True
        return False

    @classmethod
    def fit(cls, labels: Sequence[Label]) -> "CalibrationCurve":
        if not labels:
            raise ValueError("cannot fit a curve from zero labels")
        bases = {lab.basis for lab in labels}
        unknown = bases - _VALID_BASES
        if unknown:
            raise UnknownBasisError(f"unknown label basis: {sorted(unknown)!r}")
        # A single synthetic label contaminates the whole curve.  Weakest link wins.
        basis: Basis = "field" if bases == {"field"} else "synthetic"
        field_type = labels[0].field_type
        xs = [lab.predicted_confidence for lab in labels]
        ys = [1.0 if lab.was_correct else 0.0 for lab in labels]
        knots = fit_isotonic(xs, ys)
        pairs = sorted(zip(xs, knots))
        return cls(field_type=field_type, basis=basis, n=len(labels),
                   xs=tuple(x for x, _ in pairs), knots=tuple(k for _, k in pairs))

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
        """The lowest score whose HELD-OUT accuracy has a lower bound meeting the
        target.

        REVISED (F-47, F-48). Two changes, both about earning the number:

        1. The original read the FITTED knot value — a point estimate from a
           curve with one knot per observation, judged on its own training data.
           This reads the held-out split (F-48) and takes a Clopper-Pearson lower
           bound on it (F-47), so "correct at least N% of the time" is a claim a
           controller can check rather than a number we produced.
        2. There is no MIN_LABELS constant. Too few labels simply fails to meet
           the bound, and `refusal_reason` says so in words.

        Returns None unless the curve was fitted from field labels. That refusal
        was always right: synthetic evidence licenses nothing.
        """
        if self.state != "calibrated:field":
            return None
        for x in sorted(set(self.holdout_xs)):
            at_or_above = [(hx, hy) for hx, hy in zip(self.holdout_xs, self.holdout_ys)
                           if hx >= x]
            successes = sum(1 for _hx, hy in at_or_above if hy >= 0.5)
            if clopper_pearson_lower(successes, len(at_or_above)) >= target_accuracy:
                return x
        return None

    def refusal_reason(self, target_accuracy: float) -> str | None:
        """Why there is no threshold. The UI renders this verbatim — a greyed-out
        meter with no explanation is the thing this programme exists to correct."""
        if self.state == "uncalibrated":
            return (f"{self.n} labels is not enough to demonstrate "
                    f"{target_accuracy:.1%} accuracy with 95% confidence; "
                    f"roughly {int(3 / (1 - target_accuracy))} are needed")
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
  knots TEXT NOT NULL
);
"""


class CalibrationStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._conn = sqlite3.connect(self._path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def add_labels(self, labels: Sequence[Label]) -> None:
        self._conn.executemany(
            "INSERT INTO labels VALUES (?,?,?,?,?,?)",
            [(lab.field_type, lab.predicted_confidence, int(lab.was_correct),
              lab.observed_at.isoformat(), lab.source_claim_id, lab.basis)
             for lab in labels])
        self._conn.commit()

    def labels_for(self, field_type: str) -> list[Label]:
        rows = self._conn.execute(
            "SELECT field_type, predicted_confidence, was_correct, observed_at,"
            " source_claim_id, basis FROM labels WHERE field_type = ?", (field_type,)).fetchall()
        return [Label(r[0], r[1], bool(r[2]), datetime.fromisoformat(r[3]), r[4], r[5])
                for r in rows]

    def count(self, field_type: str) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT basis, COUNT(*) FROM labels WHERE field_type = ? GROUP BY basis",
            (field_type,)).fetchall()
        return {basis: n for basis, n in rows}

    def save_curve(self, curve: CalibrationCurve) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO curves VALUES (?,?,?,?,?)",
            (curve.field_type, curve.basis, curve.n,
             json.dumps(list(curve.xs)), json.dumps(list(curve.knots))))
        self._conn.commit()

    def load_curve(self, field_type: str) -> CalibrationCurve | None:
        row = self._conn.execute(
            "SELECT field_type, basis, n, xs, knots FROM curves WHERE field_type = ?",
            (field_type,)).fetchone()
        if row is None:
            return None
        return CalibrationCurve(row[0], row[1], row[2],
                                tuple(json.loads(row[3])), tuple(json.loads(row[4])))

    def all_curves(self) -> list[CalibrationCurve]:
        rows = self._conn.execute("SELECT field_type FROM curves").fetchall()
        return [c for c in (self.load_curve(r[0]) for r in rows) if c is not None]
```

- [ ] **Step 3: Run test to verify it passes**

Run: `uv run pytest tests/route/test_calibration.py -v`
Expected: PASS, 17 tests.

- [ ] **Step 4: Commit**

```bash
git add ledger_sense/route/ tests/route/test_calibration.py
git commit -m "feat(route): isotonic calibration with three states; synthetic curves yield no threshold"
```

---

### Task 41: The router — DoD 4

**Files:**
- Create: `ledger_sense/route/router.py`
- Test: `tests/route/test_router.py`

- [ ] **Step 1: Write the tests (RED)**

```python
# tests/route/test_router.py
import ast
import pathlib
from datetime import datetime, timezone

import pytest

from ledger_sense.route.calibration import CalibrationCurve, CalibrationStore, Label
from ledger_sense.route.router import Router, TARGET_ACCURACY_DEFAULT


def _curve(basis, pairs):
    labels = [Label("retainage_rate", c, ok, datetime(2026, 8, 1, tzinfo=timezone.utc),
                    f"c{i}", basis) for i, (c, ok) in enumerate(pairs)]
    return CalibrationCurve.fit(labels)


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
    """The single most important test in the phase."""
    store.save_curve(_curve("synthetic", [(0.99, True)] * 300))
    d = Router(store).decide("retainage_rate", confidence=0.999)
    assert d.route == "review"
    assert d.calibration_state == "calibrated:synthetic"
    assert d.threshold is None
    assert "synthetic" in d.reason


def test_a_field_curve_routes_a_high_confidence_field_automatically(store):
    store.save_curve(_curve("field", [(0.99, True)] * 250 + [(0.20, False)] * 60))
    d = Router(store).decide("retainage_rate", confidence=0.999)
    assert d.route == "auto"
    assert d.calibration_state == "calibrated:field"
    assert d.threshold is not None


def test_a_field_curve_still_reviews_a_low_confidence_field(store):
    store.save_curve(_curve("field", [(0.99, True)] * 250 + [(0.20, False)] * 60))
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
    noisiest path in the product, with no equivalent guard."""
    import ast
    import pathlib
    offenders = []
    for path in pathlib.Path("ledger_sense/extract").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                if node.value not in (0.0, 1.0):
                    offenders.append(f"{path}:{node.lineno}: {node.value}")
    assert offenders == [], f"literal thresholds in extract/: {offenders}"


# ---- Hold-back audit: F-49 ----

def test_a_fraction_of_auto_eligible_fields_is_held_back_for_audit(store):
    """Without it, a field type that crosses its threshold stops producing labels
    entirely, and the curve can never see that extraction has degraded."""
    store.save_curve(_curve("field", [(0.99, True)] * 900))
    router = Router(store, audit_fraction=0.05)
    routes = [router.decide("retainage_rate", confidence=0.999).route
              for _ in range(400)]
    held = routes.count("review")
    assert 5 <= held <= 45, f"held back {held} of 400 at a 5% fraction"


def test_a_held_back_decision_says_why():
    """It is not a low-confidence review and must not read like one to the
    reviewer, or they will 'fix' an extraction that was right."""
    store = CalibrationStore(":memory:")
    store.save_curve(_curve("field", [(0.99, True)] * 900))
    router = Router(store, audit_fraction=1.0)
    assert "audit" in router.decide("retainage_rate", confidence=0.999).reason


def test_a_drifted_field_has_its_threshold_withdrawn_automatically(store):
    """Seeded degradation. A human who has to notice is a human who will not."""
    store.save_curve(_curve("field", [(0.99, True)] * 900))
    store.add_labels(_labels([(0.99, False)] * 60, basis="audit"))
    assert Router(store).withdraw_if_drifted("retainage_rate") is True
    assert Router(store).decide("retainage_rate", confidence=0.999).route == "review"


def test_audit_labels_never_promote_a_synthetic_curve(store):
    """basis='audit' is field evidence. basis='synthetic' contaminates. Neither
    rule may leak into the other."""
    labels = _labels([(0.99, True)] * 900, basis="audit")
    assert CalibrationCurve.fit(labels).state == "calibrated:field"
```

- [ ] **Step 2: Implement (GREEN)**

```python
# ledger_sense/route/router.py
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

from dataclasses import asdict, dataclass
from typing import Literal, Sequence

from ledger_sense.route.calibration import CalibrationStore

Route = Literal["auto", "review"]

# Policy figures the product STATES OUT LOUD. A THRESHOLD is derived from a
# curve, never written here — that distinction is the whole phase.
TARGET_ACCURACY_DEFAULT = 0.995
# F-49, and 2026-08-29-REVISIONS.md §11 Q4. Goes in docs/methodology.md, because
# it is a real trade between straight-through rate and drift detection.
AUDIT_FRACTION_DEFAULT = 0.05


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


class Router:
    def __init__(self, store: CalibrationStore,
                 target_accuracy: float = TARGET_ACCURACY_DEFAULT) -> None:
        self._store = store
        self._target = target_accuracy

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
                                   f"rule failures block auto-posting: {', '.join(rule_failures)}")

        if method != "DETERMINISTIC":
            return RoutingDecision(field, "review", curve.state, curve.basis, threshold,
                                   calibrated,
                                   f"claim method is {method}; only DETERMINISTIC may auto-post")

        if threshold is None:
            why = ("curve is synthetic; synthetic evidence licenses no automation"
                   if curve.state == "calibrated:synthetic"
                   else f"curve does not reach the {self._target} target accuracy")
            return RoutingDecision(field, "review", curve.state, curve.basis, None,
                                   calibrated, why)

        if confidence < threshold:
            return RoutingDecision(field, "review", curve.state, curve.basis, threshold,
                                   calibrated,
                                   f"confidence {confidence:.3f} below threshold {threshold:.3f}")

        # HOLD-BACK AUDIT — NEW (F-49).
        #
        # Labels come only from fields routed to review. The moment a field type
        # crosses its threshold, its high-confidence population stops producing
        # labels — so the curve freezes on the evidence that authorised it and can
        # NEVER detect that extraction has degraded. A producer changing its PDF
        # template is the most likely real-world failure and it would be invisible.
        #
        # A feedback loop failing silently in the optimistic direction, in the
        # product whose thesis is that optimistic silence is the industry's sin.
        if self._audit_sampler.should_hold_back(field):
            return RoutingDecision(field, "review", curve.state, curve.basis, threshold,
                                   calibrated,
                                   f"held back for audit ({self._audit_fraction:.0%} "
                                   f"of auto-eligible fields) so drift stays visible")

        return RoutingDecision(field, "auto", curve.state, curve.basis, threshold, calibrated,
                               f"confidence {confidence:.3f} at or above "
                               f"field-calibrated threshold {threshold:.3f}")

    def withdraw_if_drifted(self, field: str) -> bool:
        """Refit on audit labels; withdraw the threshold if the lower bound has
        fallen below the target. Automatic, because a human who has to notice is
        a human who will not."""
        audit_labels = self._store.labels_for(field, basis="audit")
        if len(audit_labels) < 30:
            return False
        curve = CalibrationCurve.fit(audit_labels)
        if curve.threshold_for(self._target) is None:
            self._store.withdraw_curve(field, reason="audit accuracy fell below target")
            return True
        return False
```

- [ ] **Step 3: Run test to verify it passes**

Run: `uv run pytest tests/route/test_router.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 4: Commit**

```bash
git add ledger_sense/route/router.py tests/route/test_router.py
git commit -m "feat(route): routing decisions derived from calibration state, never from a constant"
```

**DoD 4 is satisfied by this task** — confidence routing exists, is calibrated where evidence
allows, and demonstrably refuses to automate where it does not.

---

### Task 42: Labels from reviewer actions

**Files:**
- Create: `ledger_sense/route/labels.py`
- Modify: `ledger_sense/api/review.py` (Task 28)
- Test: `tests/route/test_labels.py`

> **`ledger_sense/review/actions.py` must exist — F-59.** This task imports `ReviewAction` from
> `ledger_sense.review.actions`, and Task 28 created `ledger_sense/api/review.py` with no such class and no such
> module. Phase 4 Task 28 now defines it, and this task consumes it. Check it is there before writing a line.
>
> **Labels carry `match_quality`, not `confidence` — F-46.** `ReviewAction.predicted_confidence` reads
> `Claim.match_quality`; `Claim.confidence` is `None` for every deterministic extraction and always was.
>
> **A third basis exists: `"audit"` — F-49.** Held-back fields produce audit labels, which are field evidence
> for calibration and are what makes drift detectable after auto-posting begins.

- [ ] **Step 1: Write the tests (RED)**

```python
# tests/route/test_labels.py
from datetime import datetime, timezone

import pytest

from ledger_sense.review.actions import ReviewAction      # from Task 28
from ledger_sense.route.labels import labels_from_action


def _action(kind, field="retainage_rate", confidence=0.8):
    return ReviewAction(kind=kind, field=field, claim_id="c1",
                        predicted_confidence=confidence,
                        actor="reviewer@example.test",
                        at=datetime(2026, 8, 1, tzinfo=timezone.utc))


def test_accept_produces_a_correct_label():
    labels = labels_from_action(_action("accept"), basis="field")
    assert len(labels) == 1
    assert labels[0].was_correct is True
    assert labels[0].basis == "field"


def test_edit_produces_an_incorrect_label():
    labels = labels_from_action(_action("edit"), basis="field")
    assert len(labels) == 1
    assert labels[0].was_correct is False


def test_reject_produces_no_label_at_all():
    """A rejection says the extraction was unusable, not that a specific value was wrong.

    Scoring it as incorrect would poison the curve with document-level failures that
    have nothing to do with this field's confidence.
    """
    assert labels_from_action(_action("reject"), basis="field") == []


def test_the_label_carries_the_predicted_confidence_not_the_corrected_value():
    labels = labels_from_action(_action("edit", confidence=0.42), basis="field")
    assert labels[0].predicted_confidence == 0.42


def test_the_label_carries_the_source_claim_id():
    labels = labels_from_action(_action("accept"), basis="field")
    assert labels[0].source_claim_id == "c1"


def test_a_synthetic_review_session_produces_synthetic_labels():
    labels = labels_from_action(_action("accept"), basis="synthetic")
    assert labels[0].basis == "synthetic"


def test_an_unknown_action_kind_raises_rather_than_being_ignored():
    with pytest.raises(ValueError):
        labels_from_action(_action("shrug"), basis="field")
```

Add one integration test to `tests/api/test_review.py`:

```python
def test_the_basis_is_derived_from_the_document_sensitivity_not_supplied_by_the_caller():
    """No route may exist by which a synthetic session is labelled as field evidence."""
    import inspect
    from ledger_sense.api import review
    sig = inspect.signature(review.record_action)
    assert "basis" not in sig.parameters
```

- [ ] **Step 2: Implement (GREEN)**

```python
# ledger_sense/route/labels.py
"""Reviewer actions become calibration labels.

The review queue is the training signal.  This is the whole reason the product
improves with use at zero inference cost.
"""
from __future__ import annotations

from ledger_sense.review.actions import ReviewAction
from ledger_sense.route.calibration import Basis, Label

_CORRECTNESS: dict[str, bool | None] = {
    "accept": True,
    "edit": False,
    "reject": None,   # unusable document, not a wrong field — see the test's docstring
}


def labels_from_action(action: ReviewAction, *, basis: Basis) -> list[Label]:
    if action.kind not in _CORRECTNESS:
        raise ValueError(f"unknown review action kind: {action.kind!r}")
    correct = _CORRECTNESS[action.kind]
    if correct is None:
        return []
    return [Label(field_type=action.field,
                  predicted_confidence=action.predicted_confidence,
                  was_correct=correct,
                  observed_at=action.at,
                  source_claim_id=action.claim_id,
                  basis=basis)]
```

- [ ] **Step 3: Wire it into the review API**

In `ledger_sense/api/review.py` (Task 28), after the HUMAN `Claim` is recorded, append
`labels_from_action(action, basis=derived)` to the `CalibrationStore`, where:

```python
derived: Basis = ("synthetic"
                  if document.sensitivity is Sensitivity.SYNTHETIC
                  else "field")
```

`record_action` must **not** accept a `basis` parameter. Derive it from the `Labelled`
sensitivity of the source document so no caller can promote synthetic labels to field evidence.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/route/test_labels.py tests/api/test_review.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/route/labels.py tests/route/test_labels.py ledger_sense/api/review.py tests/api/test_review.py
git commit -m "feat(route): reviewer actions become calibration labels; rejections label nothing"
```

---

### Task 43: The document inbox screen

**Files:**
- Create: `ledger_sense/api/inbox.py`
- Create: `ledger_sense/web/app/Inbox.tsx`
- Create: `ledger_sense/web/app/Inbox.test.tsx`
- Test: `tests/api/test_inbox.py`

- [ ] **Step 1: Write the tests (RED)**

```python
# tests/api/test_inbox.py
import ast
import pathlib

from fastapi.testclient import TestClient

from ledger_sense.api.app import app

client = TestClient(app)


def test_uploading_a_synthetic_document_returns_it_with_a_status(tmp_pdf):
    r = client.post("/api/inbox", files={"file": ("payapp.pdf", tmp_pdf.read_bytes())},
                    data={"sensitivity": "SYNTHETIC"})
    assert r.status_code == 201
    assert r.json()["status"] in {"queued", "extracted", "needs_review"}


def test_the_list_shows_status_confidence_band_and_flag_count(seeded_inbox):
    rows = client.get("/api/inbox").json()["items"]
    assert rows
    for row in rows:
        assert set(row) >= {"id", "filename", "status", "confidence_band", "flag_count",
                            "sensitivity", "received_at"}


def test_a_document_that_could_not_be_extracted_is_listed_not_hidden(seeded_inbox_with_scan):
    rows = client.get("/api/inbox").json()["items"]
    assert any(r["status"] == "cannot_extract" for r in rows)


def test_uploading_without_a_sensitivity_is_rejected(tmp_pdf):
    r = client.post("/api/inbox", files={"file": ("payapp.pdf", tmp_pdf.read_bytes())})
    assert r.status_code == 422


def test_a_client_confidential_upload_is_accepted_and_never_reaches_the_gateway(tmp_pdf, spy_gateway):
    r = client.post("/api/inbox", files={"file": ("real.pdf", tmp_pdf.read_bytes())},
                    data={"sensitivity": "CLIENT_CONFIDENTIAL"})
    assert r.status_code == 201
    assert spy_gateway.calls == []


def test_every_upload_passes_through_the_intake_door():
    """No handler may construct a document without calling accept_document (Task 13)."""
    src = pathlib.Path("ledger_sense/api/inbox.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    called = {n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
              for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert "accept_document" in called
```

```tsx
// ledger_sense/web/app/Inbox.test.tsx  — three assertions, not a full suite
test('a cannot_extract row is visible and labelled, not filtered out', ...)
test('the confidence band renders as text, not colour alone', ...)      // a11y
test('the sensitivity of each row is shown to the reviewer', ...)
```

- [ ] **Step 2: Implement (GREEN)**

`POST /api/inbox` calls `accept_document(payload, sensitivity, origin)` from Task 13 as its
first statement — the single labelled door. `GET /api/inbox` returns the row shape above.

The screen is a table with a status column, a confidence band rendered as a **word plus** a bar
(never colour alone), a flag count, and the sensitivity label. Sort newest first. `cannot_extract`
rows stay in the list with an explicit reason — hiding failures is how a demo lies.

- [ ] **Step 3: Run test to verify it passes**

Run: `uv run pytest tests/api/test_inbox.py -v && npx vitest run Inbox`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add ledger_sense/api/inbox.py ledger_sense/web/app/Inbox.tsx ledger_sense/web/app/Inbox.test.tsx tests/api/test_inbox.py
git commit -m "feat(ui): document inbox; failures listed rather than hidden"
```

---

### Task 44: The vendor ledger screen

**Files:**
- Create: `ledger_sense/api/vendor.py`
- Create: `ledger_sense/web/app/VendorLedger.tsx`
- Test: `tests/api/test_vendor.py`

- [ ] **Step 1: Write the tests (RED)**

```python
# tests/api/test_vendor.py
import json

import pytest


def test_the_ledger_lists_every_application_for_a_vendor_in_period_order(seeded):
    periods = client.get("/api/vendors/v1/ledger").json()["periods"]
    assert [p["period"] for p in periods] == sorted(p["period"] for p in periods)


def test_a_changed_field_between_periods_is_returned_with_its_prior_value(seeded):
    row = client.get("/api/vendors/v1/ledger").json()["periods"][1]
    assert row["changes"]["retainage_rate"] == {"from": "0.10", "to": "0.05"}


def test_an_unchanged_field_is_not_reported_as_a_change(seeded):
    row = client.get("/api/vendors/v1/ledger").json()["periods"][1]
    assert "vendor_name" not in row["changes"]


def test_money_is_serialised_as_a_decimal_string_never_a_float(seeded):
    raw = client.get("/api/vendors/v1/ledger").text
    assert '"scheduled_value": "' in raw
    json.loads(raw, parse_float=lambda _: pytest.fail("a float reached the wire"))


def test_no_full_account_number_is_ever_returned(seeded_with_bank_details):
    """I3 (Task 27) says we never store one; this proves we never emit one either."""
    raw = client.get("/api/vendors/v1/ledger").text
    assert "123456789" not in raw
    assert "****6789" in raw


def test_every_figure_links_back_to_its_source_claim(seeded):
    row = client.get("/api/vendors/v1/ledger").json()["periods"][0]
    assert row["provenance"]["scheduled_value"]["claim_id"]
    assert row["provenance"]["scheduled_value"]["page"] >= 1
```

- [ ] **Step 2: Implement (GREEN)**

The screen shows one row per pay application in period order, with a red-lined diff of any field
that changed against the prior period — the change orders, the retainage rate, the schedule-of-values
line count. Each figure links back to its `Claim` so a reviewer can jump to the page and bounding
box that produced it.

- [ ] **Step 3: Run test to verify it passes**

Run: `uv run pytest tests/api/test_vendor.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 4: Commit**

```bash
git add ledger_sense/api/vendor.py ledger_sense/web/app/VendorLedger.tsx tests/api/test_vendor.py
git commit -m "feat(ui): vendor ledger with period-over-period red-lined diff"
```

---

### Task 45: The metrics screen — publish the misses

**Files:**
- Create: `ledger_sense/api/metrics.py`
- Create: `ledger_sense/web/app/Metrics.tsx`
- Test: `tests/api/test_metrics.py`
- Test: `tests/e2e/metrics.spec.ts`

- [ ] **Step 1: Write the tests (RED)**

```python
# tests/api/test_metrics.py
def test_every_published_figure_is_a_claim_with_a_method_and_a_source(seeded):
    for _, v in client.get("/api/metrics").json().items():
        assert set(v) >= {"value", "method", "source", "observed_at"}


def test_the_straight_through_rate_is_published_with_its_misses(seeded):
    m = client.get("/api/metrics").json()
    assert "straight_through_rate" in m
    assert "misses" in m
    assert m["misses"]["value"] is not None


def test_the_calibration_state_is_published_alongside_the_rate(seeded):
    assert client.get("/api/metrics").json()["calibration_state"]["value"] in {
        "uncalibrated", "calibrated:synthetic", "calibrated:field"}


def test_touch_time_is_measured_from_open_to_final_action_not_from_upload(seeded):
    from ledger_sense.api.metrics import TOUCH_TIME_DEFINITION
    assert "opened" in TOUCH_TIME_DEFINITION and "upload" not in TOUCH_TIME_DEFINITION


def test_a_metric_computed_on_synthetic_data_says_so(seeded_synthetic):
    assert client.get("/api/metrics").json()["straight_through_rate"]["source"] == "synthetic corpus"


def test_no_metric_claims_full_automation():
    """CLAUDE.md forbids the phrase. This is the test that enforces it."""
    raw = client.get("/api/metrics").text.lower()
    for phrase in ("fully automated", "100% automated", "no review needed", "zero touch"):
        assert phrase not in raw
```

```ts
// tests/e2e/metrics.spec.ts
test('the misses render at the same font size as the rate', async ({ page }) => {
  const rate = await page.locator('[data-testid=straight-through-rate]')
    .evaluate(el => getComputedStyle(el).fontSize);
  const misses = await page.locator('[data-testid=misses]')
    .evaluate(el => getComputedStyle(el).fontSize);
  expect(misses).toBe(rate);
});
```

> The font-size test is not fussiness. A metrics screen that renders "94%" at 48px and "6 misses"
> at 11px is a screen that lies with typography. Same size, adjacent, always.

- [ ] **Step 2: Implement (GREEN)**

Every figure returned by `/api/metrics` is a `Claim`: value, `method="DERIVED"`, `source` naming
the corpus, `observed_at`, and `parents` listing the claim ids it was computed from. The screen
renders rate and misses as a pair, with the calibration state as a caption under both.

`TOUCH_TIME_DEFINITION` and `STRAIGHT_THROUGH_DEFINITION` are module-level string constants —
Task 46 asserts the methodology document quotes them verbatim.

- [ ] **Step 3: Run test to verify it passes**

Run: `uv run pytest tests/api/test_metrics.py -v && npx playwright test metrics`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add ledger_sense/api/metrics.py ledger_sense/web/app/Metrics.tsx tests/api/test_metrics.py tests/e2e/metrics.spec.ts
git commit -m "feat(ui): metrics published as claims, with misses at the same weight as the rate"
```

---

### Task 46: `docs/methodology.md` — DoD 9

**Files:**
- Create: `docs/methodology.md`
- Test: `tests/docs/test_methodology.py`

- [ ] **Step 1: Write the tests (RED)**

```python
# tests/docs/test_methodology.py
import pathlib
import re

DOC = pathlib.Path("docs/methodology.md").read_text(encoding="utf-8")


def test_the_document_exists_and_has_all_required_sections():
    for h in ("## What we measure", "## How we measure it", "## Corpus",
              "## Calibration", "## What these numbers do not mean"):
        assert h in DOC


def test_the_touch_time_definition_matches_the_code_verbatim():
    """A definition that drifts from the implementation is worse than none."""
    from ledger_sense.api.metrics import TOUCH_TIME_DEFINITION
    assert TOUCH_TIME_DEFINITION in DOC


def test_the_straight_through_definition_matches_the_code_verbatim():
    from ledger_sense.api.metrics import STRAIGHT_THROUGH_DEFINITION
    assert STRAIGHT_THROUGH_DEFINITION in DOC


def test_the_calibration_section_states_that_synthetic_curves_license_nothing():
    assert "synthetic" in DOC and "no threshold" in DOC.lower()


def test_the_corpus_section_states_the_document_count_and_that_it_is_generated():
    assert re.search(r"\b\d+\s+(synthetic|generated)\b", DOC, re.I)


def test_the_document_names_what_it_has_not_been_tested_on():
    section = DOC.split("## What these numbers do not mean")[1]
    assert len(section.strip()) > 400, "the limitations section must be substantive, not a sentence"


def test_the_document_contains_no_unresolved_placeholder():
    for token in ("TODO", "TBD", "REPLACE:", "XXX", "FIXME", "<insert"):
        assert token not in DOC, f"unresolved placeholder {token!r} in methodology.md"


def test_the_document_makes_no_affiliation_claim():
    lowered = DOC.lower()
    for phrase in ("american institute of architects", "g702", "g703"):
        assert phrase not in lowered, "no form references; see spec §11"
```

- [ ] **Step 2: Write the document (GREEN)**

Copy the two definition constants out of `ledger_sense/api/metrics.py` rather than retyping them —
the tests above assert byte-equality. Sections, in order:

1. **What we measure** — straight-through rate, touch time, flag precision, flag recall.
2. **How we measure it** — the verbatim definitions, and the clock boundaries for each.
3. **Corpus** — how many documents, generated how, with which producer profiles, and the explicit
   statement that no client document has been processed.
4. **Calibration** — the three states, the 200-label floor, and the sentence *"a synthetic curve
   yields no threshold, so every field routes to review."*
5. **What these numbers do not mean** — substantive. At minimum: they are not a field accuracy
   claim; the corpus was generated by the same person who wrote the extractor, which is a known
   optimism bias; the convention rules are sourced to specific contracts and jurisdictions named
   in `rules/sources.py` and do not generalise; and the OCR tail has been exercised on rendered
   scans, not on photographs of paper.

**Started in week 5, not written here from memory (F-57).** Phase 1 Task 11 originally specified a different
heading set (`## Baseline study`, `## Calibration basis`, `## Straight-through rate`), so this task's test failed
against the document that task created. Task 11 now opens **this** schema and fills `## How we measure it` and
the first entry of `## What these numbers do not mean`; week 6 fills `## Corpus`; week 11 fills
`## Calibration`. Written at the end, a limitations section is written from memory, which is how it ends up
shorter than the claims it qualifies.

**Six things the audit says must appear here, because each is a number a buyer might act on:**

| Must state | Why | Ref |
|---|---|---|
| The interval covers touch-time sampling variability only, and holds the loaded rate fixed | Otherwise the published interval is narrower than the real uncertainty, in the artifact whose whole purpose is defensibility | F-53 |
| The *t* value is tabulated to df 200, not collapsed to 1.96 above df 9 | The original understated the margin by up to 15.4% at real sample sizes, always in our favour | F-52 |
| Thresholds come from a **Clopper–Pearson lower bound on held-out data**, and roughly how many labels a 99.5% target needs | "Correct N times out of M" off a self-fitted curve is not a claim a controller can check | F-47, F-48 |
| The hold-back audit fraction, and that a drifted field has its threshold **withdrawn automatically** | It is a real trade between straight-through rate and drift detection, and the buyer is entitled to the number | F-49, Q4 |
| What `match_quality` is, its four inputs, and that it is **not a probability** | It is the number the routing runs on; leaving it undefined is the sin one layer down | F-46 |
| What we did to `DocNumber` and why | It changes what the bookkeeper sees in their own books and may disable a duplicate control they had | F-40, Q3 |

**The non-affiliation notice does not go in this file.** `test_the_document_makes_no_affiliation_claim` asserts
`"g702" not in DOC`, and the notice names G702®. It lives in the README and the UI footer, from
`ledger_sense/notice.py`; this document links to it.

- [ ] **Step 3: Run test to verify it passes**

Run: `uv run pytest tests/docs/test_methodology.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 4: Commit**

```bash
git add docs/methodology.md tests/docs/test_methodology.py
git commit -m "docs: methodology, with definitions asserted against the code that computes them"
```

**DoD 9 is satisfied by this task.**

---

### Task 47: The sixty-second demo — DoD 7

**Files:**
- Create: `scripts/demo.py`
- Create: `docs/demo-script.md`
- Test: `tests/test_demo.py`

- [ ] **Step 1: Write the tests (RED)**

```python
# tests/test_demo.py
import subprocess
import sys
import time


def _run(*extra):
    start = time.monotonic()
    proc = subprocess.run([sys.executable, "scripts/demo.py", "--headless", *extra],
                          capture_output=True, text=True, timeout=120)
    return proc, time.monotonic() - start


def test_the_demo_completes_in_under_sixty_seconds():
    proc, elapsed = _run()
    assert proc.returncode == 0, proc.stderr
    assert elapsed < 60, f"demo took {elapsed:.1f}s"


def test_the_demo_catches_a_duplicate_invoice():
    proc, _ = _run()
    assert "I1" in proc.stdout and "duplicate" in proc.stdout.lower()


def test_the_demo_catches_a_retainage_miscalculation():
    proc, _ = _run()
    assert ("C1" in proc.stdout or "C2" in proc.stdout) and "retainage" in proc.stdout.lower()


def test_the_demo_catches_a_stored_materials_double_count():
    proc, _ = _run()
    assert "C4" in proc.stdout and "stored" in proc.stdout.lower()


def test_the_demo_reports_what_it_missed():
    proc, _ = _run()
    assert "missed" in proc.stdout.lower()


def test_the_demo_states_it_runs_on_synthetic_data():
    proc, _ = _run()
    assert "synthetic" in proc.stdout.lower()


def test_the_demo_makes_no_claim_of_full_automation():
    proc, _ = _run()
    lowered = proc.stdout.lower()
    for phrase in ("fully automated", "no review needed", "zero touch"):
        assert phrase not in lowered


def test_the_demo_exits_non_zero_if_a_planted_error_is_not_caught():
    """The demo must fail loudly rather than narrate success it did not achieve."""
    proc, _ = _run("--disable-rule", "I1")
    assert proc.returncode != 0
```

- [ ] **Step 2: Implement (GREEN)**

`scripts/demo.py` seeds a fresh database from the synthetic corpus with three planted errors — a duplicate
invoice (I1), a retainage miscalculation (C1/C2) and a stored-materials double-count (C4) — runs extraction, runs
the rulebook, prints each catch with its rule id and citation, prints what it missed, prints the calibration
state, and exits non-zero if any planted error went uncaught.

> **Two of those three injectors did not exist — F-62.** `corpus/errors.py` returned the application
> **unchanged** for `I1_duplicate` and `C1_retainage_rate`:
>
> ```python
> if error in ("I1_duplicate", "C1_retainage_rate"):
>     return app
> ```
>
> So `test_the_demo_catches_a_duplicate_invoice` and
> `test_the_demo_catches_a_retainage_miscalculation` could not pass and **DoD 7 could not be met**. Phase 1
> Task 12 now implements both: `C1_retainage_rate` mutates the contract term the applied retainage disagrees
> with, and `generate_history(seed)` returns the pair of documents a duplicate needs — a duplicate is a
> history-level error and cannot be expressed as a mutated field.
>
> Seed the demo through `generate_history`, not `generate`, or I1 has nothing to find.

**The demo runs against the `demo` compose profile** (Phase 0 Task P1) — synthetic corpus only, ephemeral
database, no client volume mounted. A demo that can reach a client volume is a demo you cannot safely run on a
call.

`docs/demo-script.md` is the sixty seconds of spoken words that go with it. Write it in the first
person, name the three catches, name the misses, and end on the calibration state. Do not write a
sentence you would not say to an accountant who could check it.

- [ ] **Step 3: Run test to verify it passes**

Run: `uv run pytest tests/test_demo.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 4: Commit**

```bash
git add scripts/demo.py docs/demo-script.md tests/test_demo.py
git commit -m "feat(demo): sixty-second demo that reports its misses and fails if it misses more"
```

**DoD 7 is satisfied by this task.**

---

### Task 48: Close-out — DoD 10

**Files:**
- Modify: `README.md`
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `ledger_sense/web/components/Footer.tsx`
- Test: `tests/test_repo_closeout.py`

- [ ] **Step 1: Write the tests (RED)**

```python
# tests/test_repo_closeout.py
import pathlib
import re
import subprocess

import yaml

from ledger_sense.notice import NON_AFFILIATION_NOTICE

README = pathlib.Path("README.md").read_text(encoding="utf-8")

# REVISED (F-55). This was a SECOND, DIFFERENTLY WORDED hardcoded notice:
#   here:              "not affiliated with, endorsed by, or licensed by"
#   scripts/aia_guard: "not developed, endorsed, approved, sponsored by, or
#                       affiliated with"
# Both tests run in the same suite. They could not both pass unless the README
# carried both strings, which is absurd. One definition (Phase 0 Task P1),
# imported by the guard, by this test, and by the UI footer.
NOTICE = NON_AFFILIATION_NOTICE


def test_the_readme_states_what_the_product_does_and_does_not_do():
    assert "## What this does" in README
    assert "## What this does not do" in README


def test_the_readme_carries_the_non_affiliation_notice():
    assert NOTICE in README


def test_the_ui_carries_the_non_affiliation_notice_too():
    """A notice only in the README is a notice the user never reads."""
    footer = pathlib.Path("ledger_sense/web/components/Footer.tsx").read_text(encoding="utf-8")
    assert NOTICE in footer


def test_the_repo_contains_no_form_artifact():
    """Spec §11. No copyrighted form reproduced, named as a template, or shipped.

    REVISED (F-56). As written this test FAILED against the plan's own code.
    `scripts/aia_guard.py` contains `re.compile(r"g70[23]")` and a docstring
    naming the AIA — it has to, that is what it guards — and it sat outside the
    allowlist. The close-out test contradicted Task 12.

    The thing to forbid is a rendered FORM ARTIFACT, not the letters G702. The
    files that legitimately name the form are the guard, the notice, their tests,
    and the design record.
    """
    bad = re.compile(r"\bG70[23]\b|american institute of architects", re.I)
    allowed = {
        "docs/superpowers",              # the design record explains the exclusion
        "CLAUDE.md",
        "scripts/aia_guard.py",          # the guard must name what it guards
        "ledger_sense/notice.py",        # the notice names the trademarks
        "tests/test_aia_guard.py",
        "tests/test_repo_closeout.py",   # this file
        "README.md",                     # carries the notice, per spec §11
    }
    for p in pathlib.Path(".").rglob("*"):
        if not p.is_file() or ".git" in p.parts or "node_modules" in p.parts:
            continue
        if any(str(p).replace("\\", "/").startswith(a) for a in allowed):
            continue
        if p.suffix in {".md", ".py", ".ts", ".tsx", ".json", ".yml", ".yaml", ".txt"}:
            assert not bad.search(p.read_text(encoding="utf-8", errors="ignore")), p


def test_no_rendered_form_artifact_of_any_kind_is_committed():
    """The claim that actually matters: no image, PDF or drawing of the form."""
    from scripts.aia_guard import guard
    assert guard(".") == []


def test_compose_defines_both_client_and_demo_profiles():
    compose = yaml.safe_load(pathlib.Path("docker-compose.yml").read_text(encoding="utf-8"))
    profiles = {p for svc in compose["services"].values() for p in svc.get("profiles", [])}
    assert {"client", "demo"} <= profiles


def test_the_demo_profile_never_mounts_a_client_volume():
    compose = yaml.safe_load(pathlib.Path("docker-compose.yml").read_text(encoding="utf-8"))
    for svc in compose["services"].values():
        if "demo" in svc.get("profiles", []):
            assert not any("client" in str(v) for v in svc.get("volumes", []))


def test_env_example_contains_no_real_secret():
    text = pathlib.Path(".env.example").read_text(encoding="utf-8")
    for line in text.splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        _, _, value = line.partition("=")
        assert value.strip() in {"", "changeme"} or value.strip().startswith("<"), line


def test_the_licence_is_apache_2_0():
    assert "Apache License" in pathlib.Path("LICENSE").read_text(encoding="utf-8")


@pytest.mark.slow
def test_a_clean_clone_passes_its_own_test_suite(tmp_path):
    """The last thing that breaks is the thing that only ran on your laptop.

    REVISED (F-65b). The original ran `pytest` in the WORKING TREE from inside
    the suite — doubling CI time and testing nothing whatsoever about a clone. It
    would pass with an uncommitted file the clone does not have, which is exactly
    the failure it claims to catch.
    """
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--depth", "1", ".", str(clone)], check=True,
                   capture_output=True)
    subprocess.run(["uv", "sync", "--all-extras"], cwd=clone, check=True,
                   capture_output=True)
    r = subprocess.run(
        ["uv", "run", "pytest", "-q", "-m", "not slow",
         "--ignore=tests/writeback/test_qbo_sandbox.py"],
        cwd=clone, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-4000:]


def test_the_licence_audit_still_passes_at_close_out():
    r = subprocess.run(["uv", "run", "python", "scripts/licence_audit.py"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
```

> **Note on `test_a_clean_clone_passes_its_own_test_suite`:** it ignores its own file to avoid
> recursing into itself. Run it as the final gate, not in the ordinary loop — mark it
> `@pytest.mark.slow` and exclude `slow` from the default `pytest` invocation.

- [ ] **Step 2: Implement (GREEN)**

README gains a scope statement — what this does, what it does not do, what it has never been
tested on — plus the non-affiliation notice, the compose quickstart for both profiles, and a link
to `docs/methodology.md`. The notice goes in the UI footer as well, because a notice only in the
README is a notice nobody reads.

- [ ] **Step 3: Run test to verify it passes**

Run: `uv run pytest tests/test_repo_closeout.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 4: Commit**

```bash
git add README.md docker-compose.yml .env.example ledger_sense/web/components/Footer.tsx tests/test_repo_closeout.py
git commit -m "chore: close-out — scope statement, non-affiliation notice, compose profiles"
```

**DoD 10 is satisfied by this task.**

---

## Phase 6 exit criteria

- [ ] `uv run pytest -q` green across the whole repository
- [ ] `test_a_synthetic_curve_still_routes_everything_to_review` passes — **the test this phase exists for**
- [ ] `test_no_derived_threshold_is_hard_coded_anywhere_in_the_router` passes
- [ ] `test_every_upload_passes_through_the_intake_door` passes
- [ ] `test_no_full_account_number_is_ever_returned` passes
- [ ] `test_no_metric_claims_full_automation` passes
- [ ] Metrics screen renders misses at the same font size as the rate (Playwright)
- [ ] `docs/methodology.md` definitions assert equal to the code constants
- [ ] Demo runs in under sixty seconds, catches three planted errors, reports its misses
- [ ] `axe-core` gate from Task 33 still green with the three new screens added
- [ ] `uv run mypy --strict ledger_sense` green
- [ ] `uv run python scripts/licence_audit.py --check-lockfile uv.lock` green — and it **reads the real
      environment**, which as originally written it did not (F-33: the module had no `__main__`, so the job
      exited 0 unconditionally)

**Audit regressions that must be green (`2026-08-29-REVISIONS.md`):**

- [ ] `test_two_hundred_labels_cannot_support_the_target_accuracy` and
      `test_no_min_labels_constant_survives_anywhere_in_the_module` — **F-47.** The phase built to refuse
      unearned thresholds was emitting one, from a constant traced to nothing
- [ ] `test_the_threshold_is_derived_on_held_out_data` and `test_a_curve_fitted_on_noise_yields_no_threshold` —
      F-48
- [ ] `test_a_drifted_field_has_its_threshold_withdrawn_automatically` — **F-49.** Without the hold-back sample
      the curve freezes on the evidence that authorised it and drift is invisible
- [ ] `test_every_claim_carries_a_match_quality_score` (Phase 2) green — **F-46**, or this phase has no input
      at all and DoD 4 is machinery with nothing feeding it
- [ ] `test_the_refusal_says_why_in_words` — a greyed meter with no explanation is what this programme exists
      to correct
- [ ] `test_the_same_guard_covers_extract_where_a_floor_was_hiding` — F-24
- [ ] `test_the_repo_contains_no_form_artifact` passes **against `scripts/aia_guard.py`** — F-56, where the
      close-out test contradicted Task 12's own code
- [ ] One notice string, imported from `ledger_sense/notice.py` — F-55
- [ ] `docs/methodology.md` uses **one** heading schema, opened in week 5 — F-57
- [ ] Every screen under `ledger_sense/web/` — F-58, two frontend trees for one application

---

## Week 12 close-out — the honest table

Fill this in at the end of week 12 with what is actually true, not what was planned.

Numbered as brief `02` §12 numbers them, so the two documents can be read side by side.

| # | Definition of Done item | Satisfied by | Expected state at week 12 |
|---|---|---|---|
| 1 | Baseline study, print-ready, stated method and interval | Tasks 10–11, 13b | Met — and the interval now states what it does **not** cover (F-53) |
| 2 | Deterministic extraction end to end, no model | Tasks 14–21 | Met, **across five layout variants**, not only our own (F-14) |
| 3 | Every validation rule, tripping fixture and clean control | Tasks 22–27 | Met — **sourcing is only as good as the confirmations entered in `rules/sources.py`**, and `confirmed_at` must be a real date |
| 4 | Confidence routing live, calibration state visible, thresholds from a curve | Tasks 40–42 | Met, at `calibrated:synthetic`. **Nothing auto-posts.** The threshold is now derived from a lower bound on held-out data, not read off a self-fitted curve (F-47, F-48) |
| 5 | Fifty exceptions cleared keyboard-only | Task 30 | Met |
| 6 | QBO sandbox writeback: idempotency, closed-period refusal, reversal | Tasks 34–39 | Met, sandbox only — and reversal now leaves a ledger a second write can use (F-39) |
| 7 | Synthetic corpus generator; the demo runs on it | Tasks 12, 47 | Met — required implementing the two error injectors that returned the document unchanged (F-62) |
| 8 | Boundary enforcement proven | Task 13 | Met **if `bok-core` shipped**; otherwise the upload path is deferred with it (F-60) |
| 9 | `docs/methodology.md` — baseline method, calibration basis, straight-through rate with misses | Tasks 11, 46 | Met, one heading schema, written incrementally (F-57) |
| 10 | Non-affiliation notice present; no form artifacts | Tasks 12, 48 | Met, from one imported constant (F-55, F-56) |
| 11 | **First paying client, or a signed design-partner agreement with a document corpus** | half a day per week from week 5 | **Unknown — the one no amount of code satisfies** |

**Not in the original table, and now part of close-out:** authentication and tenancy on every route (Phase 0 P2),
resource caps on document parsing (P3), encryption at rest and a retention policy that actually deletes (P5),
and the `gitleaks` / `pip-audit` / SBOM gates the hard constraints already required (P1). None of these had an
implementing task; all four are conditions of accepting a client's first document.

### On DoD 11

Half a day a week from week 5 is roughly forty hours of outreach. The week-5 baseline study
(Task 13b) is the asset that makes the ask concrete rather than a cold request for time.

If it is unmet at week 12, say so plainly, report how many hours actually went into it against the
~40 planned, and treat that number as the finding — not as the shortfall to explain away. Ten
conversations that did not happen is information about the market or about the outreach, and both
are worth more than a metrics screen with a bigger number on it.

### The day a design partner signs

From the spec §9, restated here because this is the phase where it lands:

1. Their documents arrive as `CLIENT_CONFIDENTIAL`. The intake door (Task 13) already handles it;
   nothing in the pipeline changes, and nothing reaches the model gateway.
2. Review sessions on those documents produce `basis="field"` labels automatically — Task 42
   derives the basis from sensitivity, so no code change is needed.
3. At 200 field labels for a field type, `CalibrationCurve.fit` returns `calibrated:field` and the
   router starts issuing thresholds. **This is the only moment the product becomes capable of
   auto-posting, and it happens because of evidence, not because of a decision.**
4. The convention rules in `rules/sources.py` get their `REPLACE:` placeholders filled with the
   partner's actual contract terms and jurisdiction, and the tests that currently fail on those
   placeholders start passing.
5. `docs/methodology.md` gains a field section alongside the synthetic one.

Nothing in weeks 5–12 blocks on any of that, and nothing in weeks 5–12 has to be rewritten when it
arrives. That was the design goal, and this table is where it is checked.

---

**Weeks 5–12 end here.** Six phase documents, forty-eight tasks, eleven definition-of-done items,
and one number — DoD 11 — that only the calendar can settle.
