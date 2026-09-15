# Sensitivity Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce Never #1 in code — a `CLIENT_CONFIDENTIAL` document cannot reach a model lane — with the label attached at intake, judged at a local gateway seam, persisted through the database, and guarded by behavioural and structural tests.

**Architecture:** A leaf `boundary.py` defines `Sensitivity`, `Origin`, `SensitivityViolation` and the typed `Labelled[U]`. `ingest.intake.accept_document` derives the label from the document's origin (no caller can pass a label). `gateway.admit` checks the label *before* consulting any provider state, then calls a `Transport` protocol. Migration `0003_sensitivity` adds three NOT NULL columns with named CHECKs; `store.documents` is the only writer and reader, rebuilding the same `Labelled` on load.

**Tech Stack:** Python 3.12, `uv`, pytest, SQLAlchemy 2 Core, Alembic (batch mode for SQLite), `cryptography` Fernet (existing `BlobStore`), `mypy --strict`, `ruff`.

**Spec:** `docs/superpowers/specs/2026-09-15-sensitivity-gateway-design.md`

## Global Constraints

- Never #1 (`CLAUDE.md`): "No client document reaches a free-tier model. `CLIENT_CONFIDENTIAL` hard-fails at the gateway. The model lane accepts `SYNTHETIC` or `REDACTED` only, and `REDACTED` requires the client's written opt-in plus the `bok-core` redaction verification pass."
- `00` R4: "The data boundary is enforced in code, not in discipline." `00` R1: "Code asks for a lane, never for a model." `00` R3: the deterministic path must work with every provider disabled.
- 0002's migration rule: a migration is a frozen record and imports nothing from the package — values are written out as literals.
- No `bok_core` import anywhere; it is not published. No new dependencies.
- `mypy --strict` over `ledger_sense` **and** `tests` must stay green; every test function is annotated (`-> None`).
- `ruff check .` clean; line length 100.
- Every `.py` change: `ecc:python-review` before calling a task done. Migration task: `ecc:database-migrations`. Gateway/intake tasks: `security-review`.
- `uv` is not on PATH on this machine. Run it by full path: `UV=/c/Users/berek/AppData/Roaming/Python/Python313/Scripts/uv.exe` then `"$UV" run pytest ...`. Every "Run:" line below abbreviates this as `uv run`.
- Tests run from the repo root (`pytest` `pythonpath = ["."]`); `tests/` has no `__init__.py` and mypy uses `explicit_package_bases`.
- Commit messages follow the repo's `type(scope): summary` style and end with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

---

## File map

| File | Responsibility |
|---|---|
| `tests/_ast_guard.py` (create) | `calls_to(tree, name)` — alias-resolving call finder shared by every AST guard |
| `tests/test_ast_guard.py` (create) | Proves the guard helper catches Name calls, Attribute calls, aliased imports |
| `ledger_sense/boundary.py` (create) | `Sensitivity`, `Origin`, `SensitivityViolation`, `Labelled[U]` — leaf module |
| `ledger_sense/provenance.py` (modify) | Re-export `Labelled` from `boundary` instead of defining it |
| `tests/boundary/test_boundary_types.py` (create) | Enum values, frozen `Labelled`, re-export identity |
| `tests/test_provenance.py` (modify) | Existing `Labelled` test uses the typed constructor |
| `ledger_sense/ingest/intake.py` (modify) | `RedactionVerdict`, `accept_document` — the door |
| `tests/boundary/test_intake.py` (create) | Origin→label derivation, redaction rules |
| `ledger_sense/gateway.py` (create) | `Lane`, `ProviderPolicy`, `ProviderDisabled`, `Transport`, `admit` — the seam |
| `tests/boundary/test_client_confidential_cannot_reach_a_model.py` (create) | The matrix, the ordering, the spy transport |
| `tests/test_degraded_mode.py` (modify) | Deterministic packages never import the gateway |
| `migrations/versions/0003_sensitivity.py` (create) | Three columns, two named CHECKs, defaults added then dropped |
| `ledger_sense/store/retention.py` (modify) | `documents` Table gains the columns and CheckConstraints |
| `tests/store/conftest.py` (modify) | Raw seed inserts carry the three columns |
| `tests/store/test_migrations.py` (modify) | CHECK names reflected; one-row round trip; CHECK accepts/refuses |
| `ledger_sense/store/documents.py` (create) | `DocumentNotFound`, `store_document`, `load_document` |
| `tests/store/test_documents.py` (create) | Round trip per origin, tenant scoping, orphan blob, label survives reload into `admit` |
| `tests/boundary/test_structure.py` (create) | `Labelled(` sites, `documents.insert(` sites, provider-SDK imports |
| `ledger_sense/api/fixtures.py` (modify) | `SYNTHETIC = Sensitivity.SYNTHETIC.value` |
| `docs/security.md` (modify) | Name the modules and the test directory |

---

### Task 1: AST guard helper

**Files:**
- Create: `tests/_ast_guard.py`
- Test: `tests/test_ast_guard.py`

**Interfaces:**
- Produces: `calls_to(tree: ast.Module, name: str) -> list[int]` — line numbers of every call to `name`, whether spelled `name(...)`, `module.name(...)`, or through a `from x import name as alias`. Task 7 uses it; Phase 2 Task 18, Phase 3 Task 26 and Phase 5 Task 36 will too (F-32).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ast_guard.py
"""F-32: the AST guards must see through attribute calls and import aliases.
A guard that only matches `Labelled(` is evaded by `boundary.Labelled(` or
`from ledger_sense.boundary import Labelled as L`."""

import ast

from tests._ast_guard import calls_to


def _tree(source: str) -> ast.Module:
    return ast.parse(source)


def test_a_bare_name_call_is_found() -> None:
    tree = _tree("x = Labelled(payload=b'', sensitivity=s, origin=o, provenance='p')\n")
    assert calls_to(tree, "Labelled") == [1]


def test_an_attribute_call_is_found() -> None:
    tree = _tree("import ledger_sense.boundary as b\nx = b.Labelled(1, 2, 3, 4)\n")
    assert calls_to(tree, "Labelled") == [2]


def test_an_aliased_import_is_found() -> None:
    tree = _tree("from ledger_sense.boundary import Labelled as L\n\nx = L(1, 2, 3, 4)\n")
    assert calls_to(tree, "Labelled") == [3]


def test_a_different_name_is_not_found() -> None:
    tree = _tree("from ledger_sense.boundary import Origin\nx = Origin('UPLOAD')\n")
    assert calls_to(tree, "Labelled") == []


def test_a_class_definition_is_not_a_call() -> None:
    tree = _tree("class Labelled:\n    pass\n")
    assert calls_to(tree, "Labelled") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ast_guard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests._ast_guard'`

- [ ] **Step 3: Write the helper**

```python
# tests/_ast_guard.py
"""Shared AST guard helper (F-32).

The original Task 13 guard matched only `node.func.id == "Labelled"` — an
ast.Name — and missed `bok_core.boundary.Labelled(...)` (an ast.Attribute)
and every aliased import. This resolves the module's import aliases first,
then matches both call shapes. One implementation, imported by every guard.
"""

from __future__ import annotations

import ast


def calls_to(tree: ast.Module, name: str) -> list[int]:
    """Line numbers of every call to `name` in `tree`.

    Matches `name(...)`, `anything.name(...)`, and `alias(...)` where the
    module did `from x import name as alias`.
    """
    aliases = {name}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            aliases |= {alias.asname or alias.name for alias in node.names if alias.name == name}

    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            called = func.id
        elif isinstance(func, ast.Attribute):
            called = func.attr
        else:
            continue
        if called in aliases:
            lines.append(node.lineno)
    return lines
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ast_guard.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Lint, type-check, commit**

Run: `uv run ruff check tests/_ast_guard.py tests/test_ast_guard.py && uv run mypy --strict tests/_ast_guard.py tests/test_ast_guard.py`
Expected: no output from ruff; mypy `Success`

```bash
git add tests/_ast_guard.py tests/test_ast_guard.py
git commit -m "test: shared alias-resolving AST guard helper (F-32)"
```

---

### Task 2: Boundary types

**Files:**
- Create: `ledger_sense/boundary.py`
- Modify: `ledger_sense/provenance.py:1-10` (imports/`__all__`) and `:93-100` (the `Labelled` class)
- Modify: `tests/test_provenance.py:62-66`
- Test: `tests/boundary/test_boundary_types.py`

**Interfaces:**
- Produces: `Sensitivity(str, Enum)` {`PUBLIC`, `SYNTHETIC`, `REDACTED`, `CLIENT_CONFIDENTIAL`}; `Origin(str, Enum)` {`UPLOAD`, `CORPUS`, `REDACTED_UPLOAD`}; `SensitivityViolation(Exception)`; `Labelled[U]` frozen dataclass with fields `payload: U`, `sensitivity: Sensitivity`, `origin: Origin`, `provenance: str`. Every later task imports these from `ledger_sense.boundary`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/boundary/test_boundary_types.py
"""§1 of the sensitivity-gateway design: the vocabulary, as types."""

import ast
import dataclasses
import pathlib

import pytest

from ledger_sense.boundary import Labelled, Origin, Sensitivity, SensitivityViolation


def test_sensitivity_carries_the_00_r4_vocabulary_verbatim() -> None:
    assert [s.value for s in Sensitivity] == [
        "PUBLIC", "SYNTHETIC", "REDACTED", "CLIENT_CONFIDENTIAL"]


def test_origin_names_the_three_ways_bytes_enter_this_product() -> None:
    assert [o.value for o in Origin] == ["UPLOAD", "CORPUS", "REDACTED_UPLOAD"]


def test_sensitivity_violation_is_a_plain_exception() -> None:
    with pytest.raises(SensitivityViolation):
        raise SensitivityViolation("refused")


def test_labelled_is_frozen() -> None:
    doc = Labelled(payload=b"%PDF-1.4", sensitivity=Sensitivity.SYNTHETIC,
                   origin=Origin.CORPUS, provenance="corpus://seed-1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        doc.sensitivity = Sensitivity.PUBLIC  # type: ignore[misc]


def test_labelled_is_reexported_from_provenance_unchanged() -> None:
    """F-61: `from ledger_sense.provenance import Labelled` is the spelling the
    Phase 2/4 tasks use. It must be the same class, not a second one."""
    from ledger_sense.provenance import Labelled as FromProvenance
    assert FromProvenance is Labelled


def test_boundary_is_a_leaf_module() -> None:
    """Anything may import boundary.py; boundary.py imports nothing of ours."""
    source = pathlib.Path("ledger_sense/boundary.py").read_text(encoding="utf-8")
    ours = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("ledger_sense"):
            ours.append(node.lineno)
        if isinstance(node, ast.Import) and any(
                alias.name.startswith("ledger_sense") for alias in node.names):
            ours.append(node.lineno)
    assert ours == []
```

Also change the existing test at `tests/test_provenance.py:62-66` to the typed constructor:

```python
def test_labelled_wraps_a_payload_with_sensitivity_and_provenance() -> None:
    from ledger_sense.boundary import Origin, Sensitivity
    from ledger_sense.provenance import Labelled
    wrapped = Labelled(payload=b"%PDF-1.4", sensitivity=Sensitivity.SYNTHETIC,
                       origin=Origin.CORPUS, provenance="corpus")
    assert wrapped.payload == b"%PDF-1.4"
    assert wrapped.sensitivity is Sensitivity.SYNTHETIC
    assert wrapped.provenance == "corpus"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/boundary/test_boundary_types.py tests/test_provenance.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.boundary'`

- [ ] **Step 3: Write boundary.py and re-export from provenance.py**

```python
# ledger_sense/boundary.py
"""The data boundary, as types (`00` R4, `02` Never #1).

A leaf module: stdlib only, so the door (ingest/intake.py), the seam
(gateway.py) and the store (store/documents.py) can all import it without a
cycle. `bok_core.boundary` will carry the same names; when it ships, this
module is where the import is redirected, and nothing that imports from here
changes.

`Sensitivity` is `00`'s vocabulary verbatim, including PUBLIC — nothing in
Ledger Sense produces PUBLIC (no `Origin` maps to it), but the column CHECK
carries the full vocabulary so a label written by bok-core is storable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = ["Labelled", "Origin", "Sensitivity", "SensitivityViolation"]


class Sensitivity(str, Enum):
    """What a payload may be allowed to reach. `00` R4."""

    PUBLIC = "PUBLIC"
    SYNTHETIC = "SYNTHETIC"
    REDACTED = "REDACTED"
    CLIENT_CONFIDENTIAL = "CLIENT_CONFIDENTIAL"


class Origin(str, Enum):
    """Where the bytes came from — the fact the label is derived from."""

    UPLOAD = "UPLOAD"  # a client's file, as received
    CORPUS = "CORPUS"  # our synthetic generator
    REDACTED_UPLOAD = "REDACTED_UPLOAD"  # a client's file after a verified redaction pass


class SensitivityViolation(Exception):
    """A payload was offered somewhere its label forbids. Never caught and
    retried; the caller was wrong to ask."""


@dataclass(frozen=True)
class Labelled[U]:
    """A payload that cannot be separated from its label.

    Constructed in exactly two places — `ingest.intake.accept_document`
    (where the file is opened) and `store.documents.load_document` (where a
    stored label is rebuilt) — and a test enforces that.
    """

    payload: U
    sensitivity: Sensitivity
    origin: Origin
    provenance: str
```

In `ledger_sense/provenance.py`: delete the `Labelled` class (lines 93–100) and the `U = TypeVar("U")` line above it (nothing else in the file uses `U`), remove `TypeVar` from the `typing` import, and add after `from ledger_sense.rules.provenance import RuleSource`:

```python
from ledger_sense.boundary import Labelled
```

`"Labelled"` stays in `__all__`. Ruff will flag the import as unused-but-exported only if `__all__` loses it — keep it there.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/boundary/test_boundary_types.py tests/test_provenance.py -v`
Expected: PASS, all tests in both files

- [ ] **Step 5: Full suite, lint, types, commit**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy --strict ledger_sense tests`
Expected: suite green, ruff silent, mypy `Success`

```bash
git add ledger_sense/boundary.py ledger_sense/provenance.py tests/boundary/test_boundary_types.py tests/test_provenance.py
git commit -m "feat(boundary): Sensitivity, Origin and the typed Labelled (00 R4 vocabulary)"
```

---

### Task 3: The intake door

**Files:**
- Modify: `ledger_sense/ingest/intake.py` (whole file — keep the existing `intake()` function verbatim)
- Test: `tests/boundary/test_intake.py`

**Interfaces:**
- Consumes: `Labelled`, `Origin`, `Sensitivity`, `SensitivityViolation` from `ledger_sense.boundary`.
- Produces: `RedactionVerdict(verified: bool, verifier: str, detail: str = "")` frozen dataclass; `accept_document(payload: bytes, *, origin: Origin, provenance: str, redaction: RedactionVerdict | None = None) -> Labelled[bytes]`. Task 6's tests and the future upload route call this.

- [ ] **Step 1: Write the failing tests**

```python
# tests/boundary/test_intake.py
"""§2: the label attaches where the file is opened, derived from where the
bytes came from. There is no sensitivity parameter to get wrong."""

import inspect

import pytest

from ledger_sense.boundary import Origin, Sensitivity, SensitivityViolation
from ledger_sense.ingest.intake import RedactionVerdict, accept_document, intake

PAYLOAD = b"%PDF-1.4 invoice"

EXPECTED_LABEL = {
    Origin.UPLOAD: Sensitivity.CLIENT_CONFIDENTIAL,
    Origin.CORPUS: Sensitivity.SYNTHETIC,
    Origin.REDACTED_UPLOAD: Sensitivity.REDACTED,
}


def test_every_origin_has_a_decided_label() -> None:
    assert set(EXPECTED_LABEL) == set(Origin)


@pytest.mark.parametrize("origin", [Origin.UPLOAD, Origin.CORPUS])
def test_the_label_is_derived_from_the_origin(origin: Origin) -> None:
    doc = accept_document(PAYLOAD, origin=origin, provenance="test")
    assert doc.sensitivity is EXPECTED_LABEL[origin]
    assert doc.origin is origin
    assert doc.payload == PAYLOAD
    assert doc.provenance == "test"


def test_an_upload_is_client_confidential_with_no_way_to_say_otherwise() -> None:
    """The 2am case: there is no parameter through which a real upload can be
    called SYNTHETIC."""
    assert "sensitivity" not in inspect.signature(accept_document).parameters
    doc = accept_document(PAYLOAD, origin=Origin.UPLOAD, provenance="upload:test")
    assert doc.sensitivity is Sensitivity.CLIENT_CONFIDENTIAL


def test_nothing_produces_public() -> None:
    assert Sensitivity.PUBLIC not in EXPECTED_LABEL.values()


def test_redacted_without_a_verdict_is_refused() -> None:
    with pytest.raises(SensitivityViolation, match="verif"):
        accept_document(PAYLOAD, origin=Origin.REDACTED_UPLOAD, provenance="test")


def test_redacted_with_a_failed_verdict_is_refused() -> None:
    verdict = RedactionVerdict(verified=False, verifier="test", detail="3 identifiers remain")
    with pytest.raises(SensitivityViolation, match="verif"):
        accept_document(PAYLOAD, origin=Origin.REDACTED_UPLOAD, provenance="test",
                        redaction=verdict)


def test_redacted_with_a_passing_verdict_is_labelled_redacted() -> None:
    verdict = RedactionVerdict(verified=True, verifier="test")
    doc = accept_document(PAYLOAD, origin=Origin.REDACTED_UPLOAD, provenance="test",
                          redaction=verdict)
    assert doc.sensitivity is Sensitivity.REDACTED
    assert doc.origin is Origin.REDACTED_UPLOAD


@pytest.mark.parametrize("origin", [Origin.UPLOAD, Origin.CORPUS])
def test_a_verdict_does_not_lift_a_non_redacted_origin(origin: Origin) -> None:
    """A passing verdict on an UPLOAD does not make it REDACTED; the origin
    would have to be REDACTED_UPLOAD. Refuse, so the mistake is loud."""
    verdict = RedactionVerdict(verified=True, verifier="test")
    with pytest.raises(SensitivityViolation, match="origin"):
        accept_document(PAYLOAD, origin=origin, provenance="test", redaction=verdict)


def test_the_parse_pass_through_is_untouched() -> None:
    """Rule 2: intake() is still the only caller of parse_within_limits."""
    assert callable(intake)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/boundary/test_intake.py -v`
Expected: FAIL — `ImportError: cannot import name 'RedactionVerdict' from 'ledger_sense.ingest.intake'`

- [ ] **Step 3: Write the door**

Replace `ledger_sense/ingest/intake.py` with:

```python
"""Document intake — the door (§2) and the only caller of `parse_within_limits` (Rule 2).

The label attaches where the file is OPENED, and it is derived from where the
bytes came from. `accept_document` has no sensitivity parameter: a client
upload cannot be called SYNTHETIC because there is nothing to type it into.
If the label attached late, the leak would happen early.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ledger_sense.boundary import Labelled, Origin, Sensitivity, SensitivityViolation
from ledger_sense.ingest.limits import Limits, ParseResult, parse_within_limits

__all__ = ["RedactionVerdict", "accept_document", "intake"]


@dataclass(frozen=True)
class RedactionVerdict:
    """The outcome of a redaction verification pass.

    No verifier ships with Ledger Sense: the brief accepts bok-core's pass and
    nothing else. Until it exists, every real upload is CLIENT_CONFIDENTIAL —
    the honest state, and the product works that way (more routed to review).
    """

    verified: bool
    verifier: str
    detail: str = ""


# Origin → label. Exhaustive over Origin; tests/boundary/test_intake.py checks.
# PUBLIC is deliberately absent: nothing in this product produces it.
_LABEL_OF: Final[dict[Origin, Sensitivity]] = {
    Origin.UPLOAD: Sensitivity.CLIENT_CONFIDENTIAL,
    Origin.CORPUS: Sensitivity.SYNTHETIC,
    Origin.REDACTED_UPLOAD: Sensitivity.REDACTED,
}


def accept_document(
    payload: bytes,
    *,
    origin: Origin,
    provenance: str,
    redaction: RedactionVerdict | None = None,
) -> Labelled[bytes]:
    """Label `payload` from its `origin`. The single entry point for every
    externally-supplied document; one of the two places a Labelled is built."""
    sensitivity = _LABEL_OF[origin]
    if sensitivity is Sensitivity.REDACTED:
        if redaction is None:
            raise SensitivityViolation(
                "REDACTED_UPLOAD requires a passing redaction verification pass; "
                "none was supplied")
        if not redaction.verified:
            raise SensitivityViolation(
                "REDACTED_UPLOAD requires a passing redaction verification pass; "
                f"{redaction.verifier} did not verify it: {redaction.detail or 'no detail'}")
    elif redaction is not None:
        raise SensitivityViolation(
            f"a redaction verdict does not change the label of origin {origin.value}; "
            "a verified redaction enters as REDACTED_UPLOAD")
    return Labelled(payload=payload, sensitivity=sensitivity, origin=origin,
                    provenance=provenance)


def intake(payload: bytes, limits: Limits | None = None) -> ParseResult:
    """Parse an uploaded document within resource limits."""
    return parse_within_limits(payload, limits if limits is not None else Limits())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/boundary/test_intake.py tests/ingest -v`
Expected: PASS — all of `test_intake.py` plus the existing ingest tests unchanged

- [ ] **Step 5: Lint, types, security review, commit**

Run: `uv run ruff check . && uv run mypy --strict ledger_sense tests`
Expected: clean. Then run the `security-review` skill over `ledger_sense/ingest/intake.py` and address anything it raises before committing.

```bash
git add ledger_sense/ingest/intake.py tests/boundary/test_intake.py
git commit -m "feat(ingest): accept_document derives the label from the origin (Never #1 door)"
```

---

### Task 4: The gateway seam

**Files:**
- Create: `ledger_sense/gateway.py`
- Modify: `tests/test_degraded_mode.py` (append one test after `test_gateway_call_is_trapped_when_bok_core_exists`)
- Test: `tests/boundary/test_client_confidential_cannot_reach_a_model.py`

**Interfaces:**
- Consumes: `Labelled`, `Sensitivity`, `SensitivityViolation` from `ledger_sense.boundary`.
- Produces: `Lane(str, Enum)` {`LOCAL_FALLBACK = "local-fallback"`, `EXTRACT_VISION = "extract-vision"`} with property `is_model: bool`; `ProviderPolicy(enabled: bool, redacted_opt_in: bool)` frozen dataclass; `ProviderDisabled(Exception)`; `Transport` Protocol with `send(self, lane: Lane, payload: bytes) -> bytes`; `admit(lane: Lane, doc: Labelled[bytes], *, policy: ProviderPolicy, transport: Transport) -> bytes`. Task 6's persistence test calls `admit`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/boundary/test_client_confidential_cannot_reach_a_model.py
"""Never #1, DoD 8, re-tested against the local seam (F-60: the feature is
cuttable, this file is not).

The spy doubles the TRANSPORT only. `admit` is always the real one — a mocked
boundary stops nothing at 2am in week 11.
"""

import pytest

from ledger_sense.boundary import Labelled, Origin, Sensitivity, SensitivityViolation
from ledger_sense.gateway import Lane, ProviderDisabled, ProviderPolicy, admit

PAYLOAD = b"%PDF-1.4 invoice"

# The allow-matrix for the model lane, written out. A fifth Sensitivity value
# fails test_the_matrix_names_every_sensitivity until someone decides its row.
#   PUBLIC: 00 R4 admits it; 02 Never #1 says "SYNTHETIC or REDACTED only".
#   The brief wins for this product.
ADMITTED_TO_THE_MODEL_LANE = {
    Sensitivity.PUBLIC: False,
    Sensitivity.SYNTHETIC: True,
    Sensitivity.REDACTED: True,  # with opt-in; the opt-in tests cover the flip
    Sensitivity.CLIENT_CONFIDENTIAL: False,
}

ENABLED = ProviderPolicy(enabled=True, redacted_opt_in=True)
DISABLED = ProviderPolicy(enabled=False, redacted_opt_in=True)


class SpyTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[Lane, bytes]] = []

    def send(self, lane: Lane, payload: bytes) -> bytes:
        self.sent.append((lane, payload))
        return b"response"


def _doc(sensitivity: Sensitivity) -> Labelled[bytes]:
    # Tests may construct Labelled directly; the structural guard scans
    # ledger_sense/ only. The gateway keys on sensitivity, not origin.
    return Labelled(payload=PAYLOAD, sensitivity=sensitivity, origin=Origin.UPLOAD,
                    provenance="test")


def test_the_matrix_names_every_sensitivity() -> None:
    assert set(ADMITTED_TO_THE_MODEL_LANE) == set(Sensitivity)


@pytest.mark.parametrize("sensitivity", list(Sensitivity))
def test_the_model_lane_follows_the_matrix(sensitivity: Sensitivity) -> None:
    spy = SpyTransport()
    if ADMITTED_TO_THE_MODEL_LANE[sensitivity]:
        assert admit(Lane.EXTRACT_VISION, _doc(sensitivity), policy=ENABLED,
                     transport=spy) == b"response"
        assert spy.sent == [(Lane.EXTRACT_VISION, PAYLOAD)]
    else:
        with pytest.raises(SensitivityViolation):
            admit(Lane.EXTRACT_VISION, _doc(sensitivity), policy=ENABLED, transport=spy)
        assert spy.sent == []


@pytest.mark.parametrize("policy", [ENABLED, DISABLED], ids=["enabled", "disabled"])
def test_client_confidential_is_refused_before_the_provider_is_consulted(
    policy: ProviderPolicy,
) -> None:
    """The ordering test. With the provider disabled the refusal must still be
    SensitivityViolation, never ProviderDisabled: if the provider were checked
    first, enabling it would be the only thing between a client's invoice and
    a free tier."""
    spy = SpyTransport()
    with pytest.raises(SensitivityViolation):
        admit(Lane.EXTRACT_VISION, _doc(Sensitivity.CLIENT_CONFIDENTIAL), policy=policy,
              transport=spy)
    assert spy.sent == []


def test_synthetic_with_the_provider_disabled_raises_provider_disabled() -> None:
    """The other half of the ordering: the provider check exists and is second."""
    spy = SpyTransport()
    with pytest.raises(ProviderDisabled):
        admit(Lane.EXTRACT_VISION, _doc(Sensitivity.SYNTHETIC), policy=DISABLED,
              transport=spy)
    assert spy.sent == []


def test_redacted_without_opt_in_is_refused() -> None:
    spy = SpyTransport()
    no_opt_in = ProviderPolicy(enabled=True, redacted_opt_in=False)
    with pytest.raises(SensitivityViolation, match="opt-in"):
        admit(Lane.EXTRACT_VISION, _doc(Sensitivity.REDACTED), policy=no_opt_in,
              transport=spy)
    assert spy.sent == []


def test_redacted_with_opt_in_is_sent() -> None:
    spy = SpyTransport()
    admit(Lane.EXTRACT_VISION, _doc(Sensitivity.REDACTED), policy=ENABLED, transport=spy)
    assert spy.sent == [(Lane.EXTRACT_VISION, PAYLOAD)]


@pytest.mark.parametrize("sensitivity", list(Sensitivity))
def test_the_local_lane_admits_everything_with_every_provider_disabled(
    sensitivity: Sensitivity,
) -> None:
    """00 R3 / degraded mode: the deterministic path is not a provider."""
    spy = SpyTransport()
    admit(Lane.LOCAL_FALLBACK, _doc(sensitivity), policy=DISABLED, transport=spy)
    assert spy.sent == [(Lane.LOCAL_FALLBACK, PAYLOAD)]


def test_only_the_local_fallback_lane_is_not_a_model_lane() -> None:
    assert [lane for lane in Lane if not lane.is_model] == [Lane.LOCAL_FALLBACK]
```

Append to `tests/test_degraded_mode.py`, after `test_gateway_call_is_trapped_when_bok_core_exists`:

```python
def test_the_deterministic_packages_never_import_the_gateway() -> None:
    """The local trap for the local seam: if extract/rules/ingest/domain/corpus/
    baseline imported ledger_sense.gateway, the 70% deterministic floor would
    depend on a provider. Static, so an `import ... as` cannot dodge it."""
    import ast
    import pathlib

    deterministic = ("extract", "rules", "ingest", "domain", "corpus", "baseline")
    offenders = []
    for package in deterministic:
        for path in (pathlib.Path("ledger_sense") / package).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                elif isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                else:
                    continue
                if any(name.startswith("ledger_sense.gateway") for name in names):
                    offenders.append(f"{path}:{node.lineno}")
    assert offenders == [], f"deterministic code imports the gateway: {offenders}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/boundary/test_client_confidential_cannot_reach_a_model.py tests/test_degraded_mode.py -v`
Expected: the boundary file FAILS with `ModuleNotFoundError: No module named 'ledger_sense.gateway'`; the degraded-mode file passes (nothing imports a module that does not exist yet).

- [ ] **Step 3: Write the seam**

```python
# ledger_sense/gateway.py
"""The model-lane seam (§3): where a label is judged, before anything else.

`bok_core.gateway` will replace the transport behind this module; `admit` and
its order stay. The order is the point:

    1. local lane → nothing to judge, the deterministic path takes everything
    2. label check → SensitivityViolation           (before any provider state)
    3. provider check → ProviderDisabled
    4. transport.send

Step 2 runs before step 3 so that no configuration change — enabling a
provider, adding a key, flipping BOK_PROVIDERS_DISABLED — can open the door.
tests/boundary/test_client_confidential_cannot_reach_a_model.py proves the
order, not just the outcome.

Deliberately not implemented: `00` R4(b), CLIENT_CONFIDENTIAL to a provider
marked trains_on_data=false on a client-paid key. `02` Never #1 forbids it in
v1, so ProviderPolicy carries no such fields. Adding them is the day that
decision is reversed, not before.

No Transport implementation ships here. The first one arrives with
models.yaml and a working free-tier account (`00` B1, `00` R1: code asks for a
lane, never a model).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final, Protocol

from ledger_sense.boundary import Labelled, Sensitivity, SensitivityViolation

__all__ = ["Lane", "ProviderDisabled", "ProviderPolicy", "Transport", "admit"]


class Lane(str, Enum):
    """`00` R1 lane names. local-fallback must exist for every lane."""

    LOCAL_FALLBACK = "local-fallback"
    EXTRACT_VISION = "extract-vision"

    @property
    def is_model(self) -> bool:
        return self is not Lane.LOCAL_FALLBACK


@dataclass(frozen=True)
class ProviderPolicy:
    """What the caller knows about the provider and the tenant.

    `enabled` is false when BOK_PROVIDERS_DISABLED is set, when no key is
    configured, or when no models.yaml names a provider for the lane — the
    caller decides; this module only orders the checks. `redacted_opt_in` is
    the client's written opt-in, a per-tenant fact the caller supplies.
    """

    enabled: bool
    redacted_opt_in: bool


class ProviderDisabled(Exception):
    """The label allowed it; the provider is not there to send it to."""


class Transport(Protocol):
    def send(self, lane: Lane, payload: bytes) -> bytes: ...


# Never #1: the model lane accepts SYNTHETIC or REDACTED only.
_MODEL_LANE_ADMITS: Final[frozenset[Sensitivity]] = frozenset(
    {Sensitivity.SYNTHETIC, Sensitivity.REDACTED})


def _check_label(lane: Lane, doc: Labelled[bytes], policy: ProviderPolicy) -> None:
    if doc.sensitivity not in _MODEL_LANE_ADMITS:
        raise SensitivityViolation(
            f"{doc.sensitivity.value} may not reach the {lane.value} lane "
            f"(02 Never #1: SYNTHETIC or REDACTED only); origin {doc.origin.value}, "
            f"provenance {doc.provenance}")
    if doc.sensitivity is Sensitivity.REDACTED and not policy.redacted_opt_in:
        raise SensitivityViolation(
            f"REDACTED may reach the {lane.value} lane only with the client's written "
            "opt-in, and this tenant has not opted in")


def admit(
    lane: Lane,
    doc: Labelled[bytes],
    *,
    policy: ProviderPolicy,
    transport: Transport,
) -> bytes:
    """Send `doc` down `lane` if — and only after — its label allows it."""
    if lane.is_model:
        _check_label(lane, doc, policy)
        if not policy.enabled:
            raise ProviderDisabled(f"no provider is enabled for the {lane.value} lane")
    return transport.send(lane, doc.payload)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/boundary tests/test_degraded_mode.py -v`
Expected: PASS — 15 tests in the new file (1 + 4 + 2 + 1 + 1 + 1 + 4 + 1), plus every degraded-mode test including the new import guard

- [ ] **Step 5: Lint, types, security review, commit**

Run: `uv run ruff check . && uv run mypy --strict ledger_sense tests`
Expected: clean. Run the `security-review` skill over `ledger_sense/gateway.py`; address findings.

```bash
git add ledger_sense/gateway.py tests/boundary/test_client_confidential_cannot_reach_a_model.py tests/test_degraded_mode.py
git commit -m "feat(gateway): local seam judges the label before any provider state (Never #1)"
```

---

### Task 5: Migration 0003 and the metadata

**Files:**
- Create: `migrations/versions/0003_sensitivity.py`
- Modify: `ledger_sense/store/retention.py:43-51` (the `documents` Table)
- Modify: `tests/store/conftest.py:68-77` and `:104-109` (the two raw inserts)
- Modify: `tests/store/test_migrations.py` (extend `_assert_migration_builds_the_schema_the_code_queries`; add three tests)

**Interfaces:**
- Consumes: `Sensitivity`, `Origin` from `ledger_sense.boundary` (metadata and tests only — the migration imports nothing from the package).
- Produces: columns `documents.sensitivity`, `documents.origin`, `documents.provenance` (all `TEXT NOT NULL`), CHECK constraints `ck_documents_sensitivity`, `ck_documents_origin`. Task 6 writes and reads them.

- [ ] **Step 1: Update the two seed inserts so the existing suite keeps passing once the columns are NOT NULL**

In `tests/store/conftest.py`, add to the imports:

```python
from ledger_sense.boundary import Origin, Sensitivity
```

Change `add_document` (line 68–77) to:

```python
    def add_document(self, document_id: str, *, age_days: int,
                     blob_id: str | None = None) -> str:
        """Seed one more document, for tests that need a blob attached.

        A raw insert, not store_document: these rows carry blob_id=None (a
        purged or never-landed blob), which the one writer cannot produce.
        The label columns are explicit because the schema refuses a row
        without them — that refusal is the point of 0003.
        """
        with store_session.app_engine().begin() as conn:
            conn.execute(documents.insert().values(
                id=document_id,
                tenant_id=self.tenant_id,
                blob_id=blob_id,
                received_at=datetime.now(UTC) - timedelta(days=age_days),
                sensitivity=Sensitivity.SYNTHETIC.value,
                origin=Origin.CORPUS.value,
                provenance="tests/store/conftest.py",
            ))
        return document_id
```

And the seeded insert (line 104–109) to:

```python
            conn.execute(documents.insert().values(
                id=EXPIRED_DOCUMENT_ID,
                tenant_id=tenant_id,
                blob_id=None,
                received_at=datetime.now(UTC) - timedelta(days=DOCUMENT_AGE_DAYS),
                sensitivity=Sensitivity.SYNTHETIC.value,
                origin=Origin.CORPUS.value,
                provenance="tests/store/conftest.py",
            ))
```

- [ ] **Step 2: Write the failing tests**

In `tests/store/test_migrations.py`, inside `_assert_migration_builds_the_schema_the_code_queries`, after the `for name, table in metadata.tables.items():` loop and before `command.downgrade(config, "base")`, add:

```python
        # 0003's CHECKs are named so SQLite batch recreate (which Phase 2 and 4
        # will run on this table again) keeps them. Reflection sees the names.
        checks = {c["name"] for c in inspector.get_check_constraints("documents")}
        assert {"ck_documents_sensitivity", "ck_documents_origin"} <= checks, checks
```

Add these imports at the top of the file (keep the existing ones):

```python
from datetime import UTC, datetime

from ledger_sense.auth.models import tenants
from ledger_sense.boundary import Origin, Sensitivity
from tests.store.conftest import SeededDocuments
```

And append these tests:

```python
_PRE_0003_ROW = {
    "id": "doc-pre-0003",
    "tenant_id": "tenant-pre-0003",
    # SQLAlchemy's SQLite DateTime storage format, so the Table can read it back.
    "received_at": "2026-09-14 00:00:00.000000",
}


def _seed_a_pre_0003_document(conn: sa.Connection) -> None:
    conn.execute(tenants.insert().values(
        id=_PRE_0003_ROW["tenant_id"], name="pre-0003",
        created_at=datetime.now(UTC), retention_days=365))
    conn.execute(sa.text(
        "INSERT INTO documents (id, tenant_id, blob_id, received_at) "
        "VALUES (:id, :tenant_id, NULL, :received_at)"), _PRE_0003_ROW)
    conn.execute(sa.text(
        "INSERT INTO claims (id, tenant_id, document_id) "
        "VALUES ('claim-pre-0003', :tenant_id, :id)"), _PRE_0003_ROW)


def test_0003_round_trips_on_a_populated_database(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A migration that fails on a populated table fails in front of a client.
    A row that predates the label is backfilled as confidential-from-an-upload
    — the only honest guess — and survives downgrade and re-upgrade. The claim
    row rides along: SQLite batch recreate on a table that claims references
    must not lose either side."""
    database = (tmp_path / "populated.db").as_posix()
    monkeypatch.setenv("LEDGER_SENSE_DB_URL", f"sqlite:///{database}")
    store_session.reset_app_engine()
    try:
        config = _alembic_config()
        command.upgrade(config, "0002_retention")
        with store_session.app_engine().begin() as conn:
            _seed_a_pre_0003_document(conn)

        command.upgrade(config, "head")
        with store_session.app_engine().connect() as conn:
            row = conn.execute(sa.text(
                "SELECT sensitivity, origin, provenance FROM documents WHERE id = :id"),
                {"id": _PRE_0003_ROW["id"]}).one()
        assert tuple(row) == ("CLIENT_CONFIDENTIAL", "UPLOAD", "unknown:pre-0003")

        command.downgrade(config, "0002_retention")
        columns = {c["name"] for c in sa.inspect(store_session.app_engine())
                   .get_columns("documents")}
        assert columns == {"id", "tenant_id", "blob_id", "received_at"}
        with store_session.app_engine().connect() as conn:
            assert conn.execute(sa.text("SELECT COUNT(*) FROM documents")).scalar() == 1
            assert conn.execute(sa.text("SELECT COUNT(*) FROM claims")).scalar() == 1

        command.upgrade(config, "head")
        with store_session.app_engine().connect() as conn:
            assert conn.execute(sa.text("SELECT COUNT(*) FROM documents")).scalar() == 1
    finally:
        store_session.reset_app_engine()


def _raw_insert(conn: sa.Connection, tenant_id: str, *, document_id: str,
                sensitivity: str, origin: str) -> None:
    conn.execute(sa.text(
        "INSERT INTO documents (id, tenant_id, blob_id, received_at, sensitivity, origin, "
        "provenance) VALUES (:id, :tenant_id, NULL, :received_at, :sensitivity, :origin, "
        "'tests/store/test_migrations.py')"),
        {"id": document_id, "tenant_id": tenant_id, "received_at": _PRE_0003_ROW["received_at"],
         "sensitivity": sensitivity, "origin": origin})


def test_the_check_refuses_a_label_outside_the_vocabulary(
    seeded_documents: SeededDocuments,
) -> None:
    with pytest.raises(sa.exc.IntegrityError), store_session.app_engine().begin() as conn:
        _raw_insert(conn, seeded_documents.tenant_id, document_id="doc-bad-label",
                    sensitivity="FREE_TIER_OK", origin="UPLOAD")
    with pytest.raises(sa.exc.IntegrityError), store_session.app_engine().begin() as conn:
        _raw_insert(conn, seeded_documents.tenant_id, document_id="doc-bad-origin",
                    sensitivity="SYNTHETIC", origin="EMAIL")


@pytest.mark.parametrize("sensitivity", list(Sensitivity))
@pytest.mark.parametrize("origin", list(Origin))
def test_the_check_accepts_every_enum_value(
    seeded_documents: SeededDocuments, sensitivity: Sensitivity, origin: Origin
) -> None:
    """The enum and the CHECK agree in both directions: the migration wrote the
    values out as literals, so this is the test that they were copied right."""
    with store_session.app_engine().begin() as conn:
        _raw_insert(conn, seeded_documents.tenant_id,
                    document_id=f"doc-{sensitivity.value}-{origin.value}",
                    sensitivity=sensitivity.value, origin=origin.value)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/store -v`
Expected: FAIL. `test_0003_round_trips_on_a_populated_database` fails at `SELECT sensitivity ...` with `no such column`; the two CHECK tests fail with `no such column: sensitivity`; the existing retention tests fail because conftest now inserts columns the schema lacks. All expected until the migration and metadata land.

- [ ] **Step 4: Write the migration**

```python
# migrations/versions/0003_sensitivity.py
"""sensitivity: documents.sensitivity, origin, provenance

Revision ID: 0003_sensitivity
Revises: 0002_retention
Create Date: 2026-09-15

`02` Never #1 / `00` R4: a document row cannot exist without its label, and the
label must survive a restart as the same value it came in as. Three columns,
all NOT NULL, two of them CHECKed against the vocabulary.

Four decisions:

1. **Values are literals, not imports.** Same rule as 0002: a migration is a
   frozen record and must emit the DDL it emitted the day it ran, even after
   the enum moves or grows. If a fifth Sensitivity value ever exists, that is
   migration 0004, not an edit here.

2. **Server defaults, then dropped.** ALTER TABLE ADD COLUMN NOT NULL needs a
   default for rows already there. Every real deployment has an empty
   documents table today — but a migration that fails on a populated table is
   a migration that fails in front of a client. A row that predates the label
   is backfilled CLIENT_CONFIDENTIAL / UPLOAD / 'unknown:pre-0003': treating
   it as a client's upload is the only honest guess. The defaults are then
   dropped so that no future raw INSERT can omit the label and get one
   invented for it — the schema refuses, which is the point.

3. **Two batch blocks, not one.** On SQLite, batch_alter_table reflects the
   table, applies every op in the block, recreates the table once and copies
   the rows in. A single block that adds the columns AND drops their defaults
   would copy existing rows into a NOT NULL column with nothing to fill it.
   Block one adds with defaults (the copy fills them); block two drops the
   defaults (a second recreate, on a table whose rows now carry values). On
   Postgres both blocks are plain ALTERs and the split costs nothing.

4. **The CHECKs are named.** SQLite batch recreate silently drops UNNAMED check
   constraints, and 0002 promises Phase 2 and Phase 4 will ALTER this table
   again. Named, they are reflected and carried across every future recreate.
   store/retention.py mirrors the names on the Table so metadata and schema
   agree.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_sensitivity"
down_revision: str | None = "0002_retention"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors ledger_sense.boundary.Sensitivity / Origin. Written out on purpose.
_SENSITIVITIES = ("PUBLIC", "SYNTHETIC", "REDACTED", "CLIENT_CONFIDENTIAL")
_ORIGINS = ("UPLOAD", "CORPUS", "REDACTED_UPLOAD")


def _one_of(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("sensitivity", sa.Text(), nullable=False,
                                   server_default="CLIENT_CONFIDENTIAL"))
        batch.add_column(sa.Column("origin", sa.Text(), nullable=False,
                                   server_default="UPLOAD"))
        batch.add_column(sa.Column("provenance", sa.Text(), nullable=False,
                                   server_default="unknown:pre-0003"))
        batch.create_check_constraint("ck_documents_sensitivity",
                                      _one_of("sensitivity", _SENSITIVITIES))
        batch.create_check_constraint("ck_documents_origin", _one_of("origin", _ORIGINS))

    with op.batch_alter_table("documents") as batch:
        batch.alter_column("sensitivity", server_default=None)
        batch.alter_column("origin", server_default=None)
        batch.alter_column("provenance", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_constraint("ck_documents_origin", type_="check")
        batch.drop_constraint("ck_documents_sensitivity", type_="check")
        batch.drop_column("provenance")
        batch.drop_column("origin")
        batch.drop_column("sensitivity")
```

- [ ] **Step 5: Grow the Table in retention.py**

Add the import near the top of `ledger_sense/store/retention.py`:

```python
from ledger_sense.boundary import Origin, Sensitivity
```

Replace the `documents` Table (lines 43–51) with:

```python
documents = sa.Table(
    "documents",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("tenant_id", sa.Text, sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("blob_id", sa.Text, nullable=True),
    sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    # 0003: the label, persisted. NOT NULL with no default — a row without a
    # label is refused by the schema, and store/documents.py is the one writer.
    sa.Column("sensitivity", sa.Text, nullable=False),
    sa.Column("origin", sa.Text, nullable=False),
    sa.Column("provenance", sa.Text, nullable=False),
    # Named to match 0003 exactly: SQLite batch recreate keeps named CHECKs.
    sa.CheckConstraint(
        "sensitivity IN (" + ", ".join(f"'{s.value}'" for s in Sensitivity) + ")",
        name="ck_documents_sensitivity"),
    sa.CheckConstraint(
        "origin IN (" + ", ".join(f"'{o.value}'" for o in Origin) + ")",
        name="ck_documents_origin"),
    sa.Index("ix_documents_tenant_id_id", "tenant_id", "id"),
)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/store tests/api -v`
Expected: PASS — the migration tests including the populated round trip, the CHECK tests (12 parametrized accepts, 1 refusal), and every retention and API test that runs `alembic upgrade head`

- [ ] **Step 7: Lint, types, migration review, commit**

Run: `uv run ruff check . && uv run mypy --strict ledger_sense tests`
Expected: clean. Run the `ecc:database-migrations` skill over the new migration and address findings.

```bash
git add migrations/versions/0003_sensitivity.py ledger_sense/store/retention.py tests/store/conftest.py tests/store/test_migrations.py
git commit -m "feat(store): 0003 persists the label — sensitivity, origin, provenance with named CHECKs"
```

---

### Task 6: One writer, one reader

**Files:**
- Create: `ledger_sense/store/documents.py`
- Test: `tests/store/test_documents.py`

**Interfaces:**
- Consumes: `documents` Table from `ledger_sense.store.retention`; `BlobStore.put(tenant_id, payload) -> str`, `.get(tenant_id, blob_id) -> bytes | None`, `.delete(tenant_id, blob_id) -> bool`, `.raw(blob_id) -> bytes | None` from `ledger_sense.store.blobs`; `Labelled`, `Sensitivity`, `Origin`, `SensitivityViolation` from `ledger_sense.boundary`; `accept_document`, `RedactionVerdict` (Task 3); `admit`, `Lane`, `ProviderPolicy` (Task 4); `seeded_documents` fixture and `SeededDocuments` from `tests/store/conftest.py`.
- Produces: `DocumentNotFound(LookupError)`; `store_document(conn: Connection, *, tenant_id: str, doc: Labelled[bytes], blobs: BlobStore, received_at: datetime) -> str`; `load_document(conn: Connection, *, tenant_id: str, document_id: str, blobs: BlobStore) -> Labelled[bytes]`. The Phase 1 upload route will call `store_document` and nothing else.

- [ ] **Step 1: Write the failing tests**

```python
# tests/store/test_documents.py
"""§4: the label survives a restart. A stored document reaches the gateway as
the same Labelled it came in as, never re-labelled by hand."""

import pathlib
from datetime import UTC, datetime
from typing import cast

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Connection

from ledger_sense.boundary import Labelled, Origin, Sensitivity, SensitivityViolation
from ledger_sense.gateway import Lane, ProviderPolicy, admit
from ledger_sense.ingest.intake import RedactionVerdict, accept_document
from ledger_sense.store import session as store_session
from ledger_sense.store.blobs import BlobStore
from ledger_sense.store.documents import DocumentNotFound, load_document, store_document
from tests.store.conftest import SeededDocuments

RECEIVED_AT = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)


@pytest.fixture
def blobs(tmp_path: pathlib.Path) -> BlobStore:
    return BlobStore(root=str(tmp_path / "blobs"), kek=Fernet.generate_key())


def _labelled(origin: Origin) -> Labelled[bytes]:
    verdict = None
    if origin is Origin.REDACTED_UPLOAD:
        verdict = RedactionVerdict(verified=True, verifier="test")
    return accept_document(b"%PDF-1.4 " + origin.value.encode(), origin=origin,
                           provenance=f"test:{origin.value}", redaction=verdict)


@pytest.mark.parametrize("origin", list(Origin))
def test_a_document_round_trips_with_its_label(
    seeded_documents: SeededDocuments, blobs: BlobStore, origin: Origin
) -> None:
    doc = _labelled(origin)
    with store_session.app_engine().begin() as conn:
        document_id = store_document(conn, tenant_id=seeded_documents.tenant_id, doc=doc,
                                     blobs=blobs, received_at=RECEIVED_AT)
    with store_session.app_engine().connect() as conn:
        loaded = load_document(conn, tenant_id=seeded_documents.tenant_id,
                               document_id=document_id, blobs=blobs)
    assert loaded == doc


def test_the_row_carries_the_label_and_the_blob_is_not_plaintext(
    seeded_documents: SeededDocuments, blobs: BlobStore
) -> None:
    doc = _labelled(Origin.UPLOAD)
    with store_session.app_engine().begin() as conn:
        document_id = store_document(conn, tenant_id=seeded_documents.tenant_id, doc=doc,
                                     blobs=blobs, received_at=RECEIVED_AT)
    (row,) = seeded_documents.documents_for(document_id)
    assert (row.sensitivity, row.origin, row.provenance) == (
        "CLIENT_CONFIDENTIAL", "UPLOAD", "test:UPLOAD")
    assert row.blob_id is not None
    ciphertext = blobs.raw(row.blob_id)
    assert ciphertext is not None
    assert doc.payload not in ciphertext


def test_another_tenants_document_is_not_found(
    seeded_documents: SeededDocuments, blobs: BlobStore
) -> None:
    """404-not-403: the row exists; for this tenant it does not."""
    with store_session.app_engine().begin() as conn:
        document_id = store_document(conn, tenant_id=seeded_documents.tenant_id,
                                     doc=_labelled(Origin.CORPUS), blobs=blobs,
                                     received_at=RECEIVED_AT)
    with store_session.app_engine().connect() as conn, pytest.raises(DocumentNotFound):
        load_document(conn, tenant_id="someone-else", document_id=document_id, blobs=blobs)


def test_an_unknown_id_is_not_found(seeded_documents: SeededDocuments, blobs: BlobStore) -> None:
    with store_session.app_engine().connect() as conn, pytest.raises(DocumentNotFound):
        load_document(conn, tenant_id=seeded_documents.tenant_id, document_id="nope",
                      blobs=blobs)


def test_a_row_without_a_blob_is_not_found(
    seeded_documents: SeededDocuments, blobs: BlobStore
) -> None:
    """A purged (or never-landed) blob leaves a row with blob_id NULL. That is
    not a document with an empty payload; it is not a document."""
    seeded_documents.add_document("doc-no-blob", age_days=1, blob_id=None)
    with store_session.app_engine().connect() as conn, pytest.raises(DocumentNotFound):
        load_document(conn, tenant_id=seeded_documents.tenant_id, document_id="doc-no-blob",
                      blobs=blobs)


def test_a_purged_blob_is_not_found(seeded_documents: SeededDocuments, blobs: BlobStore) -> None:
    with store_session.app_engine().begin() as conn:
        document_id = store_document(conn, tenant_id=seeded_documents.tenant_id,
                                     doc=_labelled(Origin.CORPUS), blobs=blobs,
                                     received_at=RECEIVED_AT)
    (row,) = seeded_documents.documents_for(document_id)
    assert blobs.delete(seeded_documents.tenant_id, row.blob_id)
    with store_session.app_engine().connect() as conn, pytest.raises(DocumentNotFound):
        load_document(conn, tenant_id=seeded_documents.tenant_id, document_id=document_id,
                      blobs=blobs)


class _FailingConnection:
    def execute(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("simulated insert failure")


def test_a_failed_insert_leaves_no_orphan_blob(
    seeded_documents: SeededDocuments, blobs: BlobStore, tmp_path: pathlib.Path
) -> None:
    """Purge is row-driven; a blob with no row would never be deleted."""
    conn = cast(Connection, _FailingConnection())
    with pytest.raises(RuntimeError, match="simulated"):
        store_document(conn, tenant_id=seeded_documents.tenant_id,
                       doc=_labelled(Origin.CORPUS), blobs=blobs, received_at=RECEIVED_AT)
    assert list((tmp_path / "blobs").iterdir()) == []


class _Spy:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def send(self, lane: Lane, payload: bytes) -> bytes:
        self.sent.append(payload)
        return b""


def test_a_stored_confidential_document_is_still_refused_after_reload(
    seeded_documents: SeededDocuments, blobs: BlobStore
) -> None:
    """The test that ties §4 to Never #1: persistence does not launder a label."""
    with store_session.app_engine().begin() as conn:
        document_id = store_document(conn, tenant_id=seeded_documents.tenant_id,
                                     doc=_labelled(Origin.UPLOAD), blobs=blobs,
                                     received_at=RECEIVED_AT)
    with store_session.app_engine().connect() as conn:
        loaded = load_document(conn, tenant_id=seeded_documents.tenant_id,
                               document_id=document_id, blobs=blobs)
    assert loaded.sensitivity is Sensitivity.CLIENT_CONFIDENTIAL

    spy = _Spy()
    with pytest.raises(SensitivityViolation):
        admit(Lane.EXTRACT_VISION, loaded,
              policy=ProviderPolicy(enabled=True, redacted_opt_in=True), transport=spy)
    assert spy.sent == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/store/test_documents.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.store.documents'`

- [ ] **Step 3: Write the store module**

```python
# ledger_sense/store/documents.py
"""The documents table's one writer and one reader (§4).

`store_document` takes a Labelled[bytes], never bytes: a row cannot exist
without a label because the function cannot be called without one. It is the
only `documents.insert()` under ledger_sense/ — tests/boundary/test_structure.py
enforces that — so the only way into the table is through the door.

`load_document` rebuilds the same Labelled from the row. That is what makes the
label survive a restart: a stored document reaches the gateway as the value it
came in as, never re-labelled by hand.

Every miss — wrong tenant, unknown id, a row whose blob is NULL, a blob the
retention job has purged — is DocumentNotFound. 404, never 403: the row's
existence is not this tenant's to learn.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Connection

from ledger_sense.boundary import Labelled, Origin, Sensitivity
from ledger_sense.store.blobs import BlobStore
from ledger_sense.store.retention import documents

__all__ = ["DocumentNotFound", "load_document", "store_document"]


class DocumentNotFound(LookupError):
    """No document by that id is visible to this tenant."""


def store_document(
    conn: Connection,
    *,
    tenant_id: str,
    doc: Labelled[bytes],
    blobs: BlobStore,
    received_at: datetime,
) -> str:
    """Encrypt the payload, insert the row with its label, return the new id."""
    document_id = uuid.uuid4().hex
    # The blob lands first (blobs.py fsyncs before returning) so the row never
    # points at ciphertext that was not written. If the row then fails, the
    # blob is removed: purge is row-driven and would never find an orphan.
    blob_id = blobs.put(tenant_id, doc.payload)
    try:
        conn.execute(documents.insert().values(
            id=document_id,
            tenant_id=tenant_id,
            blob_id=blob_id,
            received_at=received_at,
            sensitivity=doc.sensitivity.value,
            origin=doc.origin.value,
            provenance=doc.provenance,
        ))
    except Exception:
        blobs.delete(tenant_id, blob_id)
        raise
    return document_id


def load_document(
    conn: Connection,
    *,
    tenant_id: str,
    document_id: str,
    blobs: BlobStore,
) -> Labelled[bytes]:
    """The document as it was accepted: same payload, same label."""
    row = conn.execute(
        sa.select(documents)
        .where(documents.c.tenant_id == tenant_id, documents.c.id == document_id)
    ).one_or_none()
    if row is None or row.blob_id is None:
        raise DocumentNotFound(document_id)
    payload = blobs.get(tenant_id, row.blob_id)
    if payload is None:
        raise DocumentNotFound(document_id)
    return Labelled(
        payload=payload,
        sensitivity=Sensitivity(row.sensitivity),
        origin=Origin(row.origin),
        provenance=row.provenance,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/store -v`
Expected: PASS — 3 round trips, the row/blob test, 4 not-found cases, the orphan test, the reload-into-admit test, plus every existing store test

- [ ] **Step 5: Lint, types, review, commit**

Run: `uv run ruff check . && uv run mypy --strict ledger_sense tests`
Expected: clean. Run `ecc:python-review` over `ledger_sense/store/documents.py`.

```bash
git add ledger_sense/store/documents.py tests/store/test_documents.py
git commit -m "feat(store): store_document/load_document — the label survives a restart"
```

---

### Task 7: Structural guards and housekeeping

**Files:**
- Create: `tests/boundary/test_structure.py`
- Modify: `ledger_sense/api/fixtures.py:62` (and its import block)
- Modify: `docs/security.md:30-35`

**Interfaces:**
- Consumes: `calls_to` from `tests._ast_guard` (Task 1). Nothing produced; this task closes the loop.

- [ ] **Step 1: Write the structural guards**

```python
# tests/boundary/test_structure.py
"""§5, structural: the boundary holds because the codebase has one shape.

Behavioural tests prove admit() refuses; these prove there is no path that
does not go through it. Static, over the AST, so an `import ... as` or an
attribute call cannot dodge them (F-32).
"""

import ast
import pathlib

from tests._ast_guard import calls_to

PACKAGE = pathlib.Path("ledger_sense")

# The two places a Labelled may be built: where the file is opened, and where
# a stored label is rebuilt. Adding a third is a design change, not an edit here.
LABELLED_CONSTRUCTION_SITES = {
    PACKAGE / "ingest" / "intake.py",
    PACKAGE / "store" / "documents.py",
}

# Anything that could carry bytes to a provider. gateway.py is the only module
# allowed to know these exist — and today it does not import them either.
PROVIDER_CLIENTS = (
    "httpx", "requests", "openai", "anthropic", "google.generativeai", "groq",
    "mistralai", "cohere",
)


def _sources() -> list[tuple[pathlib.Path, ast.Module]]:
    # ledger_sense/web is the Next.js app; its .next build cache holds no Python.
    return [(path, ast.parse(path.read_text(encoding="utf-8")))
            for path in sorted(PACKAGE.rglob("*.py")) if "web" not in path.parts]


def test_labelled_is_constructed_only_at_the_door_and_the_store() -> None:
    hits = [f"{path}:{line}" for path, tree in _sources()
            if path not in LABELLED_CONSTRUCTION_SITES
            for line in calls_to(tree, "Labelled")]
    assert hits == [], f"Labelled constructed outside intake.py/documents.py: {hits}"


def test_the_door_and_the_store_do_construct_it() -> None:
    """The guard above would pass vacuously if the names moved. Pin them."""
    for site in LABELLED_CONSTRUCTION_SITES:
        tree = ast.parse(site.read_text(encoding="utf-8"))
        assert calls_to(tree, "Labelled"), f"{site} no longer builds a Labelled"


def test_documents_insert_is_called_only_by_store_document() -> None:
    offenders = [
        str(path) for path, _tree in _sources()
        if path != PACKAGE / "store" / "documents.py"
        and "documents.insert(" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"a second writer to documents: {offenders}"


def test_no_module_outside_the_gateway_imports_a_provider_client() -> None:
    offenders = []
    for path, tree in _sources():
        if path == PACKAGE / "gateway.py":
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            if any(name == client or name.startswith(client + ".")
                   for name in names for client in PROVIDER_CLIENTS):
                offenders.append(f"{path}:{node.lineno}")
    assert offenders == [], f"a provider client outside gateway.py: {offenders}"
```

- [ ] **Step 2: Run the guards**

Run: `uv run pytest tests/boundary/test_structure.py -v`
Expected: PASS, 4 tests — the codebase already has this shape after Tasks 3–6. (If `test_labelled_is_constructed_only_at_the_door_and_the_store` lists a hit, that is a real second construction site: fix the code, not the test.)

- [ ] **Step 3: One spelling of the label in fixtures.py**

In `ledger_sense/api/fixtures.py`, add to the import block:

```python
from ledger_sense.boundary import Sensitivity
```

and change line 62 from `SYNTHETIC = "SYNTHETIC"` to:

```python
SYNTHETIC = Sensitivity.SYNTHETIC.value
```

Run: `uv run pytest tests/api -v`
Expected: PASS — `tests/api/test_fixtures.py` still sees `"SYNTHETIC"` on the wire

- [ ] **Step 4: Say so in the docs**

In `docs/security.md`, in the bullet beginning "**A client document reaching a third-party model provider.**", after the sentence ending "See rule 1 in `CLAUDE.md`." insert:

```
  The label is a type (`ledger_sense/boundary.py`), attached at the door
  (`ingest/intake.py::accept_document`, derived from the document's origin —
  there is no parameter through which an upload can be called synthetic),
  judged at the seam (`gateway.py::admit`, before any provider state is
  consulted) and persisted with a CHECK constraint (migration 0003). The
  tests in `tests/boundary/` prove the refusal, its ordering, and that the
  codebase has no second path.
```

- [ ] **Step 5: Full verification, then commit**

Run each, and paste the tail of each into the task's completion report:

```
uv run pytest -q
uv run ruff check .
uv run mypy --strict ledger_sense tests
uv run python -m scripts.check_no_float
uv run python -m scripts.aia_guard
```

Expected: suite green; ruff silent; mypy `Success`; both scripts green.

```bash
git add tests/boundary/test_structure.py ledger_sense/api/fixtures.py docs/security.md
git commit -m "test(boundary): structural guards — one door, one writer, no provider client outside the seam"
```

---

## Self-review against the spec

- §1 → Task 2. §2 → Task 3. §3 → Task 4 (matrix, ordering, opt-in, local lane; the deterministic-never-imports-gateway guard lives in `test_degraded_mode.py`). §4 → Tasks 5–6 (migration, metadata, conftest, writer/reader, not-found cases, orphan blob). §5 behavioural → Tasks 4–6; §5 structural → Task 7 plus Task 4's import guard. Housekeeping (`fixtures.py`, `security.md`) → Task 7.
- Names used across tasks: `Sensitivity`, `Origin`, `SensitivityViolation`, `Labelled` (Task 2) ← Tasks 3–7; `RedactionVerdict`, `accept_document` (Task 3) ← Task 6; `Lane`, `ProviderPolicy`, `ProviderDisabled`, `Transport`, `admit` (Task 4) ← Task 6; `DocumentNotFound`, `store_document`, `load_document` (Task 6) ← none later; `calls_to` (Task 1) ← Task 7; `SeededDocuments` (existing conftest) ← Tasks 5–6. Signatures match.
- Not in this plan, and said so: an upload route; a real `Transport`; a redaction verifier; `00` R4(b); `tenants.redacted_opt_in` storage (the policy takes it as a value from the caller).
