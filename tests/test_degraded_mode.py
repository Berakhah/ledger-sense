# tests/test_degraded_mode.py
"""DoD 2 and 00 §R3: with every provider disabled, the deterministic path
extracts and validates digital-PDF pay applications end to end."""

import pytest

from ledger_sense.corpus.generate import generate
from ledger_sense.extract.assemble import assemble_pay_application

# Imported for their registration side effect only (F-67). The list below used to
# be written out by hand in three separate files; reclassifying ONE rule (A9, from
# `arithmetic` to `convention`) broke all three at once, and two of them silently.
# Ask the registry instead.
from ledger_sense.rules.arithmetic import foot, lines, payment  # noqa: F401
from ledger_sense.rules.registry import registered_rules

ALL_RULES = [r.fn for r in registered_rules().values()
             if r.rule_class == "arithmetic"]


@pytest.fixture(autouse=True)
def every_provider_disabled(monkeypatch):
    monkeypatch.setenv("BOK_PROVIDERS_DISABLED", "1")

    bok_core_gateway = pytest.importorskip(
        "bok_core.gateway",
        reason="not yet published; the deterministic pipeline calls no gateway regardless")

    def _explode(*args, **kwargs):
        raise AssertionError("a model call was attempted in degraded mode")

    monkeypatch.setattr(bok_core_gateway.Gateway, "call", _explode)


@pytest.mark.parametrize("seed", list(range(1, 11)))
def test_clean_documents_extract_and_validate_with_no_model(seed):
    contract, _app, pdf = generate(seed=seed, error="clean")
    built = assemble_pay_application(pdf, f"corpus://seed-{seed}")
    assert built.provenance["_unextractable"] == []
    for rule in ALL_RULES:
        assert rule(built, contract) == [], f"{rule.__name__} fired on a clean document"


@pytest.mark.parametrize("error,expected_rule", [
    ("A1_footing", "A1"), ("A2_line_sum", "A2"), ("A8_retainage", "A8")])
def test_injected_errors_are_caught_with_no_model(error, expected_rule):
    contract, _app, pdf = generate(seed=1, error=error)
    built = assemble_pay_application(pdf, "corpus://seed-1")
    fired = {f.rule_id for rule in ALL_RULES for f in rule(built, contract)}
    assert expected_rule in fired
