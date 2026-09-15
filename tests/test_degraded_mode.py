# tests/test_degraded_mode.py
"""DoD 2 and 00 §R3: with every provider disabled, the deterministic path
extracts and validates digital-PDF pay applications end to end."""

import pytest

from ledger_sense.corpus.errors import ErrorClass
from ledger_sense.corpus.generate import generate
from ledger_sense.extract.assemble import assemble_pay_application

# Imported for their registration side effect only (F-67). A hand-written
# ALL_RULES list used to be duplicated across three files; reclassifying ONE
# rule (A9, from `arithmetic` to `convention`) broke all three at once, and
# two of them silently. run_rules(rule_classes=...) asks the registry instead.
from ledger_sense.rules.arithmetic import foot, lines, payment  # noqa: F401
from ledger_sense.rules.engine import run_rules


@pytest.fixture(autouse=True)
def every_provider_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOK_PROVIDERS_DISABLED", "1")


@pytest.fixture
def gateway_call_traps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not autouse, and not used by the 13 tests below. `bok_core` isn't published
    yet, so this fixture skips (via importorskip) whenever it's absent -- it exists
    only to prove the trap wires up correctly once `bok_core` does exist, so a
    regression that made the deterministic path start calling the gateway would be
    caught the moment the dependency is added, not silently."""
    bok_core_gateway = pytest.importorskip(
        "bok_core.gateway",
        reason="not yet published; the deterministic pipeline calls no gateway regardless")

    def _explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("a model call was attempted in degraded mode")

    monkeypatch.setattr(bok_core_gateway.Gateway, "call", _explode)


def test_gateway_call_is_trapped_when_bok_core_exists(gateway_call_traps: None) -> None:
    """Dedicated proof that, once `bok_core` is a real dependency, a stray call
    to Gateway.call blows up loudly instead of silently reaching a model. Skips
    for the same reason `gateway_call_traps` skips: `bok_core` isn't published yet."""
    from bok_core.gateway import Gateway  # type: ignore[import-not-found]

    with pytest.raises(AssertionError, match="a model call was attempted in degraded mode"):
        Gateway.call()


def test_the_deterministic_packages_never_import_the_gateway() -> None:
    """The local trap for the local seam: if extract/rules/ingest/domain/corpus/
    baseline imported ledger_sense.gateway, the 70% deterministic floor would
    depend on a provider. Static, so none of `import ledger_sense.gateway`,
    `import ledger_sense.gateway as gw`, `from ledger_sense import gateway`,
    `from ledger_sense.gateway import admit`, or a relative `from .. import
    gateway` / `from ..gateway import admit` can dodge it. Each deterministic
    package directory is also asserted to exist, so a misspelled package name
    here fails loudly instead of scanning nothing."""
    import ast
    import pathlib

    def _resolve_relative(
        file_path: pathlib.Path, level: int, module: str | None,
    ) -> str:
        """What `from <level dots><module> import ...` resolves to, absolute,
        given the importing file's own path. Mirrors
        importlib._bootstrap._resolve_name: the file's own package is its
        containing directory as a dotted path; level 1 stays in that package,
        each extra level goes up one more."""
        package_parts = list(file_path.parent.parts)
        base_parts = package_parts[: max(0, len(package_parts) - (level - 1))]
        if module:
            return ".".join((*base_parts, module))
        return ".".join(base_parts)

    deterministic = ("extract", "rules", "ingest", "domain", "corpus", "baseline")
    offenders = []
    for package in deterministic:
        package_dir = pathlib.Path("ledger_sense") / package
        assert package_dir.is_dir(), f"deterministic package missing: {package_dir}"
        for path in package_dir.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.level > 0:
                        resolved = _resolve_relative(path, node.level, node.module)
                    else:
                        resolved = node.module or ""
                    is_offender = (
                        resolved == "ledger_sense.gateway"
                        or resolved.startswith("ledger_sense.gateway.")
                        or (
                            resolved == "ledger_sense"
                            and any(alias.name == "gateway" for alias in node.names)
                        )
                    )
                    if is_offender:
                        offenders.append(f"{path}:{node.lineno}")
                elif isinstance(node, ast.Import):
                    if any(
                        alias.name == "ledger_sense.gateway"
                        or alias.name.startswith("ledger_sense.gateway.")
                        for alias in node.names
                    ):
                        offenders.append(f"{path}:{node.lineno}")
    assert offenders == [], f"deterministic code imports the gateway: {offenders}"


@pytest.mark.parametrize("seed", list(range(1, 11)))
def test_clean_documents_extract_and_validate_with_no_model(seed: int) -> None:
    contract, _app, pdf = generate(seed=seed, error="clean")
    built = assemble_pay_application(pdf, f"corpus://seed-{seed}")
    assert built.provenance["_unextractable"] == []
    findings = run_rules(built, contract, rule_classes={"arithmetic"})
    assert findings == [], f"rules fired on a clean document: {findings}"


@pytest.mark.parametrize("error,expected_rule", [
    ("A1_footing", "A1"), ("A2_line_sum", "A2"), ("A8_retainage", "A8")])
def test_injected_errors_are_caught_with_no_model(error: ErrorClass, expected_rule: str) -> None:
    contract, _app, pdf = generate(seed=1, error=error)
    built = assemble_pay_application(pdf, "corpus://seed-1")
    fired = {f.rule_id for f in run_rules(built, contract, rule_classes={"arithmetic"})}
    assert expected_rule in fired
