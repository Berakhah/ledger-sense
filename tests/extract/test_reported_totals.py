"""LS-06: the printed 'total earned less retainage' and 'current payment due'
are extracted, kept with their provenance, and a missing one is recorded as
unextractable instead of being silently dropped."""

import pytest

from ledger_sense.corpus.generate import generate
from ledger_sense.domain.money import money
from ledger_sense.extract.assemble import assemble_pay_application
from ledger_sense.provenance import Claim, FileSource

# Imported for their registration side effect only (F-67): run_rules asks the
# registry, which is empty in a process that never imported the rule modules.
from ledger_sense.rules.arithmetic import foot, lines, payment  # noqa: F401
from ledger_sense.rules.engine import run_rules


def test_the_printed_totals_are_extracted_with_a_box_on_the_page() -> None:
    _contract, app, pdf = generate(seed=1, error="clean")
    built = assemble_pay_application(pdf, "corpus://seed-1")
    earned = money(app.total_completed_and_stored - app.retainage_total)
    assert built.reported_total_earned_less_retainage == earned
    assert built.reported_current_payment_due == money(earned - app.previous_payments)
    for field in ("reported_total_earned_less_retainage", "reported_current_payment_due"):
        claim = built.provenance[field]
        assert isinstance(claim, Claim)
        assert isinstance(claim.source, FileSource)
        assert claim.source.bbox is not None


@pytest.mark.parametrize("error,rule,field", [
    ("A5b_earned_misprinted", "A5b", "reported_total_earned_less_retainage"),
    ("A6b_payment_due_misprinted", "A6b", "reported_current_payment_due")])
def test_a_misprinted_total_is_flagged_and_points_at_the_printed_cell(
        error: str, rule: str, field: str) -> None:
    contract, _app, pdf = generate(seed=1, error=error)  # type: ignore[arg-type]
    built = assemble_pay_application(pdf, "corpus://seed-1")
    findings = run_rules(built, contract, rule_classes={"arithmetic"})
    assert [f.rule_id for f in findings] == [rule]
    assert findings[0].field == field
    assert field in built.provenance


def test_a_printed_total_that_cannot_be_found_is_recorded_not_dropped() -> None:
    _contract, _app, pdf = generate(seed=1, error="clean")
    built = assemble_pay_application(pdf, "corpus://seed-1", frozenset({"Due"}))
    assert built.reported_current_payment_due is None
    unextractable = built.provenance["_unextractable"]
    assert isinstance(unextractable, list)
    assert "reported_current_payment_due" in {entry.field for entry in unextractable}
