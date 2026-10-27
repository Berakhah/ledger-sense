"""A5b / A6b — the REPORTED payment totals against the computed chain (LS-06).

A5/A6 only check the computed chain for a negative amount. Neither compares what
the document PRINTS for "total earned less retainage" or "current payment due"
with what its own components imply, so a mis-printed payment total passed with
no finding. Same shape as A3/A4: reported versus computed, and a missing
reported value is None (not extracted), never zero."""

from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger_sense.domain.models import Contract, PayApplication, RetainageTerms
from ledger_sense.domain.money import RoundingPolicy, money
from ledger_sense.rules.arithmetic.payment import rule_a5b, rule_a6b


def _contract() -> Contract:
    return Contract(id="C-1", original_sum=money("100000.00"), rounding=RoundingPolicy(2, 4),
                    retainage=RetainageTerms(base_rate=Decimal("0.10")))


def _app(*, earned: str | None = None, due: str | None = None) -> PayApplication:
    """completed 1000.00, retainage 50.00, previous 100.00 -> earned 950.00, due 850.00."""
    return PayApplication(
        contract_id="C-1", application_no=2, period_to=date(2026, 8, 31),
        change_orders_net=money("0.00"), retainage_total=money("50.00"),
        previous_payments=money("100.00"), total_completed_and_stored=money("1000.00"),
        reported_total_earned_less_retainage=None if earned is None else money(earned),
        reported_current_payment_due=None if due is None else money(due))


def test_a5b_clean_control_when_the_printed_earned_total_agrees() -> None:
    assert rule_a5b(_app(earned="950.00"), _contract()) == []


def test_a5b_trips_when_the_printed_earned_total_disagrees() -> None:
    findings = rule_a5b(_app(earned="9999999.00"), _contract())
    assert [(f.rule_id, f.field) for f in findings] == [
        ("A5b", "reported_total_earned_less_retainage")]
    assert findings[0].expected == money("950.00")
    assert findings[0].actual == money("9999999.00")


def test_a5b_is_silent_when_the_printed_value_was_not_extracted() -> None:
    assert rule_a5b(_app(earned=None), _contract()) == []


def test_a6b_clean_control_when_the_printed_payment_due_agrees() -> None:
    assert rule_a6b(_app(due="850.00"), _contract()) == []


def test_a6b_trips_when_the_printed_payment_due_disagrees() -> None:
    findings = rule_a6b(_app(due="9999999.00"), _contract())
    assert [(f.rule_id, f.field) for f in findings] == [
        ("A6b", "reported_current_payment_due")]
    assert findings[0].expected == money("850.00")
    assert findings[0].actual == money("9999999.00")


def test_a6b_is_silent_when_the_printed_value_was_not_extracted() -> None:
    assert rule_a6b(_app(due=None), _contract()) == []


def test_a5b_and_a6b_are_independent() -> None:
    """A wrong printed payment due must not trip the earned rule, and vice versa."""
    assert rule_a5b(_app(earned="950.00", due="1.00"), _contract()) == []
    assert rule_a6b(_app(earned="1.00", due="850.00"), _contract()) == []


@given(completed=st.integers(0, 10**9), retainage=st.integers(0, 10**9),
       previous=st.integers(0, 10**9))
def test_property_a_correctly_printed_chain_never_trips(
        completed: int, retainage: int, previous: int) -> None:
    """Whatever the cents, a document that prints exactly what its components
    imply produces no A5b/A6b finding (money arithmetic is exact)."""
    c, r, p = (money(f"{Decimal(n).scaleb(-2):.2f}") for n in (completed, retainage, previous))
    earned = money(c - r)
    app = PayApplication(
        change_orders_net=money("0.00"), retainage_total=r, previous_payments=p,
        total_completed_and_stored=c, reported_total_earned_less_retainage=earned,
        reported_current_payment_due=money(earned - p))
    assert rule_a5b(app, _contract()) == []
    assert rule_a6b(app, _contract()) == []


@pytest.mark.parametrize("delta", ["0.01", "-0.01", "100.00"])
def test_any_disagreement_however_small_trips(delta: str) -> None:
    assert len(rule_a6b(_app(due=str(money("850.00") + money(delta))), _contract())) == 1
