from datetime import date
from decimal import Decimal

from ledger_sense.domain.models import ContinuationLine, Contract, PayApplication
from ledger_sense.domain.money import RoundingPolicy, money
from ledger_sense.rules.arithmetic.payment import (
    current_payment_due,
    rule_a8,
    total_earned_less_retainage,
)


def _c() -> Contract:
    return Contract(id="C-1", original_sum=money("1000.00"),
                    rounding=RoundingPolicy(2, 4), retainage_rate=Decimal("0.10"),
                    retainage_steps=[], jurisdiction=None)


def _app(total: str, retainage: str, previous_payments: str,
         lines: tuple[ContinuationLine, ...] = ()) -> PayApplication:
    return PayApplication(contract_id="C-1", application_no=2, period_to=date(2026, 8, 31),
                          lines=list(lines), change_orders_net=money("0.00"),
                          retainage_total=money(retainage),
                          previous_payments=money(previous_payments),
                          total_completed_and_stored=money(total))


def _line(item_no: str, retainage: str) -> ContinuationLine:
    return ContinuationLine(item_no=item_no, description="x",
                            scheduled_value=money("100.00"), previous=money("0.00"),
                            this_period=money("100.00"), stored=money("0.00"),
                            total_completed_and_stored=money("100.00"),
                            retainage=money(retainage))


def test_a5_total_earned_less_retainage_excludes_previous_payments() -> None:
    """The rule the design diagnostic got wrong. Previous payments are NOT
    subtracted here — they come off at the payment-due step."""
    assert total_earned_less_retainage(_app("500.00", "50.00", "200.00")) == money("450.00")


def test_a6_current_payment_due_subtracts_previous_payments_once() -> None:
    assert current_payment_due(_app("500.00", "50.00", "200.00")) == money("250.00")


def test_a6_previous_payments_are_not_double_counted() -> None:
    """Regression guard: 500 - 50 - 200 - 200 = 50 would be the wrong answer."""
    assert current_payment_due(_app("500.00", "50.00", "200.00")) != money("50.00")


def test_a8_per_line_retainage_sums_to_summary_retainage() -> None:
    lines = [_line(str(i), "10.00") for i in range(5)]
    assert rule_a8(_app("500.00", "50.00", "0.00", tuple(lines)), _c()) == []


def test_a8_trips_when_per_line_retainage_misses_the_summary() -> None:
    findings = rule_a8(_app("100.00", "25.00", "0.00", (_line("1", "10.00"),)), _c())
    assert [f.rule_id for f in findings] == ["A8"]
    assert findings[0].expected == money("10.00")
