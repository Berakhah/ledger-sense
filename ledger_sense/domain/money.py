"""Money is Decimal with an explicit context. Never float, anywhere.

The rounding policy is a CONTRACT TERM, not a global constant. Percentage
complete is computed 'to the contract's stated rounding' — hardcoding it is
the same class of error as hardcoding a retainage rate.
"""

import decimal
from decimal import Decimal
from typing import Annotated, NamedTuple

from pydantic import Strict

MONEY_CONTEXT = decimal.Context(prec=28, rounding=decimal.ROUND_HALF_UP)

# LAYER 4 (F-01). `Annotated[Decimal, "money"]` alone is NOT enough: Pydantic v2
# in lax mode accepts a float into a Decimal field and coerces it. Verified:
#   LineItem(amount=1000.0) -> Decimal('1000.0')
#   0.1 + 0.2               -> Decimal('0.30000000000000004')
# `Strict()` is what makes the annotation refuse. Every domain model additionally
# sets model_config = ConfigDict(strict=True); belt and braces, because this is
# the money path and the failure is silent.
Money = Annotated[Decimal, Strict(), "money"]


class RoundingPolicy(NamedTuple):
    money_places: int
    percent_places: int


def money(value: str | int | Decimal) -> Money:
    # bool must be rejected BEFORE float: bool is not a float, so the float guard
    # passes it and Decimal(True) succeeds, silently yielding one dollar (F-02).
    if isinstance(value, bool):
        raise TypeError("bool is not permitted in the money path")
    if isinstance(value, float):
        raise TypeError(
            "float is not permitted in the money path; pass a str, int or Decimal"
        )
    return MONEY_CONTEXT.create_decimal(value)


def quantize(value: Money, places: int) -> Money:
    exponent = Decimal(10) ** -places
    return MONEY_CONTEXT.quantize(value, exponent)
