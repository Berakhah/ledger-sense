from datetime import date
from decimal import Decimal

from ledger_sense.domain.models import ContinuationLine, Contract, PayApplication
from ledger_sense.domain.money import Money, RoundingPolicy, money
from ledger_sense.rules.arithmetic.lines import rule_a2, rule_a3, rule_a3b, rule_a4, rule_a4b


def _c(places: int = 2) -> Contract:
    return Contract(id="C-1", original_sum=money("1000.00"),
                    rounding=RoundingPolicy(places, 4), retainage_rate=Decimal("0.10"),
                    retainage_steps=[], jurisdiction=None)


def _line(prev: str, this: str, stored: str, total: str, sv: str = "1000.00",
          balance_to_finish: Money | None = None,
          percent_complete: Decimal | None = None) -> ContinuationLine:
    return ContinuationLine(item_no="1", description="x", scheduled_value=money(sv),
                            previous=money(prev), this_period=money(this),
                            stored=money(stored), total_completed_and_stored=money(total),
                            retainage=money("0.00"), balance_to_finish=balance_to_finish,
                            percent_complete=percent_complete)


def _app(line: ContinuationLine) -> PayApplication:
    return PayApplication(contract_id="C-1", application_no=1, period_to=date(2026, 8, 31),
                          lines=[line], change_orders_net=money("0.00"),
                          retainage_total=money("0.00"), previous_payments=money("0.00"),
                          total_completed_and_stored=line.total_completed_and_stored)


def test_a2_clean_control() -> None:
    assert rule_a2(_app(_line("100.00", "50.00", "25.00", "175.00")), _c()) == []


def test_a2_trips_when_components_do_not_sum() -> None:
    findings = rule_a2(_app(_line("100.00", "50.00", "25.00", "180.00")), _c())
    assert [f.rule_id for f in findings] == ["A2"]
    assert findings[0].expected == money("175.00")


# BUG FIX (pre-flight review): as printed in the brief this test called rule_a3, but its
# fixture never sets balance_to_finish (defaults to None), and rule_a3 skips any line where
# that field is None. As printed the test would get [] back, not ["A3"]. The scenario it
# actually exercises — total exceeding scheduled value — is A3b's docstring exactly, so it
# now calls rule_a3b and asserts the rule_id that function actually emits.
def test_a3_trips_when_total_exceeds_scheduled_value() -> None:
    findings = rule_a3b(_app(_line("0.00", "1100.00", "0.00", "1100.00", sv="1000.00")), _c())
    assert [f.rule_id for f in findings] == ["A3b"]


def test_a3b_clean_control() -> None:
    """Total at or under scheduled value: no overbilling, A3b stays silent."""
    assert rule_a3b(_app(_line("0.00", "175.00", "0.00", "175.00", sv="1000.00")), _c()) == []


def test_a3_silent_when_balance_to_finish_not_extracted() -> None:
    """None means the column was not extracted, not that it agrees. A3 must not
    manufacture a finding from a value the extractor never read — even here, where
    the underlying total (1100) exceeds the scheduled value (1000) and would trip
    A3b. A3 only ever compares REPORTED against COMPUTED."""
    line = _line("0.00", "1100.00", "0.00", "1100.00", sv="1000.00")
    assert line.balance_to_finish is None
    assert rule_a3(_app(line), _c()) == []


def test_a3_reconciliation_clean_control_when_reported_matches_computed() -> None:
    line = _line("200.00", "100.00", "0.00", "300.00", sv="1000.00",
                 balance_to_finish=money("700.00"))
    assert rule_a3(_app(line), _c()) == []


def test_a3_reconciliation_trips_when_reported_balance_is_a_cent_off() -> None:
    line = _line("200.00", "100.00", "0.00", "300.00", sv="1000.00",
                 balance_to_finish=money("700.01"))
    findings = rule_a3(_app(line), _c())
    assert [f.rule_id for f in findings] == ["A3"]
    assert findings[0].expected == money("700.00")
    assert findings[0].actual == money("700.01")


# BUG FIX (pre-flight review): as printed in the brief this test called rule_a4, but its
# fixture never sets percent_complete (defaults to None), and rule_a4 skips any line where
# that field is None. As printed the test would get [] back, not ["A4"]. The scenario it
# actually exercises — work billed against a zero scheduled value — is A4b's docstring
# exactly, so it now calls rule_a4b and asserts the rule_id that function actually emits.
def test_a4_trips_on_work_billed_against_a_zero_scheduled_value() -> None:
    line = _line("0.00", "500.00", "0.00", "500.00", sv="0.00")
    findings = rule_a4b(_app(line), _c())
    assert [f.rule_id for f in findings] == ["A4b"]


def test_a4b_clean_control() -> None:
    """Non-zero scheduled value: no division-by-zero exposure, A4b stays silent."""
    line = _line("0.00", "500.00", "0.00", "500.00", sv="1000.00")
    assert rule_a4b(_app(line), _c()) == []


def test_a4_silent_when_percent_complete_not_extracted() -> None:
    """None means the column was not extracted. A4 must not manufacture a finding
    from a value the extractor never read."""
    line = _line("0.00", "500.00", "0.00", "500.00", sv="1000.00")
    assert line.percent_complete is None
    assert rule_a4(_app(line), _c()) == []


def test_a4_reconciliation_clean_control_when_reported_matches_computed() -> None:
    line = _line("0.00", "250.00", "0.00", "250.00", sv="1000.00",
                 percent_complete=Decimal("0.2500"))
    assert rule_a4(_app(line), _c()) == []


def test_a4_reconciliation_trips_when_reported_percent_is_off_at_the_contracts_rounding() -> None:
    line = _line("0.00", "250.00", "0.00", "250.00", sv="1000.00",
                 percent_complete=Decimal("0.2501"))
    findings = rule_a4(_app(line), _c())
    assert [f.rule_id for f in findings] == ["A4"]
    assert findings[0].expected == Decimal("0.2500")
    assert findings[0].actual == Decimal("0.2501")
