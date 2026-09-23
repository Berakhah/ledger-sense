from datetime import date
from decimal import Decimal

from ledger_sense.domain.models import (
    ContinuationLine,
    Contract,
    PayApplication,
    RetainageStep,
    RetainageTerms,
)
from ledger_sense.domain.money import RoundingPolicy, money


def _contract() -> Contract:
    return Contract(
        id="C-1",
        original_sum=money("100000.00"),
        rounding=RoundingPolicy(money_places=2, percent_places=4),
        retainage=RetainageTerms(
            base_rate=Decimal("0.10"),
            steps=[RetainageStep(at_percent_complete=Decimal("0.50"),
                                 new_rate=Decimal("0.05"))],
        ),
    )


def _empty_app(co: str = "0.00") -> PayApplication:
    return PayApplication(
        contract_id="C-1", application_no=1, period_to=date(2026, 8, 31),
        lines=[], change_orders_net=money(co),
        retainage_total=money("0.00"), previous_payments=money("0.00"),
        total_completed_and_stored=money("0.00"),
    )


def test_contract_carries_its_own_rounding_and_retainage_terms() -> None:
    c = _contract()
    assert c.rounding.money_places == 2
    assert c.retainage.base_rate == Decimal("0.10")
    assert c.retainage.steps[0].new_rate == Decimal("0.05")


def test_adjusted_contract_sum_is_original_plus_net_change_orders() -> None:
    assert _empty_app(co="5000.00").adjusted_contract_sum(_contract()) == money("105000.00")


def test_provenance_sidecar_defaults_empty_and_is_per_instance() -> None:
    a = _empty_app()
    b = _empty_app()
    a.provenance["contract_sum"] = "claim-1"
    assert b.provenance == {}


def test_money_fields_serialise_as_strings_never_floats() -> None:
    line = ContinuationLine(
        item_no="1", description="Sitework", scheduled_value=money("1000.00"),
        previous=money("0.00"), this_period=money("500.00"), stored=money("0.00"),
        total_completed_and_stored=money("500.00"), retainage=money("50.00"),
    )
    dumped = line.model_dump_json().replace(" ", "")
    assert '"scheduled_value":"1000.00"' in dumped


def test_unset_optional_money_field_serialises_to_json_null_not_the_string_none() -> None:
    """balance_to_finish is Money | None. A naive `str(value)` serializer turns
    a missing value into the string "None" instead of JSON null (F-03 fix)."""
    line = ContinuationLine(
        item_no="1", description="Sitework", scheduled_value=money("1000.00"),
        previous=money("0.00"), this_period=money("500.00"), stored=money("0.00"),
        total_completed_and_stored=money("500.00"), retainage=money("50.00"),
        balance_to_finish=None,
    )
    dumped = line.model_dump_json().replace(" ", "")
    assert '"balance_to_finish":null' in dumped


def test_vendor_name_defaults_to_none_and_is_never_guessed() -> None:
    app = PayApplication(
        change_orders_net=money("0.00"), retainage_total=money("0.00"),
        previous_payments=money("0.00"), total_completed_and_stored=money("0.00"))
    assert app.vendor_name is None


def test_vendor_name_accepts_an_extracted_string() -> None:
    app = PayApplication(
        vendor_name="Acme Steel Company",
        change_orders_net=money("0.00"), retainage_total=money("0.00"),
        previous_payments=money("0.00"), total_completed_and_stored=money("0.00"))
    assert app.vendor_name == "Acme Steel Company"
