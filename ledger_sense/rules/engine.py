from collections.abc import Iterable
from typing import NamedTuple

from ledger_sense.domain.models import Contract, PayApplication
from ledger_sense.domain.money import Money
from ledger_sense.rules.registry import RuleClass, registered_rules


class Finding(NamedTuple):
    rule_id: str
    field: str
    message: str
    expected: Money
    actual: Money


def run_rules(
    app: PayApplication,
    contract: Contract,
    *,
    rule_classes: Iterable[RuleClass] | None = None,
) -> list[Finding]:
    """Run every registered rule (or only the given rule classes) and collect findings.

    Replaces the `ALL_RULES = [...]` list that was being hand-built at each call
    site. A rule that trusts a defaulted Money("0.00") still reports "consistent"
    here — this function answers "did the rules agree", not "was the data real".
    See `unverifiable_money_fields` for the second question.
    """
    wanted = None if rule_classes is None else frozenset(rule_classes)
    findings: list[Finding] = []
    for rule in registered_rules().values():
        if wanted is not None and rule.rule_class not in wanted:
            continue
        findings.extend(rule.fn(app, contract))
    return findings
