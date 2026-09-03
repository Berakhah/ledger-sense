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


_SUMMARY_MONEY_FIELDS = frozenset({
    "total_completed_and_stored", "retainage_total",
    "previous_payments", "change_orders_net",
})

_ALL_MONEY_FIELDS: tuple[str, ...] = (
    "lines", "total_completed_and_stored", "retainage_total",
    "previous_payments", "change_orders_net",
)


def unverifiable_money_fields(app: PayApplication) -> tuple[str, ...]:
    """Which Money-relevant fields could not really be extracted.

    Reads `app.provenance["_unextractable"]` (a list of objects carrying a
    `.field: str`, populated by the extraction pipeline) and returns the
    subset that feeds arithmetic rules: the continuation-sheet grid, any
    per-line cell, or one of the four summary Money totals. Header scalars
    (contract_id, application_no, period_to) are excluded — no arithmetic
    rule reads them, so their loss shouldn't taint a footing check.

    `run_rules()` answers "did the rules agree". This answers "was the data
    real". Trust the former only where this returns an empty tuple.

    A malformed `_unextractable` value (present but not a list) fails
    CLOSED: every Money-relevant field is reported unverifiable, rather
    than silently trusting data whose shape this function doesn't
    recognise — the same "until calibrated, everything routes to review"
    posture the confidence-routing design uses elsewhere.
    """
    if "_unextractable" not in app.provenance:
        return ()
    entries = app.provenance["_unextractable"]
    if not isinstance(entries, list):
        return _ALL_MONEY_FIELDS
    fields: list[str] = []
    for entry in entries:
        field = entry.get("field") if isinstance(entry, dict) else getattr(entry, "field", None)
        if not isinstance(field, str):
            continue
        if field == "lines" or field.startswith("line[") or field in _SUMMARY_MONEY_FIELDS:
            fields.append(field)
    return tuple(dict.fromkeys(fields))
