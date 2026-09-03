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
