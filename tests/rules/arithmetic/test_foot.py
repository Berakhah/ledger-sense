from datetime import date
from decimal import Decimal

from ledger_sense.domain.models import ContinuationLine, Contract, PayApplication
from ledger_sense.domain.money import RoundingPolicy, money
from ledger_sense.rules.arithmetic.foot import rule_a1


def _contract(original: str = "1000.00") -> Contract:
    return Contract(id="C-1", original_sum=money(original),
                    rounding=RoundingPolicy(2, 4), retainage_rate=Decimal("0.10"),
                    retainage_steps=[], jurisdiction=None)


def _line(sv: str, total: str) -> ContinuationLine:
    return ContinuationLine(item_no="1", description="x", scheduled_value=money(sv),
                            previous=money("0.00"), this_period=money(total),
                            stored=money("0.00"), total_completed_and_stored=money(total),
                            retainage=money("0.00"))


def _app(lines: list[ContinuationLine], total: str, co: str = "0.00") -> PayApplication:
    return PayApplication(contract_id="C-1", application_no=1, period_to=date(2026, 8, 31),
                          lines=lines, change_orders_net=money(co),
                          retainage_total=money("0.00"), previous_payments=money("0.00"),
                          total_completed_and_stored=money(total))


def test_a1_clean_control_produces_no_finding() -> None:
    app = _app([_line("600.00", "300.00"), _line("400.00", "200.00")], "500.00")
    assert rule_a1(app, _contract()) == []


def test_a1_trips_on_a_one_cent_discrepancy() -> None:
    """Exact, not approximate. A penny is a finding."""
    app = _app([_line("600.00", "300.00"), _line("400.00", "200.00")], "500.01")
    findings = rule_a1(app, _contract())
    assert len(findings) == 1
    assert findings[0].rule_id == "A1"
    assert findings[0].expected == money("500.00")
    assert findings[0].actual == money("500.01")
