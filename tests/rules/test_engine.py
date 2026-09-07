from datetime import date
from decimal import Decimal
from typing import NamedTuple

from ledger_sense.domain.models import Contract, PayApplication, RetainageTerms
from ledger_sense.domain.money import RoundingPolicy, money
from ledger_sense.rules.engine import Finding, run_rules, unverifiable_money_fields
from ledger_sense.rules.registry import register


def _contract() -> Contract:
    return Contract(id="C-1", original_sum=money("1000.00"),
                    rounding=RoundingPolicy(2, 4),
                    retainage=RetainageTerms(base_rate=Decimal("0.10")))


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


class _Unextractable(NamedTuple):
    """Stand-in for extract.assemble.UnextractableField (unmerged branch).

    unverifiable_money_fields() duck-types on `.field` precisely so it does not
    need to import that type — any object with a `.field: str` attribute works.
    """
    field: str
    reason: str = "not found on page"


def test_unverifiable_money_fields_is_empty_with_no_provenance_key() -> None:
    app = _app()
    assert unverifiable_money_fields(app) == ()


def test_unverifiable_money_fields_is_empty_when_unextractable_list_is_empty() -> None:
    app = _app().model_copy(update={"provenance": {"_unextractable": []}})
    assert unverifiable_money_fields(app) == ()


def test_unverifiable_money_fields_excludes_header_scalars() -> None:
    """Arithmetic rules never read contract_id/application_no/period_to (F-15) —
    losing them shouldn't taint a footing check."""
    app = _app().model_copy(update={"provenance": {"_unextractable": [
        _Unextractable("contract_id"),
        _Unextractable("application_no"),
        _Unextractable("period_to"),
    ]}})
    assert unverifiable_money_fields(app) == ()


def test_unverifiable_money_fields_includes_the_lines_grid() -> None:
    app = _app().model_copy(update={"provenance": {"_unextractable": [
        _Unextractable("lines", "continuation grid could not be reconstructed"),
    ]}})
    assert unverifiable_money_fields(app) == ("lines",)


def test_unverifiable_money_fields_includes_a_per_line_cell() -> None:
    app = _app().model_copy(update={"provenance": {"_unextractable": [
        _Unextractable("line[3].stored", "value did not parse as money"),
    ]}})
    assert unverifiable_money_fields(app) == ("line[3].stored",)


def test_unverifiable_money_fields_includes_the_four_summary_totals() -> None:
    app = _app().model_copy(update={"provenance": {"_unextractable": [
        _Unextractable("total_completed_and_stored"),
        _Unextractable("retainage_total"),
        _Unextractable("previous_payments"),
        _Unextractable("change_orders_net"),
        _Unextractable("contract_id"),  # excluded, mixed in on purpose
    ]}})
    assert unverifiable_money_fields(app) == (
        "total_completed_and_stored", "retainage_total",
        "previous_payments", "change_orders_net",
    )


def test_unverifiable_money_fields_accepts_dict_shaped_entries() -> None:
    """A serialization round-trip (e.g. JSON) can turn UnextractableField
    into a plain dict; the checkpoint must still recognise it."""
    app = _app().model_copy(update={"provenance": {"_unextractable": [
        {"field": "lines", "reason": "continuation grid could not be reconstructed"},
    ]}})
    assert unverifiable_money_fields(app) == ("lines",)


def test_unverifiable_money_fields_fails_closed_on_a_malformed_container() -> None:
    """If `_unextractable` is present but not a list, the shape is not what
    this function understands. Fail closed: report every Money-relevant
    field as unverifiable, not none."""
    app = _app().model_copy(update={"provenance": {"_unextractable": "not-a-list"}})
    assert unverifiable_money_fields(app) == (
        "lines", "total_completed_and_stored", "retainage_total",
        "previous_payments", "change_orders_net",
    )


def test_unverifiable_money_fields_deduplicates_preserving_order() -> None:
    app = _app().model_copy(update={"provenance": {"_unextractable": [
        _Unextractable("lines"),
        _Unextractable("lines"),
    ]}})
    assert unverifiable_money_fields(app) == ("lines",)


def test_a1_silent_pass_is_caught_by_the_checkpoint_not_by_a1() -> None:
    """The bug this plan exists to fix: A1 correctly reports the (fabricated)
    data as internally consistent when both the grid and the summary total
    failed to extract and were defaulted. A1 alone can't tell the difference
    between "nothing to bill" and "nothing was extracted" — that is exactly
    why this is a separate checkpoint, not a change to A1 itself.
    """
    from ledger_sense.rules.arithmetic import foot  # noqa: F401  (registers A1)

    app = _app().model_copy(update={"provenance": {"_unextractable": [
        _Unextractable("lines", "continuation grid produced no data rows"),
        _Unextractable("total_completed_and_stored", "label not found on page"),
    ]}})

    findings = run_rules(app, _contract(), rule_classes={"arithmetic"})
    assert not any(f.rule_id == "A1" for f in findings), "A1 should stay silent here"

    unverifiable = unverifiable_money_fields(app)
    assert unverifiable == ("lines", "total_completed_and_stored")
