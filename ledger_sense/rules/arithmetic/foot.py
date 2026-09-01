"""A1 — footing. Exact equality; no tolerance."""

from ledger_sense.domain.models import Contract, PayApplication
from ledger_sense.domain.money import money
from ledger_sense.rules.engine import Finding
from ledger_sense.rules.registry import register


@register("A1", "arithmetic")
def rule_a1(app: PayApplication, contract: Contract) -> list[Finding]:
    expected = money(sum((line.total_completed_and_stored for line in app.lines),
                         money("0")))
    if expected != app.total_completed_and_stored:
        return [Finding(
            rule_id="A1", field="total_completed_and_stored",
            message="continuation-sheet lines do not foot to the summary",
            expected=expected, actual=app.total_completed_and_stored,
        )]
    return []


# A9 IS NOT HERE — REVISED (F-04). It reads as first-principles checkable and is
# not: it fires on every application where an approved change order exists but the
# schedule of values has not yet been revised to carry it, which is the normal
# mid-period state. Classed `arithmetic` it bypassed the RuleSource gate built to
# stop exactly this, and it would have been the noisiest false positive in the
# product — the "trains a reviewer to dismiss findings" failure the plan correctly
# identifies for C8.
#
# It is a convention rule. It ships in `rules/convention/change_orders.py`
# alongside C7 and C8, with the CHANGE_ORDERS source and a suppression, in
# Phase 3 Task 24. It cannot ship here: `CHANGE_ORDERS` (Phase 3 Task 22) and
# `PayApplication.prior` (Phase 3 Task 21b) do not exist in week 6, so this module
# would not import.
