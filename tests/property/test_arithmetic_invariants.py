from hypothesis import given, settings

from ledger_sense.domain.models import Contract, PayApplication
from ledger_sense.domain.money import money
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


@given(consistent_pay_applications())
@settings(max_examples=300)
def test_payment_chain_never_double_counts_previous_payments(
    pair: tuple[Contract, PayApplication],
) -> None:
    _, app = pair
    app = app.model_copy(update={"previous_payments": money("100.00"),
                                 "retainage_total": money("0.00")})
    assert current_payment_due(app) == money(
        total_earned_less_retainage(app) - money("100.00"))


@given(consistent_pay_applications())
@settings(max_examples=300)
def test_no_computed_value_exceeds_the_contracts_decimal_places(
    pair: tuple[Contract, PayApplication],
) -> None:
    contract, app = pair
    due = current_payment_due(app)
    exponent = due.as_tuple().exponent
    assert isinstance(exponent, int)  # money is never NaN/Infinity (F-01)
    assert -exponent <= contract.rounding.money_places
