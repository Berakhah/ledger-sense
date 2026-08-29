# Ledger Sense — Weeks 5–12 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a construction pay-application document-intelligence product that extracts deterministically, validates against a provenanced domain rulebook, routes uncertain fields to a keyboard-driven review queue, and writes drafts into QuickBooks Online — with no client document ever reaching a model provider.

**Architecture:** Deterministic-first extraction (text layer + geometry + table reconstruction, with a learned per-producer profile cache fed by reviewer corrections), a three-class validation rulebook where domain-convention rules cannot be registered without provenance, and confidence routing that refuses to auto-post until a reliability curve fitted on real labels exists. A model lane exists but is fenced to `SYNTHETIC`/`REDACTED` payloads and is optional.

**Tech Stack:** Python 3.12+, `uv`, `ruff`, `mypy --strict`, FastAPI, Pydantic v2, `pytest` + `hypothesis`, Postgres + `alembic`, Next.js 15 + `bok-ui`, `pypdfium2` / `pdfplumber` / `docling[rapidocr]`, Playwright, `axe-core`.

**Spec:** `docs/superpowers/specs/2025-03-11-ledger-sense-design.md` — read it before Task 1. The plan argues from the spec; executors read both.

---

## Global Constraints

Every task's requirements implicitly include this section. Values copied verbatim from the spec.

- **Python 3.12+**, `uv` for dependencies, `ruff` for lint+format, `mypy --strict` on every module.
- **Licence: Apache-2.0.** Permissive dependencies only — MIT / Apache-2.0 / BSD / OFL.
- **No AGPL.** `PyMuPDF` (AGPL, confirmed in its own PyPI metadata) and `ocrmypdf` (MPL-2.0 package, but requires an AGPL-3.0 Ghostscript binary) are **rejected by name**. `camelot-py` is permitted **only without its `ghostscript` extra**. `docling` is pinned as **`docling[rapidocr]`**, never `docling[easyocr]`.
- **No floats in the money path.** `Decimal` with an explicit context. Enforced by three independent layers (Task 3).
- **No AIA form artifact anywhere in the repo.** The synthetic corpus uses our own layout with equivalent column semantics. The non-affiliation notice ships in README, docs and UI:
  > Not developed, endorsed, approved, sponsored by, or affiliated with the American Institute of Architects. AIA®, G702® and G703® are registered trademarks of the American Institute of Architects. References describe format compatibility only.
- **No model name hardcoded.** Model selection resolves through `bok-core` lanes.
- **No secrets in the repo, ever — including test fixtures.** `gitleaks` pre-commit hook.
- **$0 recurring cost.** One-off spend (the week-5 controller session, the legal review) is permitted; recurring subscriptions are not.
- **Everything runs from `docker compose up`** on a fresh machine, with only `.env.example` to copy.
- **Coverage floor: 75%.**
- **Never claim full automation.** Straight-through rate is published including the misses.
- **No CLI.** Dev entrypoint is `python -m ledger_sense`. No Typer/Click, no `[project.scripts]`.

### Boundary gate (blocks specific tasks, named where they occur)

`bok_core.boundary` must be published, and **ledger-sense's own test must assert `CLIENT_CONFIDENTIAL` raises `SensitivityViolation` against the real published package — not a mock**, before either of these ships:

1. Any code that accepts a document from outside the system (upload, email watcher, API intake).
2. Any code that calls the gateway.

Deterministic extraction developed against the synthetic corpus is **not** gated — it calls no model and touches no client data.

### Request these from the `backoffice-kit` session before Task 13

`bok-core` must ship five additions beyond `00` §4, specified in spec §4.1. Task 13 will not compile without them. Raise them in the `backoffice-kit` session now, not in week 6:

1. `FileSource(path, page, bbox)` — `00` specifies "file+line", which is scanner-shaped. Without a bbox the review queue cannot box a field and the provenance rail cannot point at a cell.
2. `Claim[Decimal]` must serialise as a **string, never a float**.
3. `RuleSource(authority, citation, confirmed_by, confirmed_at)` as a `Source` variant. Task 5 consumes it structurally and does not block on it, but `bok-core` should own the concrete class.
4. `Labelled[T]` must wrap **bytes and file handles**, not only prompts.
5. `ConfidenceMeter` (bok-ui) takes a calibration **basis object** `{state, basis, curve_id, n}`, not a float — Task 41 needs three distinguishable states.

### One external check that gates nothing but changes scope

Spec §2.2: **does any free-tier, vision-capable provider state in writing that it does not train on inputs?** If no, the model lane is `SYNTHETIC`-only permanently and `REDACTED` never unlocks — a simpler product. Answer before Phase 6. No task depends on it.

---

## File Structure

```
ledger_sense/
  __main__.py                    # dev entrypoint: python -m ledger_sense
  domain/
    money.py                     # Money type, Decimal context, rounding policy
    models.py                    # Contract, PayApplication, ContinuationLine, Invoice, Waiver
    vendor.py                    # CanonicalVendor, VendorAlias, PaymentDetail history
  rules/
    provenance.py                # RuleSource protocol (structural; bok-core's class satisfies it)
    registry.py                  # register(); refuses a rule with no RuleSource
    engine.py                    # runs registered rules over a document, emits Findings
    arithmetic/foot.py           # A1, A9
    arithmetic/lines.py          # A2, A3, A4
    arithmetic/payment.py        # A5, A6, A7, A8
    integrity/duplicates.py      # I1
    integrity/vendors.py         # I2
    integrity/bank_details.py    # I3
    convention/retainage.py      # C1-C3
    convention/stored.py         # C4-C6
    convention/change_orders.py  # C7, C8
    convention/tax.py            # C9, C10
    convention/waivers.py        # C11-C14
  baseline/
    study.py                     # sampling frame, timing instrument, loaded-rate model
    report.py                    # print-ready measured-cost report with confidence interval
  corpus/
    layouts.py                   # OUR layouts. No AIA form geometry, ever.
    generate.py                  # emits pay applications at known ground truth
    errors.py                    # injected error classes, one per validation rule
  ingest/
    intake.py                    # the single labelled door for external documents
    classify.py                  # rules -> document type
    fingerprint.py               # PDF Producer + layout hash -> profile key
  extract/
    textlayer.py · tables.py · anchors.py · profiles.py · ocr.py · assemble.py
  route/
    calibration.py · router.py
  writeback/
    interface.py · qbo.py · export.py · idempotency.py
  api/                           # FastAPI
  web/                           # Next.js 15 + bok-ui

scripts/
  licence_audit.py               # SPDX deny-list + native-layer check
  check_no_float.py              # AST scan of domain/, rules/, writeback/
  aia_guard.py                   # no form artifacts; notice present
tests/
  mirrors the package tree; plus tests/boundary/ and tests/property/
docs/
  methodology.md                 # written incrementally: weeks 5, 11, 12
```

### One design refinement, decided here

The spec says `extract/` emits `Claim`s and no bare value crosses into `domain/`. Implemented as: **domain models carry typed `Money` fields *plus* a `provenance: dict[str, Claim]` sidecar.** Rules compute on the values; findings cite `provenance[field_name]`; the UI renders every figure through its claim. Week 5 builds models with an empty provenance dict, week 7 populates it — **no rewrite, and no generic-over-Claim explosion in the rule code.**

`RuleSource` is consumed as a **structural Protocol** (`rules/provenance.py`), so week 5 is not blocked on `bok-core`. When `bok_core.provenance.RuleSource` lands it satisfies the Protocol with no change. This is a structural type, not a shim — there is nothing to delete later.

---

# PHASE 1 — Weeks 5–6 (step-level, executable now)

Nothing in Phase 1 is blocked by `bok-core` except Task 13.

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
```

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


DENIED_SPDX_FRAGMENTS = ("AGPL", "GPL-2", "GPL-3", "SSPL", "NON-COMMERCIAL", "CC-BY-NC")
ALLOWED_SPDX_FRAGMENTS = ("LGPL",)  # LGPL is not caught by the GPL fragments above

# Packages whose Python metadata is permissive but whose *native* dependency is not.
NATIVE_DENY = {
    "ocrmypdf": "requires an external Ghostscript binary, which is AGPL-3.0 (Artifex)",
}

# Extras that pull a denied licence into an otherwise clean package.
EXTRA_DENY = {
    ("camelot-py", "ghostscript"): "the ghostscript extra pulls AGPL-3.0 Ghostscript",
    ("docling", "easyocr"): "easyocr bundles CRAFT weights with research-use ambiguity",
}


def _spdx_denied(spdx: str) -> bool:
    upper = spdx.upper()
    if any(frag in upper for frag in ALLOWED_SPDX_FRAGMENTS):
        return False
    return any(frag in upper for frag in DENIED_SPDX_FRAGMENTS)


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_licence_audit.py -v`
Expected: PASS, 6 tests.

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

MONEY_CONTEXT = decimal.Context(prec=28, rounding=decimal.ROUND_HALF_UP)

Money = Annotated[Decimal, "money"]


class RoundingPolicy(NamedTuple):
    money_places: int
    percent_places: int


def money(value: str | int | Decimal) -> Money:
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

### Task 3: No-float enforcement — three independent layers

One layer is not enough. `mypy` catches assignment, the AST scan catches literals, the model walk catches the serialisation case both miss.

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
  - `ContinuationLine(item_no: str, description: str, scheduled_value: Money, previous: Money, this_period: Money, stored: Money, total_completed_and_stored: Money, retainage: Money)`
  - `PayApplication(contract_id: str, application_no: int, period_to: date, lines: list[ContinuationLine], change_orders_net: Money, retainage_total: Money, previous_payments: Money, total_completed_and_stored: Money, provenance: dict[str, object])`
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
    model_config = ConfigDict(arbitrary_types_allowed=False)


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

    @field_serializer("scheduled_value", "previous", "this_period", "stored",
                      "total_completed_and_stored", "retainage")
    def _as_string(self, value: Decimal) -> str:
        """Decimal must never round-trip through float. Serialise as a string."""
        return str(value)


class PayApplication(_Base):
    contract_id: str
    application_no: int
    period_to: date
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

### Task 6: Arithmetic rules A1, A9 — footing

**Files:**
- Create: `ledger_sense/rules/engine.py`
- Create: `ledger_sense/rules/arithmetic/foot.py`
- Test: `tests/rules/arithmetic/test_foot.py`

**Interfaces:**
- Consumes: `register` (Task 5), `PayApplication` / `Contract` (Task 4).
- Produces: `Finding = NamedTuple("Finding", [("rule_id", str), ("field", str), ("message", str), ("expected", Money), ("actual", Money)])` in `ledger_sense/rules/engine.py`; rules `rule_a1`, `rule_a9`, each `(app, contract) -> list[Finding]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/rules/arithmetic/test_foot.py
from datetime import date
from decimal import Decimal
from ledger_sense.domain.models import Contract, ContinuationLine, PayApplication
from ledger_sense.domain.money import RoundingPolicy, money
from ledger_sense.rules.arithmetic.foot import rule_a1, rule_a9


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


def test_a9_clean_control_scheduled_values_sum_to_adjusted_contract_sum():
    app = _app([_line("600.00", "0.00"), _line("400.00", "0.00")], "0.00")
    assert rule_a9(app, _contract("1000.00")) == []


def test_a9_trips_when_scheduled_values_miss_the_adjusted_sum():
    app = _app([_line("600.00", "0.00"), _line("400.00", "0.00")], "0.00", co="50.00")
    findings = rule_a9(app, _contract("1000.00"))
    assert len(findings) == 1
    assert findings[0].expected == money("1050.00")
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
"""A1, A9 — footing. Exact equality; no tolerance."""

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


@register("A9", "arithmetic")
def rule_a9(app: PayApplication, contract: Contract) -> list[Finding]:
    expected = app.adjusted_contract_sum(contract)
    actual = money(sum((line.scheduled_value for line in app.lines), money("0")))
    if expected != actual:
        return [Finding(
            rule_id="A9", field="scheduled_value",
            message="sum of scheduled values does not equal the adjusted contract sum",
            expected=expected, actual=actual,
        )]
    return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/rules/arithmetic/test_foot.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/rules/engine.py ledger_sense/rules/arithmetic/foot.py tests/rules/arithmetic/test_foot.py
git commit -m "feat(rules): A1 exact footing and A9 scheduled-value reconciliation"
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
    findings = []
    for line in app.lines:
        balance = money(line.scheduled_value - line.total_completed_and_stored)
        if balance < 0:
            findings.append(Finding(
                "A3", f"line[{line.item_no}].balance_to_finish",
                "total completed and stored exceeds the scheduled value",
                money("0.00"), balance))
    return findings


@register("A4", "arithmetic")
def rule_a4(app: PayApplication, contract: Contract) -> list[Finding]:
    findings = []
    for line in app.lines:
        if line.scheduled_value == 0:
            if line.total_completed_and_stored != 0:
                findings.append(Finding(
                    "A4", f"line[{line.item_no}].percent_complete",
                    "work billed against a line with no scheduled value",
                    money("0.00"), line.total_completed_and_stored))
            continue
        try:
            pct = quantize(
                money(line.total_completed_and_stored / line.scheduled_value),
                contract.rounding.percent_places)
        except (DivisionByZero, InvalidOperation):
            continue
        if pct < 0:
            findings.append(Finding(
                "A4", f"line[{line.item_no}].percent_complete",
                "percentage complete is negative", money("0.00"), pct))
    return findings
```

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
    """Yields a (contract, application) pair satisfying A1-A9 by construction.
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
from ledger_sense.rules.arithmetic.foot import rule_a1, rule_a9
from ledger_sense.rules.arithmetic.lines import rule_a2, rule_a3, rule_a4
from ledger_sense.rules.arithmetic.payment import (
    current_payment_due, rule_a5, rule_a6, rule_a8, total_earned_less_retainage,
)
from tests.property.strategies import consistent_pay_applications

ALL_RULES = [rule_a1, rule_a2, rule_a3, rule_a4, rule_a5, rule_a6, rule_a8, rule_a9]


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
git commit -m "test(rules): hypothesis invariants over A1-A9"
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
    tight_lo, tight_hi = _study([360, 360, 360, 360, 360]).confidence_interval()
    wide_lo, wide_hi = _study([60, 360, 660, 120, 600]).confidence_interval()
    assert (wide_hi - wide_lo) > (tight_hi - tight_lo)


def test_a_single_sample_refuses_to_report_an_interval():
    """One observation has no defensible interval. Say so rather than invent one."""
    with pytest.raises(ValueError, match="at least 2"):
        _study([360]).confidence_interval()


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
# Student's t, two-tailed, 95%, indexed by degrees of freedom (n - 1).
_T_95 = {1: Decimal("12.706"), 2: Decimal("4.303"), 3: Decimal("3.182"),
         4: Decimal("2.776"), 5: Decimal("2.571"), 6: Decimal("2.447"),
         7: Decimal("2.365"), 8: Decimal("2.306"), 9: Decimal("2.262")}
_T_95_LARGE = Decimal("1.96")

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

    def effective_hourly(self) -> Money:
        return quantize(money(self.hourly * self.overhead_multiplier), 2)


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

    def confidence_interval(self, level: Decimal = Decimal("0.95")) -> tuple[Money, Money]:
        n = len(self.samples)
        if n < 2:
            raise ValueError("a confidence interval requires at least 2 samples")
        seconds = [Decimal(s.touch_seconds) for s in self.samples]
        mean = self._mean_seconds()
        variance = sum((x - mean) ** 2 for x in seconds) / (n - 1)
        std_err = variance.sqrt() / Decimal(n).sqrt()
        margin_seconds = _T_95.get(n - 1, _T_95_LARGE) * std_err
        rate = self.loaded_rate.effective_hourly()
        lo = quantize(money((mean - margin_seconds) / SECONDS_PER_HOUR * rate), 2)
        hi = quantize(money((mean + margin_seconds) / SECONDS_PER_HOUR * rate), 2)
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
            f"{self.loaded_rate.overhead_multiplier} to cover employer overhead."
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
                 for i, s in enumerate([300, 360, 420, 380, 340])],
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

Create `docs/methodology.md` with a `## Baseline study` section stating: sampling frame, sample
size, collection period, timing protocol, the touch-time definition copied verbatim from
`ledger_sense/baseline/study.py::TOUCH_TIME_DEFINITION`, the loaded-rate definition, and the
interval method (Student's *t*, two-tailed, 95%). Add `## Calibration basis` and
`## Straight-through rate` as headed sections, each with a one-line note that they are filled in
weeks 11 and 12 respectively.

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

NOTICE_FRAGMENT = "not developed, endorsed, approved, sponsored by, or affiliated with"


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

FORM_NAME = re.compile(r"g70[23]", re.IGNORECASE)
BINARY_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".dwg"}
NOTICE_FRAGMENT = "not developed, endorsed, approved, sponsored by, or affiliated with"
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

SUMMARY_FIELDS: tuple[tuple[str, str], ...] = (
    ("Original Contract Sum",          "original_sum"),
    ("Net Change by Change Orders",    "change_orders_net"),
    ("Contract Sum to Date",           "adjusted_contract_sum"),
    ("Total Completed and Stored",     "total_completed_and_stored"),
    ("Retainage",                      "retainage_total"),
    ("Total Earned Less Retainage",    "total_earned_less_retainage"),
    ("Less Previous Payments",         "previous_payments"),
    ("Current Payment Due",            "current_payment_due"),
)
```

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
    if error in ("I1_duplicate", "C1_retainage_rate"):
        # I1 is a history-level error; C1 needs a contract term to disagree with.
        # Both are exercised by their own rule's tests in Phase 3, not here.
        return app
    raise ValueError(f"unknown error class: {error}")
```

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


def _render(app: PayApplication) -> bytes:
    """Render OUR layout with a real text layer. Extraction depends on the
    text layer existing — a scanned-only corpus would be useless."""
    pdf = pdfium.PdfDocument.new()
    font = pdf.add_font_helv()
    page = pdf.new_page(PAGE_WIDTH_PT, PAGE_HEIGHT_PT)
    for column in CONTINUATION_COLUMNS:
        page.insert_text(column.header, column.x_pt, HEADER_Y_PT, HEADER_FONT_PT, font)
    y = HEADER_Y_PT - ROW_HEIGHT_PT
    for line in app.lines:
        for column in CONTINUATION_COLUMNS:
            page.insert_text(str(getattr(line, column.field)),
                             column.x_pt, y, BODY_FONT_PT, font)
        y -= ROW_HEIGHT_PT
    page.gen_content()
    return pdf.save_to_bytes()


def generate(seed: int, error: ErrorClass) -> tuple[Contract, PayApplication, bytes]:
    contract, app = _clean(seed)
    mutated = inject(app, error)
    return contract, mutated, _render(mutated)


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
    hits = []
    for path in pathlib.Path("ledger_sense").rglob("*.py"):
        if path.name == "intake.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Labelled":
                hits.append(f"{path}:{node.lineno}")
    assert hits == [], f"Labelled constructed outside intake.py: {hits}"
```

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

# PHASES 2–6 — Weeks 7–12

**Each phase has its own step-level plan document.** The tables below are the index; the linked
document carries the TDD cycles, test code, implementation and commits.

| Phase | Weeks | Tasks | Document |
|---|---|---|---|
| 2 — Extraction | 7 | 13b, 14–21 | `2025-03-11-ledger-sense-phase-2-extraction.md` |
| 3 — Rules | 8 | 22–27 | `2025-03-11-ledger-sense-phase-3-rules.md` |
| 4 — Review UI | 9 | 28–33 | `2025-03-11-ledger-sense-phase-4-review-ui.md` |
| 5 — Writeback | 10 | 34–39 | `2025-03-11-ledger-sense-phase-5-writeback.md` |
| 6 — Routing & close-out | 11–12 | 40–48 | `2025-03-11-ledger-sense-phase-6-routing-closeout.md` |

Every phase document inherits this document's **Global Constraints** and **File Structure**
sections. Read this file first, then the phase file.

---

## Phase 2 — Week 7: Extraction, plus the deferred baseline screen

| Task | Files | Verification |
|---|---|---|
| **13b. Baseline study screen** (spec §8 item 1; Tasks 10–11 built the engine and the printable report, this is the interactive instrument) | `ledger_sense/api/baseline.py`, `ledger_sense/web/app/baseline/` | Volume counter; a start/stop timing instrument writing `TimedSample` rows; loaded-rate input; exception log. Playwright: time three documents, enter a loaded rate, produce the Task-11 report with a real interval. **Runs before the pipeline exists — it is what you sell in week one, and it is the DoD 11 outreach asset** |
| **14. Text layer + coordinates** | `ledger_sense/extract/textlayer.py`, `tests/extract/test_textlayer.py` | Extract every word with its bbox from a Task-12 generated PDF; assert positions match the layout constants in `corpus/layouts.py` within 1pt |
| **15. Document fingerprint** | `ledger_sense/ingest/fingerprint.py`, `tests/ingest/test_fingerprint.py` | Two documents from the same generator layout produce the same key; a different layout produces a different key. Uses PDF `Producer` metadata plus a hash of column x-offsets |
| **16. Continuation-sheet table reconstruction** | `ledger_sense/extract/tables.py`, `tests/extract/test_tables.py` | Reconstruct a grid from ruling lines and column alignment; map columns by header semantics; assert every cell matches `corpus.ground_truth()` across 20 seeds |
| **17. Summary anchor + geometry extraction** | `ledger_sense/extract/anchors.py`, `tests/extract/test_anchors.py` | Locate each labelled field by anchor text and take the geometrically adjacent value; assert against `ground_truth()` |
| **18. Claim assembly** | `ledger_sense/extract/assemble.py`, `tests/extract/test_assemble.py` | Every extracted field emits a `Claim` with `method=DETERMINISTIC`, `confidence=None`, `FileSource(path, page, bbox)`. AST test (pattern as in Task 13 step 1) asserting no bare value is constructed into `domain/` outside `assemble.py` |
| **19. Learned profile cache** | `ledger_sense/extract/profiles.py`, `tests/extract/test_profiles.py` | A simulated reviewer correction on an unseen layout writes geometry to the cache; the next document with the same fingerprint extracts that field without correction. Assert the cache persists **geometry only, never a value** |
| **20. Classification + OCR tail** | `ledger_sense/ingest/classify.py`, `ledger_sense/extract/ocr.py`, tests | Classifier assigns the right type for each corpus doc type; the OCR path returns an explicit `CannotExtract` for a rasterised input rather than a low-confidence guess |
| **21. Degraded-mode proof** | `tests/test_degraded_mode.py` | With every provider disabled, the pipeline extracts and validates digital-PDF pay applications end to end. **DoD 2** |

## Phase 3 — Week 8: Convention and integrity rules

**Blocked on the week-5 controller session closing.** `registry.py` enforces this — an unsourced convention rule raises at import time.

| Task | Files | Verification |
|---|---|---|
| **22. Retainage C1–C3** | `ledger_sense/rules/convention/retainage.py`, tests | Rate read from `Contract`, never a constant. Stepping fires at the contract's own milestone. Tripping fixture + clean control per rule. `RuleSource.confirmed_by` names the controller |
| **23. Stored materials C4–C6** | `ledger_sense/rules/convention/stored.py`, tests | Double-count fixture trips C4; a legitimate stored→installed transition across two periods trips nothing; stored-without-backup trips C6 |
| **24. Change orders C7–C8** | `ledger_sense/rules/convention/change_orders.py`, tests | C8 **must not** fire when an additive change order raised the scheduled value in the same period — the false-positive regression test is mandatory |
| **25. Tax C9–C10, one jurisdiction** | `ledger_sense/rules/convention/tax.py`, tests | The configured jurisdiction validates; every other jurisdiction returns an explicit "tax rules not configured for this jurisdiction" finding rather than passing silently |
| **26. Waivers C11–C14** | `ledger_sense/rules/convention/waivers.py`, tests | **C11 regression test:** conditional-for-payment passes; unconditional-before-funds-clear trips. Assert the module generates no document — extract, validate, flag, stop |
| **27. Integrity I1–I3** | `ledger_sense/rules/integrity/{duplicates,vendors,bank_details}.py`, `ledger_sense/domain/vendor.py`, tests | Duplicate across full history by three fingerprints; vendor first-match requires confirmation and merges reverse cleanly with alias provenance; **bank-detail change is a hard stop — assert no code path updates a payment record without an explicit human confirmation token.** **DoD 3** |

## Phase 4 — Week 9: Review queue and reconciliation

| Task | Files | Verification |
|---|---|---|
| **28. Review-queue API** | `ledger_sense/api/review.py`, tests | Exception list, accept/edit/reject transitions, each writing a `HUMAN`-method Claim |
| **29. Split-view document renderer** | `ledger_sense/web/app/review/`, `pdf.js` | Document at fidelity, field under review boxed from its Claim's bbox, everything else dimmed |
| **30. Keyboard model** | same, Playwright test | `J`/`K`/`Enter`/`E`/`R`. **Playwright clears fifty exceptions with zero mouse events. DoD 5** |
| **31. Accumulating verification rule** | same | An accepted field's box fills with a thin green rule that persists across navigation |
| **32. Reconciliation view** | `ledger_sense/web/app/reconciliation/` | Continuation lines against the summary, discrepancy highlighted at the exact cell, arithmetic inline. Print stylesheet |
| **33. Accessibility** | CI job | `axe-core`, zero serious/critical, at 375px and in all three densities |

## Phase 5 — Week 10: Writeback

Highest-consequence code in the product. Sandbox only; the QBO production gate is not on the critical path.

| Task | Files | Verification |
|---|---|---|
| **34. Writeback port** | `ledger_sense/writeback/interface.py`, tests | `Protocol` with `create_draft_bill`, `is_period_closed`, `reverse`. Two adapters satisfy it |
| **35. Idempotency keys** | `ledger_sense/writeback/idempotency.py`, tests | **Force-fail mid-write and retry; assert exactly one bill exists.** Key derived from document hash + contract + application number |
| **36. QBO adapter** | `ledger_sense/writeback/qbo.py`, tests | Against sandbox. Creates **unapproved** bills only — assert no code path posts an approved transaction. OAuth tokens encrypted at rest with rotation |
| **37. Closed-period refusal** | same | Reads the book-close date first; refuses with an explanation. Test asserts a refusal, not a caught exception |
| **38. Reversal** | same | One-click reversal restores prior state; audit trail records document, approver, timestamp. **DoD 6** |
| **39. CSV/IIF export adapter** | `ledger_sense/writeback/export.py`, tests | Peer adapter, not a fallback. Golden-file diff. Makes the product useful with zero API approval |

## Phase 6 — Weeks 11–12: Routing, calibration, close-out

| Task | Files | Verification |
|---|---|---|
| **40. Calibration machinery** | `ledger_sense/route/calibration.py`, tests | Isotonic fit, curve storage, reliability diagram render, threshold derivation from a stated target |
| **41. Three calibration states** | `ledger_sense/route/router.py`, tests | `uncalibrated` **and** `calibrated:synthetic` both route everything to review. Only `calibrated:field` derives a threshold. Test asserts no constant threshold exists anywhere in the module. **DoD 4** |
| **42. Reviewer corrections become labels** | `ledger_sense/route/calibration.py` | Each `HUMAN` Claim from Task 28 appends a label; assert `calibrated:field` becomes reachable at a stated n |
| **43. Inbox** | `ledger_sense/web/app/inbox/` | Drag-and-drop routed through `intake.accept_document` only. Email watcher is the first cut if week 9 or 10 slipped |
| **44. Vendor ledger** | `ledger_sense/web/app/vendors/` | Payment-detail history as a red-lined diff with date and source document. The screen is the first cut; the Task-27 data model is not |
| **45. Metrics** | `ledger_sense/web/app/metrics/` | Every figure a `Claim` opening the rail. **Straight-through rate published including misses**, caveat stating sample and period. Print stylesheet |
| **46. `docs/methodology.md` complete** | `docs/methodology.md` | Baseline method (Task 11), calibration basis (Task 40), straight-through rate with misses (Task 45). **DoD 9** |
| **47. The sixty-second demo** | `docs/demo.md`, `scripts/demo.py` | Twenty synthetic pay applications in sixty seconds; the queue catches a duplicate, a retainage miscalculation and a stored-materials double-count; straight-through rate shown honestly. **DoD 7** |
| **48. Close-out** | README, `docker compose up` on a clean machine | Non-affiliation notice in README, docs and UI. `aia-guard` and `licence-audit` green. Coverage ≥75%. **DoD 10** |

---

## Not a build task, but on the plan: DoD 11

**Half a day every week from week 5** — roughly forty hours across the phase — on deliberate
partner-finding, with the week-5 baseline study as the outreach asset. This is the only DoD item
no amount of coding satisfies and the one most likely to be unmet at week 12.

If a partner signs, spec §9 states exactly what changes. Two items move immediately: the week-5
controller session becomes their onboarding call, and **the data agreement plus the three-clause
legal review must complete before their corpus arrives** — you become a data processor on document
one, and document one can precede any invoice.

**After roughly week 10, do not re-target the pipeline at real documents.** A late partner
contributes DoD 11 and the baseline case study; re-targeting extraction in week 11 costs weeks
11–12 and finishes nothing.

---

## Cut order if weeks 9–10 slip

In sequence: **vendor ledger screen** (keep the Task-27 data model) → **inbox email watcher**
(drag-and-drop only) → **OCR tail** (route all scanned documents to review, which is the honest
behaviour anyway).

**Never cut:** the boundary test (Task 13), the no-float tests (Task 3), the writeback safety
tests (Tasks 35, 37, 38), the non-affiliation notice, `docs/methodology.md`.
