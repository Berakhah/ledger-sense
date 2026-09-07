from datetime import date
from decimal import Decimal

from ledger_sense.domain.models import (
    ChangeOrder,
    Contract,
    LienWaiver,
    PayApplication,
    RetainageStep,
    RetainageTerms,
    StoredMaterialsBacking,
)
from ledger_sense.domain.money import RoundingPolicy, money


def _terms(**kwargs):
    defaults = {"base_rate": Decimal("0.10"), "steps": [],
                "stored_materials_rate": None,
                "released_at_substantial_completion": False}
    return RetainageTerms(**{**defaults, **kwargs})


def test_retainage_terms_carry_a_separate_stored_materials_rate():
    terms = _terms(stored_materials_rate=Decimal("0.00"))
    assert terms.stored_materials_rate == Decimal("0.00")
    assert terms.base_rate == Decimal("0.10")


def test_retainage_terms_default_to_no_stepping():
    """Stepping is contract-specific, NOT a universal 10%->5%@50% schedule."""
    assert _terms().steps == []


def test_rate_at_returns_the_base_rate_below_the_first_step():
    terms = _terms(steps=[RetainageStep(at_percent_complete=Decimal("0.50"),
                                        new_rate=Decimal("0.05"))])
    assert terms.rate_at(Decimal("0.30")) == Decimal("0.10")


def test_rate_at_returns_the_stepped_rate_at_and_above_the_milestone():
    terms = _terms(steps=[RetainageStep(at_percent_complete=Decimal("0.50"),
                                        new_rate=Decimal("0.05"))])
    assert terms.rate_at(Decimal("0.50")) == Decimal("0.05")
    assert terms.rate_at(Decimal("0.90")) == Decimal("0.05")


def test_change_order_records_approval_state_and_date():
    co = ChangeOrder(number="CO-001", amount=money("5000.00"), approved=True,
                     approved_on=date(2026, 7, 15), affects_item_no="010")
    assert co.approved and co.approved_on == date(2026, 7, 15)


def test_unapproved_change_order_has_no_approval_date():
    co = ChangeOrder(number="CO-002", amount=money("1000.00"), approved=False,
                     approved_on=None, affects_item_no=None)
    assert co.approved_on is None


def test_stored_materials_backing_tracks_three_independent_documents():
    backing = StoredMaterialsBacking(item_no="010", supplier_invoice=True,
                                     insurance=False, bonded_storage=False)
    assert backing.is_complete() is False
    assert backing.missing() == ["insurance", "bonded_storage"]


def test_lien_waiver_records_whether_funds_have_cleared():
    waiver = LienWaiver(waiver_type="conditional_progress",
                        through_date=date(2026, 8, 31), amount=money("250.00"),
                        funds_cleared=False, statutory_text_matches=True)
    assert waiver.funds_cleared is False


def test_pay_application_can_reference_its_prior_period():
    contract = Contract(id="C-1", original_sum=money("1000.00"),
                        rounding=RoundingPolicy(2, 4), retainage=_terms(),
                        change_orders=[], tax_jurisdiction=None)
    first = PayApplication(contract_id="C-1", application_no=1,
                           period_to=date(2026, 7, 31), lines=[],
                           change_orders_net=money("0.00"),
                           retainage_total=money("0.00"),
                           previous_payments=money("0.00"),
                           total_completed_and_stored=money("0.00"))
    second = first.model_copy(update={"application_no": 2, "prior": first})
    assert second.prior is not None and second.prior.application_no == 1
    assert contract.retainage.base_rate == Decimal("0.10")
