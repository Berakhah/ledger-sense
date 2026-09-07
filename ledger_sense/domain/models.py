"""Typed pay-application domain model.

Money fields are Decimal and serialise as strings. The `provenance` sidecar
maps field name -> Claim; week 5 leaves it empty, extraction populates it.
Rules compute on the values; findings cite provenance[field].
"""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from ledger_sense.domain.money import Money, RoundingPolicy, money


def _decimal_to_str(value: Decimal | None) -> str | None:
    """Decimal must never round-trip through float. Serialise as a string.

    Single shared implementation for every Money field serializer below: a
    Money field is sometimes Optional (ContinuationLine.balance_to_finish),
    and a serializer without the None guard turns a missing value into the
    JSON string "None" instead of null. One function means that guard can't
    silently be missing from one caller and present in another.
    """
    return str(value) if value is not None else None


class _Base(BaseModel):
    # strict=True is LAYER 4 of the no-float rule (F-01). Without it Pydantic
    # accepts a JSON float into a Decimal field and coerces it, and every other
    # layer stays green while the money is wrong.
    model_config = ConfigDict(arbitrary_types_allowed=False, strict=True,
                              extra="forbid")


class RetainageStep(_Base):
    at_percent_complete: Decimal
    new_rate: Decimal


WaiverType = Literal["conditional_progress", "unconditional_progress",
                     "conditional_final", "unconditional_final"]


class RetainageTerms(_Base):
    """Retainage is a NEGOTIATED TERM, never a constant.

    Flat 5%, flat 10%, 10% stepping at 50%, 0% on stored materials, release at
    substantial completion — all common. Several states cap it statutorily,
    differently for public and private work. Read the rate from here; never
    hardcode one.
    """

    base_rate: Decimal
    steps: list[RetainageStep] = Field(default_factory=list)
    stored_materials_rate: Decimal | None = None
    released_at_substantial_completion: bool = False

    def rate_at(self, percent_complete: Decimal) -> Decimal:
        applicable = [s for s in self.steps if percent_complete >= s.at_percent_complete]
        if not applicable:
            return self.base_rate
        return max(applicable, key=lambda s: s.at_percent_complete).new_rate


class ChangeOrder(_Base):
    number: str
    amount: Money
    approved: bool
    approved_on: date | None = None
    affects_item_no: str | None = None

    @field_serializer("amount")
    def _as_string(self, value: Decimal) -> str:
        return str(value)


class StoredMaterialsBacking(_Base):
    item_no: str
    supplier_invoice: bool = False
    insurance: bool = False
    bonded_storage: bool = False

    def missing(self) -> list[str]:
        return [name for name in ("supplier_invoice", "insurance", "bonded_storage")
                if not getattr(self, name)]

    def is_complete(self) -> bool:
        return not self.missing()


class LienWaiver(_Base):
    waiver_type: WaiverType
    through_date: date
    amount: Money
    funds_cleared: bool
    statutory_text_matches: bool | None = None

    @field_serializer("amount")
    def _as_string(self, value: Decimal) -> str:
        return str(value)


class Contract(_Base):
    id: str
    original_sum: Money
    rounding: RoundingPolicy
    retainage: RetainageTerms
    change_orders: list[ChangeOrder] = Field(default_factory=list)
    tax_jurisdiction: str | None = None
    tax_exempt: bool = False


class ContinuationLine(_Base):
    item_no: str
    description: str
    scheduled_value: Money
    previous: Money
    this_period: Money
    stored: Money
    total_completed_and_stored: Money
    retainage: Money
    # Both columns exist on a real continuation sheet. Without them A3 and A4
    # cannot compare REPORTED against COMPUTED, which is what spec §6.2 defines
    # them as (F-03). None means the column was not extracted, never zero.
    balance_to_finish: Money | None = None
    percent_complete: Decimal | None = None

    @field_serializer("scheduled_value", "previous", "this_period", "stored",
                      "total_completed_and_stored", "retainage", "balance_to_finish")
    def _as_string(self, value: Decimal | None) -> str | None:
        return _decimal_to_str(value)


class PayApplication(_Base):
    # None means "not extracted", recorded in provenance["_unextractable"].
    # Defaulting these to "", 0 and date.today() was F-15: a guessed period_to
    # makes C12 flag every waiver on a document processed a month late.
    contract_id: str | None = None
    application_no: int | None = None
    period_to: date | None = None
    lines: list[ContinuationLine] = Field(default_factory=list)
    change_orders_net: Money
    retainage_total: Money
    previous_payments: Money
    total_completed_and_stored: Money
    provenance: dict[str, object] = Field(default_factory=dict)
    prior: "PayApplication | None" = None
    stored_backing: list[StoredMaterialsBacking] = Field(default_factory=list)
    waivers: list[LienWaiver] = Field(default_factory=list)

    @field_serializer("change_orders_net", "retainage_total", "previous_payments",
                      "total_completed_and_stored")
    def _as_string(self, value: Decimal | None) -> str | None:
        return _decimal_to_str(value)

    def adjusted_contract_sum(self, contract: Contract) -> Money:
        return money(contract.original_sum + self.change_orders_net)


PayApplication.model_rebuild()
