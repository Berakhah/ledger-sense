# Rule Engine Unverifiable-Money Checkpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a shared checkpoint to `ledger_sense/rules/engine.py` — `run_rules()` and `unverifiable_money_fields()` — so no caller can mistake "the arithmetic rules found nothing wrong" for "the data was real" when extraction silently defaulted a Money field to zero.

**Architecture:** Two pure functions added to the currently-empty `ledger_sense/rules/engine.py` (today it holds only the `Finding` NamedTuple). `run_rules(app, contract, *, rule_classes=None)` replaces the `ALL_RULES = [...]` list that gets hand-built at every call site by iterating the existing `registered_rules()` registry and calling each rule's `.fn`. `unverifiable_money_fields(app)` duck-types over `app.provenance["_unextractable"]` (a list of objects carrying a `.field` string — populated by the not-yet-merged extraction pipeline on the `phase-2-extraction` branch) and returns which Money-relevant fields were never really extracted. Neither function touches the rule bodies in `foot.py`, `lines.py`, or `payment.py` — those keep answering "is the data internally consistent," which stays true even on a defaulted `Money("0.00")`. The new checkpoint answers the separate question "was that data real." A caller trusts `run_rules()` output only where `unverifiable_money_fields()` is empty.

**Tech Stack:** Python 3.12, pydantic v2 (`PayApplication.provenance: dict[str, object]`), pytest, `hypothesis` (existing property suite, not modified in logic — only its `ALL_RULES` wiring).

**Spec:** No separate spec document. This plan's design rationale (why a shared checkpoint over patching each rule, why duck-typing, why the four summary fields plus `"lines"`/`"line[...]"` and not the header scalars) is recorded above and in Task 2, arrived at after evaluating three approaches (patch `rule_a1` only; duplicate the guard into all five arithmetic rules; one shared checkpoint) against the live code in `ledger_sense/rules/` and `ledger_sense/domain/models.py`.

## Global Constraints

- `uv run mypy --strict ledger_sense tests` and `uv run ruff check .` must both pass on every commit — no `# type: ignore`, no `Any` leaking into a public return type.
- No floats anywhere in the money path (Never-rule #2). All money literals in new tests go through `money("...")`, never a bare numeric literal.
- `PayApplication.provenance` is typed `dict[str, object]` (domain/models.py:83) — treat every value read from it as untyped until narrowed with `isinstance`.
- Every new function ships with a test that fails without it and passes with it (RED-GREEN, no exceptions).
- `run_rules`/`unverifiable_money_fields` must not import anything from `ledger_sense.extract.*` or `ledger_sense.provenance` (the `UnextractableField` type lives there, on the unmerged `phase-2-extraction` branch) — duck-type on `.field` instead, so this plan builds and tests cleanly on `main` today.
- `uv run pytest -q` must show 0 failures after every task, including the pre-existing 197 tests.

---

### Task 1: `run_rules()` — one call site for every registered rule

**Files:**
- Modify: `ledger_sense/rules/engine.py`
- Test: `tests/rules/test_engine.py` (new file)

**Interfaces:**
- Consumes: `ledger_sense.rules.registry.registered_rules() -> dict[str, RegisteredRule]` and `RegisteredRule.{rule_id: str, rule_class: RuleClass, fn: Callable[..., list[Any]], source: RuleSource | None}` (both already exist, unchanged). `RuleClass = Literal["arithmetic", "integrity", "convention"]` (`ledger_sense/rules/registry.py:14`).
- Produces: `run_rules(app: PayApplication, contract: Contract, *, rule_classes: Iterable[RuleClass] | None = None) -> list[Finding]`. Later tasks and any future caller (Phase 4 reconciliation, Phase 6 router glue) import this instead of re-building an `ALL_RULES` list.

- [ ] **Step 1: Write the failing tests**

Create `tests/rules/test_engine.py`:

```python
from datetime import date
from decimal import Decimal

from ledger_sense.domain.models import Contract, PayApplication
from ledger_sense.domain.money import RoundingPolicy, money
from ledger_sense.rules.engine import Finding, run_rules
from ledger_sense.rules.registry import register


def _contract() -> Contract:
    return Contract(id="C-1", original_sum=money("1000.00"),
                    rounding=RoundingPolicy(2, 4), retainage_rate=Decimal("0.10"),
                    retainage_steps=[], jurisdiction=None)


def _app() -> PayApplication:
    return PayApplication(contract_id="C-1", application_no=1, period_to=date(2026, 8, 31),
                          lines=[], change_orders_net=money("0.00"),
                          retainage_total=money("0.00"), previous_payments=money("0.00"),
                          total_completed_and_stored=money("0.00"))


class _Source:
    authority: str = "test fixture"
    citation: str = "n/a"
    confirmed_by: str | None = "T. Ester"
    confirmed_at: date | None = date(2026, 9, 3)


def test_run_rules_collects_findings_from_every_registered_rule() -> None:
    @register("TEST_ENGINE_A", "arithmetic")
    def rule_a(app: PayApplication, contract: Contract) -> list[Finding]:
        return [Finding("TEST_ENGINE_A", "x", "always fires", money("0.00"), money("1.00"))]

    @register("TEST_ENGINE_B", "arithmetic")
    def rule_b(app: PayApplication, contract: Contract) -> list[Finding]:
        return [Finding("TEST_ENGINE_B", "y", "always fires", money("0.00"), money("2.00"))]

    findings = run_rules(_app(), _contract())
    fired = {f.rule_id for f in findings}
    assert "TEST_ENGINE_A" in fired
    assert "TEST_ENGINE_B" in fired


def test_run_rules_returns_empty_list_when_no_rule_fires() -> None:
    @register("TEST_ENGINE_C", "arithmetic")
    def rule_c(app: PayApplication, contract: Contract) -> list[Finding]:
        return []

    findings = run_rules(_app(), _contract())
    assert not any(f.rule_id == "TEST_ENGINE_C" for f in findings)


def test_run_rules_filters_by_rule_classes() -> None:
    @register("TEST_ENGINE_ARITH", "arithmetic")
    def rule_arith(app: PayApplication, contract: Contract) -> list[Finding]:
        return [Finding("TEST_ENGINE_ARITH", "x", "arith fires", money("0.00"), money("1.00"))]

    @register("TEST_ENGINE_CONV", "convention", source=_Source())
    def rule_conv(app: PayApplication, contract: Contract) -> list[Finding]:
        return [Finding("TEST_ENGINE_CONV", "y", "conv fires", money("0.00"), money("1.00"))]

    arithmetic_only = run_rules(_app(), _contract(), rule_classes={"arithmetic"})
    fired = {f.rule_id for f in arithmetic_only}
    assert "TEST_ENGINE_ARITH" in fired
    assert "TEST_ENGINE_CONV" not in fired

    both = run_rules(_app(), _contract(), rule_classes={"arithmetic", "convention"})
    fired_both = {f.rule_id for f in both}
    assert "TEST_ENGINE_ARITH" in fired_both
    assert "TEST_ENGINE_CONV" in fired_both
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/rules/test_engine.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_rules' from 'ledger_sense.rules.engine'`

- [ ] **Step 3: Implement `run_rules`**

Replace the contents of `ledger_sense/rules/engine.py` with:

```python
from collections.abc import Iterable
from typing import NamedTuple

from ledger_sense.domain.models import Contract, PayApplication
from ledger_sense.domain.money import Money
from ledger_sense.rules.registry import RuleClass, registered_rules


class Finding(NamedTuple):
    rule_id: str
    field: str
    message: str
    expected: Money
    actual: Money


def run_rules(
    app: PayApplication,
    contract: Contract,
    *,
    rule_classes: Iterable[RuleClass] | None = None,
) -> list[Finding]:
    """Run every registered rule (or only the given rule classes) and collect findings.

    Replaces the `ALL_RULES = [...]` list that was being hand-built at each call
    site. A rule that trusts a defaulted Money("0.00") still reports "consistent"
    here — this function answers "did the rules agree", not "was the data real".
    See `unverifiable_money_fields` for the second question.
    """
    wanted = None if rule_classes is None else frozenset(rule_classes)
    findings: list[Finding] = []
    for rule in registered_rules().values():
        if wanted is not None and rule.rule_class not in wanted:
            continue
        findings.extend(rule.fn(app, contract))
    return findings
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/rules/test_engine.py -v`
Expected: PASS (3 passed)

Also run the full suite to confirm nothing else broke:

Run: `uv run pytest -q`
Expected: all prior tests still pass, plus the 3 new ones (200 passed, 1 skipped)

- [ ] **Step 5: Type-check and lint**

Run: `uv run mypy --strict ledger_sense tests`
Expected: no errors

Run: `uv run ruff check .`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add ledger_sense/rules/engine.py tests/rules/test_engine.py
git commit -m "feat(rules): add run_rules() as the single rule-execution entry point"
```

---

### Task 2: `unverifiable_money_fields()` — the "was that data real" checkpoint

**Files:**
- Modify: `ledger_sense/rules/engine.py`
- Test: `tests/rules/test_engine.py` (append to the file from Task 1)

**Interfaces:**
- Consumes: `PayApplication.provenance: dict[str, object]` (`ledger_sense/domain/models.py:83`). Duck-types each entry in `provenance["_unextractable"]` for a `.field: str` attribute — does not import `UnextractableField` (it lives in `ledger_sense/extract/assemble.py` on the unmerged `phase-2-extraction` branch).
- Produces: `unverifiable_money_fields(app: PayApplication) -> tuple[str, ...]`. A future caller combines this with `run_rules()`: **`run_rules()` output is trustworthy only where `unverifiable_money_fields()` is empty.**

- [ ] **Step 1: Write the failing tests**

Append to `tests/rules/test_engine.py`:

```python
from typing import NamedTuple

from ledger_sense.rules.engine import unverifiable_money_fields


class _Unextractable(NamedTuple):
    """Stand-in for extract.assemble.UnextractableField (unmerged branch).

    unverifiable_money_fields() duck-types on `.field` precisely so it does not
    need to import that type — any object with a `.field: str` attribute works.
    """
    field: str
    reason: str = "not found on page"


def test_unverifiable_money_fields_is_empty_with_no_provenance_key() -> None:
    app = _app()
    assert unverifiable_money_fields(app) == ()


def test_unverifiable_money_fields_is_empty_when_unextractable_list_is_empty() -> None:
    app = _app().model_copy(update={"provenance": {"_unextractable": []}})
    assert unverifiable_money_fields(app) == ()


def test_unverifiable_money_fields_excludes_header_scalars() -> None:
    """Arithmetic rules never read contract_id/application_no/period_to (F-15) —
    losing them shouldn't taint a footing check."""
    app = _app().model_copy(update={"provenance": {"_unextractable": [
        _Unextractable("contract_id"),
        _Unextractable("application_no"),
        _Unextractable("period_to"),
    ]}})
    assert unverifiable_money_fields(app) == ()


def test_unverifiable_money_fields_includes_the_lines_grid() -> None:
    app = _app().model_copy(update={"provenance": {"_unextractable": [
        _Unextractable("lines", "continuation grid could not be reconstructed"),
    ]}})
    assert unverifiable_money_fields(app) == ("lines",)


def test_unverifiable_money_fields_includes_a_per_line_cell() -> None:
    app = _app().model_copy(update={"provenance": {"_unextractable": [
        _Unextractable("line[3].stored", "value did not parse as money"),
    ]}})
    assert unverifiable_money_fields(app) == ("line[3].stored",)


def test_unverifiable_money_fields_includes_the_four_summary_totals() -> None:
    app = _app().model_copy(update={"provenance": {"_unextractable": [
        _Unextractable("total_completed_and_stored"),
        _Unextractable("retainage_total"),
        _Unextractable("previous_payments"),
        _Unextractable("change_orders_net"),
        _Unextractable("contract_id"),  # excluded, mixed in on purpose
    ]}})
    assert unverifiable_money_fields(app) == (
        "total_completed_and_stored", "retainage_total",
        "previous_payments", "change_orders_net",
    )


def test_unverifiable_money_fields_deduplicates_preserving_order() -> None:
    app = _app().model_copy(update={"provenance": {"_unextractable": [
        _Unextractable("lines"),
        _Unextractable("lines"),
    ]}})
    assert unverifiable_money_fields(app) == ("lines",)


def test_a1_silent_pass_is_caught_by_the_checkpoint_not_by_a1() -> None:
    """The bug this plan exists to fix: A1 correctly reports the (fabricated)
    data as internally consistent when both the grid and the summary total
    failed to extract and were defaulted. A1 alone can't tell the difference
    between "nothing to bill" and "nothing was extracted" — that is exactly
    why this is a separate checkpoint, not a change to A1 itself.
    """
    from ledger_sense.rules.arithmetic import foot  # noqa: F401  (registers A1)

    app = _app().model_copy(update={"provenance": {"_unextractable": [
        _Unextractable("lines", "continuation grid produced no data rows"),
        _Unextractable("total_completed_and_stored", "label not found on page"),
    ]}})

    findings = run_rules(app, _contract(), rule_classes={"arithmetic"})
    assert not any(f.rule_id == "A1" for f in findings), "A1 should stay silent here"

    unverifiable = unverifiable_money_fields(app)
    assert unverifiable == ("lines", "total_completed_and_stored")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/rules/test_engine.py -v`
Expected: FAIL — `ImportError: cannot import name 'unverifiable_money_fields' from 'ledger_sense.rules.engine'`

- [ ] **Step 3: Implement `unverifiable_money_fields`**

Add to `ledger_sense/rules/engine.py` (after `run_rules`):

```python
_SUMMARY_MONEY_FIELDS = frozenset({
    "total_completed_and_stored", "retainage_total",
    "previous_payments", "change_orders_net",
})


def unverifiable_money_fields(app: PayApplication) -> tuple[str, ...]:
    """Which Money-relevant fields could not really be extracted.

    Reads `app.provenance["_unextractable"]` (a list of objects carrying a
    `.field: str`, populated by the extraction pipeline) and returns the
    subset that feeds arithmetic rules: the continuation-sheet grid, any
    per-line cell, or one of the four summary Money totals. Header scalars
    (contract_id, application_no, period_to) are excluded — no arithmetic
    rule reads them, so their loss shouldn't taint a footing check.

    `run_rules()` answers "did the rules agree". This answers "was the data
    real". Trust the former only where this returns an empty tuple.
    """
    entries = app.provenance.get("_unextractable", [])
    if not isinstance(entries, list):
        return ()
    fields: list[str] = []
    for entry in entries:
        field = getattr(entry, "field", None)
        if not isinstance(field, str):
            continue
        if field == "lines" or field.startswith("line[") or field in _SUMMARY_MONEY_FIELDS:
            fields.append(field)
    return tuple(dict.fromkeys(fields))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/rules/test_engine.py -v`
Expected: PASS (11 passed)

Run: `uv run pytest -q`
Expected: all tests pass (208 passed, 1 skipped)

- [ ] **Step 5: Type-check and lint**

Run: `uv run mypy --strict ledger_sense tests`
Expected: no errors

Run: `uv run ruff check .`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add ledger_sense/rules/engine.py tests/rules/test_engine.py
git commit -m "feat(rules): add unverifiable_money_fields() so a fabricated zero can't pass as verified"
```

---

### Task 3: Replace the property suite's hand-built `ALL_RULES` with `run_rules()`

**Files:**
- Modify: `tests/property/test_arithmetic_invariants.py`

**Interfaces:**
- Consumes: `run_rules(app, contract, *, rule_classes=None) -> list[Finding]` from Task 1.
- Produces: nothing new — this task proves `run_rules()` is a real substitute for the pattern it was written to replace, on the one hand-duplicated call site that exists on `main` today. (`tests/test_degraded_mode.py`'s own copy of this pattern lives only on the unmerged `phase-2-extraction` branch and is out of scope here — flag it for whoever rebases that branch onto this change.)

- [ ] **Step 1: Confirm current behavior before changing it**

Run: `uv run pytest tests/property/test_arithmetic_invariants.py -v`
Expected: PASS (3 passed) — this is the baseline the refactor must not break.

- [ ] **Step 2: Replace the `ALL_RULES` list and the rule-firing test with `run_rules()`**

In `tests/property/test_arithmetic_invariants.py`, replace:

```python
from ledger_sense.rules.arithmetic.foot import rule_a1
from ledger_sense.rules.arithmetic.lines import rule_a2, rule_a3, rule_a3b, rule_a4, rule_a4b
from ledger_sense.rules.arithmetic.payment import (
    current_payment_due,
    rule_a5,
    rule_a6,
    rule_a7,
    rule_a8,
    total_earned_less_retainage,
)
from tests.property.strategies import consistent_pay_applications

# A9 is absent: it is a convention rule and ships in Phase 3 Task 24, which adds it
# to this list (F-04). A property suite that imports it here would not import at all.
ALL_RULES = [rule_a1, rule_a2, rule_a3, rule_a3b, rule_a4, rule_a4b, rule_a5, rule_a6,
            rule_a7, rule_a8]


@given(consistent_pay_applications())
@settings(max_examples=300)
def test_no_rule_fires_on_a_consistent_application(pair: tuple[Contract, PayApplication]) -> None:
    contract, app = pair
    for rule in ALL_RULES:
        assert rule(app, contract) == [], f"{rule.__name__} false-positived"
```

with:

```python
from ledger_sense.rules.arithmetic import foot, lines  # noqa: F401  (registration side effect)
from ledger_sense.rules.arithmetic.payment import current_payment_due, total_earned_less_retainage
from ledger_sense.rules.engine import run_rules
from tests.property.strategies import consistent_pay_applications


@given(consistent_pay_applications())
@settings(max_examples=300)
def test_no_rule_fires_on_a_consistent_application(pair: tuple[Contract, PayApplication]) -> None:
    """A9 is absent from the registry: it is a convention rule and ships in Phase 3
    Task 24 (F-04). `run_rules(rule_classes={"arithmetic"})` only ever sees what's
    registered, so this test does not need to know that list by name."""
    contract, app = pair
    findings = run_rules(app, contract, rule_classes={"arithmetic"})
    assert findings == [], f"{[f.rule_id for f in findings]} false-positived"
```

Leave the other two tests in the file (`test_payment_chain_never_double_counts_previous_payments`,
`test_no_computed_value_exceeds_the_contracts_decimal_places`) untouched — they call
`current_payment_due`/`total_earned_less_retainage` directly and still need that import.

- [ ] **Step 3: Run the property suite**

Run: `uv run pytest tests/property/test_arithmetic_invariants.py -v`
Expected: PASS (3 passed) — same count and same guarantee as Step 1, now routed through `run_rules`.

- [ ] **Step 4: Run the full suite, type-check, and lint**

Run: `uv run pytest -q`
Expected: all tests pass (208 passed, 1 skipped)

Run: `uv run mypy --strict ledger_sense tests`
Expected: no errors

Run: `uv run ruff check .`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add tests/property/test_arithmetic_invariants.py
git commit -m "test(property): route the arithmetic invariant suite through run_rules()"
```

---

## After this plan

`run_rules()` and `unverifiable_money_fields()` exist and are tested, but nothing in `ledger_sense/api` or `ledger_sense/extract` calls them yet — wiring a real caller (the Phase 4 reconciliation screen, or Phase 6's router-feeding glue) is future work, not part of this plan. When `phase-2-extraction` (PR #1) merges or rebases onto `main`, its own `tests/test_degraded_mode.py` copy of the `ALL_RULES` pattern should switch to `run_rules(app, contract, rule_classes={"arithmetic"})` the same way Task 3 did here.
