# Ledger Sense — Phase 3: Convention and Integrity Rules (Week 8)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the fourteen convention rules and three integrity rules, each with a tripping fixture and a clean control, and each convention rule carrying provenance naming the human who confirmed it.

**Architecture:** Convention rules read negotiated terms from the `Contract` record rather than from constants. `rules/registry.py` refuses to register any convention rule without a `RuleSource` whose `confirmed_by` names a person. Integrity rules need no construction knowledge but do need history, so they land here alongside them.

**Tech Stack:** Python 3.12+, Pydantic v2, `pytest`, `rapidfuzz` (MIT) for vendor name resolution.

**Spec:** `docs/superpowers/specs/2025-03-11-ledger-sense-design.md` §6.1, §6.3, §6.4
**Parent plan:** `2025-03-11-ledger-sense-weeks-5-12.md` — its Global Constraints and File Structure apply here in full.

---

## HARD PRECONDITION — read before Task 21b

**The week-5 domain-immersion block must have closed.** Concretely: the paid controller session
has happened, and you hold, for each convention rule, an answer to *"who told you this, and what
were they looking at when they said it?"*

`rules/registry.py` enforces this mechanically — a convention rule without a `RuleSource` whose
`confirmed_by` names a person raises `UnsourcedRuleError` at import time. **Do not route around it
by inventing a source.** Spec §6.1 records four rules that were drafted during design and were
wrong; every one was in this class, and every one read as completely reasonable prose.

If the session has not happened, **do Phase 4 first.** Phase 4 has no domain dependency.

## The known-wrong rules — regression tests are mandatory

| Rule | The plausible wrong version | The correct version |
|---|---|---|
| C1–C2 | Retainage is 10%, stepping to 5% at 50% | Rate and stepping are **contract terms**; read from `Contract`, flag deviation from *that* |
| C8 | Percentage complete must never decrease | An additive change order raises the scheduled value, so it legitimately falls — **suppress when the scheduled value moved and work did not go backwards** |
| C11 | Unconditional waiver is exchanged for payment | **Reversed.** Conditional is exchanged for payment; unconditional goes out only after funds clear |

---

### Task 21b: Domain model extensions for convention rules

Convention rules need terms and history the Phase 1 model does not carry.

**Files:**
- Modify: `ledger_sense/domain/models.py`
- Test: `tests/domain/test_convention_models.py`

**Interfaces:**
- Consumes: `Money`, `money`, `RoundingPolicy` (Task 2); existing `Contract`, `PayApplication`, `RetainageStep` (Task 4).
- Produces:
  - `RetainageTerms(base_rate, steps, stored_materials_rate, released_at_substantial_completion)` with `.rate_at(percent_complete) -> Decimal` — replaces the bare `retainage_rate` / `retainage_steps` pair on `Contract`
  - `ChangeOrder(number, amount, approved, approved_on, affects_item_no)`
  - `StoredMaterialsBacking(item_no, supplier_invoice, insurance, bonded_storage)` with `.missing() -> list[str]`, `.is_complete() -> bool`
  - `WaiverType = Literal["conditional_progress", "unconditional_progress", "conditional_final", "unconditional_final"]`
  - `LienWaiver(waiver_type, through_date, amount, funds_cleared, statutory_text_matches)`
  - `Contract` gains `retainage: RetainageTerms`, `change_orders: list[ChangeOrder]`, `tax_jurisdiction: str | None`, `tax_exempt: bool`
  - `PayApplication` gains `prior: PayApplication | None`, `stored_backing: list[StoredMaterialsBacking]`, `waivers: list[LienWaiver]`

- [ ] **Step 1: Write the failing test**

```python
# tests/domain/test_convention_models.py
from datetime import date
from decimal import Decimal

from ledger_sense.domain.models import (
    ChangeOrder, Contract, LienWaiver, PayApplication, RetainageStep,
    RetainageTerms, StoredMaterialsBacking,
)
from ledger_sense.domain.money import RoundingPolicy, money


def _terms(**kwargs):
    defaults = dict(base_rate=Decimal("0.10"), steps=[],
                    stored_materials_rate=None,
                    released_at_substantial_completion=False)
    return RetainageTerms(**{**defaults, **kwargs})


def test_retainage_terms_carry_a_separate_stored_materials_rate():
    terms = _terms(stored_materials_rate=Decimal("0.00"))
    assert terms.stored_materials_rate == Decimal("0.00")
    assert terms.base_rate == Decimal("0.10")


def test_retainage_terms_default_to_no_stepping():
    """Stepping is contract-specific, NOT a universal 10%->5%@50% schedule."""
    assert _terms().steps == []


def test_rate_at_returns_the_base_rate_below_the_first_step():
    terms = _terms(steps=[RetainageStep(at_percent_complete=Decimal("0.50"),
                                        new_rate=Decimal("0.05"))])
    assert terms.rate_at(Decimal("0.30")) == Decimal("0.10")


def test_rate_at_returns_the_stepped_rate_at_and_above_the_milestone():
    terms = _terms(steps=[RetainageStep(at_percent_complete=Decimal("0.50"),
                                        new_rate=Decimal("0.05"))])
    assert terms.rate_at(Decimal("0.50")) == Decimal("0.05")
    assert terms.rate_at(Decimal("0.90")) == Decimal("0.05")


def test_change_order_records_approval_state_and_date():
    co = ChangeOrder(number="CO-001", amount=money("5000.00"), approved=True,
                     approved_on=date(2026, 7, 15), affects_item_no="010")
    assert co.approved and co.approved_on == date(2026, 7, 15)


def test_unapproved_change_order_has_no_approval_date():
    co = ChangeOrder(number="CO-002", amount=money("1000.00"), approved=False,
                     approved_on=None, affects_item_no=None)
    assert co.approved_on is None


def test_stored_materials_backing_tracks_three_independent_documents():
    backing = StoredMaterialsBacking(item_no="010", supplier_invoice=True,
                                     insurance=False, bonded_storage=False)
    assert backing.is_complete() is False
    assert backing.missing() == ["insurance", "bonded_storage"]


def test_lien_waiver_records_whether_funds_have_cleared():
    waiver = LienWaiver(waiver_type="conditional_progress",
                        through_date=date(2026, 8, 31), amount=money("250.00"),
                        funds_cleared=False, statutory_text_matches=True)
    assert waiver.funds_cleared is False


def test_pay_application_can_reference_its_prior_period():
    contract = Contract(id="C-1", original_sum=money("1000.00"),
                        rounding=RoundingPolicy(2, 4), retainage=_terms(),
                        change_orders=[], tax_jurisdiction=None)
    first = PayApplication(contract_id="C-1", application_no=1,
                           period_to=date(2026, 7, 31), lines=[],
                           change_orders_net=money("0.00"),
                           retainage_total=money("0.00"),
                           previous_payments=money("0.00"),
                           total_completed_and_stored=money("0.00"))
    second = first.model_copy(update={"application_no": 2, "prior": first})
    assert second.prior is not None and second.prior.application_no == 1
    assert contract.retainage.base_rate == Decimal("0.10")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/domain/test_convention_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'RetainageTerms' from 'ledger_sense.domain.models'`

- [ ] **Step 3: Extend the domain models**

Append to `ledger_sense/domain/models.py`:

```python
from typing import Literal

WaiverType = Literal["conditional_progress", "unconditional_progress",
                     "conditional_final", "unconditional_final"]


class RetainageTerms(_Base):
    """Retainage is a NEGOTIATED TERM, never a constant.

    Flat 5%, flat 10%, 10% stepping at 50%, 0% on stored materials, release at
    substantial completion — all common. Several states cap it statutorily,
    differently for public and private work. Read the rate from here; never
    hardcode one.
    """

    base_rate: Decimal
    steps: list[RetainageStep] = Field(default_factory=list)
    stored_materials_rate: Decimal | None = None
    released_at_substantial_completion: bool = False

    def rate_at(self, percent_complete: Decimal) -> Decimal:
        applicable = [s for s in self.steps if percent_complete >= s.at_percent_complete]
        if not applicable:
            return self.base_rate
        return max(applicable, key=lambda s: s.at_percent_complete).new_rate


class ChangeOrder(_Base):
    number: str
    amount: Money
    approved: bool
    approved_on: date | None = None
    affects_item_no: str | None = None

    @field_serializer("amount")
    def _as_string(self, value: Decimal) -> str:
        return str(value)


class StoredMaterialsBacking(_Base):
    item_no: str
    supplier_invoice: bool = False
    insurance: bool = False
    bonded_storage: bool = False

    def missing(self) -> list[str]:
        return [name for name in ("supplier_invoice", "insurance", "bonded_storage")
                if not getattr(self, name)]

    def is_complete(self) -> bool:
        return not self.missing()


class LienWaiver(_Base):
    waiver_type: WaiverType
    through_date: date
    amount: Money
    funds_cleared: bool
    statutory_text_matches: bool | None = None

    @field_serializer("amount")
    def _as_string(self, value: Decimal) -> str:
        return str(value)
```

Then **modify** `Contract` — replace `retainage_rate` and `retainage_steps` with a single
`retainage: RetainageTerms`, and add `change_orders: list[ChangeOrder] = Field(default_factory=list)`,
`tax_jurisdiction: str | None = None`, `tax_exempt: bool = False`.

**Modify** `PayApplication` — add `prior: "PayApplication | None" = None`,
`stored_backing: list[StoredMaterialsBacking] = Field(default_factory=list)`,
`waivers: list[LienWaiver] = Field(default_factory=list)`, then call
`PayApplication.model_rebuild()` at module end to resolve the self-reference.

- [ ] **Step 4: Update Phase 1 and Phase 2 call sites**

Every `Contract(...)` construction in `tests/` and in `ledger_sense/corpus/generate.py` now passes
`retainage=RetainageTerms(base_rate=Decimal("0.10"))` instead of
`retainage_rate=Decimal("0.10"), retainage_steps=[]`.

Run: `uv run pytest tests/ -v`
Expected: PASS. All Phase 1 and Phase 2 tests stay green — this is an additive change plus one
field rename, and no arithmetic rule reads the retainage rate.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/domain/models.py ledger_sense/corpus/generate.py tests/
git commit -m "feat(domain): retainage terms, change orders, stored-materials backing, waivers"
```

---

### Task 22: Rule provenance registry and retainage C1–C3

**Files:**
- Create: `ledger_sense/rules/convention/sources.py`
- Create: `ledger_sense/rules/convention/retainage.py`
- Test: `tests/rules/convention/test_sources.py`
- Test: `tests/rules/convention/test_retainage.py`

**Interfaces:**
- Consumes: `register` (Task 5), `RetainageTerms`, `Contract`, `PayApplication`, `Finding`.
- Produces:
  - `ConfirmedSource(authority, citation, confirmed_by, confirmed_at)` — a concrete `RuleSource`
  - Named module-level sources: `RETAINAGE`, `STORED_MATERIALS`, `CHANGE_ORDERS`, `TAX`, `WAIVERS`
  - `percent_complete(app, contract) -> Decimal`
  - `rule_c1`, `rule_c2`, `rule_c3`

- [ ] **Step 1: Write the failing tests**

```python
# tests/rules/convention/test_sources.py
from datetime import date, timedelta

from ledger_sense.rules.registry import registered_rules

STALE_AFTER = timedelta(days=365)


def _convention_rules():
    return {rid: r for rid, r in registered_rules().items()
            if r.rule_class == "convention" and not rid.startswith("TEST_")}


def test_every_convention_rule_has_a_source_naming_a_person():
    for rule_id, rule in _convention_rules().items():
        assert rule.source is not None, rule_id
        assert rule.source.confirmed_by, f"{rule_id}: confirmed_by is empty"
        assert rule.source.confirmed_at is not None, f"{rule_id}: no confirmation date"


def test_no_convention_rule_cites_a_web_search_or_a_placeholder():
    forbidden = ("web search", "google", "blog", "chatgpt", "claude",
                 "unknown", "n/a", "replace")
    for rule_id, rule in _convention_rules().items():
        haystack = f"{rule.source.authority} {rule.source.confirmed_by}".lower()
        for bad in forbidden:
            assert bad not in haystack, \
                f"{rule_id}: {rule.source.authority!r} / " \
                f"{rule.source.confirmed_by!r} is not a real confirmation"


def test_no_convention_rule_provenance_is_stale():
    """A rule confirmed against a contract that has since been renegotiated is
    not confirmed. Re-confirm annually."""
    stale = [rid for rid, rule in _convention_rules().items()
             if date.today() - rule.source.confirmed_at > STALE_AFTER]
    assert stale == [], f"provenance older than one year: {stale}"
```

```python
# tests/rules/convention/test_retainage.py
from datetime import date
from decimal import Decimal

from ledger_sense.domain.models import (
    ContinuationLine, Contract, PayApplication, RetainageStep, RetainageTerms,
)
from ledger_sense.domain.money import RoundingPolicy, money
from ledger_sense.rules.convention.retainage import rule_c1, rule_c2, rule_c3


def _contract(terms):
    return Contract(id="C-1", original_sum=money("1000.00"),
                    rounding=RoundingPolicy(2, 4), retainage=terms,
                    change_orders=[], tax_jurisdiction=None)


def _line(total, retainage, stored="0.00", sv="1000.00"):
    return ContinuationLine(item_no="010", description="Sitework",
                            scheduled_value=money(sv), previous=money("0.00"),
                            this_period=money(total), stored=money(stored),
                            total_completed_and_stored=money(total),
                            retainage=money(retainage))


def _app(lines, retainage_total):
    total = money(sum((ln.total_completed_and_stored for ln in lines), money("0")))
    return PayApplication(contract_id="C-1", application_no=1,
                          period_to=date(2026, 8, 31), lines=list(lines),
                          change_orders_net=money("0.00"),
                          retainage_total=money(retainage_total),
                          previous_payments=money("0.00"),
                          total_completed_and_stored=total)


# ---- C1: applied rate matches the contract term ----

def test_c1_clean_control_rate_matches_the_contract():
    app = _app([_line("500.00", "50.00")], "50.00")
    assert rule_c1(app, _contract(RetainageTerms(base_rate=Decimal("0.10")))) == []


def test_c1_trips_when_the_applied_rate_does_not_match_the_contract():
    app = _app([_line("500.00", "25.00")], "25.00")
    findings = rule_c1(app, _contract(RetainageTerms(base_rate=Decimal("0.10"))))
    assert [f.rule_id for f in findings] == ["C1"]


def test_c1_accepts_a_five_percent_contract_without_complaint():
    """REGRESSION — spec §6.1. Ten percent is not universal."""
    app = _app([_line("500.00", "25.00")], "25.00")
    assert rule_c1(app, _contract(RetainageTerms(base_rate=Decimal("0.05")))) == []


# ---- C2: stepping fires at the CONTRACT'S milestone ----

def test_c2_clean_control_no_stepping_configured():
    """REGRESSION — a contract with no stepping is never flagged for failing to
    step at 50%. Stepping is contract-specific, not statutory-universal."""
    terms = RetainageTerms(base_rate=Decimal("0.10"), steps=[])
    app = _app([_line("900.00", "90.00", sv="1000.00")], "90.00")
    assert rule_c2(app, _contract(terms)) == []


def test_c2_trips_when_a_configured_step_was_not_applied():
    terms = RetainageTerms(base_rate=Decimal("0.10"),
                           steps=[RetainageStep(at_percent_complete=Decimal("0.50"),
                                                new_rate=Decimal("0.05"))])
    app = _app([_line("900.00", "90.00", sv="1000.00")], "90.00")
    assert [f.rule_id for f in rule_c2(app, _contract(terms))] == ["C2"]


def test_c2_clean_control_step_correctly_applied():
    terms = RetainageTerms(base_rate=Decimal("0.10"),
                           steps=[RetainageStep(at_percent_complete=Decimal("0.50"),
                                                new_rate=Decimal("0.05"))])
    app = _app([_line("900.00", "45.00", sv="1000.00")], "45.00")
    assert rule_c2(app, _contract(terms)) == []


# ---- C3: stored materials may carry their own rate ----

def test_c3_clean_control_stored_materials_at_their_own_rate():
    terms = RetainageTerms(base_rate=Decimal("0.10"),
                           stored_materials_rate=Decimal("0.00"))
    app = _app([_line("500.00", "40.00", stored="100.00")], "40.00")
    assert rule_c3(app, _contract(terms)) == []


def test_c3_trips_when_stored_materials_are_held_at_the_base_rate():
    terms = RetainageTerms(base_rate=Decimal("0.10"),
                           stored_materials_rate=Decimal("0.00"))
    app = _app([_line("500.00", "50.00", stored="100.00")], "50.00")
    assert [f.rule_id for f in rule_c3(app, _contract(terms))] == ["C3"]


def test_c3_is_inert_when_the_contract_states_no_separate_rate():
    terms = RetainageTerms(base_rate=Decimal("0.10"), stored_materials_rate=None)
    app = _app([_line("500.00", "50.00", stored="100.00")], "50.00")
    assert rule_c3(app, _contract(terms)) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/rules/convention/ -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.rules.convention.sources'`

- [ ] **Step 3: Write the source registry**

**Replace every `REPLACE:` with the real values from your week-5 session.** Left as-is they fail
`test_no_convention_rule_cites_a_web_search_or_a_placeholder`, which is the mechanism working.

```python
# ledger_sense/rules/convention/sources.py
"""Provenance for every convention rule.

A rule here encodes negotiated or statutory behaviour. It is fluent to state
and silent when wrong. Each source names the human who confirmed it and what
they were looking at when they did.

`confirmed_at` drives a staleness test: re-confirm annually, because a rule
confirmed against a contract that has since been renegotiated is not confirmed.
"""

from datetime import date
from typing import NamedTuple


class ConfirmedSource(NamedTuple):
    authority: str
    citation: str
    confirmed_by: str | None
    confirmed_at: date | None


RETAINAGE = ConfirmedSource(
    authority="REPLACE: client contract, General Conditions, retainage clause",
    citation="REPLACE: clause number covering rate, stepping milestone, stored materials",
    confirmed_by="REPLACE: name of the controller who confirmed this",
    confirmed_at=date(2026, 9, 2))

STORED_MATERIALS = ConfirmedSource(
    authority="REPLACE: client contract, stored materials clause",
    citation="REPLACE: clause number covering required backup documentation",
    confirmed_by="REPLACE: name of the controller who confirmed this",
    confirmed_at=date(2026, 9, 2))

CHANGE_ORDERS = ConfirmedSource(
    authority="REPLACE: client contract, Change Order procedure",
    citation="REPLACE: clause requiring approval before work is billed",
    confirmed_by="REPLACE: name of the controller who confirmed this",
    confirmed_at=date(2026, 9, 2))

TAX = ConfirmedSource(
    authority="REPLACE: statute cite for the single configured jurisdiction",
    citation="REPLACE: section covering contractor material vs labour treatment",
    confirmed_by="REPLACE: name of the person who confirmed this",
    confirmed_at=date(2026, 9, 2))

WAIVERS = ConfirmedSource(
    authority="REPLACE: state lien statute cite for the configured jurisdiction",
    citation="REPLACE: section prescribing conditional and unconditional forms",
    confirmed_by="REPLACE: name of the person who confirmed this",
    confirmed_at=date(2026, 9, 2))
```

- [ ] **Step 4: Write the retainage rules**

```python
# ledger_sense/rules/convention/retainage.py
"""C1-C3 — retainage.

THE RATE IS A CONTRACT TERM. Flat 5%, flat 10%, 10% stepping at 50%, 0% on
stored materials, release at substantial completion — all common, and several
states cap it statutorily with different limits for public and private work.

Spec §6.1 records the wrong version as first drafted: "retainage is 10%,
stepping to 5% at 50% completion". Hardcoded, that fires false positives on
most real contracts. Read the rate from the Contract.
"""

from decimal import Decimal

from ledger_sense.domain.models import Contract, PayApplication
from ledger_sense.domain.money import money, quantize
from ledger_sense.rules.convention.sources import RETAINAGE
from ledger_sense.rules.engine import Finding
from ledger_sense.rules.registry import register


def percent_complete(app: PayApplication, contract: Contract) -> Decimal:
    scheduled = money(sum((ln.scheduled_value for ln in app.lines), money("0")))
    if scheduled == 0:
        return Decimal("0")
    return quantize(money(app.total_completed_and_stored / scheduled),
                    contract.rounding.percent_places)


def _held(rate: Decimal, base: Decimal) -> Decimal:
    return quantize(money(base * rate), 2)


@register("C1", "convention", source=RETAINAGE)
def rule_c1(app: PayApplication, contract: Contract) -> list[Finding]:
    """Applied retainage matches the contract's base rate. C2 owns stepped contracts."""
    if contract.retainage.steps:
        return []
    expected = _held(contract.retainage.base_rate, app.total_completed_and_stored)
    if expected != app.retainage_total:
        return [Finding("C1", "retainage_total",
                        f"applied retainage does not match the contract rate of "
                        f"{contract.retainage.base_rate}",
                        expected, app.retainage_total)]
    return []


@register("C2", "convention", source=RETAINAGE)
def rule_c2(app: PayApplication, contract: Contract) -> list[Finding]:
    """Stepping applied at the CONTRACT'S milestone.

    A contract with no configured steps is never flagged for failing to step.
    """
    if not contract.retainage.steps:
        return []
    complete = percent_complete(app, contract)
    rate = contract.retainage.rate_at(complete)
    expected = _held(rate, app.total_completed_and_stored)
    if expected != app.retainage_total:
        return [Finding("C2", "retainage_total",
                        f"at {complete} complete the contract rate is {rate}; the "
                        f"applied retainage does not reflect it",
                        expected, app.retainage_total)]
    return []


@register("C3", "convention", source=RETAINAGE)
def rule_c3(app: PayApplication, contract: Contract) -> list[Finding]:
    """Stored materials held at their own rate where the contract states one."""
    stored_rate = contract.retainage.stored_materials_rate
    if stored_rate is None:
        return []
    stored = money(sum((ln.stored for ln in app.lines), money("0")))
    installed = money(app.total_completed_and_stored - stored)
    expected = money(_held(contract.retainage.base_rate, installed)
                     + _held(stored_rate, stored))
    if expected != app.retainage_total:
        return [Finding("C3", "retainage_total",
                        f"stored materials carry a separate retainage rate of "
                        f"{stored_rate} under this contract",
                        expected, app.retainage_total)]
    return []
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/rules/convention/test_retainage.py -v`
Expected: PASS, 9 tests.

Run: `uv run pytest tests/rules/convention/test_sources.py -v`
Expected: **FAIL** on the `REPLACE:` placeholders until you enter real confirmations. That failure
is the Q7 mitigation working as designed. Do not delete the test.

- [ ] **Step 6: Commit**

```bash
git add ledger_sense/rules/convention/sources.py ledger_sense/rules/convention/retainage.py tests/rules/convention/
git commit -m "feat(rules): C1-C3 retainage read from contract terms, with provenance"
```

---

### Task 23: Stored materials C4–C6

**Files:**
- Create: `ledger_sense/rules/convention/stored.py`
- Test: `tests/rules/convention/test_stored.py`

**Interfaces:**
- Consumes: `StoredMaterialsBacking`, `PayApplication.prior`, `STORED_MATERIALS` source.
- Produces: `rule_c4`, `rule_c5`, `rule_c6`.

- [ ] **Step 1: Write the failing test**

```python
# tests/rules/convention/test_stored.py
from datetime import date
from decimal import Decimal

from ledger_sense.domain.models import (
    ContinuationLine, Contract, PayApplication, RetainageTerms, StoredMaterialsBacking,
)
from ledger_sense.domain.money import RoundingPolicy, money
from ledger_sense.rules.convention.stored import rule_c4, rule_c5, rule_c6


def _contract():
    return Contract(id="C-1", original_sum=money("10000.00"),
                    rounding=RoundingPolicy(2, 4),
                    retainage=RetainageTerms(base_rate=Decimal("0.10")),
                    change_orders=[], tax_jurisdiction=None)


def _line(previous, this_period, stored, sv="10000.00", total=None):
    computed = total if total is not None else str(
        money(money(previous) + money(this_period) + money(stored)))
    return ContinuationLine(item_no="010", description="Structural Steel",
                            scheduled_value=money(sv), previous=money(previous),
                            this_period=money(this_period), stored=money(stored),
                            total_completed_and_stored=money(computed),
                            retainage=money("0.00"))


def _app(line, prior=None, backing=(), number=2):
    return PayApplication(contract_id="C-1", application_no=number,
                          period_to=date(2026, 8, 31), lines=[line],
                          change_orders_net=money("0.00"),
                          retainage_total=money("0.00"),
                          previous_payments=money("0.00"),
                          total_completed_and_stored=line.total_completed_and_stored,
                          prior=prior, stored_backing=list(backing))


# ---- C4: stored must not also appear in completed work ----

def test_c4_clean_control_stored_and_completed_are_disjoint():
    assert rule_c4(_app(_line("1000.00", "500.00", "300.00")), _contract()) == []


def test_c4_trips_when_stored_is_double_counted_into_this_period():
    """The classic SILENT pay-application error: material billed as stored AND
    billed again as work completed. The arithmetic still foots."""
    prior = _app(_line("0.00", "1000.00", "300.00"), number=1)
    current = _line("1000.00", "300.00", "300.00", total="1600.00")
    assert [f.rule_id for f in rule_c4(_app(current, prior=prior), _contract())] == ["C4"]


def test_c4_is_inert_when_nothing_is_stored():
    assert rule_c4(_app(_line("1000.00", "500.00", "0.00")), _contract()) == []


# ---- C5: stored must LEAVE the stored column when installed ----

def test_c5_clean_control_stored_moves_out_as_work_moves_in():
    prior = _app(_line("0.00", "1000.00", "500.00"), number=1)
    current = _line("1500.00", "500.00", "0.00")
    assert rule_c5(_app(current, prior=prior), _contract()) == []


def test_c5_trips_when_stored_stays_while_the_same_value_is_installed():
    prior = _app(_line("0.00", "1000.00", "500.00"), number=1)
    current = _line("1500.00", "500.00", "500.00")
    assert [f.rule_id for f in rule_c5(_app(current, prior=prior), _contract())] == ["C5"]


def test_c5_is_inert_on_a_first_application_with_no_prior():
    assert rule_c5(_app(_line("0.00", "500.00", "300.00")), _contract()) == []


# ---- C6: stored materials require backup ----

def test_c6_clean_control_complete_backing():
    backing = StoredMaterialsBacking(item_no="010", supplier_invoice=True,
                                     insurance=True, bonded_storage=True)
    assert rule_c6(_app(_line("0.00", "500.00", "300.00"), backing=[backing]),
                   _contract()) == []


def test_c6_trips_when_stored_is_claimed_with_no_backing_at_all():
    findings = rule_c6(_app(_line("0.00", "500.00", "300.00")), _contract())
    assert [f.rule_id for f in findings] == ["C6"]
    assert "supplier_invoice" in findings[0].message


def test_c6_names_only_the_missing_documents():
    backing = StoredMaterialsBacking(item_no="010", supplier_invoice=True,
                                     insurance=False, bonded_storage=False)
    findings = rule_c6(_app(_line("0.00", "500.00", "300.00"), backing=[backing]),
                       _contract())
    assert "supplier_invoice" not in findings[0].message
    assert "insurance" in findings[0].message


def test_c6_is_inert_when_nothing_is_claimed_as_stored():
    assert rule_c6(_app(_line("0.00", "500.00", "0.00")), _contract()) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/rules/convention/test_stored.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.rules.convention.stored'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/rules/convention/stored.py
"""C4-C6 — stored materials.

Double-counting stored materials is the classic pay-application error and it is
SILENT: the arithmetic still foots, because the same value is simply counted in
two columns. Only a rule that knows the domain catches it.
"""

from ledger_sense.domain.models import Contract, PayApplication
from ledger_sense.domain.money import money
from ledger_sense.rules.convention.sources import STORED_MATERIALS
from ledger_sense.rules.engine import Finding
from ledger_sense.rules.registry import register


def _prior_line(app: PayApplication, item_no: str):
    if app.prior is None:
        return None
    return next((ln for ln in app.prior.lines if ln.item_no == item_no), None)


@register("C4", "convention", source=STORED_MATERIALS)
def rule_c4(app: PayApplication, contract: Contract) -> list[Finding]:
    """Material billed as stored must not also be billed as completed work.

    Detected by comparing this period's installed work against the stored value
    carried over: when the same amount appears in both, it is counted twice.
    """
    findings = []
    for line in app.lines:
        if line.stored == 0:
            continue
        prior = _prior_line(app, line.item_no)
        carried = prior.stored if prior is not None else money("0.00")
        if carried > 0 and line.this_period == carried and line.stored == carried:
            findings.append(Finding(
                "C4", f"line[{line.item_no}].stored",
                "material carried as stored is also billed as completed this "
                "period — the same value is counted twice",
                money("0.00"), line.stored))
    return findings


@register("C5", "convention", source=STORED_MATERIALS)
def rule_c5(app: PayApplication, contract: Contract) -> list[Finding]:
    """Stored material must leave the stored column in the period it is installed."""
    if app.prior is None:
        return []
    findings = []
    for line in app.lines:
        prior = _prior_line(app, line.item_no)
        if prior is None or prior.stored == 0:
            continue
        if line.this_period >= prior.stored and line.stored >= prior.stored:
            findings.append(Finding(
                "C5", f"line[{line.item_no}].stored",
                "material was installed this period but has not left the stored "
                "materials column",
                money(line.stored - prior.stored), line.stored))
    return findings


@register("C6", "convention", source=STORED_MATERIALS)
def rule_c6(app: PayApplication, contract: Contract) -> list[Finding]:
    """Stored materials require backup: supplier invoice, insurance, bonded storage."""
    backing = {b.item_no: b for b in app.stored_backing}
    findings = []
    for line in app.lines:
        if line.stored == 0:
            continue
        record = backing.get(line.item_no)
        missing = record.missing() if record is not None else [
            "supplier_invoice", "insurance", "bonded_storage"]
        if missing:
            findings.append(Finding(
                "C6", f"line[{line.item_no}].stored",
                f"stored materials claimed without backup: {', '.join(missing)}",
                money("0.00"), line.stored))
    return findings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/rules/convention/test_stored.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/rules/convention/stored.py tests/rules/convention/test_stored.py
git commit -m "feat(rules): C4-C6 stored materials, double-count and missing-backup detection"
```

---

### Task 24: Change orders C7–C8

**C8 is a known-wrong rule.** The false-positive suppression tests are mandatory.

**Files:**
- Create: `ledger_sense/rules/convention/change_orders.py`
- Test: `tests/rules/convention/test_change_orders.py`

**Interfaces:**
- Consumes: `ChangeOrder`, `PayApplication.prior`, `CHANGE_ORDERS` source.
- Produces: `rule_c7`, `rule_c8`.

- [ ] **Step 1: Write the failing test**

```python
# tests/rules/convention/test_change_orders.py
from datetime import date
from decimal import Decimal

from ledger_sense.domain.models import (
    ChangeOrder, ContinuationLine, Contract, PayApplication, RetainageTerms,
)
from ledger_sense.domain.money import RoundingPolicy, money
from ledger_sense.rules.convention.change_orders import rule_c7, rule_c8


def _contract(change_orders=()):
    return Contract(id="C-1", original_sum=money("10000.00"),
                    rounding=RoundingPolicy(2, 4),
                    retainage=RetainageTerms(base_rate=Decimal("0.10")),
                    change_orders=list(change_orders), tax_jurisdiction=None)


def _line(sv, total, item_no="010"):
    return ContinuationLine(item_no=item_no, description="Sitework",
                            scheduled_value=money(sv), previous=money("0.00"),
                            this_period=money(total), stored=money("0.00"),
                            total_completed_and_stored=money(total),
                            retainage=money("0.00"))


def _app(lines, co_net="0.00", prior=None, number=2):
    total = money(sum((ln.total_completed_and_stored for ln in lines), money("0")))
    return PayApplication(contract_id="C-1", application_no=number,
                          period_to=date(2026, 8, 31), lines=list(lines),
                          change_orders_net=money(co_net),
                          retainage_total=money("0.00"),
                          previous_payments=money("0.00"),
                          total_completed_and_stored=total, prior=prior)


# ---- C7 ----

def test_c7_clean_control_net_matches_approved_change_orders():
    orders = [ChangeOrder(number="CO-001", amount=money("5000.00"), approved=True,
                          approved_on=date(2026, 7, 15), affects_item_no=None)]
    assert rule_c7(_app([_line("15000.00", "0.00")], co_net="5000.00"),
                   _contract(orders)) == []


def test_c7_trips_when_the_net_includes_an_unapproved_change_order():
    orders = [ChangeOrder(number="CO-002", amount=money("5000.00"), approved=False,
                          approved_on=None, affects_item_no=None)]
    findings = rule_c7(_app([_line("15000.00", "0.00")], co_net="5000.00"),
                       _contract(orders))
    assert [f.rule_id for f in findings] == ["C7"]
    assert "CO-002" in findings[0].message


def test_c7_trips_when_no_change_order_accounts_for_the_amount():
    findings = rule_c7(_app([_line("15000.00", "0.00")], co_net="5000.00"),
                       _contract([]))
    assert [f.rule_id for f in findings] == ["C7"]


def test_c7_is_inert_when_no_change_orders_are_billed():
    assert rule_c7(_app([_line("10000.00", "0.00")]), _contract([])) == []


# ---- C8: the false-positive rule ----

def test_c8_clean_control_percentage_rises():
    prior = _app([_line("10000.00", "2000.00")], number=1)
    assert rule_c8(_app([_line("10000.00", "4000.00")], prior=prior), _contract()) == []


def test_c8_trips_on_an_unexplained_decrease():
    prior = _app([_line("10000.00", "4000.00")], number=1)
    findings = rule_c8(_app([_line("10000.00", "2000.00")], prior=prior), _contract())
    assert [f.rule_id for f in findings] == ["C8"]


def test_c8_does_not_fire_when_an_additive_change_order_raised_the_scheduled_value():
    """REGRESSION — spec §6.1. An additive change order raises the line's
    scheduled value, so percentage complete LEGITIMATELY falls. Stated
    absolutely, C8 flags every change order and trains reviewers to ignore it."""
    prior = _app([_line("10000.00", "4000.00")], number=1)
    assert rule_c8(_app([_line("20000.00", "4000.00")], prior=prior), _contract()) == []


def test_c8_still_fires_when_the_scheduled_value_moved_and_work_also_reversed():
    """Suppression must be narrow, not a blanket exemption."""
    prior = _app([_line("10000.00", "4000.00")], number=1)
    findings = rule_c8(_app([_line("20000.00", "1000.00")], prior=prior), _contract())
    assert [f.rule_id for f in findings] == ["C8"]


def test_c8_is_inert_on_a_first_application():
    assert rule_c8(_app([_line("10000.00", "2000.00")]), _contract()) == []


def test_c8_ignores_a_line_absent_from_the_prior_application():
    prior = _app([_line("10000.00", "4000.00", item_no="010")], number=1)
    current = _app([_line("5000.00", "0.00", item_no="020")], prior=prior)
    assert rule_c8(current, _contract()) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/rules/convention/test_change_orders.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.rules.convention.change_orders'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/rules/convention/change_orders.py
"""C7-C8 — change orders.

C8 IS THE FALSE-POSITIVE RULE. Spec §6.1 records the wrong version:
"percentage complete must never decrease". An ADDITIVE change order raises the
line's scheduled value, so percentage complete legitimately falls that period.
Stated absolutely, the rule flags every change order — which trains a reviewer
to dismiss it, which is worse than not having the rule at all.

Suppression is NARROW: the scheduled value moved AND completed work did not go
backwards. Both conditions, or it fires.
"""

from decimal import Decimal

from ledger_sense.domain.models import Contract, PayApplication
from ledger_sense.domain.money import money, quantize
from ledger_sense.rules.convention.sources import CHANGE_ORDERS
from ledger_sense.rules.engine import Finding
from ledger_sense.rules.registry import register


def _line_percent(line, places: int) -> Decimal:
    if line.scheduled_value == 0:
        return Decimal("0")
    return quantize(money(line.total_completed_and_stored / line.scheduled_value), places)


@register("C7", "convention", source=CHANGE_ORDERS)
def rule_c7(app: PayApplication, contract: Contract) -> list[Finding]:
    """The net change-order figure must be backed by APPROVED change orders."""
    if app.change_orders_net == 0:
        return []
    approved = money(sum((co.amount for co in contract.change_orders if co.approved),
                         money("0")))
    if approved == app.change_orders_net:
        return []
    unapproved = [co.number for co in contract.change_orders if not co.approved]
    detail = (f"unapproved change orders on file: {', '.join(unapproved)}"
              if unapproved else "no approved change order accounts for this amount")
    return [Finding("C7", "change_orders_net",
                    f"net change orders billed do not reconcile to approved change "
                    f"orders — {detail}",
                    approved, app.change_orders_net)]


@register("C8", "convention", source=CHANGE_ORDERS)
def rule_c8(app: PayApplication, contract: Contract) -> list[Finding]:
    """Percentage complete fell. Flag it — unless a scheduled-value change explains it."""
    if app.prior is None:
        return []
    places = contract.rounding.percent_places
    prior_lines = {ln.item_no: ln for ln in app.prior.lines}

    findings = []
    for line in app.lines:
        prior = prior_lines.get(line.item_no)
        if prior is None:
            continue
        now = _line_percent(line, places)
        before = _line_percent(prior, places)
        if now >= before:
            continue

        scheduled_value_moved = line.scheduled_value != prior.scheduled_value
        work_went_backwards = (
            line.total_completed_and_stored < prior.total_completed_and_stored)
        if scheduled_value_moved and not work_went_backwards:
            continue  # explained by the change order; not a finding

        findings.append(Finding(
            "C8", f"line[{line.item_no}].percent_complete",
            "percentage complete decreased and no scheduled-value change explains it",
            before, now))
    return findings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/rules/convention/test_change_orders.py -v`
Expected: PASS, 10 tests — including both suppression regression tests.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/rules/convention/change_orders.py tests/rules/convention/test_change_orders.py
git commit -m "feat(rules): C7-C8 change orders, with narrow C8 false-positive suppression"
```

---

### Task 25: Tax C9–C10, one named jurisdiction

**Scope is deliberately one jurisdiction.** Everywhere else returns an explicit "not configured" finding rather than passing silently. Naming the limit is cheaper and more credible than implying coverage.

**Files:**
- Create: `ledger_sense/rules/convention/tax.py`
- Test: `tests/rules/convention/test_tax.py`

**Interfaces:**
- Consumes: `Contract.tax_jurisdiction`, `Contract.tax_exempt`, `TAX` source.
- Produces: `CONFIGURED_JURISDICTION: str`, `rule_c9`, `rule_c10`.

- [ ] **Step 1: Write the failing test**

```python
# tests/rules/convention/test_tax.py
from datetime import date
from decimal import Decimal

from ledger_sense.domain.models import (
    ContinuationLine, Contract, PayApplication, RetainageTerms,
)
from ledger_sense.domain.money import RoundingPolicy, money
from ledger_sense.rules.convention.tax import CONFIGURED_JURISDICTION, rule_c9, rule_c10


def _contract(jurisdiction, tax_exempt=False):
    return Contract(id="C-1", original_sum=money("10000.00"),
                    rounding=RoundingPolicy(2, 4),
                    retainage=RetainageTerms(base_rate=Decimal("0.10")),
                    change_orders=[], tax_jurisdiction=jurisdiction,
                    tax_exempt=tax_exempt)


def _app(lines=()):
    total = money(sum((ln.total_completed_and_stored for ln in lines), money("0")))
    return PayApplication(contract_id="C-1", application_no=1,
                          period_to=date(2026, 8, 31), lines=list(lines),
                          change_orders_net=money("0.00"),
                          retainage_total=money("0.00"),
                          previous_payments=money("0.00"),
                          total_completed_and_stored=total)


def _line(description):
    return ContinuationLine(item_no="900", description=description,
                            scheduled_value=money("500.00"), previous=money("0.00"),
                            this_period=money("500.00"), stored=money("0.00"),
                            total_completed_and_stored=money("500.00"),
                            retainage=money("0.00"))


def test_an_unconfigured_jurisdiction_is_reported_not_silently_passed():
    """We cover one jurisdiction. Everywhere else says so out loud."""
    findings = rule_c9(_app(), _contract("XX-Nowhere"))
    assert [f.rule_id for f in findings] == ["C9"]
    assert "not configured" in findings[0].message.lower()
    assert "XX-Nowhere" in findings[0].message


def test_a_contract_with_no_jurisdiction_is_reported():
    assert [f.rule_id for f in rule_c9(_app(), _contract(None))] == ["C9"]


def test_the_configured_jurisdiction_is_evaluated_rather_than_deferred():
    findings = rule_c9(_app(), _contract(CONFIGURED_JURISDICTION))
    assert all("not configured" not in f.message.lower() for f in findings)


def test_c10_tax_exempt_project_flags_a_tax_line():
    findings = rule_c10(_app([_line("Sales Tax")]),
                        _contract(CONFIGURED_JURISDICTION, tax_exempt=True))
    assert [f.rule_id for f in findings] == ["C10"]


def test_c10_clean_control_tax_exempt_project_with_no_tax_line():
    assert rule_c10(_app([_line("Sitework")]),
                    _contract(CONFIGURED_JURISDICTION, tax_exempt=True)) == []


def test_c10_is_inert_on_a_non_exempt_project():
    assert rule_c10(_app([_line("Sales Tax")]),
                    _contract(CONFIGURED_JURISDICTION)) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/rules/convention/test_tax.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.rules.convention.tax'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/rules/convention/tax.py
"""C9-C10 — sales and use tax, for ONE named jurisdiction.

Generalised tax-by-jurisdiction is a research project with legal exposure
attached, and it is the rule most likely to be confidently wrong. v1 covers a
single jurisdiction and says so out loud everywhere else.

Set CONFIGURED_JURISDICTION to the jurisdiction your week-5 session actually
confirmed, and cite the statute in sources.TAX.
"""

from ledger_sense.domain.models import Contract, PayApplication
from ledger_sense.domain.money import money
from ledger_sense.rules.convention.sources import TAX
from ledger_sense.rules.engine import Finding
from ledger_sense.rules.registry import register

# REPLACE with the jurisdiction confirmed in the week-5 session.
CONFIGURED_JURISDICTION = "REPLACE-ME"

_TAX_DESCRIPTIONS = ("sales tax", "use tax", "sales/use tax")


@register("C9", "convention", source=TAX)
def rule_c9(app: PayApplication, contract: Contract) -> list[Finding]:
    """Evaluate tax treatment, or state plainly that we cannot."""
    if contract.tax_jurisdiction != CONFIGURED_JURISDICTION:
        return [Finding(
            "C9", "tax_jurisdiction",
            f"tax rules are not configured for jurisdiction "
            f"{contract.tax_jurisdiction!r}; this application routes to review for "
            f"a manual tax check. Configured jurisdiction: {CONFIGURED_JURISDICTION}",
            money("0.00"), money("0.00"))]
    # Jurisdiction-specific material/labour split checks belong here, sourced from
    # the statute cited in sources.TAX. Until that statute is read and confirmed,
    # this returns no finding rather than a guessed one.
    return []


@register("C10", "convention", source=TAX)
def rule_c10(app: PayApplication, contract: Contract) -> list[Finding]:
    """A tax-exempt project should carry no tax line."""
    if not contract.tax_exempt:
        return []
    return [
        Finding("C10", f"line[{line.item_no}].description",
                "tax billed on a project recorded as tax-exempt",
                money("0.00"), line.total_completed_and_stored)
        for line in app.lines
        if any(marker in line.description.lower() for marker in _TAX_DESCRIPTIONS)
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/rules/convention/test_tax.py -v`
Expected: PASS, 6 tests. `test_the_configured_jurisdiction_is_evaluated_rather_than_deferred`
passes trivially while `CONFIGURED_JURISDICTION` is `"REPLACE-ME"`; it becomes meaningful once you
set a real value.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/rules/convention/tax.py tests/rules/convention/test_tax.py
git commit -m "feat(rules): C9-C10 tax scoped to one named jurisdiction, others routed to review"
```

---

### Task 26: Lien waivers C11–C14

**C11 is the most dangerous known-wrong rule.** The wrong version advises a subcontractor to surrender lien rights before being paid. **This module extracts, validates and flags. It never generates a document and never opines on statutory language** (B4).

**Files:**
- Create: `ledger_sense/rules/convention/waivers.py`
- Test: `tests/rules/convention/test_waivers.py`

**Interfaces:**
- Consumes: `LienWaiver`, `WaiverType`, `WAIVERS` source.
- Produces: `rule_c11`, `rule_c12`, `rule_c13`, `rule_c14`.

- [ ] **Step 1: Write the failing test**

```python
# tests/rules/convention/test_waivers.py
import ast
import pathlib
from datetime import date
from decimal import Decimal

from ledger_sense.domain.models import (
    Contract, LienWaiver, PayApplication, RetainageTerms,
)
from ledger_sense.domain.money import RoundingPolicy, money
from ledger_sense.rules.convention.waivers import rule_c11, rule_c12, rule_c13, rule_c14


def _contract():
    return Contract(id="C-1", original_sum=money("10000.00"),
                    rounding=RoundingPolicy(2, 4),
                    retainage=RetainageTerms(base_rate=Decimal("0.10")),
                    change_orders=[], tax_jurisdiction=None)


def _app(waivers, period_to=date(2026, 8, 31), payment="250.00"):
    return PayApplication(contract_id="C-1", application_no=1, period_to=period_to,
                          lines=[], change_orders_net=money("0.00"),
                          retainage_total=money("0.00"),
                          previous_payments=money("0.00"),
                          total_completed_and_stored=money(payment),
                          waivers=list(waivers))


def _waiver(waiver_type, funds_cleared, through=date(2026, 8, 31),
            amount="250.00", statutory=True):
    return LienWaiver(waiver_type=waiver_type, through_date=through,
                      amount=money(amount), funds_cleared=funds_cleared,
                      statutory_text_matches=statutory)


# ---- C11: THE REVERSED RULE ----

def test_c11_clean_control_conditional_waiver_exchanged_for_payment():
    """CORRECT: the CONDITIONAL waiver is exchanged for the payment, conditioned
    on it clearing. That is what protects the claimant if the cheque bounces."""
    assert rule_c11(_app([_waiver("conditional_progress", funds_cleared=False)]),
                    _contract()) == []


def test_c11_clean_control_unconditional_waiver_after_funds_clear():
    """CORRECT: the UNCONDITIONAL waiver goes out only after funds clear,
    because it extinguishes the lien right outright."""
    assert rule_c11(_app([_waiver("unconditional_progress", funds_cleared=True)]),
                    _contract()) == []


def test_c11_trips_on_an_unconditional_waiver_before_funds_clear():
    """REGRESSION — spec §6.1. The wrong version had this backwards and would
    have advised surrendering lien rights before being paid."""
    findings = rule_c11(_app([_waiver("unconditional_progress", funds_cleared=False)]),
                        _contract())
    assert [f.rule_id for f in findings] == ["C11"]
    assert "before funds" in findings[0].message.lower()


def test_c11_does_not_flag_a_conditional_waiver_merely_because_funds_cleared():
    assert rule_c11(_app([_waiver("conditional_progress", funds_cleared=True)]),
                    _contract()) == []


def test_c11_applies_to_final_waivers_too():
    findings = rule_c11(_app([_waiver("unconditional_final", funds_cleared=False)]),
                        _contract())
    assert [f.rule_id for f in findings] == ["C11"]


# ---- C12, C13, C14 ----

def test_c12_clean_control_through_date_matches_the_billing_period():
    assert rule_c12(_app([_waiver("conditional_progress", False,
                                  through=date(2026, 8, 31))]), _contract()) == []


def test_c12_trips_when_the_through_date_misses_the_period():
    findings = rule_c12(_app([_waiver("conditional_progress", False,
                                      through=date(2026, 7, 31))]), _contract())
    assert [f.rule_id for f in findings] == ["C12"]


def test_c13_clean_control_amount_matches_the_payment():
    assert rule_c13(_app([_waiver("conditional_progress", False, amount="250.00")],
                         payment="250.00"), _contract()) == []


def test_c13_trips_when_the_waiver_amount_differs_from_the_payment():
    findings = rule_c13(_app([_waiver("conditional_progress", False, amount="200.00")],
                             payment="250.00"), _contract())
    assert [f.rule_id for f in findings] == ["C13"]


def test_c14_flags_statutory_deviation_without_opining_on_it():
    findings = rule_c14(_app([_waiver("conditional_progress", False, statutory=False)]),
                        _contract())
    assert [f.rule_id for f in findings] == ["C14"]
    message = findings[0].message.lower()
    assert "review" in message or "counsel" in message
    for opinion in ("invalid", "unenforceable", "you should", "we recommend"):
        assert opinion not in message


def test_c14_is_silent_when_statutory_match_is_unknown():
    """Unknown is not deviation. Do not manufacture a legal finding."""
    waiver = LienWaiver(waiver_type="conditional_progress",
                        through_date=date(2026, 8, 31), amount=money("250.00"),
                        funds_cleared=False, statutory_text_matches=None)
    assert rule_c14(_app([waiver]), _contract()) == []


def test_the_module_never_generates_a_waiver_document():
    """B4: lien waivers are legal instruments. Extract, validate, flag, stop."""
    tree = ast.parse(pathlib.Path("ledger_sense/rules/convention/waivers.py")
                     .read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
            assert name not in {"render", "generate", "build_waiver", "write_text",
                                "LienWaiver"}, f"{name} at line {node.lineno}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/rules/convention/test_waivers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.rules.convention.waivers'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/rules/convention/waivers.py
"""C11-C14 — lien waivers.

THE DIRECTION MATTERS AND IS COMMONLY STATED BACKWARDS:

    CONDITIONAL waiver   -> exchanged FOR the payment, conditioned on it
                            clearing. Protects the claimant if the cheque bounces.
    UNCONDITIONAL waiver -> delivered only AFTER funds have cleared, because it
                            extinguishes the lien right outright.

Spec §6.1 records the reversed version, which would advise a subcontractor to
surrender lien rights before being paid.

B4: this module EXTRACTS, VALIDATES and FLAGS. It never generates a waiver and
never opines on statutory language — that edges toward unauthorised practice of
law and creates liability out of proportion to the feature's value.
"""

from ledger_sense.domain.models import Contract, PayApplication
from ledger_sense.domain.money import money
from ledger_sense.rules.convention.sources import WAIVERS
from ledger_sense.rules.engine import Finding
from ledger_sense.rules.registry import register

_UNCONDITIONAL = frozenset({"unconditional_progress", "unconditional_final"})


@register("C11", "convention", source=WAIVERS)
def rule_c11(app: PayApplication, contract: Contract) -> list[Finding]:
    """An unconditional waiver before funds clear surrenders the lien right early."""
    return [
        Finding("C11", f"waiver[{index}].waiver_type",
                "an unconditional waiver has been provided before funds have "
                "cleared; a conditional waiver is the form exchanged for payment",
                money("0.00"), waiver.amount)
        for index, waiver in enumerate(app.waivers)
        if waiver.waiver_type in _UNCONDITIONAL and not waiver.funds_cleared
    ]


@register("C12", "convention", source=WAIVERS)
def rule_c12(app: PayApplication, contract: Contract) -> list[Finding]:
    """Through-date matches the billing period."""
    return [
        Finding("C12", f"waiver[{index}].through_date",
                f"waiver through-date {waiver.through_date.isoformat()} does not "
                f"match the billing period ending {app.period_to.isoformat()}",
                money("0.00"), money("0.00"))
        for index, waiver in enumerate(app.waivers)
        if waiver.through_date != app.period_to
    ]


@register("C13", "convention", source=WAIVERS)
def rule_c13(app: PayApplication, contract: Contract) -> list[Finding]:
    """Waiver amount matches the payment it accompanies."""
    return [
        Finding("C13", f"waiver[{index}].amount",
                "waiver amount does not match the payment it accompanies",
                app.total_completed_and_stored, waiver.amount)
        for index, waiver in enumerate(app.waivers)
        if waiver.amount != app.total_completed_and_stored
    ]


@register("C14", "convention", source=WAIVERS)
def rule_c14(app: PayApplication, contract: Contract) -> list[Finding]:
    """Statutory-form deviation is SURFACED, never adjudicated.

    `statutory_text_matches is None` means unknown, which is not deviation.
    """
    return [
        Finding("C14", f"waiver[{index}].statutory_text_matches",
                "this waiver's language differs from the statutory form on file; "
                "sent to review for counsel to consider",
                money("0.00"), money("0.00"))
        for index, waiver in enumerate(app.waivers)
        if waiver.statutory_text_matches is False
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/rules/convention/test_waivers.py -v`
Expected: PASS, 12 tests — including the C11 direction regression and the no-generation guard.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/rules/convention/waivers.py tests/rules/convention/test_waivers.py
git commit -m "feat(rules): C11-C14 lien waivers, correct conditional/unconditional direction"
```

---

### Task 27: Integrity rules I1–I3 — **DoD 3**

No construction knowledge required, so no `RuleSource`. **I3 is the highest-consequence rule in the product.**

**Files:**
- Create: `ledger_sense/domain/vendor.py`
- Create: `ledger_sense/rules/integrity/duplicates.py`
- Create: `ledger_sense/rules/integrity/vendors.py`
- Create: `ledger_sense/rules/integrity/bank_details.py`
- Test: `tests/rules/integrity/test_duplicates.py`
- Test: `tests/rules/integrity/test_vendors.py`
- Test: `tests/rules/integrity/test_bank_details.py`

**Interfaces:**
- Consumes: `Money`, `money`, `Finding`.
- Produces:
  - `PaymentDetail(account_last4, routing_last4, observed_at, source_document)` — **last four only**
  - `VendorAlias(name, first_seen, source_document, confirmed_by)`
  - `CanonicalVendor(id, name, aliases, payment_history)`
  - `Match(vendor_id, score, needs_confirmation)`, `MergeToken(keeper, absorbed_snapshot, confirmed_by)`
  - `VendorLedger` with `create`, `get`, `resolve`, `confirm_alias`, `merge`, `unmerge`, `record_payment_detail`
  - `DocumentFingerprint(vendor, amount, doc_date, invoice_number, line_hash)`, `DuplicateMatch(reason, against)`, `find_duplicates(candidate, history)`
  - `check_vendor_resolution(ledger, name) -> list[Finding]`
  - `BankDetailChangeError`, `check_payment_detail(ledger, vendor_id, incoming) -> list[Finding]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/rules/integrity/test_duplicates.py
from datetime import date, timedelta

from ledger_sense.domain.money import money
from ledger_sense.rules.integrity.duplicates import DocumentFingerprint, find_duplicates


def _fp(vendor="Acme Steel", amount="1000.00", days=0, invoice="INV-1", line_hash="h1"):
    return DocumentFingerprint(vendor=vendor, amount=money(amount),
                               doc_date=date(2026, 8, 1) + timedelta(days=days),
                               invoice_number=invoice, line_hash=line_hash)


def test_clean_control_distinct_documents_produce_no_duplicate():
    history = [_fp(invoice="INV-1"),
               _fp(amount="2000.00", invoice="INV-2", line_hash="h2")]
    assert find_duplicates(
        _fp(amount="3000.00", invoice="INV-3", line_hash="h3"), history) == []


def test_same_invoice_number_is_a_duplicate():
    matches = find_duplicates(_fp(invoice="INV-1"), [_fp(invoice="INV-1")])
    assert matches and matches[0].reason == "same invoice number"


def test_same_vendor_amount_within_the_date_window_is_a_duplicate():
    """Detection spans FULL history, not just the current period."""
    matches = find_duplicates(_fp(days=5, invoice="INV-9", line_hash="h9"),
                              [_fp(days=0, invoice="INV-1")])
    assert matches and matches[0].reason == "vendor+amount within date window"


def test_outside_the_date_window_is_not_a_duplicate():
    assert find_duplicates(_fp(days=400, invoice="INV-9", line_hash="h9"),
                           [_fp(days=0, invoice="INV-1")]) == []


def test_identical_line_item_fingerprint_is_a_duplicate():
    matches = find_duplicates(_fp(vendor="Acme Steel Co", amount="999.00",
                                  days=200, invoice="INV-9", line_hash="h1"),
                              [_fp(line_hash="h1")])
    assert matches and matches[0].reason == "identical line-item fingerprint"


def test_an_empty_history_produces_no_duplicates():
    assert find_duplicates(_fp(), []) == []
```

```python
# tests/rules/integrity/test_vendors.py
import pytest

from ledger_sense.domain.vendor import VendorLedger


@pytest.fixture()
def ledger():
    led = VendorLedger()
    led.create("Acme Steel Company")
    return led


def test_an_exact_name_resolves_without_confirmation(ledger):
    match = ledger.resolve("Acme Steel Company")
    assert match.vendor_id is not None and match.needs_confirmation is False


def test_a_close_name_requires_human_confirmation_on_first_match(ledger):
    """B6: auto-merging two vendors is unrecoverable."""
    match = ledger.resolve("Acme Steel Co.")
    assert match.vendor_id is not None
    assert match.needs_confirmation is True


def test_a_confirmed_alias_resolves_without_confirmation_next_time(ledger):
    match = ledger.resolve("Acme Steel Co.")
    ledger.confirm_alias(match.vendor_id, "Acme Steel Co.",
                         source_document="doc://1", confirmed_by="A. Bookkeeper")
    assert ledger.resolve("Acme Steel Co.").needs_confirmation is False


def test_an_unrelated_name_does_not_resolve(ledger):
    assert ledger.resolve("Zenith Glazing").vendor_id is None


def test_every_alias_carries_provenance(ledger):
    match = ledger.resolve("Acme Steel Co.")
    ledger.confirm_alias(match.vendor_id, "Acme Steel Co.",
                         source_document="doc://1", confirmed_by="A. Bookkeeper")
    alias = ledger.get(match.vendor_id).aliases[-1]
    assert alias.source_document == "doc://1"
    assert alias.confirmed_by == "A. Bookkeeper"


def test_confirm_alias_refuses_without_a_named_confirmer(ledger):
    match = ledger.resolve("Acme Steel Co.")
    with pytest.raises(ValueError, match="confirmed_by"):
        ledger.confirm_alias(match.vendor_id, "Acme Steel Co.",
                             source_document="doc://1", confirmed_by="")


def test_a_merge_is_reversible(ledger):
    other = ledger.create("ACME Steel Co")
    keeper = ledger.resolve("Acme Steel Company").vendor_id
    token = ledger.merge(keeper=keeper, absorbed=other, confirmed_by="A. Bookkeeper")
    assert ledger.get(other) is None
    ledger.unmerge(token)
    assert ledger.get(other) is not None


def test_merge_refuses_without_a_named_confirmer(ledger):
    other = ledger.create("ACME Steel Co")
    keeper = ledger.resolve("Acme Steel Company").vendor_id
    with pytest.raises(ValueError, match="confirmed_by"):
        ledger.merge(keeper=keeper, absorbed=other, confirmed_by="")
```

```python
# tests/rules/integrity/test_bank_details.py
import ast
import pathlib
from datetime import datetime, timezone

import pytest

from ledger_sense.domain.vendor import PaymentDetail, VendorLedger
from ledger_sense.rules.integrity.bank_details import (
    BankDetailChangeError, check_payment_detail,
)


@pytest.fixture()
def ledger():
    led = VendorLedger()
    vendor_id = led.create("Acme Steel Company")
    led.record_payment_detail(vendor_id, PaymentDetail(
        account_last4="4321", routing_last4="8765",
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source_document="doc://original"), confirmed_by="A. Bookkeeper")
    return led, vendor_id


def _incoming(account="4321", routing="8765"):
    return PaymentDetail(account_last4=account, routing_last4=routing,
                         observed_at=datetime.now(timezone.utc),
                         source_document="doc://current")


def test_matching_payment_details_produce_no_finding(ledger):
    led, vendor_id = ledger
    assert check_payment_detail(led, vendor_id, _incoming()) == []


def test_a_changed_account_number_is_a_hard_stop(ledger):
    led, vendor_id = ledger
    findings = check_payment_detail(led, vendor_id, _incoming(account="9999"))
    assert [f.rule_id for f in findings] == ["I3"]
    assert "hard stop" in findings[0].message.lower()


def test_a_changed_routing_number_is_also_a_hard_stop(ledger):
    led, vendor_id = ledger
    assert [f.rule_id for f in
            check_payment_detail(led, vendor_id, _incoming(routing="0000"))] == ["I3"]


def test_recording_a_changed_detail_without_confirmation_raises(ledger):
    """Never auto-approve and never auto-update. B5."""
    led, vendor_id = ledger
    with pytest.raises(BankDetailChangeError, match="human confirmation"):
        led.record_payment_detail(vendor_id, _incoming(account="9999"), confirmed_by="")


def test_history_is_append_only_so_the_diff_survives(ledger):
    led, vendor_id = ledger
    led.record_payment_detail(vendor_id, _incoming(account="9999"),
                              confirmed_by="A. Bookkeeper")
    history = led.get(vendor_id).payment_history
    assert len(history) == 2
    assert history[0].account_last4 == "4321"


def test_no_code_path_mutates_payment_history_outside_the_ledger():
    """record_payment_detail is the only writer, and it takes confirmed_by."""
    offenders = []
    for path in pathlib.Path("ledger_sense").rglob("*.py"):
        if path.name == "vendor.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            attr = getattr(node.func, "attr", "")
            owner = getattr(getattr(node.func, "value", None), "attr", "")
            if attr in {"append", "extend", "clear"} and owner == "payment_history":
                offenders.append(f"{path}:{node.lineno}")
    assert offenders == [], f"payment_history mutated outside vendor.py: {offenders}"


def test_no_full_account_number_is_ever_stored():
    """Last four only. A full account number in our store is a breach waiting."""
    assert set(PaymentDetail.model_fields) == {
        "account_last4", "routing_last4", "observed_at", "source_document"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/rules/integrity/ -v`
Expected: FAIL — `ModuleNotFoundError` for `ledger_sense.domain.vendor` and the three rule modules.

- [ ] **Step 3: Write the vendor ledger**

```python
# ledger_sense/domain/vendor.py
"""Canonical vendors, aliases and payment-detail history.

B6: auto-merging two vendors is UNRECOVERABLE. Every first-time fuzzy match
requires human confirmation; merges are reversible; every alias keeps provenance.

B5: a bank-detail change is a HARD STOP. This module surfaces and records; it
never auto-approves and never auto-updates. Invoice redirection fraud is the
single most expensive failure mode in AP.

Only the last four digits are ever stored. A full account number in our store
is a breach waiting to happen.
"""

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

EXACT = 100.0
CONFIRM_THRESHOLD = 85.0


class PaymentDetail(BaseModel):
    account_last4: str
    routing_last4: str
    observed_at: datetime
    source_document: str


class VendorAlias(BaseModel):
    name: str
    first_seen: datetime
    source_document: str
    confirmed_by: str


class CanonicalVendor(BaseModel):
    id: str
    name: str
    aliases: list[VendorAlias] = Field(default_factory=list)
    payment_history: list[PaymentDetail] = Field(default_factory=list)


class Match(BaseModel):
    vendor_id: str | None
    score: float
    needs_confirmation: bool


class MergeToken(BaseModel):
    keeper: str
    absorbed_snapshot: CanonicalVendor
    confirmed_by: str


class BankDetailChangeError(Exception):
    pass


class VendorLedger:
    def __init__(self) -> None:
        self._vendors: dict[str, CanonicalVendor] = {}

    def create(self, name: str) -> str:
        vendor_id = str(uuid.uuid4())
        self._vendors[vendor_id] = CanonicalVendor(id=vendor_id, name=name)
        return vendor_id

    def get(self, vendor_id: str) -> CanonicalVendor | None:
        return self._vendors.get(vendor_id)

    def resolve(self, name: str) -> Match:
        best_id, best_score = None, 0.0
        for vendor in self._vendors.values():
            candidates = [vendor.name] + [a.name for a in vendor.aliases]
            score = max(fuzz.token_sort_ratio(name, c) for c in candidates)
            if score > best_score:
                best_id, best_score = vendor.id, score
        if best_score >= EXACT:
            return Match(vendor_id=best_id, score=best_score, needs_confirmation=False)
        if best_score >= CONFIRM_THRESHOLD:
            return Match(vendor_id=best_id, score=best_score, needs_confirmation=True)
        return Match(vendor_id=None, score=best_score, needs_confirmation=False)

    def confirm_alias(self, vendor_id: str, name: str, source_document: str,
                      confirmed_by: str) -> None:
        if not confirmed_by:
            raise ValueError("confirm_alias requires confirmed_by")
        self._vendors[vendor_id].aliases.append(VendorAlias(
            name=name, first_seen=datetime.now(timezone.utc),
            source_document=source_document, confirmed_by=confirmed_by))

    def merge(self, keeper: str, absorbed: str, confirmed_by: str) -> MergeToken:
        if not confirmed_by:
            raise ValueError("merge requires confirmed_by — B6, merges without "
                             "provenance are unrecoverable")
        snapshot = self._vendors.pop(absorbed)
        self._vendors[keeper].aliases.append(VendorAlias(
            name=snapshot.name, first_seen=datetime.now(timezone.utc),
            source_document="merge", confirmed_by=confirmed_by))
        return MergeToken(keeper=keeper, absorbed_snapshot=snapshot,
                          confirmed_by=confirmed_by)

    def unmerge(self, token: MergeToken) -> None:
        self._vendors[token.absorbed_snapshot.id] = token.absorbed_snapshot
        keeper = self._vendors[token.keeper]
        keeper.aliases = [a for a in keeper.aliases
                          if a.name != token.absorbed_snapshot.name]

    def record_payment_detail(self, vendor_id: str, detail: PaymentDetail,
                              confirmed_by: str) -> None:
        """Append-only. A CHANGED detail requires a named human confirmer."""
        vendor = self._vendors[vendor_id]
        current = vendor.payment_history[-1] if vendor.payment_history else None
        changed = current is not None and (
            current.account_last4 != detail.account_last4
            or current.routing_last4 != detail.routing_last4)
        if changed and not confirmed_by:
            raise BankDetailChangeError(
                "a bank-detail change requires human confirmation; this system "
                "never auto-approves and never auto-updates a payment record")
        vendor.payment_history.append(detail)
```

- [ ] **Step 4: Write the three rule modules**

```python
# ledger_sense/rules/integrity/duplicates.py
"""I1 — duplicate detection across FULL history, by three fingerprints."""

from datetime import date, timedelta
from typing import NamedTuple

from pydantic import BaseModel

from ledger_sense.domain.money import Money

DATE_WINDOW = timedelta(days=30)


class DocumentFingerprint(BaseModel):
    vendor: str
    amount: Money
    doc_date: date
    invoice_number: str | None
    line_hash: str


class DuplicateMatch(NamedTuple):
    reason: str
    against: DocumentFingerprint


def find_duplicates(candidate: DocumentFingerprint,
                    history: list[DocumentFingerprint]) -> list[DuplicateMatch]:
    matches: list[DuplicateMatch] = []
    for prior in history:
        if (candidate.invoice_number is not None
                and candidate.invoice_number == prior.invoice_number):
            matches.append(DuplicateMatch("same invoice number", prior))
            continue
        if (candidate.vendor == prior.vendor and candidate.amount == prior.amount
                and abs(candidate.doc_date - prior.doc_date) <= DATE_WINDOW):
            matches.append(DuplicateMatch("vendor+amount within date window", prior))
            continue
        if candidate.line_hash == prior.line_hash:
            matches.append(DuplicateMatch("identical line-item fingerprint", prior))
    return matches
```

```python
# ledger_sense/rules/integrity/bank_details.py
"""I3 — bank-detail change is a HARD STOP.

Invoice redirection fraud is the single most expensive failure mode in AP.
Catching one instance pays for the product for years.

B5 cuts both ways: flagging a legitimate change delays a payment; missing a
fraudulent one sends money to an attacker. Position: SURFACE and REQUIRE HUMAN
CONFIRMATION. Never approve, never auto-update. Terms must say the client is
responsible for verification.
"""

from ledger_sense.domain.money import money
from ledger_sense.domain.vendor import BankDetailChangeError, PaymentDetail, VendorLedger
from ledger_sense.rules.engine import Finding

__all__ = ["BankDetailChangeError", "check_payment_detail"]


def check_payment_detail(ledger: VendorLedger, vendor_id: str,
                         incoming: PaymentDetail) -> list[Finding]:
    vendor = ledger.get(vendor_id)
    if vendor is None or not vendor.payment_history:
        return []
    current = vendor.payment_history[-1]
    if (current.account_last4 == incoming.account_last4
            and current.routing_last4 == incoming.routing_last4):
        return []
    return [Finding(
        "I3", "vendor.payment_detail",
        f"HARD STOP: payment details for {vendor.name} differ from the record on "
        f"file (account ....{current.account_last4} -> ....{incoming.account_last4}). "
        f"Verify with the vendor using a known-good phone number before paying. "
        f"This system will not update the record without your confirmation.",
        money("0.00"), money("0.00"))]
```

```python
# ledger_sense/rules/integrity/vendors.py
"""I2 — vendor resolution requires human confirmation on first fuzzy match."""

from ledger_sense.domain.money import money
from ledger_sense.domain.vendor import VendorLedger
from ledger_sense.rules.engine import Finding


def check_vendor_resolution(ledger: VendorLedger, name: str) -> list[Finding]:
    match = ledger.resolve(name)
    if match.vendor_id is None:
        return [Finding("I2", "vendor.name",
                        f"{name!r} does not match any canonical vendor; confirm "
                        f"whether this is a new vendor",
                        money("0.00"), money("0.00"))]
    if match.needs_confirmation:
        canonical = ledger.get(match.vendor_id)
        return [Finding("I2", "vendor.name",
                        f"{name!r} closely matches {canonical.name!r} "
                        f"(score {match.score:.0f}). Confirm before linking — "
                        f"merging two genuinely different vendors is unrecoverable",
                        money("0.00"), money("0.00"))]
    return []
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/rules/integrity/ -v`
Expected: PASS, 21 tests. **DoD 3.**

- [ ] **Step 6: Commit**

```bash
git add ledger_sense/domain/vendor.py ledger_sense/rules/integrity/ tests/rules/integrity/
git commit -m "feat(rules): I1-I3 duplicates, vendor resolution, bank-detail hard stop"
```

---

## Phase 3 exit criteria

- [ ] `uv run pytest tests/rules -v` green — every rule has a tripping fixture and a clean control
- [ ] `tests/rules/convention/test_sources.py` green with **real** confirmations; no `REPLACE:` left anywhere
- [ ] `CONFIGURED_JURISDICTION` set to a real jurisdiction, statute cited in `sources.TAX`
- [ ] The four regression tests pass: C1 five-percent contract, C2 no-stepping contract, C8 additive-change-order suppression, C11 waiver direction
- [ ] `uv run mypy --strict ledger_sense` green
- [ ] **DoD 3 satisfied** — all validation rules implemented, each with a tripping fixture and a clean control
- [ ] Coverage ≥75% on `ledger_sense/rules`

**Then Phase 4.** It has no domain dependency and can run ahead of this phase if the controller session slips.
