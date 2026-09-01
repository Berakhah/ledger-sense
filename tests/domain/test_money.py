import pytest
from decimal import Decimal
from ledger_sense.domain.money import RoundingPolicy, money, quantize


def test_money_accepts_string_and_int():
    assert money("1234.56") == Decimal("1234.56")
    assert money(1234) == Decimal("1234")


def test_money_rejects_float_loudly():
    with pytest.raises(TypeError, match="float"):
        money(1234.56)


def test_quantize_uses_supplied_places_not_a_global_constant():
    policy = RoundingPolicy(money_places=2, percent_places=4)
    assert quantize(money("10.005"), policy.money_places) == Decimal("10.01")
    assert quantize(money("0.333333"), policy.percent_places) == Decimal("0.3333")


def test_half_up_not_bankers_rounding():
    """Python's Decimal defaults to ROUND_HALF_EVEN. Accounting expects HALF_UP."""
    assert quantize(money("2.345"), 2) == Decimal("2.35")
    assert quantize(money("2.355"), 2) == Decimal("2.36")


def test_money_rejects_bool():
    """VERIFIED F-02: bool is not float, so the guard passed it and
    Decimal(True) succeeded — money(True) returned Decimal('1'). A truthy flag
    threaded into an amount silently became one dollar."""
    with pytest.raises(TypeError, match="bool"):
        money(True)


def test_a_strict_money_field_refuses_a_float_at_the_schema():
    """LAYER 4 — F-01, VERIFIED. Pydantic v2 in lax mode ACCEPTS a float into a
    Decimal field and coerces it. All three declared layers are blind to this:
    mypy sees Decimal, the AST scan sees no literal, the model walk sees an
    annotation that is not float. Money enters as JSON, and JSON has floats."""
    from pydantic import BaseModel, ConfigDict, ValidationError
    from ledger_sense.domain.money import Money

    class _Probe(BaseModel):
        model_config = ConfigDict(strict=True)
        amount: Money

    assert _Probe(amount=Decimal("1000.00")).amount == Decimal("1000.00")
    with pytest.raises(ValidationError):
        _Probe(amount=1000.0)
    with pytest.raises(ValidationError):
        _Probe(amount=0.1 + 0.2)      # would have become Decimal('0.30000000000000004')
