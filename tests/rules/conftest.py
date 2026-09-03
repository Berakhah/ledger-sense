from collections.abc import Iterator

import pytest

from ledger_sense.rules import registry


@pytest.fixture(autouse=True)
def _isolate_rule_registry() -> Iterator[None]:
    """Every rule any test under tests/rules/ registers is a leak into the
    process-global registry (registry.py's `_REGISTRY` module dict) unless
    undone. `tests/property/test_arithmetic_invariants.py` calls
    `run_rules()` against that same global registry, so a dummy rule left
    behind here can silently poison an assertion in a completely different
    test file, depending on collection order. Restore the registry to its
    pre-test contents after every test in this directory."""
    saved = dict(registry._REGISTRY)
    yield
    registry._REGISTRY.clear()
    registry._REGISTRY.update(saved)
