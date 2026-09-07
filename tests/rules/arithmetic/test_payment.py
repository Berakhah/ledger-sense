from datetime import date
from decimal import Decimal

from ledger_sense.domain.models import (
    ContinuationLine,
    Contract,
    PayApplication,
    RetainageTerms,
)
from ledger_sense.domain.money import RoundingPolicy, money
from ledger_sense.rules.arithmetic.payment import (
    current_payment_due,
    rule_a5,
    rule_a6,
    rule_a7,
    rule_a8,
    total_earned_less_retainage,
)


def _c() -> Contract:
    return Contract(id="C-1", original_sum=money("1000.00"),
                    rounding=RoundingPolicy(2, 4),
                    retainage=RetainageTerms(base_rate=Decimal("0.10")))


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


def test_a5_clean_control_when_retainage_does_not_exceed_total() -> None:
    """rule_a5 produces no finding when retainage is less than total."""
    findings = rule_a5(_app("500.00", "50.00", "0.00"), _c())
    assert findings == []


def test_a5_trips_when_retainage_exceeds_total() -> None:
    """rule_a5 produces a finding when retainage exceeds total."""
    findings = rule_a5(_app("100.00", "150.00", "0.00"), _c())
    assert [f.rule_id for f in findings] == ["A5"]
    assert findings[0].field == "total_earned_less_retainage"
    assert findings[0].expected == money("0.00")
    assert findings[0].actual == money("-50.00")


def test_a6_clean_control_when_previous_payments_do_not_exceed_earned() -> None:
    """rule_a6 produces no finding when previous payments are less than earned."""
    findings = rule_a6(_app("500.00", "50.00", "200.00"), _c())
    assert findings == []


def test_a6_trips_when_previous_payments_exceed_earned() -> None:
    """rule_a6 produces a finding when previous payments exceed earned."""
    findings = rule_a6(_app("100.00", "50.00", "200.00"), _c())
    assert [f.rule_id for f in findings] == ["A6"]
    assert findings[0].field == "current_payment_due"
    assert findings[0].expected == money("0.00")
    assert findings[0].actual == money("-150.00")


def test_a7_clean_control_when_change_orders_do_not_reduce_sum_below_zero() -> None:
    """rule_a7 produces no finding when adjusted contract sum is non-negative."""
    findings = rule_a7(_app("0.00", "0.00", "0.00"), _c())
    assert findings == []


def test_a7_trips_when_net_change_orders_reduce_sum_below_zero() -> None:
    """rule_a7 produces a finding when net change orders reduce the contract sum below zero."""
    app = PayApplication(contract_id="C-1", application_no=2, period_to=date(2026, 8, 31),
                         lines=[], change_orders_net=money("-1500.00"),
                         retainage_total=money("0.00"),
                         previous_payments=money("0.00"),
                         total_completed_and_stored=money("0.00"))
    findings = rule_a7(app, _c())
    assert [f.rule_id for f in findings] == ["A7"]
    assert findings[0].field == "adjusted_contract_sum"
    assert findings[0].expected == money("0.00")
    assert findings[0].actual == money("-500.00")


def test_a8_silent_when_continuation_sheet_lines_not_extracted() -> None:
    """rule_a8 produces no finding when app.lines is empty (lines not extracted)."""
    findings = rule_a8(_app("500.00", "50.00", "0.00", ()), _c())
    assert findings == []
