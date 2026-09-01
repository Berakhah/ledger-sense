"""A5-A8 — the payment chain.

ORDER MATTERS AND IS THE COMMON ERROR:
    total earned less retainage = total completed and stored - retainage
    current payment due         = total earned less retainage - previous payments

Previous payments are subtracted ONCE, at the second step. Change orders are
already inside the adjusted contract sum and are not re-added here.
"""

from ledger_sense.domain.models import Contract, PayApplication
from ledger_sense.domain.money import Money, money
from ledger_sense.rules.engine import Finding
from ledger_sense.rules.registry import register


def total_earned_less_retainage(app: PayApplication) -> Money:
    return money(app.total_completed_and_stored - app.retainage_total)


def current_payment_due(app: PayApplication) -> Money:
    return money(total_earned_less_retainage(app) - app.previous_payments)


@register("A5", "arithmetic")
def rule_a5(app: PayApplication, contract: Contract) -> list[Finding]:
    value = total_earned_less_retainage(app)
    if value < 0:
        return [Finding("A5", "total_earned_less_retainage",
                        "retainage exceeds total completed and stored",
                        money("0.00"), value)]
    return []


@register("A6", "arithmetic")
def rule_a6(app: PayApplication, contract: Contract) -> list[Finding]:
    value = current_payment_due(app)
    if value < 0:
        return [Finding("A6", "current_payment_due",
                        "previous payments exceed total earned less retainage",
                        money("0.00"), value)]
    return []


@register("A7", "arithmetic")
def rule_a7(app: PayApplication, contract: Contract) -> list[Finding]:
    expected = app.adjusted_contract_sum(contract)
    if expected < 0:
        return [Finding("A7", "adjusted_contract_sum",
                        "net change orders reduce the contract sum below zero",
                        money("0.00"), expected)]
    return []


@register("A8", "arithmetic")
def rule_a8(app: PayApplication, contract: Contract) -> list[Finding]:
    if not app.lines:
        return []
    expected = money(sum((line.retainage for line in app.lines), money("0")))
    if expected != app.retainage_total:
        return [Finding("A8", "retainage_total",
                        "per-line retainage does not sum to the summary retainage",
                        expected, app.retainage_total)]
    return []
