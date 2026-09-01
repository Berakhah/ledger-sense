"""Typed pay-application domain model.

Money fields are Decimal and serialise as strings. The `provenance` sidecar
maps field name -> Claim; week 5 leaves it empty, extraction populates it.
Rules compute on the values; findings cite provenance[field].
"""

from datetime import date
from decimal import Decimal

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


class Contract(_Base):
    id: str
    original_sum: Money
    rounding: RoundingPolicy
    retainage_rate: Decimal
    retainage_steps: list[RetainageStep] = Field(default_factory=list)
    jurisdiction: str | None = None


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

    @field_serializer("change_orders_net", "retainage_total", "previous_payments",
                      "total_completed_and_stored")
    def _as_string(self, value: Decimal | None) -> str | None:
        return _decimal_to_str(value)

    def adjusted_contract_sum(self, contract: Contract) -> Money:
        return money(contract.original_sum + self.change_orders_net)
