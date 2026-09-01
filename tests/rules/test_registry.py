from datetime import date
from typing import Any

import pytest

from ledger_sense.rules.registry import (
    UnsourcedRuleError,
    register,
    registered_rules,
)


class _Source:
    authority: str = "AIA A201-2017 §9.3.1"
    citation: str = "clause 9.3.1"
    confirmed_by: str | None = "J. Controller"
    confirmed_at: date | None = date(2026, 9, 2)


def test_arithmetic_rule_registers_without_a_source() -> None:
    """Arithmetic is verifiable from first principles. No provenance required."""

    @register("TEST_A", "arithmetic")
    def rule_a(app: Any, contract: Any) -> list[Any]:
        return []

    assert "TEST_A" in registered_rules()


def test_convention_rule_without_a_source_is_refused() -> None:
    with pytest.raises(UnsourcedRuleError, match="TEST_C1"):

        @register("TEST_C1", "convention")
        def rule_c1(app: Any, contract: Any) -> list[Any]:
            return []


def test_convention_rule_with_a_source_registers() -> None:
    @register("TEST_C2", "convention", source=_Source())
    def rule_c2(app: Any, contract: Any) -> list[Any]:
        return []

    source = registered_rules()["TEST_C2"].source
    assert source is not None
    assert source.confirmed_by == "J. Controller"


def test_source_missing_confirmed_by_is_refused() -> None:
    class _Unconfirmed:
        authority: str = "a blog post"
        citation: str = "n/a"
        confirmed_by: str | None = None
        confirmed_at: date | None = None

    with pytest.raises(UnsourcedRuleError, match="confirmed_by"):

        @register("TEST_C3", "convention", source=_Unconfirmed())
        def rule_c3(app: Any, contract: Any) -> list[Any]:
            return []
