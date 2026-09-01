from hypothesis import given, settings

from ledger_sense.domain.models import Contract, PayApplication
from ledger_sense.domain.money import money
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
