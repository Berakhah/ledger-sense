# Ledger Sense — Phase 6: Routing, Calibration and Close-Out (Weeks 11–12)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the confidence-routing machinery honestly — calibrated where there is evidence, refusing to auto-post where there is not — then close weeks 5–12 with the three remaining screens, the methodology document, the sixty-second demo and a repository someone else can run.

**Architecture:** Isotonic calibration fitted per field type from reviewer labels, with three explicit states (`uncalibrated`, `calibrated:synthetic`, `calibrated:field`). Only `calibrated:field` yields a routing threshold. Everything else routes to review. The router has no derived-from-nothing constant anywhere in it, and a test proves that.

**Tech Stack:** Python stdlib only for the calibration maths (PAVA, ~15 lines), SQLite for labels and curves, FastAPI, React, Playwright.

**Spec:** `docs/superpowers/specs/2025-03-11-ledger-sense-design.md` §7, §8, §12
**Parent plan:** `2025-03-11-ledger-sense-weeks-5-12.md` — its Global Constraints and File Structure apply here in full.

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

### The three states, and what each is worth

| State | How it arises | `threshold_for()` | What may be said to a buyer |
|---|---|---|---|
| `uncalibrated` | fewer than 200 labels of any kind | `None` | "Confidence is displayed, not acted on." |
| `calibrated:synthetic` | ≥200 labels, any of them from the generated corpus | `None` | "The machinery is built and exercised. It has never seen a real document." |
| `calibrated:field` | ≥200 labels, all from real reviewed documents | a float | "At the threshold shown, this field type was correct N times out of M." |

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
    CalibrationCurve, CalibrationStore, Label, fit_isotonic,
    MIN_LABELS_FOR_FIELD, UnknownBasisError,
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


def test_min_labels_constant_is_two_hundred():
    assert MIN_LABELS_FOR_FIELD == 200


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

MIN_LABELS_FOR_FIELD = 200
_VALID_BASES: frozenset[str] = frozenset({"synthetic", "field"})


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
        if self.n < MIN_LABELS_FOR_FIELD:
            return "uncalibrated"
        return "calibrated:field" if self.basis == "field" else "calibrated:synthetic"

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
        """The lowest raw confidence whose calibrated probability meets the target.

        Returns ``None`` unless this curve was fitted from field labels.  This is the
        refusal the phase exists for: synthetic evidence licenses nothing.
        """
        if self.state != "calibrated:field":
            return None
        for x, k in zip(self.xs, self.knots):
            if k >= target_accuracy:
                return x
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
    permitted = {0.0, 1.0, TARGET_ACCURACY_DEFAULT}
    offenders = [n.value for n in ast.walk(tree)
                 if isinstance(n, ast.Constant) and isinstance(n.value, float)
                 and n.value not in permitted]
    assert offenders == [], f"literal thresholds found in router.py: {offenders}"
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

# Policy figure from the brief.  A THRESHOLD is derived from a curve, never written here.
TARGET_ACCURACY_DEFAULT = 0.995


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

        return RoutingDecision(field, "auto", curve.state, curve.basis, threshold, calibrated,
                               f"confidence {confidence:.3f} at or above "
                               f"field-calibrated threshold {threshold:.3f}")
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
- Create: `ui/src/screens/Inbox.tsx`
- Create: `ui/src/screens/Inbox.test.tsx`
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
// ui/src/screens/Inbox.test.tsx  — three assertions, not a full suite
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

Run: `uv run pytest tests/api/test_inbox.py -v && cd ui && npm test -- Inbox`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add ledger_sense/api/inbox.py ui/src/screens/Inbox.tsx ui/src/screens/Inbox.test.tsx tests/api/test_inbox.py
git commit -m "feat(ui): document inbox; failures listed rather than hidden"
```

---

### Task 44: The vendor ledger screen

**Files:**
- Create: `ledger_sense/api/vendor.py`
- Create: `ui/src/screens/VendorLedger.tsx`
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
git add ledger_sense/api/vendor.py ui/src/screens/VendorLedger.tsx tests/api/test_vendor.py
git commit -m "feat(ui): vendor ledger with period-over-period red-lined diff"
```

---

### Task 45: The metrics screen — publish the misses

**Files:**
- Create: `ledger_sense/api/metrics.py`
- Create: `ui/src/screens/Metrics.tsx`
- Test: `tests/api/test_metrics.py`
- Test: `ui/e2e/metrics.spec.ts`

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
// ui/e2e/metrics.spec.ts
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

Run: `uv run pytest tests/api/test_metrics.py -v && cd ui && npx playwright test metrics`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add ledger_sense/api/metrics.py ui/src/screens/Metrics.tsx tests/api/test_metrics.py ui/e2e/metrics.spec.ts
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

`scripts/demo.py` seeds a fresh SQLite database from the synthetic corpus with three planted
errors — a duplicate invoice (I1), a retainage miscalculation (C1/C2) and a stored-materials
double-count (C4) — runs extraction, runs the rulebook, prints each catch with its rule id and
citation, prints what it missed, prints the calibration state, and exits non-zero if any planted
error went uncaught.

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
- Modify: `ui/src/components/Footer.tsx`
- Test: `tests/test_repo_closeout.py`

- [ ] **Step 1: Write the tests (RED)**

```python
# tests/test_repo_closeout.py
import pathlib
import re
import subprocess

import yaml

README = pathlib.Path("README.md").read_text(encoding="utf-8")
NOTICE = "not affiliated with, endorsed by, or licensed by"


def test_the_readme_states_what_the_product_does_and_does_not_do():
    assert "## What this does" in README
    assert "## What this does not do" in README


def test_the_readme_carries_the_non_affiliation_notice():
    assert NOTICE in README


def test_the_ui_carries_the_non_affiliation_notice_too():
    """A notice only in the README is a notice the user never reads."""
    footer = pathlib.Path("ui/src/components/Footer.tsx").read_text(encoding="utf-8")
    assert NOTICE in footer


def test_the_repo_contains_no_form_artifact():
    """Spec §11. No copyrighted form reproduced, named as a template, or shipped."""
    bad = re.compile(r"\bG70[23]\b|american institute of architects", re.I)
    allowed = {"docs/superpowers", "CLAUDE.md"}   # the design record may discuss why we exclude it
    for p in pathlib.Path(".").rglob("*"):
        if not p.is_file() or ".git" in p.parts or "node_modules" in p.parts:
            continue
        if any(str(p).replace("\\", "/").startswith(a) for a in allowed):
            continue
        if p.suffix in {".md", ".py", ".ts", ".tsx", ".json", ".yml", ".yaml", ".txt"}:
            assert not bad.search(p.read_text(encoding="utf-8", errors="ignore")), p


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


def test_a_clean_clone_passes_its_own_test_suite():
    """The last thing that breaks is the thing that only ran on your laptop."""
    r = subprocess.run(["uv", "run", "pytest", "-q", "--ignore=tests/test_repo_closeout.py"],
                       capture_output=True, text=True)
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
git add README.md docker-compose.yml .env.example ui/src/components/Footer.tsx tests/test_repo_closeout.py
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
- [ ] `uv run python scripts/licence_audit.py` green

---

## Week 12 close-out — the honest table

Fill this in at the end of week 12 with what is actually true, not what was planned.

| # | Definition of Done item | Satisfied by | Expected state at week 12 |
|---|---|---|---|
| 1 | Deterministic extraction with per-field provenance | Tasks 14–19 | Met |
| 2 | Degraded-mode behaviour proven on scans | Tasks 20–21 | Met |
| 3 | Rulebook with sourced conventions | Tasks 22–27 | Met — **sourcing is only as good as the confirmations entered in `rules/sources.py`** |
| 4 | Confidence routing, calibrated | Tasks 40–42 | Met, at `calibrated:synthetic`. **Nothing auto-posts.** |
| 5 | Keyboard-only review, zero mouse events | Task 30 | Met |
| 6 | QBO sandbox writeback, idempotent, reversible | Tasks 34–39 | Met, sandbox only |
| 7 | Sixty-second demo | Task 47 | Met |
| 8 | Accessibility gate | Task 33 | Met |
| 9 | `docs/methodology.md` | Task 46 | Met |
| 10 | Repository someone else can run | Task 48 | Met |
| 11 | Ten conversations with construction accountants | half a day per week from week 5 | **Unknown — this is the one no amount of code satisfies** |

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
