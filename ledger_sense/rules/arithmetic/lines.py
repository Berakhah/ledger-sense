"""A2, A3, A4 — per-line arithmetic."""

from decimal import DivisionByZero, InvalidOperation

from ledger_sense.domain.models import Contract, PayApplication
from ledger_sense.domain.money import money, quantize
from ledger_sense.rules.engine import Finding
from ledger_sense.rules.registry import register


@register("A2", "arithmetic")
def rule_a2(app: PayApplication, contract: Contract) -> list[Finding]:
    findings = []
    for line in app.lines:
        expected = money(line.previous + line.this_period + line.stored)
        if expected != line.total_completed_and_stored:
            findings.append(Finding(
                "A2", f"line[{line.item_no}].total_completed_and_stored",
                "previous + this period + stored does not equal total completed and stored",
                expected, line.total_completed_and_stored))
    return findings


@register("A3", "arithmetic")
def rule_a3(app: PayApplication, contract: Contract) -> list[Finding]:
    """Spec §6.2 A3: balance to finish = scheduled value - total completed and stored.

    REVISED (F-03). The original only flagged a negative balance — a sanity check,
    not the rule the spec defines. The continuation sheet REPORTS a balance-to-
    finish column; reconciling reported against computed is what catches the
    transcription error, and it is the reason ContinuationLine now carries the field.
    """
    findings = []
    for line in app.lines:
        computed = money(line.scheduled_value - line.total_completed_and_stored)
        if line.balance_to_finish is None:
            continue                      # not extracted; A3b still guards the sign
        if computed != line.balance_to_finish:
            findings.append(Finding(
                "A3", f"line[{line.item_no}].balance_to_finish",
                "reported balance to finish does not equal scheduled value minus "
                "total completed and stored",
                computed, line.balance_to_finish))
    return findings


@register("A3b", "arithmetic")
def rule_a3b(app: PayApplication, contract: Contract) -> list[Finding]:
    """Overbilling sanity check, kept separate from A3 because it is a different
    claim and because it legitimately fires when a change order raised the
    scheduled value and the SOV line has not been revised — see A9 (F-04)."""
    return [Finding("A3b", f"line[{line.item_no}].total_completed_and_stored",
                    "total completed and stored exceeds the scheduled value",
                    line.scheduled_value, line.total_completed_and_stored)
            for line in app.lines
            if line.total_completed_and_stored > line.scheduled_value]


@register("A4", "arithmetic")
def rule_a4(app: PayApplication, contract: Contract) -> list[Finding]:
    """Spec §6.2 A4: percentage complete = total / scheduled value, at the
    contract's rounding.

    REVISED (F-03). Same defect as A3 — the original never compared against the
    REPORTED percentage, because the model had no field for it.
    """
    findings = []
    for line in app.lines:
        if line.percent_complete is None or line.scheduled_value == 0:
            continue
        try:
            computed = quantize(
                money(line.total_completed_and_stored / line.scheduled_value),
                contract.rounding.percent_places)
        except (DivisionByZero, InvalidOperation):
            continue
        if computed != line.percent_complete:
            findings.append(Finding(
                "A4", f"line[{line.item_no}].percent_complete",
                "reported percentage complete does not match total completed and "
                "stored divided by scheduled value at the contract's rounding",
                computed, line.percent_complete))
    return findings


@register("A4b", "arithmetic")
def rule_a4b(app: PayApplication, contract: Contract) -> list[Finding]:
    """Work billed against a line with no scheduled value. Not a percentage
    check — a division by zero is not a rounding disagreement."""
    return [Finding("A4b", f"line[{line.item_no}].scheduled_value",
                    "work billed against a line with no scheduled value",
                    money("0.00"), line.total_completed_and_stored)
            for line in app.lines
            if line.scheduled_value == 0 and line.total_completed_and_stored != 0]
