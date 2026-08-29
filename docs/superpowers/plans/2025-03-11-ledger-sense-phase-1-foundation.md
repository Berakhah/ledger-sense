# Ledger Sense — Phase 1: Foundation (Weeks 5–6)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the substrate every later phase stands on — a money type that cannot hold a float, a domain model, a rule registry that refuses an unsourced convention rule, the eight arithmetic rules, the billable week-one baseline instrument, the synthetic corpus that is the only document supply this product has, and the boundary door.

**Architecture:** Four independent no-float layers because the three obvious ones are all blind to the JSON body. A rule registry whose `register()` raises at import time on a convention rule with no `RuleSource`, so Phase 3 cannot begin before the controller session closes. A corpus generator using **our own layout** with equivalent column semantics, never an AIA form. A baseline study whose interval is defensible because the *t* table is complete and the loaded rate carries its own uncertainty.

**Tech Stack:** Python 3.12+, `uv`, `ruff`, `mypy --strict`, Pydantic v2 (`strict=True`), `pytest` + `hypothesis`, `reportlab` for corpus rendering, stdlib `ast` for the source guards.

**Spec:** `docs/superpowers/specs/2025-03-11-ledger-sense-design.md` §5, §6.2, §8 item 1
**Parent plan:** `2025-03-11-ledger-sense-weeks-5-12.md` — its Global Constraints and File Structure apply here in full.
**Audit:** `2026-08-29-REVISIONS.md` — read §2 and §7 before Task 1. Eighteen defects in this phase were fixed in
place; four of them (F-01, F-03, F-09, F-52) would have put a wrong number in front of a client, and F-01 was
verified by execution against Pydantic 2.13.5.

---

## Phase preconditions

- **Nothing.** This phase is the start of the build. It installs no dependency before Task 1's licence gate exists.
- **Phase 0 Tasks P1–P2 run concurrently**, in week 5, alongside Task 1. P3–P5 must land before Task 13, which is
  the first task that accepts a document from outside the system.
- **Task 13 alone is gated** on `bok_core.boundary` being published. Everything else here calls no model and
  touches no client data. See the parent plan's Global Constraints — `ledger_sense/provenance.py` (Task 4)
  removes the rest of the `bok-core` dependency (F-61).

---

### Task 1: Repo scaffold and the licence-audit gate

Ships **before any dependency is installed**. Its value is preventing adoption, not documenting it.

**Files:**
- Create: `pyproject.toml`
- Create: `scripts/licence_audit.py`
- Create: `.github/workflows/ci.yml`
- Test: `tests/test_licence_audit.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `scripts/licence_audit.py::audit(packages: list[PackageInfo]) -> list[Violation]`, where
  `PackageInfo = NamedTuple("PackageInfo", [("name", str), ("version", str), ("spdx", str), ("extras", frozenset[str])])`
  and `Violation = NamedTuple("Violation", [("package", str), ("reason", str)])`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_licence_audit.py
from scripts.licence_audit import PackageInfo, audit


def _pkg(name, spdx="MIT", extras=frozenset()):
    return PackageInfo(name=name, version="1.0.0", spdx=spdx, extras=extras)


def test_clean_package_set_produces_no_violations():
    assert audit([_pkg("pdfplumber"), _pkg("pypdfium2", spdx="BSD-3-Clause")]) == []


def test_agpl_spdx_is_rejected():
    violations = audit([_pkg("PyMuPDF", spdx="AGPL-3.0-or-later")])
    assert [v.package for v in violations] == ["PyMuPDF"]
    assert "AGPL" in violations[0].reason


def test_ocrmypdf_rejected_despite_permissive_spdx():
    """The package is MPL-2.0. It requires an AGPL Ghostscript binary.
    A metadata-only scan waves this through; ours must not."""
    violations = audit([_pkg("ocrmypdf", spdx="MPL-2.0")])
    assert [v.package for v in violations] == ["ocrmypdf"]
    assert "ghostscript" in violations[0].reason.lower()


def test_camelot_permitted_without_ghostscript_extra():
    assert audit([_pkg("camelot-py")]) == []


def test_camelot_rejected_with_ghostscript_extra():
    violations = audit([_pkg("camelot-py", extras=frozenset({"ghostscript"}))])
    assert [v.package for v in violations] == ["camelot-py"]


def test_docling_easyocr_extra_rejected():
    violations = audit([_pkg("docling", extras=frozenset({"easyocr"}))])
    assert [v.package for v in violations] == ["docling"]


# ---- F-33 regression: every one of these was VERIFIED to bypass the original ----

@pytest.mark.parametrize("spdx", [
    "AGPL-3.0-only OR LGPL-3.0",      # the allow-check short-circuited the whole expr
    "GPL-1.0-only",                    # "GPL-2"/"GPL-3" fragments missed it
    "BUSL-1.1",
    "Elastic-2.0",
    "PolyForm-Noncommercial-1.0.0",    # "NON-COMMERCIAL" != "Noncommercial"
    "SSPL-1.0",
])
def test_previously_bypassing_licences_are_now_denied(spdx):
    assert [v.package for v in audit([_pkg("sneaky", spdx=spdx)])] == ["sneaky"]


@pytest.mark.parametrize("spdx", [
    "MIT OR AGPL-3.0-only",            # we may take the MIT branch
    "Apache-2.0 WITH LLVM-exception",
    "MIT AND BSD-3-Clause",
    "LGPL-2.1-or-later",
])
def test_genuinely_permissive_expressions_are_allowed(spdx):
    assert audit([_pkg("fine", spdx=spdx)]) == []


def test_an_unknown_licence_fails_the_gate_rather_than_passing_silently():
    """A package whose licence we cannot read is not a package we may ship."""
    from scripts.licence_audit import main
    assert main([]) in (0, 1)          # smoke: the entrypoint exists and returns
    assert audit([_pkg("mystery", spdx="UNKNOWN")]) == []   # audit is silent...
    # ...and `main` is what refuses; see its UNKNOWN branch.


def test_the_gate_reads_the_real_environment_not_a_hand_written_list():
    """F-33: as first written, `audit` had no producer and CI exited 0 always."""
    from scripts.licence_audit import installed_packages
    names = {p.name.lower() for p in installed_packages()}
    assert "pytest" in names, "installed_packages() did not read the real environment"
```

Add `import pytest` to the test module.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_licence_audit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.licence_audit'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/licence_audit.py
"""Licence gate. Checks SPDX metadata AND the native/system layer.

A metadata-only scan passes ocrmypdf (MPL-2.0) while it drags in an AGPL-3.0
Ghostscript binary. That is the exact failure this exists to prevent.
"""

from typing import NamedTuple


class PackageInfo(NamedTuple):
    name: str
    version: str
    spdx: str
    extras: frozenset[str]


class Violation(NamedTuple):
    package: str
    reason: str


DENIED = ("AGPL", "GPL-1", "GPL-2", "GPL-3", "SSPL", "BUSL", "ELASTIC",
          "POLYFORM", "RSAL", "COMMONSCLAUSE", "NONCOMMERCIAL", "CCBYNC")
ALLOWED = ("LGPL",)  # LGPL is permissive enough for us and contains "GPL"

# Packages whose Python metadata is permissive but whose *native* dependency is not.
NATIVE_DENY = {
    "ocrmypdf": "requires an external Ghostscript binary, which is AGPL-3.0 (Artifex)",
}

# Extras that pull a denied licence into an otherwise clean package.
EXTRA_DENY = {
    ("camelot-py", "ghostscript"): "the ghostscript extra pulls AGPL-3.0 Ghostscript",
    ("docling", "easyocr"): "easyocr bundles CRAFT weights with research-use ambiguity",
}


def _normalise(token: str) -> str:
    return "".join(ch for ch in token.upper() if ch.isalnum())


def _atom_denied(atom: str) -> bool:
    flat = _normalise(atom)
    if any(_normalise(a) in flat for a in ALLOWED):
        return False
    return any(_normalise(d) in flat for d in DENIED)


def _spdx_denied(spdx: str) -> bool:
    """Parse the SPDX expression rather than substring-matching it.

    The original substring form had verified bypasses:
        "AGPL-3.0-only OR LGPL-3.0"     -> allowed  (the allow-check short-circuited)
        "GPL-1.0-only"                  -> allowed
        "BUSL-1.1" / "Elastic-2.0"      -> allowed
        "PolyForm-Noncommercial-1.0.0"  -> allowed  ("NON-COMMERCIAL" != "Noncommercial")

    A DISJUNCTION is denied only if EVERY branch is denied — we may take the
    permissive branch. A CONJUNCTION is denied if ANY branch is.
    """
    expression = spdx.replace("(", " ").replace(")", " ")
    if " OR " in expression.upper():
        return all(_spdx_denied(part)
                   for part in re.split(r"\s+OR\s+", expression, flags=re.I))
    if " AND " in expression.upper():
        return any(_spdx_denied(part)
                   for part in re.split(r"\s+AND\s+", expression, flags=re.I))
    return _atom_denied(re.split(r"\s+WITH\s+", expression, flags=re.I)[0])


def installed_packages() -> list[PackageInfo]:
    """Read the REAL environment. Without this, `audit` has no input and the
    gate exits 0 unconditionally — which is what it did as first written (F-33).
    """
    from importlib.metadata import distributions

    found: list[PackageInfo] = []
    for dist in distributions():
        meta = dist.metadata
        spdx = (meta.get("License-Expression") or meta.get("License") or "").strip()
        if not spdx or spdx.upper() == "UNKNOWN":
            classifiers = [c for c in meta.get_all("Classifier") or []
                           if c.startswith("License ::")]
            spdx = classifiers[-1].split("::")[-1].strip() if classifiers else "UNKNOWN"
        found.append(PackageInfo(name=dist.name, version=dist.version or "",
                                 spdx=spdx, extras=frozenset()))
    return found


def declared_extras(lockfile: str) -> set[tuple[str, str]]:
    """(package, extra) pairs actually resolved in uv.lock — an extra that is
    never installed cannot pull a licence in."""
    import tomllib

    with open(lockfile, "rb") as handle:
        lock = tomllib.load(handle)
    return {(pkg["name"], extra)
            for pkg in lock.get("package", [])
            for extra in pkg.get("optional-dependencies", {})}


def audit(packages: list[PackageInfo]) -> list[Violation]:
    violations: list[Violation] = []
    for pkg in packages:
        if _spdx_denied(pkg.spdx):
            violations.append(Violation(pkg.name, f"denied licence: {pkg.spdx}"))
            continue
        if pkg.name in NATIVE_DENY:
            violations.append(Violation(pkg.name, NATIVE_DENY[pkg.name]))
            continue
        for extra in pkg.extras:
            if (pkg.name, extra) in EXTRA_DENY:
                violations.append(Violation(pkg.name, EXTRA_DENY[(pkg.name, extra)]))
                break
    return violations


def main(argv: list[str]) -> int:
    """The entrypoint CI invokes. Without it the job exits 0 regardless (F-33)."""
    import sys

    packages = installed_packages()
    if "--check-lockfile" in argv:
        lockfile = argv[argv.index("--check-lockfile") + 1]
        extras = declared_extras(lockfile)
        packages = [p._replace(extras=frozenset(
            e for (name, e) in extras if name == p.name)) for p in packages]

    unknown = [p.name for p in packages if p.spdx == "UNKNOWN"]
    violations = audit(packages)
    for v in violations:
        print(f"DENIED {v.package}: {v.reason}", file=sys.stderr)
    for name in unknown:
        print(f"UNKNOWN LICENCE {name}: resolve manually before shipping",
              file=sys.stderr)
    return 1 if violations or unknown else 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
```

Add `import re` at the top of the module.

**On the native layer, honestly.** `NATIVE_DENY` is three names somebody typed; it is a deny-list, not a
mechanism, and a fourth AGPL native dependency would be invisible to it (F-34). Add the real check as a second
CI step and, where it cannot be made to work, say so in `docs/methodology.md` rather than implying coverage:

```bash
# Fail on any shared object an installed extension module resolves OUTSIDE the venv.
find .venv -name '*.so' -exec ldd {} + \
  | awk '/=>/ {print $3}' | sort -u \
  | grep -v -e '^\.venv' -e '^/lib' -e '^/usr/lib' -e 'linux-vdso' \
  | tee /dev/stderr | wc -l | grep -qx 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_licence_audit.py -v`
Expected: PASS, 19 tests (6 original, plus 10 parametrised F-33 regressions and 3 entrypoint tests).

- [ ] **Step 5: Wire it into CI**

```yaml
# .github/workflows/ci.yml  (licence job only; other jobs added by later tasks)
name: ci
on: [push, pull_request]
jobs:
  licence:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --all-extras
      - run: uv run python -m scripts.licence_audit --check-lockfile uv.lock
      - run: uv run pytest tests/test_licence_audit.py -v
```

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml scripts/licence_audit.py tests/test_licence_audit.py .github/workflows/ci.yml
git commit -m "ci: licence gate rejecting AGPL by SPDX and by native dependency"
```

---

### Task 2: Money — Decimal context and the contract-supplied rounding policy

**Files:**
- Create: `ledger_sense/domain/money.py`
- Test: `tests/domain/test_money.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Money = Annotated[Decimal, ...]`
  - `MONEY_CONTEXT: decimal.Context` — `prec=28`, `rounding=ROUND_HALF_UP`
  - `money(value: str | int | Decimal) -> Money` — never accepts `float`, raises `TypeError`
  - `quantize(value: Money, places: int) -> Money` — rounds at the *contract's* stated places
  - `RoundingPolicy = NamedTuple("RoundingPolicy", [("money_places", int), ("percent_places", int)])`

- [ ] **Step 1: Write the failing test**

```python
# tests/domain/test_money.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/domain/test_money.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.domain.money'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/domain/money.py
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
    exponent = Decimal(1).scaleb(-places)
    return MONEY_CONTEXT.quantize(value, exponent)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/domain/test_money.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/domain/money.py tests/domain/test_money.py
git commit -m "feat(domain): Money as Decimal with contract-supplied rounding policy"
```

---

### Task 3: No-float enforcement — four independent layers

Three layers is not enough either. `mypy` catches assignment, the AST scan catches literals, the model walk
catches the serialisation case both miss — **and all three are blind to the API boundary**, which is where money
actually enters. Layer 4 (`Strict()`, Task 2) closes it; this task adds the test that proves the boundary holds
end to end. See `2026-08-29-REVISIONS.md` F-01 for the verified evidence.

**Files:**
- Create: `scripts/check_no_float.py`
- Create: `tests/test_no_floats.py`
- Modify: `pyproject.toml` (ruff + mypy config)
- Modify: `.github/workflows/ci.yml` (add the `no-float` job)

**Interfaces:**
- Consumes: `ledger_sense.domain.money` (Task 2).
- Produces: `scripts/check_no_float.py::scan(paths: list[str]) -> list[str]` returning
  `"path:lineno: <offending source>"` strings.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_no_floats.py
from scripts.check_no_float import scan

GUARDED = ["ledger_sense/domain", "ledger_sense/rules", "ledger_sense/writeback"]


def test_scan_flags_a_float_literal(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("rate = 0.10\n")
    assert any("bad.py:1" in hit for hit in scan([str(tmp_path)]))


def test_scan_flags_a_float_call(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("x = float('1.5')\n")
    assert any("bad.py:1" in hit for hit in scan([str(tmp_path)]))


def test_scan_ignores_integer_literals(tmp_path):
    f = tmp_path / "ok.py"
    f.write_text("places = 2\n")
    assert scan([str(tmp_path)]) == []


def test_guarded_packages_contain_no_floats():
    """Layer 2: the real scan over the real tree."""
    hits = scan(GUARDED)
    assert hits == [], "float found in the money path:\n" + "\n".join(hits)


def test_no_domain_model_field_resolves_to_float():
    """Layer 3: catches the serialisation case the AST scan cannot see."""
    import ledger_sense.domain.models as models
    from pydantic import BaseModel

    offenders = []
    for name in dir(models):
        obj = getattr(models, name)
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
            for field_name, field in obj.model_fields.items():
                if field.annotation is float:
                    offenders.append(f"{obj.__name__}.{field_name}")
    assert offenders == [], f"float-annotated fields: {offenders}"


def test_every_domain_model_is_strict():
    """Layer 4 (F-01). Without strict mode a JSON float is coerced into Decimal
    and every other layer stays green while the money is wrong."""
    import ledger_sense.domain.models as models
    from pydantic import BaseModel

    lax = []
    for name in dir(models):
        obj = getattr(models, name)
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
            if not obj.model_config.get("strict"):
                lax.append(obj.__name__)
    assert lax == [], f"models not in strict mode: {lax}"


def test_no_decimal_field_anywhere_accepts_a_float():
    """The end-to-end assertion. Walk every Decimal-annotated field in the whole
    package and prove each one refuses 1.5."""
    import importlib
    import pkgutil
    from decimal import Decimal

    import ledger_sense
    from pydantic import BaseModel, ValidationError

    offenders = []
    for info in pkgutil.walk_packages(ledger_sense.__path__, "ledger_sense."):
        module = importlib.import_module(info.name)
        for name in dir(module):
            obj = getattr(module, name)
            if not (isinstance(obj, type) and issubclass(obj, BaseModel)):
                continue
            for field_name, field in obj.model_fields.items():
                if field.annotation is not Decimal:
                    continue
                try:
                    obj.__pydantic_validator__.validate_assignment(
                        obj.model_construct(), field_name, 1.5)
                    offenders.append(f"{obj.__name__}.{field_name}")
                except (ValidationError, Exception):
                    pass
    assert offenders == [], f"Decimal fields accepting a float: {offenders}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_no_floats.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.check_no_float'`
(`test_no_domain_model_field_resolves_to_float` and `test_guarded_packages_contain_no_floats` will also fail until Task 4 creates `models.py`. That is expected and correct — they go green in Task 4.)

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/check_no_float.py
"""AST scan for floats in the money path.

Layer 1 is `mypy --strict` on the Money type. Layer 3 is the Pydantic model
walk in tests/test_no_floats.py. This is layer 2.
"""

import ast
import pathlib


class _FloatFinder(ast.NodeVisitor):
    def __init__(self, path: pathlib.Path, source: str) -> None:
        self.path = path
        self.lines = source.splitlines()
        self.hits: list[str] = []

    def _record(self, node: ast.AST) -> None:
        lineno = getattr(node, "lineno", 0)
        line = self.lines[lineno - 1].strip() if 0 < lineno <= len(self.lines) else ""
        self.hits.append(f"{self.path}:{lineno}: {line}")

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, float):
            self._record(node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == "float":
            self._record(node)
        self.generic_visit(node)


def scan(paths: list[str]) -> list[str]:
    hits: list[str] = []
    for root in paths:
        for path in sorted(pathlib.Path(root).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            finder = _FloatFinder(path, source)
            finder.visit(ast.parse(source))
            hits.extend(finder.hits)
    return hits


if __name__ == "__main__":
    import sys

    found = scan(["ledger_sense/domain", "ledger_sense/rules", "ledger_sense/writeback"])
    for hit in found:
        print(hit, file=sys.stderr)
    sys.exit(1 if found else 0)
```

- [ ] **Step 4: Run test to verify the scanner tests pass**

Run: `uv run pytest tests/test_no_floats.py -v -k "scan or integer"`
Expected: PASS, 3 tests. The two whole-tree tests stay red until Task 4.

- [ ] **Step 5: Add the CI job**

```yaml
  no-float:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --all-extras
      - run: uv run python -m scripts.check_no_float
      - run: uv run mypy --strict ledger_sense
```

- [ ] **Step 6: Commit**

```bash
git add scripts/check_no_float.py tests/test_no_floats.py pyproject.toml .github/workflows/ci.yml
git commit -m "ci: three-layer no-float enforcement over the money path"
```

---

### Task 4: Domain models

The `Contract` carries the rounding policy and the retainage terms, because both are negotiated values and neither is a constant.

**Files:**
- Create: `ledger_sense/domain/models.py`
- Test: `tests/domain/test_models.py`

**Interfaces:**
- Consumes: `ledger_sense.domain.money` (Task 2).
- Produces (all Pydantic v2 `BaseModel`):
  - `RetainageStep(at_percent_complete: Decimal, new_rate: Decimal)`
  - `Contract(id: str, original_sum: Money, rounding: RoundingPolicy, retainage_rate: Decimal, retainage_steps: list[RetainageStep], jurisdiction: str | None)`
  - `ContinuationLine(item_no: str, description: str, scheduled_value: Money, previous: Money, this_period: Money, stored: Money, total_completed_and_stored: Money, retainage: Money, balance_to_finish: Money | None, percent_complete: Decimal | None)`
    — **`balance_to_finish` and `percent_complete` are new (F-03).** Both columns exist on every real
    continuation sheet, and without them A3 and A4 cannot reconcile *reported* against *computed*; they can only
    sanity-check, which is not what spec §6.2 defines them as.
  - `PayApplication(contract_id: str | None, application_no: int | None, period_to: date | None, lines: list[ContinuationLine], change_orders_net: Money, retainage_total: Money, previous_payments: Money, total_completed_and_stored: Money, provenance: dict[str, object])`
    — the first three are **`| None` (F-15)**. Task 18 originally defaulted them to `""`, `0` and
    `date.today()`. A guessed `period_to` makes C12 flag every waiver on any document processed late; a guessed
    contract sum is the exact thing Task 17's own docstring forbids. Absent means absent, recorded in
    `_unextractable`.
  - `PayApplication.adjusted_contract_sum(contract: Contract) -> Money`

- [ ] **Step 1: Write the failing test**

```python
# tests/domain/test_models.py
from datetime import date
from decimal import Decimal
from ledger_sense.domain.models import (
    Contract, ContinuationLine, PayApplication, RetainageStep,
)
from ledger_sense.domain.money import RoundingPolicy, money


def _contract():
    return Contract(
        id="C-1",
        original_sum=money("100000.00"),
        rounding=RoundingPolicy(money_places=2, percent_places=4),
        retainage_rate=Decimal("0.10"),
        retainage_steps=[RetainageStep(at_percent_complete=Decimal("0.50"),
                                       new_rate=Decimal("0.05"))],
        jurisdiction=None,
    )


def _empty_app(co="0.00"):
    return PayApplication(
        contract_id="C-1", application_no=1, period_to=date(2026, 8, 31),
        lines=[], change_orders_net=money(co),
        retainage_total=money("0.00"), previous_payments=money("0.00"),
        total_completed_and_stored=money("0.00"),
    )


def test_contract_carries_its_own_rounding_and_retainage_terms():
    c = _contract()
    assert c.rounding.money_places == 2
    assert c.retainage_rate == Decimal("0.10")
    assert c.retainage_steps[0].new_rate == Decimal("0.05")


def test_adjusted_contract_sum_is_original_plus_net_change_orders():
    assert _empty_app(co="5000.00").adjusted_contract_sum(_contract()) == money("105000.00")


def test_provenance_sidecar_defaults_empty_and_is_per_instance():
    a = _empty_app()
    b = _empty_app()
    a.provenance["contract_sum"] = "claim-1"
    assert b.provenance == {}


def test_money_fields_serialise_as_strings_never_floats():
    line = ContinuationLine(
        item_no="1", description="Sitework", scheduled_value=money("1000.00"),
        previous=money("0.00"), this_period=money("500.00"), stored=money("0.00"),
        total_completed_and_stored=money("500.00"), retainage=money("50.00"),
    )
    dumped = line.model_dump_json().replace(" ", "")
    assert '"scheduled_value":"1000.00"' in dumped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/domain/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.domain.models'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/domain/models.py
"""Typed pay-application domain model.

Money fields are Decimal and serialise as strings. The `provenance` sidecar
maps field name -> Claim; week 5 leaves it empty, extraction populates it.
Rules compute on the values; findings cite provenance[field].
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from ledger_sense.domain.money import Money, RoundingPolicy, money


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
    def _as_string(self, value: Decimal) -> str:
        """Decimal must never round-trip through float. Serialise as a string."""
        return str(value)


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
    def _as_string(self, value: Decimal) -> str:
        return str(value)

    def adjusted_contract_sum(self, contract: Contract) -> Money:
        return money(contract.original_sum + self.change_orders_net)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/domain/ tests/test_no_floats.py -v`
Expected: PASS. `test_guarded_packages_contain_no_floats` and
`test_no_domain_model_field_resolves_to_float` now go green — Task 3's two deferred tests close here.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/domain/models.py tests/domain/test_models.py
git commit -m "feat(domain): pay application model with contract-owned rounding and retainage terms"
```

---

### Task 5: Rule registry that refuses an unsourced rule

This is the §13 Q7 mitigation enforced in code. A rule sourced from a web search cannot be registered, therefore cannot ship.

**Files:**
- Create: `ledger_sense/rules/provenance.py`
- Create: `ledger_sense/rules/registry.py`
- Test: `tests/rules/test_registry.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `RuleSource` — a `typing.Protocol` with `authority: str`, `citation: str`, `confirmed_by: str | None`, `confirmed_at: date | None`. `bok_core.provenance.RuleSource` will satisfy it structurally.
  - `RuleClass = Literal["arithmetic", "integrity", "convention"]`
  - `register(rule_id: str, rule_class: RuleClass, source: RuleSource | None = None)` decorator
  - `UnsourcedRuleError(Exception)`
  - `RegisteredRule(rule_id, rule_class, fn, source)`
  - `registered_rules() -> dict[str, RegisteredRule]`

- [ ] **Step 1: Write the failing test**

```python
# tests/rules/test_registry.py
from datetime import date
import pytest
from ledger_sense.rules.registry import (
    UnsourcedRuleError, register, registered_rules,
)


class _Source:
    authority = "AIA A201-2017 §9.3.1"
    citation = "clause 9.3.1"
    confirmed_by = "J. Controller"
    confirmed_at = date(2026, 9, 2)


def test_arithmetic_rule_registers_without_a_source():
    """Arithmetic is verifiable from first principles. No provenance required."""

    @register("TEST_A", "arithmetic")
    def rule_a(app, contract):
        return []

    assert "TEST_A" in registered_rules()


def test_convention_rule_without_a_source_is_refused():
    with pytest.raises(UnsourcedRuleError, match="TEST_C1"):

        @register("TEST_C1", "convention")
        def rule_c1(app, contract):
            return []


def test_convention_rule_with_a_source_registers():
    @register("TEST_C2", "convention", source=_Source())
    def rule_c2(app, contract):
        return []

    assert registered_rules()["TEST_C2"].source.confirmed_by == "J. Controller"


def test_source_missing_confirmed_by_is_refused():
    class _Unconfirmed:
        authority = "a blog post"
        citation = "n/a"
        confirmed_by = None
        confirmed_at = None

    with pytest.raises(UnsourcedRuleError, match="confirmed_by"):

        @register("TEST_C3", "convention", source=_Unconfirmed())
        def rule_c3(app, contract):
            return []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/rules/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.rules.registry'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/rules/provenance.py
from datetime import date
from typing import Protocol, runtime_checkable


@runtime_checkable
class RuleSource(Protocol):
    """Structural type. bok_core.provenance.RuleSource satisfies this as-is."""

    authority: str
    citation: str
    confirmed_by: str | None
    confirmed_at: date | None
```

```python
# ledger_sense/rules/registry.py
"""Rule registry.

Convention rules encode negotiated or statutory behaviour and are the class
that is fluent to state and silent when wrong. They cannot be registered
without a RuleSource naming a human who confirmed them. Arithmetic and
integrity rules are verifiable from first principles and need no source.
"""

from typing import Callable, Literal, NamedTuple

from ledger_sense.rules.provenance import RuleSource

RuleClass = Literal["arithmetic", "integrity", "convention"]
NEEDS_SOURCE: frozenset[str] = frozenset({"convention"})


class UnsourcedRuleError(Exception):
    pass


class RegisteredRule(NamedTuple):
    rule_id: str
    rule_class: RuleClass
    fn: Callable[..., list]
    source: RuleSource | None


_REGISTRY: dict[str, RegisteredRule] = {}


def register(
    rule_id: str, rule_class: RuleClass, source: RuleSource | None = None
) -> Callable[[Callable[..., list]], Callable[..., list]]:
    def decorate(fn: Callable[..., list]) -> Callable[..., list]:
        if rule_class in NEEDS_SOURCE:
            if source is None:
                raise UnsourcedRuleError(
                    f"{rule_id} is a {rule_class} rule and requires a RuleSource"
                )
            if not getattr(source, "confirmed_by", None):
                raise UnsourcedRuleError(
                    f"{rule_id}: RuleSource.confirmed_by must name the person who "
                    f"confirmed this rule"
                )
        _REGISTRY[rule_id] = RegisteredRule(rule_id, rule_class, fn, source)
        return fn

    return decorate


def registered_rules() -> dict[str, RegisteredRule]:
    return dict(_REGISTRY)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/rules/test_registry.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/rules/provenance.py ledger_sense/rules/registry.py tests/rules/test_registry.py
git commit -m "feat(rules): registry refusing convention rules without confirmed provenance"
```

---

### Task 6: Arithmetic rule A1 — footing

**Files:**
- Create: `ledger_sense/rules/engine.py`
- Create: `ledger_sense/rules/arithmetic/foot.py`
- Test: `tests/rules/arithmetic/test_foot.py`

**Interfaces:**
- Consumes: `register` (Task 5), `PayApplication` / `Contract` (Task 4).
- Produces: `Finding = NamedTuple("Finding", [("rule_id", str), ("field", str), ("message", str), ("expected", Money), ("actual", Money)])` in `ledger_sense/rules/engine.py`; rule `rule_a1(app, contract) -> list[Finding]`. **A9 is not here** — it is a convention
  rule and ships in Phase 3 Task 24 (F-04).

- [ ] **Step 1: Write the failing test**

```python
# tests/rules/arithmetic/test_foot.py
from datetime import date
from decimal import Decimal
from ledger_sense.domain.models import Contract, ContinuationLine, PayApplication
from ledger_sense.domain.money import RoundingPolicy, money
from ledger_sense.rules.arithmetic.foot import rule_a1


def _contract(original="1000.00"):
    return Contract(id="C-1", original_sum=money(original),
                    rounding=RoundingPolicy(2, 4), retainage_rate=Decimal("0.10"),
                    retainage_steps=[], jurisdiction=None)


def _line(sv, total):
    return ContinuationLine(item_no="1", description="x", scheduled_value=money(sv),
                            previous=money("0.00"), this_period=money(total),
                            stored=money("0.00"), total_completed_and_stored=money(total),
                            retainage=money("0.00"))


def _app(lines, total, co="0.00"):
    return PayApplication(contract_id="C-1", application_no=1, period_to=date(2026, 8, 31),
                          lines=lines, change_orders_net=money(co),
                          retainage_total=money("0.00"), previous_payments=money("0.00"),
                          total_completed_and_stored=money(total))


def test_a1_clean_control_produces_no_finding():
    app = _app([_line("600.00", "300.00"), _line("400.00", "200.00")], "500.00")
    assert rule_a1(app, _contract()) == []


def test_a1_trips_on_a_one_cent_discrepancy():
    """Exact, not approximate. A penny is a finding."""
    app = _app([_line("600.00", "300.00"), _line("400.00", "200.00")], "500.01")
    findings = rule_a1(app, _contract())
    assert len(findings) == 1
    assert findings[0].rule_id == "A1"
    assert findings[0].expected == money("500.00")
    assert findings[0].actual == money("500.01")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/rules/arithmetic/test_foot.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.rules.arithmetic.foot'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/rules/engine.py
from typing import NamedTuple

from ledger_sense.domain.money import Money


class Finding(NamedTuple):
    rule_id: str
    field: str
    message: str
    expected: Money
    actual: Money
```

```python
# ledger_sense/rules/arithmetic/foot.py
"""A1 — footing. Exact equality; no tolerance."""

from ledger_sense.domain.models import Contract, PayApplication
from ledger_sense.domain.money import money
from ledger_sense.rules.engine import Finding
from ledger_sense.rules.registry import register


@register("A1", "arithmetic")
def rule_a1(app: PayApplication, contract: Contract) -> list[Finding]:
    expected = money(sum((line.total_completed_and_stored for line in app.lines),
                         money("0")))
    if expected != app.total_completed_and_stored:
        return [Finding(
            rule_id="A1", field="total_completed_and_stored",
            message="continuation-sheet lines do not foot to the summary",
            expected=expected, actual=app.total_completed_and_stored,
        )]
    return []


# A9 IS NOT HERE — REVISED (F-04). It reads as first-principles checkable and is
# not: it fires on every application where an approved change order exists but the
# schedule of values has not yet been revised to carry it, which is the normal
# mid-period state. Classed `arithmetic` it bypassed the RuleSource gate built to
# stop exactly this, and it would have been the noisiest false positive in the
# product — the "trains a reviewer to dismiss findings" failure the plan correctly
# identifies for C8.
#
# It is a convention rule. It ships in `rules/convention/change_orders.py`
# alongside C7 and C8, with the CHANGE_ORDERS source and a suppression, in
# Phase 3 Task 24. It cannot ship here: `CHANGE_ORDERS` (Phase 3 Task 22) and
# `PayApplication.prior` (Phase 3 Task 21b) do not exist in week 6, so this module
# would not import.
```

**Where A9 went (F-04).** Task 6 ships **A1 only**. `rule_a9` and its two cases are implemented in **Phase 3
Task 24**, in `rules/convention/change_orders.py`, registered `convention` with the `CHANGE_ORDERS` source and
the SOV-lag suppression. The dependency runs the wrong way for it to live here: it needs `CHANGE_ORDERS`
(Phase 3 Task 22) and `PayApplication.prior` (Phase 3 Task 21b), neither of which exists in week 6.

A1 stays `arithmetic` and stays here: footing is genuinely checkable from first principles and depends on no
negotiated term. That is exactly the line the two classes are drawn on.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/rules/arithmetic/test_foot.py -v`
Expected: PASS, 2 tests (the two A1 cases; the two A9 cases moved to Phase 3 Task 24).

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/rules/engine.py ledger_sense/rules/arithmetic/foot.py tests/rules/arithmetic/test_foot.py
git commit -m "feat(rules): A1 exact footing"
```

---

### Task 7: Arithmetic rules A2, A3, A4 — per-line

**Files:**
- Create: `ledger_sense/rules/arithmetic/lines.py`
- Test: `tests/rules/arithmetic/test_lines.py`

**Interfaces:**
- Consumes: `Finding` (Task 6), `register`, `ContinuationLine`, `RoundingPolicy`.
- Produces: `rule_a2`, `rule_a3`, `rule_a4`, each `(app, contract) -> list[Finding]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/rules/arithmetic/test_lines.py
from datetime import date
from decimal import Decimal
from ledger_sense.domain.models import Contract, ContinuationLine, PayApplication
from ledger_sense.domain.money import RoundingPolicy, money
from ledger_sense.rules.arithmetic.lines import rule_a2, rule_a3, rule_a4


def _c(places=2):
    return Contract(id="C-1", original_sum=money("1000.00"),
                    rounding=RoundingPolicy(places, 4), retainage_rate=Decimal("0.10"),
                    retainage_steps=[], jurisdiction=None)


def _line(prev, this, stored, total, sv="1000.00"):
    return ContinuationLine(item_no="1", description="x", scheduled_value=money(sv),
                            previous=money(prev), this_period=money(this),
                            stored=money(stored), total_completed_and_stored=money(total),
                            retainage=money("0.00"))


def _app(line):
    return PayApplication(contract_id="C-1", application_no=1, period_to=date(2026, 8, 31),
                          lines=[line], change_orders_net=money("0.00"),
                          retainage_total=money("0.00"), previous_payments=money("0.00"),
                          total_completed_and_stored=line.total_completed_and_stored)


def test_a2_clean_control():
    assert rule_a2(_app(_line("100.00", "50.00", "25.00", "175.00")), _c()) == []


def test_a2_trips_when_components_do_not_sum():
    findings = rule_a2(_app(_line("100.00", "50.00", "25.00", "180.00")), _c())
    assert [f.rule_id for f in findings] == ["A2"]
    assert findings[0].expected == money("175.00")


def test_a3_clean_control_total_within_scheduled_value():
    assert rule_a3(_app(_line("0.00", "175.00", "0.00", "175.00", sv="1000.00")), _c()) == []


def test_a3_trips_when_total_exceeds_scheduled_value():
    findings = rule_a3(_app(_line("0.00", "1100.00", "0.00", "1100.00", sv="1000.00")), _c())
    assert [f.rule_id for f in findings] == ["A3"]


def test_a4_clean_control_at_the_contracts_rounding():
    line = _line("0.00", "333.33", "0.00", "333.33", sv="1000.00")
    assert rule_a4(_app(line), _c()) == []


def test_a4_trips_on_work_billed_against_a_zero_scheduled_value():
    line = _line("0.00", "500.00", "0.00", "500.00", sv="0.00")
    findings = rule_a4(_app(line), _c())
    assert [f.rule_id for f in findings] == ["A4"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/rules/arithmetic/test_lines.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.rules.arithmetic.lines'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/rules/arithmetic/lines.py
"""A2, A3, A4 — per-line arithmetic."""

from decimal import DivisionByZero, InvalidOperation

from ledger_sense.domain.models import Contract, PayApplication
from ledger_sense.domain.money import money, quantize
from ledger_sense.rules.engine import Finding
from ledger_sense.rules.registry import register


@register("A2", "arithmetic")
def rule_a2(app: PayApplication, contract: Contract) -> list[Finding]:
    findings = []
    for line in app.lines:
        expected = money(line.previous + line.this_period + line.stored)
        if expected != line.total_completed_and_stored:
            findings.append(Finding(
                "A2", f"line[{line.item_no}].total_completed_and_stored",
                "previous + this period + stored does not equal total completed and stored",
                expected, line.total_completed_and_stored))
    return findings


@register("A3", "arithmetic")
def rule_a3(app: PayApplication, contract: Contract) -> list[Finding]:
    """Spec §6.2 A3: balance to finish = scheduled value - total completed and stored.

    REVISED (F-03). The original only flagged a negative balance — a sanity check,
    not the rule the spec defines. The continuation sheet REPORTS a balance-to-
    finish column; reconciling reported against computed is what catches the
    transcription error, and it is the reason ContinuationLine now carries the field.
    """
    findings = []
    for line in app.lines:
        computed = money(line.scheduled_value - line.total_completed_and_stored)
        if line.balance_to_finish is None:
            continue                      # not extracted; A3b still guards the sign
        if computed != line.balance_to_finish:
            findings.append(Finding(
                "A3", f"line[{line.item_no}].balance_to_finish",
                "reported balance to finish does not equal scheduled value minus "
                "total completed and stored",
                computed, line.balance_to_finish))
    return findings


@register("A3b", "arithmetic")
def rule_a3b(app: PayApplication, contract: Contract) -> list[Finding]:
    """Overbilling sanity check, kept separate from A3 because it is a different
    claim and because it legitimately fires when a change order raised the
    scheduled value and the SOV line has not been revised — see A9 (F-04)."""
    return [Finding("A3b", f"line[{line.item_no}].total_completed_and_stored",
                    "total completed and stored exceeds the scheduled value",
                    line.scheduled_value, line.total_completed_and_stored)
            for line in app.lines
            if line.total_completed_and_stored > line.scheduled_value]


@register("A4", "arithmetic")
def rule_a4(app: PayApplication, contract: Contract) -> list[Finding]:
    """Spec §6.2 A4: percentage complete = total / scheduled value, at the
    contract's rounding.

    REVISED (F-03). Same defect as A3 — the original never compared against the
    REPORTED percentage, because the model had no field for it.
    """
    findings = []
    for line in app.lines:
        if line.percent_complete is None or line.scheduled_value == 0:
            continue
        try:
            computed = quantize(
                money(line.total_completed_and_stored / line.scheduled_value),
                contract.rounding.percent_places)
        except (DivisionByZero, InvalidOperation):
            continue
        if computed != line.percent_complete:
            findings.append(Finding(
                "A4", f"line[{line.item_no}].percent_complete",
                "reported percentage complete does not match total completed and "
                "stored divided by scheduled value at the contract's rounding",
                computed, line.percent_complete))
    return findings


@register("A4b", "arithmetic")
def rule_a4b(app: PayApplication, contract: Contract) -> list[Finding]:
    """Work billed against a line with no scheduled value. Not a percentage
    check — a division by zero is not a rounding disagreement."""
    return [Finding("A4b", f"line[{line.item_no}].scheduled_value",
                    "work billed against a line with no scheduled value",
                    money("0.00"), line.total_completed_and_stored)
            for line in app.lines
            if line.scheduled_value == 0 and line.total_completed_and_stored != 0]
```

Add the reconciliation cases to `tests/rules/arithmetic/test_lines.py` — a clean control where the reported
column agrees, and a tripping fixture where it is out by a cent — plus a control asserting A3 and A4 stay silent
when the column was not extracted (`None`). A rule that fires on a field it could not read is a rule that
punishes the extractor for being honest.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/rules/arithmetic/test_lines.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/rules/arithmetic/lines.py tests/rules/arithmetic/test_lines.py
git commit -m "feat(rules): A2-A4 per-line arithmetic at the contract's rounding"
```

---

### Task 8: Arithmetic rules A5, A6, A7, A8 — the payment chain

**A6 is the rule the design diagnostic got wrong.** Previous payments come off *after* retainage, to reach current payment due. Folding them in early subtracts them twice. Read spec §6.1 before writing this.

**Files:**
- Create: `ledger_sense/rules/arithmetic/payment.py`
- Test: `tests/rules/arithmetic/test_payment.py`

**Interfaces:**
- Consumes: `Finding` (Task 6), `register`, `PayApplication`, `Contract`.
- Produces: `total_earned_less_retainage(app) -> Money`, `current_payment_due(app) -> Money`, and rules `rule_a5`, `rule_a6`, `rule_a7`, `rule_a8`.

- [ ] **Step 1: Write the failing test**

```python
# tests/rules/arithmetic/test_payment.py
from datetime import date
from decimal import Decimal
from ledger_sense.domain.models import Contract, ContinuationLine, PayApplication
from ledger_sense.domain.money import RoundingPolicy, money
from ledger_sense.rules.arithmetic.payment import (
    current_payment_due, rule_a8, total_earned_less_retainage,
)


def _c():
    return Contract(id="C-1", original_sum=money("1000.00"),
                    rounding=RoundingPolicy(2, 4), retainage_rate=Decimal("0.10"),
                    retainage_steps=[], jurisdiction=None)


def _app(total, retainage, previous_payments, lines=()):
    return PayApplication(contract_id="C-1", application_no=2, period_to=date(2026, 8, 31),
                          lines=list(lines), change_orders_net=money("0.00"),
                          retainage_total=money(retainage),
                          previous_payments=money(previous_payments),
                          total_completed_and_stored=money(total))


def _line(item_no, retainage):
    return ContinuationLine(item_no=item_no, description="x",
                            scheduled_value=money("100.00"), previous=money("0.00"),
                            this_period=money("100.00"), stored=money("0.00"),
                            total_completed_and_stored=money("100.00"),
                            retainage=money(retainage))


def test_a5_total_earned_less_retainage_excludes_previous_payments():
    """The rule the design diagnostic got wrong. Previous payments are NOT
    subtracted here — they come off at the payment-due step."""
    assert total_earned_less_retainage(_app("500.00", "50.00", "200.00")) == money("450.00")


def test_a6_current_payment_due_subtracts_previous_payments_once():
    assert current_payment_due(_app("500.00", "50.00", "200.00")) == money("250.00")


def test_a6_previous_payments_are_not_double_counted():
    """Regression guard: 500 - 50 - 200 - 200 = 50 would be the wrong answer."""
    assert current_payment_due(_app("500.00", "50.00", "200.00")) != money("50.00")


def test_a8_per_line_retainage_sums_to_summary_retainage():
    lines = [_line(str(i), "10.00") for i in range(5)]
    assert rule_a8(_app("500.00", "50.00", "0.00", lines), _c()) == []


def test_a8_trips_when_per_line_retainage_misses_the_summary():
    findings = rule_a8(_app("100.00", "25.00", "0.00", [_line("1", "10.00")]), _c())
    assert [f.rule_id for f in findings] == ["A8"]
    assert findings[0].expected == money("10.00")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/rules/arithmetic/test_payment.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.rules.arithmetic.payment'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/rules/arithmetic/payment.py
"""A5-A8 — the payment chain.

ORDER MATTERS AND IS THE COMMON ERROR:
    total earned less retainage = total completed and stored - retainage
    current payment due         = total earned less retainage - previous payments

Previous payments are subtracted ONCE, at the second step. Change orders are
already inside the adjusted contract sum and are not re-added here.
"""

from ledger_sense.domain.models import Contract, PayApplication
from ledger_sense.domain.money import Money, money
from ledger_sense.rules.engine import Finding
from ledger_sense.rules.registry import register


def total_earned_less_retainage(app: PayApplication) -> Money:
    return money(app.total_completed_and_stored - app.retainage_total)


def current_payment_due(app: PayApplication) -> Money:
    return money(total_earned_less_retainage(app) - app.previous_payments)


@register("A5", "arithmetic")
def rule_a5(app: PayApplication, contract: Contract) -> list[Finding]:
    value = total_earned_less_retainage(app)
    if value < 0:
        return [Finding("A5", "total_earned_less_retainage",
                        "retainage exceeds total completed and stored",
                        money("0.00"), value)]
    return []


@register("A6", "arithmetic")
def rule_a6(app: PayApplication, contract: Contract) -> list[Finding]:
    value = current_payment_due(app)
    if value < 0:
        return [Finding("A6", "current_payment_due",
                        "previous payments exceed total earned less retainage",
                        money("0.00"), value)]
    return []


@register("A7", "arithmetic")
def rule_a7(app: PayApplication, contract: Contract) -> list[Finding]:
    expected = app.adjusted_contract_sum(contract)
    if expected < 0:
        return [Finding("A7", "adjusted_contract_sum",
                        "net change orders reduce the contract sum below zero",
                        money("0.00"), expected)]
    return []


@register("A8", "arithmetic")
def rule_a8(app: PayApplication, contract: Contract) -> list[Finding]:
    if not app.lines:
        return []
    expected = money(sum((line.retainage for line in app.lines), money("0")))
    if expected != app.retainage_total:
        return [Finding("A8", "retainage_total",
                        "per-line retainage does not sum to the summary retainage",
                        expected, app.retainage_total)]
    return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/rules/arithmetic/test_payment.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/rules/arithmetic/payment.py tests/rules/arithmetic/test_payment.py
git commit -m "feat(rules): A5-A8 payment chain with previous payments subtracted once"
```

---

### Task 9: `hypothesis` property tests over the arithmetic rules

Task 8's regression guard catches one known error. Property tests catch the ones nobody thought of.

**Files:**
- Create: `tests/property/__init__.py` (empty)
- Create: `tests/property/strategies.py`
- Create: `tests/property/test_arithmetic_invariants.py`

**Interfaces:**
- Consumes: every rule from Tasks 6–8.
- Produces: `tests/property/strategies.py::consistent_pay_applications()` — a `hypothesis`
  strategy yielding `tuple[Contract, PayApplication]` internally consistent by construction.

- [ ] **Step 1: Write the strategy and the failing test**

```python
# tests/property/strategies.py
from datetime import date
from decimal import Decimal

from hypothesis import strategies as st

from ledger_sense.domain.models import Contract, ContinuationLine, PayApplication
from ledger_sense.domain.money import RoundingPolicy, money

_amounts = st.decimals(min_value=Decimal("0"), max_value=Decimal("1000000"),
                       places=2, allow_nan=False, allow_infinity=False)


@st.composite
def consistent_pay_applications(draw):
    """Yields a (contract, application) pair satisfying A1-A8 by construction (A9 is a convention rule, Phase 3 Task 24).
    Any rule that fires on this is a false positive."""
    n = draw(st.integers(min_value=1, max_value=12))
    lines = []
    for i in range(n):
        previous = money(draw(_amounts))
        this_period = money(draw(_amounts))
        stored = money(draw(_amounts))
        total = money(previous + this_period + stored)
        lines.append(ContinuationLine(
            item_no=str(i), description="item", scheduled_value=total,
            previous=previous, this_period=this_period, stored=stored,
            total_completed_and_stored=total, retainage=money("0.00")))
    grand_total = money(sum((ln.total_completed_and_stored for ln in lines), money("0")))
    contract = Contract(id="C", original_sum=grand_total,
                        rounding=RoundingPolicy(2, 4), retainage_rate=Decimal("0.10"),
                        retainage_steps=[], jurisdiction=None)
    app = PayApplication(contract_id="C", application_no=1, period_to=date(2026, 8, 31),
                         lines=lines, change_orders_net=money("0.00"),
                         retainage_total=money("0.00"),
                         previous_payments=money("0.00"),
                         total_completed_and_stored=grand_total)
    return contract, app
```

```python
# tests/property/test_arithmetic_invariants.py
from hypothesis import given, settings

from ledger_sense.domain.money import money
from ledger_sense.rules.arithmetic.foot import rule_a1
from ledger_sense.rules.arithmetic.lines import rule_a2, rule_a3, rule_a4
from ledger_sense.rules.arithmetic.payment import (
    current_payment_due, rule_a5, rule_a6, rule_a8, total_earned_less_retainage,
)
from tests.property.strategies import consistent_pay_applications

# A9 is absent: it is a convention rule and ships in Phase 3 Task 24, which adds it
# to this list (F-04). A property suite that imports it here would not import at all.
ALL_RULES = [rule_a1, rule_a2, rule_a3, rule_a4, rule_a5, rule_a6, rule_a8]


@given(consistent_pay_applications())
@settings(max_examples=300)
def test_no_rule_fires_on_a_consistent_application(pair):
    contract, app = pair
    for rule in ALL_RULES:
        assert rule(app, contract) == [], f"{rule.__name__} false-positived"


@given(consistent_pay_applications())
@settings(max_examples=300)
def test_payment_chain_never_double_counts_previous_payments(pair):
    _, app = pair
    app = app.model_copy(update={"previous_payments": money("100.00"),
                                 "retainage_total": money("0.00")})
    assert current_payment_due(app) == money(
        total_earned_less_retainage(app) - money("100.00"))


@given(consistent_pay_applications())
@settings(max_examples=300)
def test_no_computed_value_exceeds_the_contracts_decimal_places(pair):
    contract, app = pair
    due = current_payment_due(app)
    assert -due.as_tuple().exponent <= contract.rounding.money_places
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/property/ -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests.property.strategies'` before the
files exist. Once created, expect any genuine invariant violation to surface here.

- [ ] **Step 3: Fix any rule the property tests falsify**

No new implementation file. If `test_no_rule_fires_on_a_consistent_application` fails, **the rule
is wrong, not the strategy.** Read the counterexample `hypothesis` prints and fix the rule.
Weakening the strategy to make a test pass defeats the purpose of this task.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/property/ -v`
Expected: PASS, 3 property tests, 300 examples each.

- [ ] **Step 5: Commit**

```bash
git add tests/property/
git commit -m "test(rules): hypothesis invariants over A1-A8"
```

---

### Task 10: Baseline study — the billable week-one instrument

Built in week 5, not week 12. It touches no document, is not gated by `bok-core`, is what you sell, and is the outreach asset for DoD 11.

**Files:**
- Create: `ledger_sense/baseline/study.py`
- Test: `tests/baseline/test_study.py`

**Interfaces:**
- Consumes: `ledger_sense.domain.money` (Task 2).
- Produces:
  - `TimedSample(document_id: str, doc_type: str, touch_seconds: int, exceptions: int)`
  - `LoadedRate(hourly: Money, overhead_multiplier: Decimal)` with `.effective_hourly() -> Money`
  - `BaselineStudy(samples, loaded_rate, population_size, sampling_frame, collection_period)` with
    `.cost_per_document() -> Money`, `.confidence_interval(level=Decimal("0.95")) -> tuple[Money, Money]`,
    `.method_statement() -> str`
  - `TOUCH_TIME_DEFINITION: str`

- [ ] **Step 1: Write the failing test**

```python
# tests/baseline/test_study.py
from datetime import date
from decimal import Decimal
import pytest
from ledger_sense.baseline.study import BaselineStudy, LoadedRate, TimedSample
from ledger_sense.domain.money import money


def _study(seconds_list):
    return BaselineStudy(
        samples=[TimedSample(document_id=str(i), doc_type="pay_application",
                             touch_seconds=s, exceptions=0)
                 for i, s in enumerate(seconds_list)],
        loaded_rate=LoadedRate(hourly=money("30.00"),
                               overhead_multiplier=Decimal("1.4")),
        population_size=500, sampling_frame="all AP documents received Aug 2026",
        collection_period=(date(2026, 8, 1), date(2026, 8, 31)))


def test_effective_hourly_applies_the_overhead_multiplier():
    assert LoadedRate(hourly=money("30.00"),
                      overhead_multiplier=Decimal("1.4")).effective_hourly() == money("42.00")


def test_cost_per_document_uses_mean_touch_time_and_loaded_rate():
    # mean 360s = 0.1h; 0.1 * 42.00 = 4.20
    assert _study([300, 360, 420]).cost_per_document() == money("4.20")


def test_confidence_interval_widens_with_variance():
    tight_lo, tight_hi = _study([360] * 8).confidence_interval()
    wide_lo, wide_hi = _study([60, 360, 660, 120, 600, 90, 540, 300]).confidence_interval()
    assert (wide_hi - wide_lo) > (tight_hi - tight_lo)


def test_too_few_samples_refuses_to_report_an_interval():
    """F-54. Three observations of a right-skewed quantity has no defensible
    t-interval. Say so rather than invent one — B13 is the whole differentiator."""
    with pytest.raises(ValueError, match="at least 8"):
        _study([360, 300, 420]).confidence_interval()


def test_the_t_value_does_not_collapse_to_the_normal_approximation():
    """F-52, VERIFIED. The original table stopped at df=9 and used 1.96 above it,
    understating the margin by 15.4% at df=9 and 6.8% at df=19 — always in the
    direction that flatters us."""
    from ledger_sense.baseline.study import _t_95
    assert _t_95(9) == Decimal("2.262")
    assert _t_95(19) == Decimal("2.093")
    assert _t_95(49) >= Decimal("2.009")
    assert _t_95(500) == Decimal("1.960")


def test_an_uncertain_loaded_rate_widens_the_interval():
    """F-53. The overhead multiplier is a figure the client gives off the top of
    their head. Treating it as exact makes the published interval too narrow."""
    seconds = [300, 360, 420, 380, 340, 400, 320, 360]
    tight = _study(seconds)
    wide = _study(seconds)
    wide.loaded_rate = LoadedRate(hourly=money("30.00"),
                                  overhead_multiplier=Decimal("1.4"),
                                  hourly_low=money("26.00"),
                                  hourly_high=money("34.00"))
    t_lo, t_hi = tight.confidence_interval()
    w_lo, w_hi = wide.confidence_interval()
    assert (w_hi - w_lo) > (t_hi - t_lo)


def test_the_method_statement_states_what_the_interval_does_not_cover():
    statement = _study([300, 360, 420, 380, 340, 400, 320, 360]).method_statement()
    assert "holds the loaded rate fixed" in statement
    assert "median" in statement.lower()


def test_method_statement_names_every_element_the_number_depends_on():
    statement = _study([300, 360, 420]).method_statement().lower()
    for required in ["sample size", "sampling frame", "touch time",
                     "loaded rate", "collection period"]:
        assert required in statement
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/baseline/test_study.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.baseline.study'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/baseline/study.py
"""The baseline study. The whole differentiator collapses if this is vibes.

Every number carries its method: sample size, sampling frame, timing protocol,
what counts as touch time, loaded-rate definition, and a real interval.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from ledger_sense.domain.money import Money, money, quantize

SECONDS_PER_HOUR = 3600
MIN_SAMPLES_FOR_AN_INTERVAL = 8

# Student's t, two-tailed, 95%, indexed by degrees of freedom (n - 1).
#
# REVISED (F-52). The original table stopped at df=9 and fell back to 1.96 for
# everything larger. VERIFIED understatement of the margin, at exactly the sample
# sizes a real baseline study uses:
#     df= 9  true 2.262  vs 1.96  ->  15.4% too narrow
#     df=19  true 2.093  vs 1.96  ->   6.8% too narrow
#     df=49  true 2.010  vs 1.96  ->   2.6% too narrow
# Every error was in the direction that makes our interval look tighter than it
# is, in the artifact whose entire purpose is defensibility (B13).
_T_95 = {1: Decimal("12.706"), 2: Decimal("4.303"), 3: Decimal("3.182"),
         4: Decimal("2.776"), 5: Decimal("2.571"), 6: Decimal("2.447"),
         7: Decimal("2.365"), 8: Decimal("2.306"), 9: Decimal("2.262"),
         10: Decimal("2.228"), 11: Decimal("2.201"), 12: Decimal("2.179"),
         13: Decimal("2.160"), 14: Decimal("2.145"), 15: Decimal("2.131"),
         16: Decimal("2.120"), 17: Decimal("2.110"), 18: Decimal("2.101"),
         19: Decimal("2.093"), 20: Decimal("2.086"), 22: Decimal("2.074"),
         24: Decimal("2.064"), 26: Decimal("2.056"), 28: Decimal("2.048"),
         30: Decimal("2.042"), 35: Decimal("2.030"), 40: Decimal("2.021"),
         50: Decimal("2.009"), 60: Decimal("2.000"), 80: Decimal("1.990"),
         100: Decimal("1.984"), 150: Decimal("1.976"), 200: Decimal("1.972")}
_T_95_ASYMPTOTIC = Decimal("1.960")


def _t_95(df: int) -> Decimal:
    """Conservative lookup: below 200 df, take the next SMALLER tabulated df, so
    an interpolation error can only widen the interval, never narrow it."""
    if df in _T_95:
        return _T_95[df]
    smaller = [k for k in _T_95 if k < df]
    return _T_95[max(smaller)] if smaller and df < 200 else _T_95_ASYMPTOTIC


INTERVAL_SCOPE = (
    "The interval covers sampling variability in observed touch time only. It "
    "holds the loaded rate fixed at the figure you supplied; if that figure is "
    "itself uncertain, the true interval on cost per document is wider. Supply "
    "a low and high hourly rate to have that uncertainty included."
)

TOUCH_TIME_DEFINITION = (
    "Touch time is wall-clock time from opening the document to the moment it is "
    "filed or posted, including interruptions shorter than 60 seconds and excluding "
    "longer ones. It does not include time waiting on a third party."
)


class TimedSample(BaseModel):
    document_id: str
    doc_type: str
    touch_seconds: int
    exceptions: int


class LoadedRate(BaseModel):
    hourly: Money
    overhead_multiplier: Decimal
    # Optional. When supplied, the reported interval widens to include the
    # client's own uncertainty about the rate rather than treating a figure they
    # gave off the top of their head as exact (F-53).
    hourly_low: Money | None = None
    hourly_high: Money | None = None

    def effective_hourly(self) -> Money:
        return quantize(money(self.hourly * self.overhead_multiplier), 2)

    def effective_range(self) -> tuple[Money, Money]:
        low = self.hourly_low if self.hourly_low is not None else self.hourly
        high = self.hourly_high if self.hourly_high is not None else self.hourly
        return (quantize(money(low * self.overhead_multiplier), 2),
                quantize(money(high * self.overhead_multiplier), 2))


class BaselineStudy(BaseModel):
    samples: list[TimedSample]
    loaded_rate: LoadedRate
    population_size: int
    sampling_frame: str
    collection_period: tuple[date, date]

    def _mean_seconds(self) -> Decimal:
        total = sum(Decimal(s.touch_seconds) for s in self.samples)
        return (total / Decimal(len(self.samples))).quantize(Decimal("0.0001"))

    def cost_per_document(self) -> Money:
        hours = self._mean_seconds() / SECONDS_PER_HOUR
        return quantize(money(hours * self.loaded_rate.effective_hourly()), 2)

    def median_seconds(self) -> Decimal:
        """Reported alongside the mean. Touch time is right-skewed; a controller
        who asks 'what does a typical document cost' means the median."""
        ordered = sorted(Decimal(s.touch_seconds) for s in self.samples)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[mid]
        return (ordered[mid - 1] + ordered[mid]) / 2

    def confidence_interval(self, level: Decimal = Decimal("0.95")) -> tuple[Money, Money]:
        """REVISED (F-52, F-53, F-54).

        Three changes. The t value is looked up properly rather than collapsing
        to 1.96 above df=9. The floor rises from 2 to 8, because a t-interval on
        three observations of a right-skewed quantity is not defensible to a
        controller who asks — and B13 says the whole differentiator collapses if
        the baseline is vibes. And the rate's own uncertainty is propagated when
        the client supplies a range, rather than being silently assumed away.
        """
        n = len(self.samples)
        if n < MIN_SAMPLES_FOR_AN_INTERVAL:
            raise ValueError(
                f"a defensible interval requires at least {MIN_SAMPLES_FOR_AN_INTERVAL} "
                f"timed samples; you have {n}. Time more documents rather than "
                f"reporting an interval this narrow sample cannot support.")
        seconds = [Decimal(s.touch_seconds) for s in self.samples]
        mean = self._mean_seconds()
        variance = sum((x - mean) ** 2 for x in seconds) / (n - 1)
        std_err = variance.sqrt() / Decimal(n).sqrt()
        margin_seconds = _t_95(n - 1) * std_err

        rate_lo, rate_hi = self.loaded_rate.effective_range()
        lo = quantize(money((mean - margin_seconds) / SECONDS_PER_HOUR * rate_lo), 2)
        hi = quantize(money((mean + margin_seconds) / SECONDS_PER_HOUR * rate_hi), 2)
        return lo, hi

    def method_statement(self) -> str:
        start, end = self.collection_period
        return (
            f"Sample size: {len(self.samples)} of a population of "
            f"{self.population_size}.\n"
            f"Sampling frame: {self.sampling_frame}.\n"
            f"Collection period: {start.isoformat()} to {end.isoformat()}.\n"
            f"Timing protocol: each sampled document timed individually by "
            f"observation, not recalled.\n"
            f"Touch time: {TOUCH_TIME_DEFINITION}\n"
            f"Loaded rate: base hourly {self.loaded_rate.hourly} multiplied by "
            f"{self.loaded_rate.overhead_multiplier} to cover employer overhead.\n"
            f"Mean touch time {self._mean_seconds()}s; median "
            f"{self.median_seconds()}s. Touch time is right-skewed, so the two "
            f"differ; the cost figure uses the mean.\n"
            f"Interval method: Student's t, two-tailed, 95%, on the mean.\n"
            f"Interval scope: {INTERVAL_SCOPE}"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/baseline/test_study.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/baseline/study.py tests/baseline/test_study.py
git commit -m "feat(baseline): measured-cost study with a defensible confidence interval"
```

---

### Task 11: Baseline report + `docs/methodology.md` baseline section

**Files:**
- Create: `ledger_sense/baseline/report.py`
- Create: `docs/methodology.md`
- Test: `tests/baseline/test_report.py`

**Interfaces:**
- Consumes: `BaselineStudy` (Task 10).
- Produces: `render_report(study: BaselineStudy) -> str` — print-ready HTML embedding
  `study.method_statement()` verbatim, stating the interval alongside the point estimate.

- [ ] **Step 1: Write the failing test**

```python
# tests/baseline/test_report.py
from datetime import date
from decimal import Decimal
from ledger_sense.baseline.report import render_report
from ledger_sense.baseline.study import BaselineStudy, LoadedRate, TimedSample
from ledger_sense.domain.money import money


def _study():
    return BaselineStudy(
        samples=[TimedSample(document_id=str(i), doc_type="pay_application",
                             touch_seconds=s, exceptions=0)
                 for i, s in enumerate([300, 360, 420, 380, 340, 400, 320, 360])],
        loaded_rate=LoadedRate(hourly=money("30.00"),
                               overhead_multiplier=Decimal("1.4")),
        population_size=500, sampling_frame="all AP documents received Aug 2026",
        collection_period=(date(2026, 8, 1), date(2026, 8, 31)))


def test_report_never_shows_a_point_estimate_without_its_interval():
    html = render_report(_study())
    assert "95% confidence interval" in html


def test_report_embeds_the_method_statement_verbatim():
    study = _study()
    assert study.method_statement() in render_report(study)


def test_report_carries_a_print_stylesheet():
    assert "@media print" in render_report(_study())


def test_report_makes_no_automation_claim():
    html = render_report(_study()).lower()
    for forbidden in ["fully automated", "100% accurate", "eliminates"]:
        assert forbidden not in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/baseline/test_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.baseline.report'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/baseline/report.py
"""Print-ready measured-cost report. The point estimate never appears without
its interval, and the method statement travels with the number."""

from ledger_sense.baseline.study import BaselineStudy

_PRINT_CSS = """
@media print {
  body { font-family: Newsreader, Georgia, serif; color: #1a1a1a; }
  .no-print { display: none; }
  .figure { font-variant-numeric: tabular-nums; }
}
"""


def render_report(study: BaselineStudy) -> str:
    point = study.cost_per_document()
    lo, hi = study.confidence_interval()
    return f"""<!doctype html>
<style>{_PRINT_CSS}</style>
<h1>Measured cost per document</h1>
<p class="figure"><strong>${point}</strong> per document
   (95% confidence interval ${lo} to ${hi})</p>
<h2>Method</h2>
<pre>{study.method_statement()}</pre>
<h2>Scope</h2>
<p>This figure describes the sampled documents only, over the stated collection
   period. It is not an industry average and is not comparable to published
   per-invoice cost figures, whose provenance we could not verify.</p>
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/baseline/test_report.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Write the methodology baseline section**

**REVISED (F-57).** The original heading set here (`## Baseline study`, `## Calibration basis`,
`## Straight-through rate`) is not the one Phase 6 Task 46 asserts, and Task 46's test therefore fails against
the document this task creates. **Use Task 46's schema from the start** — the document is written incrementally
into its final shape, which is the point of writing it incrementally:

```
## What we measure
## How we measure it          <- fill now: the verbatim TOUCH_TIME_DEFINITION and INTERVAL_SCOPE constants
## Corpus                     <- fill in week 6
## Calibration                <- fill in week 11
## What these numbers do not mean
```

Fill `## How we measure it` now with: sampling frame, sample size, collection period, timing protocol, the
touch-time definition copied **verbatim** from `study.py::TOUCH_TIME_DEFINITION` (Task 46 asserts byte
equality), the loaded-rate definition, the interval method (Student's *t*, two-tailed, 95%, `_t_95` tabulated to
df 200), and `INTERVAL_SCOPE` verbatim — the sentence stating that the interval covers touch-time sampling
variability only.

Open `## What these numbers do not mean` now rather than in week 12, with the first entry already true: *the
figure describes the sampled documents over the stated period and is not an industry average.* A limitations
section written at the end is written from memory.

The non-affiliation notice does **not** go in this file — Task 46 asserts `"g702" not in DOC` and the notice
names G702®. It lives in the README and the UI footer (Phase 0 Task P1, `ledger_sense/notice.py`), and this
document links to it.

- [ ] **Step 6: Commit**

```bash
git add ledger_sense/baseline/report.py docs/methodology.md tests/baseline/test_report.py
git commit -m "feat(baseline): print-ready report carrying its own method statement"
```

---

### Task 12: Synthetic corpus generator + the AIA guard

**Our own layout.** Equivalent column semantics; never the form's design. The guard ships with the generator because week 6 is where the copyright risk actually lives.

**Files:**
- Create: `ledger_sense/corpus/layouts.py`
- Create: `ledger_sense/corpus/errors.py`
- Create: `ledger_sense/corpus/generate.py`
- Create: `scripts/aia_guard.py`
- Test: `tests/corpus/test_generate.py`
- Test: `tests/test_aia_guard.py`
- Modify: `.github/workflows/ci.yml` (add the `aia-guard` job)

**Interfaces:**
- Consumes: `PayApplication`, `Contract`, `money` (Tasks 2, 4); rules from Tasks 6–8 in tests.
- Produces:
  - `ErrorClass = Literal["clean", "A1_footing", "A2_line_sum", "A8_retainage", "I1_duplicate", "C4_stored_double_count", "C1_retainage_rate"]`
  - `generate(seed: int, error: ErrorClass) -> tuple[Contract, PayApplication, bytes]` — `bytes` is a rendered PDF in **our** layout
  - `ground_truth(seed: int, error: ErrorClass) -> dict[str, str]` — field name → exact expected value
  - `scripts/aia_guard.py::guard(repo_root: str) -> list[str]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_aia_guard.py
import pathlib
from scripts.aia_guard import guard

# REVISED (F-55). The notice was hardcoded here AND, with DIFFERENT wording, in
# Phase 6 Task 48's close-out test. Both run in the same suite; they could not
# both pass. One definition (Phase 0 Task P1), imported everywhere.
from ledger_sense.notice import NON_AFFILIATION_NOTICE

NOTICE_FRAGMENT = NON_AFFILIATION_NOTICE[:60].lower()


def test_guard_flags_a_file_named_after_an_aia_form(tmp_path):
    (tmp_path / "g702_template.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "README.md").write_text(NOTICE_FRAGMENT)
    assert any("g702_template.pdf" in hit for hit in guard(str(tmp_path)))


def test_guard_flags_a_missing_non_affiliation_notice(tmp_path):
    (tmp_path / "README.md").write_text("Ledger Sense")
    assert any("non-affiliation notice" in hit for hit in guard(str(tmp_path)))


def test_guard_passes_a_clean_repo(tmp_path):
    (tmp_path / "README.md").write_text(NOTICE_FRAGMENT)
    (tmp_path / "our_layout.py").write_text("COLUMNS = ['scheduled_value']")
    assert guard(str(tmp_path)) == []


def test_real_repo_passes_the_guard():
    assert guard(str(pathlib.Path(__file__).resolve().parent.parent)) == []
```

```python
# tests/corpus/test_generate.py
from ledger_sense.corpus.generate import generate, ground_truth
from ledger_sense.rules.arithmetic.foot import rule_a1
from ledger_sense.rules.arithmetic.payment import rule_a8


def test_clean_document_trips_no_arithmetic_rule():
    contract, app, _pdf = generate(seed=1, error="clean")
    assert rule_a1(app, contract) == []
    assert rule_a8(app, contract) == []


def test_footing_error_class_trips_exactly_a1():
    contract, app, _pdf = generate(seed=1, error="A1_footing")
    assert [f.rule_id for f in rule_a1(app, contract)] == ["A1"]


def test_retainage_error_class_trips_exactly_a8():
    contract, app, _pdf = generate(seed=1, error="A8_retainage")
    assert [f.rule_id for f in rule_a8(app, contract)] == ["A8"]


def test_generation_is_deterministic_for_a_seed():
    _c1, app1, _p1 = generate(seed=7, error="clean")
    _c2, app2, _p2 = generate(seed=7, error="clean")
    assert app1.model_dump_json() == app2.model_dump_json()


def test_ground_truth_values_match_the_generated_model():
    _contract, app, _pdf = generate(seed=3, error="clean")
    truth = ground_truth(seed=3, error="clean")
    assert truth["total_completed_and_stored"] == str(app.total_completed_and_stored)


def test_rendered_pdf_carries_a_text_layer():
    """Deterministic extraction depends on this; a scanned-only corpus is useless."""
    import pypdfium2 as pdfium
    _contract, _app, pdf = generate(seed=1, error="clean")
    doc = pdfium.PdfDocument(pdf)
    text = doc[0].get_textpage().get_text_range()
    assert "Scheduled Value" in text


def test_the_summary_block_is_actually_rendered():
    """F-10. SUMMARY_FIELDS was defined and never drawn, so Task 17's summary
    test could not pass and Task 21's `_unextractable == []` could not hold. The
    original plan discovered this three tasks later, in the task proving DoD 2."""
    import pypdfium2 as pdfium
    _contract, app, pdf = generate(seed=1, error="clean")
    text = pdfium.PdfDocument(pdf)[0].get_textpage().get_text_range()
    assert "Summary — Current Payment Due" in text
    assert str(app.total_completed_and_stored) in text


def test_no_summary_label_collides_with_a_column_header():
    """F-09, VERIFIED. The original set shared 'Retainage' and 'Total Completed
    and Stored' with the grid headers, so extract_summary's first-match anchor
    returned a LINE ITEM cell as the summary figure — with a bbox pointing at the
    wrong cell, and A1 passing on a document that does not foot."""
    from ledger_sense.corpus.layouts import CONTINUATION_COLUMNS, SUMMARY_FIELDS
    headers = {c.header for c in CONTINUATION_COLUMNS}
    labels = {label for label, _ in SUMMARY_FIELDS}
    assert headers & labels == set()


def test_the_duplicate_injector_produces_a_second_document():
    """F-62. `inject` returned the application unchanged for I1 and C1, so Task
    47's demo could not catch either and DoD 7 could not be met."""
    from ledger_sense.corpus.errors import generate_history
    history = generate_history(seed=1)
    assert len(history) == 2
    assert history[0][1].period_to == history[1][1].period_to


def test_the_retainage_injector_disagrees_with_the_contract_term():
    contract, app, _pdf = generate(seed=1, error="C1_retainage_rate")
    expected = contract.retainage.base_rate * app.total_completed_and_stored
    assert app.retainage_total != expected


@pytest.mark.parametrize("layout", ["baseline", "right_aligned", "alternate_headers",
                                    "wrapped_descriptions", "two_page"])
def test_every_layout_variant_renders_with_a_text_layer(layout):
    """F-14. Five layouts so the extraction tests have something to disagree with,
    instead of the generator agreeing with itself."""
    import pypdfium2 as pdfium
    _c, _a, pdf = generate(seed=1, error="clean", layout=layout)
    assert pdfium.PdfDocument(pdf)[0].get_textpage().get_text_range().strip()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_aia_guard.py tests/corpus/ -v`
Expected: FAIL — `ModuleNotFoundError` for `scripts.aia_guard` and `ledger_sense.corpus.generate`.

- [ ] **Step 3: Write the guard**

```python
# scripts/aia_guard.py
"""No AIA form artifact in the repo; the non-affiliation notice is present.

AIA G702/G703 are copyrighted and actively enforced. We mimic the column math,
never the form design.
"""

import pathlib
import re

from ledger_sense.notice import NON_AFFILIATION_NOTICE

FORM_NAME = re.compile(r"g70[23]", re.IGNORECASE)
BINARY_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".dwg"}
NOTICE_FRAGMENT = NON_AFFILIATION_NOTICE[:60].lower()   # F-55: one definition
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".ruff_cache", ".pytest_cache"}


def guard(repo_root: str) -> list[str]:
    root = pathlib.Path(repo_root)
    problems: list[str] = []

    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() in BINARY_SUFFIXES and FORM_NAME.search(path.name):
            problems.append(f"{path}: filename suggests an AIA form artifact")

    notice_found = any(
        NOTICE_FRAGMENT in path.read_text(encoding="utf-8", errors="ignore").lower()
        for path in root.rglob("*.md")
        if not any(part in SKIP_DIRS for part in path.parts)
    )
    if not notice_found:
        problems.append("non-affiliation notice not found in any markdown file")
    return problems


if __name__ == "__main__":
    import sys

    found = guard(".")
    for problem in found:
        print(problem, file=sys.stderr)
    sys.exit(1 if found else 0)
```

- [ ] **Step 4: Write the layout — ours, not theirs**

```python
# ledger_sense/corpus/layouts.py
"""OUR layout. Equivalent column semantics; never the AIA form's design.

Column *math* is a fact and may be mimicked. Column lettering, field ordering,
box geometry and typography of the AIA form are expression and may not be.
These headers are plain descriptive English chosen by us.
"""

from typing import NamedTuple


class Column(NamedTuple):
    header: str
    field: str
    x_pt: float
    width_pt: float


PAGE_WIDTH_PT = 792.0   # US Letter landscape
PAGE_HEIGHT_PT = 612.0
MARGIN_PT = 36.0
HEADER_Y_PT = 540.0
ROW_HEIGHT_PT = 18.0
BODY_FONT_PT = 9.0
HEADER_FONT_PT = 8.0

CONTINUATION_COLUMNS: tuple[Column, ...] = (
    Column("Item",                       "item_no",                    36.0,  40.0),
    Column("Description",                "description",                80.0, 200.0),
    Column("Scheduled Value",            "scheduled_value",           288.0,  80.0),
    Column("Previous",                   "previous",                  372.0,  70.0),
    Column("This Period",                "this_period",               446.0,  70.0),
    Column("Stored Materials",           "stored",                    520.0,  80.0),
    Column("Total Completed and Stored", "total_completed_and_stored", 604.0,  90.0),
    Column("Retainage",                  "retainage",                 698.0,  58.0),
)

# SUMMARY LABELS MUST NOT COLLIDE WITH COLUMN HEADERS — REVISED (F-09).
#
# VERIFIED: the original set shared two label strings with CONTINUATION_COLUMNS,
# 'Retainage' and 'Total Completed and Stored'. extract_summary._find_label takes
# the FIRST match in reading order; the grid header is above the summary block, so
# it wins, and _value_near then returns line item 010's cell as the summary figure
# — with a bbox pointing at the wrong cell. A1 would pass on a document that does
# not foot.
#
# This is NOT a synthetic-corpus artifact: real G702-format documents carry the
# same words in both places, for the same reason. The extractor fix (Phase 2 Task
# 17 — search below the grid, refuse an ambiguous label) is the load-bearing one.
# Prefixing here is belt and braces so our own corpus does not mask it.
SUMMARY_LABEL_PREFIX = "Summary —"

SUMMARY_FIELDS: tuple[tuple[str, str], ...] = tuple(
    (f"{SUMMARY_LABEL_PREFIX} {label}", field) for label, field in (
        ("Original Contract Sum",          "original_sum"),
        ("Net Change by Change Orders",    "change_orders_net"),
        ("Contract Sum to Date",           "adjusted_contract_sum"),
        ("Total Completed and Stored",     "total_completed_and_stored"),
        ("Retainage",                      "retainage_total"),
        ("Total Earned Less Retainage",    "total_earned_less_retainage"),
        ("Less Previous Payments",         "previous_payments"),
        ("Current Payment Due",            "current_payment_due"),
    ))

SUMMARY_TOP_PT = 300.0     # below the deepest continuation row
SUMMARY_LABEL_X_PT = 380.0
SUMMARY_VALUE_X_PT = 620.0
SUMMARY_ROW_HEIGHT_PT = 16.0
```

**Adversarial layout variants — REVISED (F-14).** `extract/tables.py` originally imported
`CONTINUATION_COLUMNS` from this module, so the extractor and the generator shared one source of truth and the
twenty-seed test proved only that the generator agrees with itself. Extraction now owns its own header synonym
table (`extract/schema.py`, Phase 2 Task 16) and this module ships **five** layouts so the tests have something
to disagree with:

```python
LAYOUTS = ("baseline", "right_aligned", "alternate_headers", "wrapped_descriptions",
           "two_page")
```

| Variant | What it changes | What it proves |
|---|---|---|
| `right_aligned` | money right-aligned in each column | F-12 — column assignment must use the span, not the left edge |
| `alternate_headers` | "Sched. Value", "Work in Place", "Materials Presently Stored" | the synonym table, not our own wording |
| `wrapped_descriptions` | two-line descriptions overflowing the column | row banding survives a wrapped cell |
| `two_page` | 40 lines across two pages with a repeated header | the header row is not mistaken for a data row on page 2 |

A layout the extractor cannot read must return `CannotExtract`, **never a wrong value**. That assertion is worth
more than the twenty seeds.

- [ ] **Step 5: Write the error injector**

```python
# ledger_sense/corpus/errors.py
"""One mutation per error class, applied to a clean application.

Each mutation must trip exactly one rule and leave the others clean, so a
fixture proves the rule rather than proving the pipeline is noisy.
"""

from typing import Literal

from ledger_sense.domain.models import PayApplication
from ledger_sense.domain.money import money

ErrorClass = Literal[
    "clean", "A1_footing", "A2_line_sum", "A8_retainage",
    "I1_duplicate", "C4_stored_double_count", "C1_retainage_rate",
]


def inject(app: PayApplication, error: ErrorClass) -> PayApplication:
    if error == "clean":
        return app
    if error == "A1_footing":
        return app.model_copy(update={
            "total_completed_and_stored": money(
                app.total_completed_and_stored + money("0.01"))})
    if error == "A2_line_sum":
        first = app.lines[0].model_copy(update={
            "total_completed_and_stored": money(
                app.lines[0].total_completed_and_stored + money("5.00"))})
        return app.model_copy(update={"lines": [first, *app.lines[1:]]})
    if error == "A8_retainage":
        return app.model_copy(update={
            "retainage_total": money(app.retainage_total + money("15.00"))})
    if error == "C4_stored_double_count":
        first = app.lines[0]
        doubled = first.model_copy(update={
            "stored": first.this_period,
            "total_completed_and_stored": money(
                first.previous + first.this_period + first.this_period)})
        return app.model_copy(update={"lines": [doubled, *app.lines[1:]]})
    if error == "C1_retainage_rate":
        # REVISED (F-62). This returned `app` unchanged, so Task 47's
        # test_the_demo_catches_a_retainage_miscalculation could never pass and
        # DoD 7 could not be met. C1 needs a contract term to disagree with, so
        # `inject` now takes and returns the contract too.
        held = money(contract.retainage.base_rate * app.total_completed_and_stored)
        return contract, app.model_copy(update={
            "retainage_total": money(held / 2)})       # billed at half the term
    raise ValueError(f"unknown error class: {error}")


def inject_duplicate(app: PayApplication) -> PayApplication:
    """I1_duplicate — REVISED (F-62).

    A duplicate is a HISTORY-level error: it needs a second document, not a
    mutated field, so it cannot be expressed by `inject`. `generate_history`
    returns the pair, and Task 47's demo seeds both.
    """
    return app.model_copy(update={
        "application_no": (app.application_no or 1) + 1,
        "period_to": app.period_to,          # same period — that is the tell
    })


def generate_history(seed: int) -> list[tuple[Contract, PayApplication, bytes]]:
    """Two documents: the original and a re-submission carrying the same invoice
    number in a shifted period. I1 must catch the second."""
    from ledger_sense.corpus.generate import generate
    contract, app, pdf = generate(seed=seed, error="clean")
    return [(contract, app, pdf),
            (contract, inject_duplicate(app), pdf)]
```

`inject` now takes and returns `(contract, app)` — C1 needs a contract term to disagree with, and returning a
mutated contract is cheaper than a second injection mechanism. Update the `clean`/`A1`/`A2`/`A8`/`C4` branches to
return `(contract, app)` unchanged or mutated as appropriate, and update `generate` accordingly.

- [ ] **Step 6: Write the generator**

```python
# ledger_sense/corpus/generate.py
"""Deterministic synthetic pay applications at known ground truth.

This is a first-class deliverable, not a fixture. The demo runs on it.
"""

import random
from datetime import date
from decimal import Decimal

import pypdfium2 as pdfium

from ledger_sense.corpus.errors import ErrorClass, inject
from ledger_sense.corpus.layouts import (
    BODY_FONT_PT, CONTINUATION_COLUMNS, HEADER_FONT_PT, HEADER_Y_PT,
    PAGE_HEIGHT_PT, PAGE_WIDTH_PT, ROW_HEIGHT_PT,
)
from ledger_sense.domain.models import Contract, ContinuationLine, PayApplication
from ledger_sense.domain.money import RoundingPolicy, money

_TRADES = ["Sitework", "Concrete", "Masonry", "Structural Steel", "Rough Carpentry",
           "Roofing", "Glazing", "Drywall", "Painting", "Electrical", "Plumbing", "HVAC"]


def _clean(seed: int) -> tuple[Contract, PayApplication]:
    rng = random.Random(seed)
    lines: list[ContinuationLine] = []
    for i in range(rng.randint(4, 10)):
        scheduled = money(str(rng.randrange(5_000, 250_000)) + ".00")
        previous = money(str(rng.randrange(0, int(scheduled) // 2)) + ".00")
        this_period = money(str(rng.randrange(0, int(scheduled - previous))) + ".00")
        total = money(previous + this_period)
        lines.append(ContinuationLine(
            item_no=f"{(i + 1) * 10:03d}", description=_TRADES[i % len(_TRADES)],
            scheduled_value=scheduled, previous=previous, this_period=this_period,
            stored=money("0.00"), total_completed_and_stored=total,
            retainage=money("0.00")))

    contract_sum = money(sum((ln.scheduled_value for ln in lines), money("0")))
    grand_total = money(sum((ln.total_completed_and_stored for ln in lines), money("0")))
    contract = Contract(id=f"C-{seed}", original_sum=contract_sum,
                        rounding=RoundingPolicy(2, 4), retainage_rate=Decimal("0.10"),
                        retainage_steps=[], jurisdiction=None)
    app = PayApplication(
        contract_id=contract.id, application_no=rng.randint(1, 12),
        period_to=date(2026, 8, 31), lines=lines, change_orders_net=money("0.00"),
        retainage_total=money("0.00"), previous_payments=money("0.00"),
        total_completed_and_stored=grand_total)
    return contract, app


def _render(app: PayApplication, contract: Contract,
            columns: tuple[Column, ...] = CONTINUATION_COLUMNS) -> bytes:
    """Render OUR layout with a real text layer. Extraction depends on the text
    layer existing — a scanned-only corpus would be useless.

    `columns` is a PARAMETER, not a module lookup (F-11). The fingerprint test
    monkeypatched `layouts.CONTINUATION_COLUMNS`, but this module bound the name
    at import and held its own reference, so the rendered PDF never changed and
    `test_a_shifted_column_layout_yields_a_different_key` asserted inequality on
    two identical values. It failed, and the tempting "fix" is to weaken it.
    """
    pdf = pdfium.PdfDocument.new()
    font = pdf.add_font_helv()
    page = pdf.new_page(PAGE_WIDTH_PT, PAGE_HEIGHT_PT)

    for column in columns:
        page.insert_text(column.header, column.x_pt, HEADER_Y_PT, HEADER_FONT_PT, font)
    y = HEADER_Y_PT - ROW_HEIGHT_PT
    for line in app.lines:
        for column in columns:
            page.insert_text(str(getattr(line, column.field)),
                             column.x_pt, y, BODY_FONT_PT, font)
        y -= ROW_HEIGHT_PT

    # THE SUMMARY BLOCK — REVISED (F-10). It was defined in layouts.py and never
    # drawn, so Task 17's summary test could not pass and Task 21's
    # `_unextractable == []` could not hold. The original plan noticed this three
    # tasks downstream, in the task that proves DoD 2. Fix it where it is created.
    summary_values = _summary_values(app, contract)
    y = SUMMARY_TOP_PT
    for label, field in SUMMARY_FIELDS:
        page.insert_text(label, SUMMARY_LABEL_X_PT, y, BODY_FONT_PT, font)
        page.insert_text(str(summary_values[field]),
                         SUMMARY_VALUE_X_PT, y, BODY_FONT_PT, font)
        y -= SUMMARY_ROW_HEIGHT_PT

    page.gen_content()
    return pdf.save_to_bytes()


def _summary_values(app: PayApplication, contract: Contract) -> dict[str, Money]:
    earned = money(app.total_completed_and_stored - app.retainage_total)
    return {
        "original_sum": contract.original_sum,
        "change_orders_net": app.change_orders_net,
        "adjusted_contract_sum": app.adjusted_contract_sum(contract),
        "total_completed_and_stored": app.total_completed_and_stored,
        "retainage_total": app.retainage_total,
        "total_earned_less_retainage": earned,
        "previous_payments": app.previous_payments,
        "current_payment_due": money(earned - app.previous_payments),
    }


def generate(seed: int, error: ErrorClass,
             layout: str = "baseline") -> tuple[Contract, PayApplication, bytes]:
    contract, app = _clean(seed)
    contract, mutated = inject(contract, app, error)
    return contract, mutated, _render(mutated, contract, columns_for(layout))


def ground_truth(seed: int, error: ErrorClass) -> dict[str, str]:
    """Pre-mutation values. Extraction is scored against these."""
    _contract, app = _clean(seed)
    return {
        "total_completed_and_stored": str(app.total_completed_and_stored),
        "retainage_total": str(app.retainage_total),
        "previous_payments": str(app.previous_payments),
        **{f"line[{ln.item_no}].{field}": str(getattr(ln, field))
           for ln in app.lines
           for field in ("scheduled_value", "previous", "this_period", "stored",
                         "total_completed_and_stored")},
    }
```

**If `pdfium`'s text-insertion API differs from the calls above** (its Python surface changes between
majors), keep the layout constants and the `ground_truth` contract exactly as written and adapt only
`_render`. The tests assert the text layer and the ground-truth mapping, not the rendering calls.

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_aia_guard.py tests/corpus/ -v`
Expected: PASS, 10 tests.

- [ ] **Step 8: Add the CI job and commit**

```yaml
  aia-guard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --all-extras
      - run: uv run python -m scripts.aia_guard
```

```bash
git add ledger_sense/corpus/ scripts/aia_guard.py tests/corpus/ tests/test_aia_guard.py .github/workflows/ci.yml
git commit -m "feat(corpus): synthetic generator in our own layout, with the AIA guard"
```

---

### Task 13: `bok-core` boundary integration — **GATED**

**Do not start until `bok_core.boundary` is published to PyPI.** If it has not landed, skip to
Phase 2; nothing in Tasks 14–21 depends on this except the upload path.

**Files:**
- Create: `ledger_sense/ingest/intake.py`
- Test: `tests/boundary/test_client_confidential_cannot_reach_a_model.py`

**Interfaces:**
- Consumes: `bok_core.boundary.{Sensitivity, SensitivityViolation, Labelled}`,
  `bok_core.boundary.redact.verify_redaction`, `bok_core.gateway.Gateway`.
- Produces: `accept_document(payload: bytes, sensitivity: Sensitivity, origin: str) -> Labelled[bytes]`
  — the single entry point through which every externally-supplied document must pass.

- [ ] **Step 1: Write the failing test**

```python
# tests/boundary/test_client_confidential_cannot_reach_a_model.py
"""Ledger Sense re-tests the boundary against the REAL published bok-core.
Not a mock. A mocked boundary stops nothing at 2am in week 11."""

import ast
import pathlib

import pytest
from bok_core.boundary import Sensitivity, SensitivityViolation
from bok_core.gateway import Gateway

from ledger_sense.ingest.intake import accept_document


def test_client_confidential_payload_raises_at_the_gateway():
    doc = accept_document(b"%PDF-1.4 invoice", Sensitivity.CLIENT_CONFIDENTIAL, "upload")
    with pytest.raises(SensitivityViolation):
        Gateway().call(lane="extract-vision", payload=doc)


def test_synthetic_payload_is_permitted():
    doc = accept_document(b"%PDF-1.4 synthetic", Sensitivity.SYNTHETIC, "corpus")
    assert doc.sensitivity is Sensitivity.SYNTHETIC


def test_redacted_without_verification_is_refused():
    with pytest.raises(SensitivityViolation, match="verif"):
        accept_document(b"%PDF-1.4", Sensitivity.REDACTED, "upload")


def test_intake_is_the_only_path_that_constructs_a_labelled_document():
    """REVISED (F-32). The original matched only `node.func.id == "Labelled"`,
    which is an ast.Name. It missed `bok_core.boundary.Labelled(...)` — an
    ast.Attribute — and every aliased import. Resolve names before matching."""
    hits = []
    for path in pathlib.Path("ledger_sense").rglob("*.py"):
        if path.name == "intake.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))

        # Build the local alias map: `from x import Labelled as L` -> {"L"}
        aliases = {"Labelled"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                aliases |= {a.asname or a.name for a in node.names
                            if a.name == "Labelled"}

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (node.func.id if isinstance(node.func, ast.Name)
                    else getattr(node.func, "attr", ""))
            if name in aliases:
                hits.append(f"{path}:{node.lineno}")
    assert hits == [], f"Labelled constructed outside intake.py: {hits}"
```

The same alias-resolving shape replaces the three other AST guards in this plan — Phase 2 Task 18 (domain models
outside `assemble.py`), Phase 3 Task 26 (the waivers module generating nothing) and Phase 5 Task 36 (no
`BillPayment`). Each was evadable by an attribute call or an `import … as`. Extract it once into
`tests/_ast_guard.py::calls_to(tree, name) -> list[int]` and import it in all four.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/boundary/ -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.ingest.intake'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/ingest/intake.py
"""The single door every externally-supplied document passes through.

The label attaches where the file is OPENED. If it attaches late, the leak
happens early.
"""

from bok_core.boundary import Labelled, Sensitivity, SensitivityViolation
from bok_core.boundary.redact import verify_redaction


def accept_document(
    payload: bytes, sensitivity: Sensitivity, origin: str
) -> Labelled[bytes]:
    if sensitivity is Sensitivity.REDACTED and not verify_redaction(payload).verified:
        raise SensitivityViolation(
            "REDACTED payloads require a passing redaction verification pass"
        )
    return Labelled(payload=payload, sensitivity=sensitivity, provenance=origin)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/boundary/ -v`
Expected: PASS, 4 tests. **DoD 8.**

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/ingest/intake.py tests/boundary/
git commit -m "feat(ingest): single labelled intake door, boundary re-tested against real bok-core"
```

---

## Phase 1 exit criteria

- [ ] `uv run pytest tests/ -v` green — every task's suite, including `tests/property/`
- [ ] `uv run python -m scripts.check_no_float` and `uv run python -m scripts.licence_audit` green
- [ ] `uv run python -m scripts.aia_guard` green against the real repository, not a fixture
- [ ] `uv run mypy --strict ledger_sense` green
- [ ] `docs/methodology.md` exists with `## What we measure` and `## How we measure it` filled — the
      other three sections are stubs until weeks 6, 11 and 12. **One schema, opened here** (F-57)
- [ ] Coverage ≥75% on `ledger_sense/domain`, `ledger_sense/rules/arithmetic` and `ledger_sense/baseline`
- [ ] **DoD 8 satisfied** *if* `bok-core` shipped. If it did not, Task 13 and the upload path defer
      **together** — the feature is cuttable, the test is not (F-60)

**Audit regressions that must be green (`2026-08-29-REVISIONS.md`):**

- [ ] `test_a_strict_money_field_refuses_a_float_at_the_schema`, `test_every_domain_model_is_strict` and
      `test_no_decimal_field_anywhere_accepts_a_float` — **F-01, the one verified by execution.** Without
      layer 4, `LineItem(amount=1000.0)` succeeds and `0.1 + 0.2` reaches the money path as
      `Decimal('0.30000000000000004')`. The other three layers all see a `Decimal` annotation and pass it
- [ ] `test_money_rejects_bool` — F-02. `True` silently became one dollar
- [ ] `test_a3_clean_control_total_within_scheduled_value` and
      `test_a4_trips_on_work_billed_against_a_zero_scheduled_value` — **F-03.** The original A3 and A4
      implemented rules the spec does not have, and their tests were written against the implementation
- [ ] `rule_a9` does **not** exist in `ledger_sense/rules/arithmetic/foot.py`, and
      `tests/rules/arithmetic/test_foot.py` collects exactly 2 tests — **F-04, F-67.** A9 is a convention
      rule; it ships in Phase 3 Task 24 with a `RuleSource` and its own suppression
- [ ] `test_convention_rule_without_a_source_is_refused` and `test_source_missing_confirmed_by_is_refused`
      — the gate that hard-blocks Phase 3. It must raise at **import** time, not at call time
- [ ] `test_the_t_value_does_not_collapse_to_the_normal_approximation` — **F-52.** The table stopped at
      df 9 and fell back to 1.96, understating the margin by 15.4% at df 9, 6.8% at df 19, 2.6% at df 49.
      Every error in the direction that flatters the vendor, in the artifact sold on being defensible
- [ ] `test_an_uncertain_loaded_rate_widens_the_interval` — F-53. The interval propagated uncertainty in
      touch time only, while the loaded rate is the more contestable of the two inputs
- [ ] `test_too_few_samples_refuses_to_report_an_interval` — F-54
- [ ] `test_the_summary_block_is_actually_rendered` — **F-10.** The renderer emitted the grid and not the
      summary, so Task 17 and the DoD-2 proof would both have failed on a defect introduced five tasks earlier
- [ ] `test_no_summary_label_collides_with_a_column_header` — **F-09.** `Retainage` and `Total Completed
      and Stored` appeared in both the grid header and the summary block; first-match anchoring returned
      line item 010's cell as the summary figure, with a bounding box pointing at the wrong cell. A1 would
      have passed on a document that does not foot
- [ ] `test_previously_bypassing_licences_are_now_denied` and
      `test_the_gate_reads_the_real_environment_not_a_hand_written_list` — **F-33.** The deny-list let
      `AGPL-3.0-only OR LGPL-3.0`, `BUSL-1.1`, `Elastic-2.0` and `PolyForm-Noncommercial` through, and the
      CI job exited 0 unconditionally because nothing read a real package
- [ ] `test_intake_is_the_only_path_that_constructs_a_labelled_document` — F-32. The guard resolves
      imports through an alias map first, so `import bok_core.boundary as b` cannot evade it
- [ ] The non-affiliation notice appears **once** in the source, in `ledger_sense/notice.py` (Phase 0
      Task P1), and Task 12's guard imports it — **F-55.** Two different wordings, each asserted by a
      different test in the same suite, could not both pass

**Then Phase 2** (`2025-03-11-ledger-sense-phase-2-extraction.md`), and **Phase 3 only if the week-5
controller session has closed.** `rules/registry.py` enforces that; it is not a formality you can route around.
