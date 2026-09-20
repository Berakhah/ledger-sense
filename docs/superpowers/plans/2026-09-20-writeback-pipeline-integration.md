# Writeback Pipeline Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Phase 5's writeback stack (`QBOAdapter`, `ExportAdapter`, `IdempotencyLedger`, `AuditedAdapter`) into a human-triggered HTTP surface that reconstructs a `WriteRequest` from a document's stored `claims`, gates on the review queue being clear, and posts a draft bill through whichever adapter the caller names.

**Architecture:** A new `ledger_sense/writeback/assemble.py` module turns persisted `claims` rows into a `WriteRequest`, with a deterministic idempotency key derived independently of vendor identity. A new `ledger_sense/api/writeback.py` router (mounted through `deps.register()` like every other route) gates on `findings.state`, calls `assemble.py`, and posts through an `AuditedAdapter`-wrapped `QBOAdapter` or `ExportAdapter` built from new environment factories mirroring `store/blobs.py::blob_store_from_environment()`. Because the idempotency key excludes vendor identity, "is this document already posted, and with what vendor" is answered from the audit log (the one place both adapters write through uniformly), not from `IdempotencyLedger` (which `ExportAdapter` never touches).

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy (Postgres/SQLite via `store/session.py::app_engine()`), the existing `writeback/` package's own SQLite-backed stores (`IdempotencyLedger`, `AuditLog`, `TokenStore`), `httpx` for the QBO sandbox.

**Spec:** `docs/superpowers/specs/2026-09-19-writeback-pipeline-integration-design.md` — read it in full before starting; this plan implements it, including the two rulings resolved after review (vendor-mismatch-on-replay is a 409 conflict, and the single-process constraint is pinned to a source-level `WORKERS` constant).

## Global Constraints

- No floats anywhere in the money path (Never-rule #2). `LineItem.amount` is `Money` (`Annotated[Decimal, Strict()]`); nothing in this slice constructs one from a float.
- Writeback stays draft-only (Never-rule #3): every route in this plan calls `create_unapproved_bill`, never anything that pays or approves.
- Idempotency keys on every write (Never-rule #4): the key is `build_idempotency_key(document_hash, contract_id, application_no)`, already built in `writeback/interface.py` — nothing here invents a second key scheme.
- No hardcoded model names, no secrets in the repo (`00` R1, B2). Every new credential-shaped value (QBO realm id, default account ref, writeback storage directory) comes from an environment variable, matching `store/blobs.py::blob_store_from_environment()`'s pattern; none has a literal default.
- `mypy --strict` and `ruff` clean on every task (per `ecc:python-review`/`ecc:fastapi-review` — run both before each commit).
- Every new route is mounted through `ledger_sense.api.deps.register()`, never `include_router` — `tests/api/test_route_registration.py` and `tests/api/test_auth.py::test_every_api_route_requires_a_session` depend on the route table staying walkable.
- `ledger_sense/writeback/` (and now `ledger_sense/api/writeback.py`) must never import `ledger_sense.extract`, `ledger_sense.ingest`, or `ledger_sense.gateway` — enforced by `tests/boundary/test_structure.py`.
- `owned()` returns 404, never 403, on a cross-tenant id — every route below checks it before touching writeback state.

---

## Task 1: Extraction emits a Claim for each line's description

Phase 6's `build_write_request` (Task 7) needs to reconstruct a bill line's description with provenance, matching every other grid cell. Today it cannot: `extract/assemble.py` writes a `Claim` for every field in `_LINE_FIELDS` (`scheduled_value`, `previous`, `this_period`, `stored`, `total_completed_and_stored`, `retainage`) but never for `description` — the same asymmetry `test_a_found_header_field_carries_a_claim_too` already caught and fixed for the document-header fields. This is a small, well-understood gap-closing fix, not a debugging exercise, so it goes through ordinary TDD.

**Files:**
- Modify: `ledger_sense/extract/assemble.py:203-233` (the per-row claim-writing loop inside `assemble_pay_application`)
- Test: `tests/extract/test_assemble.py`

**Interfaces:**
- Consumes: `ledger_sense.extract.tables.Cell` (`field: str, text: str, bbox: BBox, page: int`), `ledger_sense.provenance.Claim`/`Method`, the existing `_claim()` helper and `match_quality()` in the same module.
- Produces: `built.provenance[f"line[{item_no}].description"]` — a `Claim[str]` with `method=Method.DETERMINISTIC`, `confidence=None`, value equal to `line.description` — for every line whose row has a `description` column. Task 6/7 (`writeback/assemble.py::build_write_request`) reads this key directly.

- [ ] **Step 1: Write the failing test**

```python
def test_every_line_description_carries_a_claim_too() -> None:
    """Phase 6's writeback assembly (ledger_sense/writeback/assemble.py) needs
    a claim for every line's description, matching every other grid cell —
    the same asymmetry test_a_found_header_field_carries_a_claim_too already
    caught for the document-header fields. Without this, build_write_request
    can never resolve a bill line's description with provenance."""
    _c, app, pdf = generate(seed=1, error="clean")
    built = assemble_pay_application(pdf, "corpus://seed-1")
    assert built.lines, "fixture produced no lines"
    for line in built.lines:
        field = f"line[{line.item_no}].description"
        assert field in built.provenance, field
        c = claim(built, field)
        assert c.method is Method.DETERMINISTIC, field
        assert c.confidence is None, field
        assert c.value == line.description
```

Add this to `tests/extract/test_assemble.py`, near `test_a_found_header_field_carries_a_claim_too` (the file already imports `generate`, `assemble_pay_application`, `Method`, and defines the `claim()` helper).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/extract/test_assemble.py::test_every_line_description_carries_a_claim_too -v`
Expected: FAIL — `assert field in built.provenance` fails because no `description` claim exists yet.

- [ ] **Step 3: Write the minimal implementation**

In `ledger_sense/extract/assemble.py`, immediately after the existing `for field in _LINE_FIELDS:` claim-writing loop (currently ending at line 229) and before `lines.append(ContinuationLine(...))`:

```python
        for field in _LINE_FIELDS:
            cell = row[field]
            provenance[f"line[{item_no}].{field}"] = _claim(
                source_path, cell.page, cell.bbox, values[field],
                match_quality=match_quality(label_exact=True, value_unique=True,
                                            column_unambiguous=True, profile_hit=False))
        if "description" in row:
            desc_cell = row["description"]
            provenance[f"line[{item_no}].description"] = _claim(
                source_path, desc_cell.page, desc_cell.bbox, desc_cell.text,
                match_quality=match_quality(label_exact=True, value_unique=True,
                                            column_unambiguous=True, profile_hit=False))
        lines.append(ContinuationLine(
            item_no=item_no,
            description=row["description"].text if "description" in row else "",
            **values))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/extract/test_assemble.py -v`
Expected: PASS — including every pre-existing test in the file (the new claim key must not break `test_every_populated_field_carries_a_deterministic_claim` or `test_every_claim_points_at_a_page_and_a_box`, which iterate every non-`_`-prefixed provenance key generically).

- [ ] **Step 5: Run mypy/ruff and commit**

Run: `uv run mypy --strict ledger_sense/extract/assemble.py && uv run ruff check ledger_sense/extract/assemble.py`

```bash
git add ledger_sense/extract/assemble.py tests/extract/test_assemble.py
git commit -m "feat(extract): emit a Claim for every line's description

Matches every other grid cell — description was the one line field with no
provenance, which is what the Phase 6 writeback assembly needs to
reconstruct a bill line's description with a located claim behind it."
```

---

## Task 2: AuditLog records vendor identity on every write

Ruling 5 (spec update) says a second `post` call for an already-posted document with *different* `vendor_ref`/`vendor_invoice_number` is a 409 conflict, not a silent replay — because the idempotency key deliberately excludes vendor identity (it is derived from `document_hash + contract_id + application_no` only). Detecting that conflict needs to know what vendor fields the *original* post used. `IdempotencyLedger` cannot answer this for both adapters: `ExportAdapter.create_unapproved_bill` never touches `IdempotencyLedger` at all (its own CSV/IIF key-scan is its state). The `audit` table, however, is written through uniformly by `AuditedAdapter.create_unapproved_bill` for *every* adapter — so it becomes the one place "what vendor was this posted under" can be answered adapter-agnostically.

**Files:**
- Modify: `ledger_sense/writeback/audit.py` (`_PAYLOAD_FIELDS`, `_SCHEMA`, `AuditEntry`, `AuditLog.record_write`, `AuditLog.for_document`, `AuditedAdapter.create_unapproved_bill`)
- Test: `tests/writeback/test_audit.py`

**Interfaces:**
- Consumes: `ledger_sense.writeback.interface.WriteRequest` (already carries `vendor_ref: str`, `vendor_invoice_number: str | None`).
- Produces: `AuditEntry.vendor_ref: str | None`, `AuditEntry.vendor_invoice_number: str | None` (both `None` on a `reversal` entry, since a reversal carries no vendor data of its own). Task 9/10 (`api/writeback.py`) reads these two fields off entries returned by `AuditLog.for_document()`.

- [ ] **Step 1: Write the failing test**

First read `tests/writeback/test_audit.py` in full to match its existing fixture/helper conventions (its own `FakeAdapter`, if it defines one, or an import from `test_interface.py`), then add:

```python
def test_record_write_carries_the_requests_vendor_fields(tmp_path) -> None:
    """F-40's DocNumber/PrivateNote split lives in qbo.py; this is the
    audit-side half — the vendor identity a bill was posted under must be
    recoverable later without re-deriving it, because the idempotency key
    (build_idempotency_key) deliberately excludes vendor identity (Ruling 5,
    Phase 6 spec) and a later call with different vendor fields needs
    something to compare against."""
    audit = AuditLog(str(tmp_path / "audit.db"))
    adapter = AuditedAdapter(FakeAdapter(), audit, approved_by="reviewer@example.test")
    request = WriteRequest(
        idempotency_key=build_idempotency_key("h1", "C-1", 1), doc_id="doc-1",
        vendor_ref="V-9", vendor_invoice_number="INV-1", txn_date=date(2026, 1, 1),
        line_items=[LineItem(description="Sitework", amount=money("100.00"), account_ref="54")],
        memo="m")
    adapter.create_unapproved_bill(request)

    entries = audit.for_document("doc-1")
    assert len(entries) == 1
    assert entries[0].vendor_ref == "V-9"
    assert entries[0].vendor_invoice_number == "INV-1"


def test_record_reversal_carries_no_vendor_fields(tmp_path) -> None:
    """A reversal call (remote_id + idempotency_key only) has no vendor data
    of its own to record — None, not a stale copy of the original write's."""
    audit = AuditLog(str(tmp_path / "audit.db"))
    adapter = AuditedAdapter(FakeAdapter(), audit, approved_by="reviewer@example.test")
    request = WriteRequest(
        idempotency_key=build_idempotency_key("h1", "C-1", 1), doc_id="doc-1",
        vendor_ref="V-9", vendor_invoice_number=None, txn_date=date(2026, 1, 1),
        line_items=[LineItem(description="Sitework", amount=money("100.00"), account_ref="54")],
        memo="m")
    result = adapter.create_unapproved_bill(request)
    adapter.reverse(result.remote_id, result.idempotency_key)

    entries = audit.for_document("doc-1")
    reversal = next(e for e in entries if e.action == "reversal")
    assert reversal.vendor_ref is None
    assert reversal.vendor_invoice_number is None
```

Add `WriteRequest`, `LineItem`, `build_idempotency_key`, `money`, `date` to the file's imports if not already present; reuse whichever `FakeAdapter` the file already relies on (do not define a second one).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/writeback/test_audit.py -k vendor_fields -v`
Expected: FAIL — `pydantic.ValidationError` or `AttributeError` because `AuditEntry` has no `vendor_ref` field yet.

- [ ] **Step 3: Write the minimal implementation**

In `ledger_sense/writeback/audit.py`, extend the schema:

```python
_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id                    TEXT PRIMARY KEY,
    tenant_id             TEXT NOT NULL,
    doc_id                TEXT NOT NULL,
    adapter               TEXT NOT NULL,
    action                TEXT NOT NULL,
    remote_id             TEXT NOT NULL,
    idempotency_key       TEXT,
    vendor_ref            TEXT,
    vendor_invoice_number TEXT,
    approved_by           TEXT NOT NULL,
    occurred_at           TEXT NOT NULL,
    detail                TEXT NOT NULL,
    prev_hash             TEXT NOT NULL,
    entry_hash            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_by_remote ON audit (tenant_id, remote_id);
"""
```

```python
_PAYLOAD_FIELDS = (
    "id", "tenant_id", "doc_id", "adapter", "action", "remote_id",
    "idempotency_key", "vendor_ref", "vendor_invoice_number",
    "approved_by", "occurred_at", "detail",
)
```

(`_CHAIN_FIELDS`, `_ALL_COLUMNS`, `_INSERT_SQL`, `_VERIFY_COLUMNS` are all built from `_PAYLOAD_FIELDS` per the module's own docstring — no further edit needed there.)

```python
class AuditEntry(BaseModel):
    id: str
    doc_id: str
    adapter: str
    action: Action
    remote_id: str
    idempotency_key: str | None
    vendor_ref: str | None
    vendor_invoice_number: str | None
    approved_by: str
    occurred_at: datetime
    detail: str
```

```python
    def record_write(self, doc_id: str, adapter: str, result: WriteResult,
                     approved_by: str, *, vendor_ref: str,
                     vendor_invoice_number: str | None) -> None:
        self._append(AuditEntry(
            id=str(uuid.uuid4()), doc_id=doc_id, adapter=adapter, action="write",
            remote_id=result.remote_id, idempotency_key=result.idempotency_key,
            vendor_ref=vendor_ref, vendor_invoice_number=vendor_invoice_number,
            approved_by=approved_by, occurred_at=result.created_at,
            detail="replay of an earlier write" if result.was_replay
                   else "unapproved bill created, awaiting review"))

    def record_reversal(self, doc_id: str, adapter: str, result: ReversalResult,
                        approved_by: str, idempotency_key: str) -> None:
        self._append(AuditEntry(
            id=str(uuid.uuid4()), doc_id=doc_id, adapter=adapter, action="reversal",
            remote_id=result.remote_id, idempotency_key=idempotency_key,
            vendor_ref=None, vendor_invoice_number=None,
            approved_by=approved_by, occurred_at=result.reversed_at,
            detail=f"reversed by {result.method}"))

    def for_document(self, doc_id: str) -> list[AuditEntry]:
        rows = self._conn.execute(
            "SELECT id, doc_id, adapter, action, remote_id, idempotency_key, "
            "vendor_ref, vendor_invoice_number, approved_by, occurred_at, detail "
            "FROM audit WHERE tenant_id = ? AND doc_id = ? ORDER BY rowid",
            (self._tenant_id, doc_id)).fetchall()
        return [AuditEntry(
            id=r[0], doc_id=r[1], adapter=r[2], action=r[3], remote_id=r[4],
            idempotency_key=r[5], vendor_ref=r[6], vendor_invoice_number=r[7],
            approved_by=r[8], occurred_at=datetime.fromisoformat(r[9]), detail=r[10])
            for r in rows]
```

And update `AuditedAdapter.create_unapproved_bill`, the only caller of `record_write`:

```python
    def create_unapproved_bill(self, request: WriteRequest) -> WriteResult:
        if not self._approved_by:
            raise ValueError(
                "a write requires approved_by — nothing reaches a client's books "
                "without a named human on the record")
        result = self._inner.create_unapproved_bill(request)
        self._audit.record_write(
            request.doc_id, self.name, result, self._approved_by,
            vendor_ref=request.vendor_ref,
            vendor_invoice_number=request.vendor_invoice_number)
        return result
```

No production `AuditLog` file exists yet outside tests — every instance in the codebase today is built from a fresh `tmp_path` file, and the only "real" one will be the one Task 3's new factory creates — so this schema change needs no migration path.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/writeback/test_audit.py -v`
Expected: PASS — including every pre-existing test (the hash-chain tests must still pass unchanged).

- [ ] **Step 5: Run mypy/ruff and commit**

Run: `uv run mypy --strict ledger_sense/writeback/audit.py && uv run ruff check ledger_sense/writeback/audit.py`

```bash
git add ledger_sense/writeback/audit.py tests/writeback/test_audit.py
git commit -m "feat(writeback): record vendor identity on every audited write

The idempotency key deliberately excludes vendor identity, so detecting a
vendor-mismatch conflict on a later post (Phase 6 Ruling 5) needs the
original request's vendor_ref/vendor_invoice_number recoverable from
somewhere both adapters write through uniformly — the audit log."
```

---

## Task 3: Environment factories for the idempotency ledger and audit log

Mirrors `store/blobs.py::blob_store_from_environment()`'s pattern: a factory, built where writeback is wired (not a module singleton), raising loudly if its directory is unset. Both new stores live under one new environment variable, `LEDGER_SENSE_WRITEBACK_DIR`, as sibling `.db` files — same "one root directory, several files" shape `BlobStore` already uses for a single kind of file.

**Files:**
- Modify: `ledger_sense/writeback/idempotency.py` (add `ledger_from_environment`)
- Modify: `ledger_sense/writeback/audit.py` (add `audit_log_from_environment`)
- Test: `tests/writeback/test_idempotency.py`, `tests/writeback/test_audit.py`

**Interfaces:**
- Produces: `ledger_from_environment() -> IdempotencyLedger`, `audit_log_from_environment() -> AuditLog`. Task 4, 9, 10, 11 (adapter factories and the API routes) call these instead of constructing the stores by hand.

- [ ] **Step 1: Write the failing tests**

Add to `tests/writeback/test_idempotency.py`:

```python
def test_ledger_from_environment_builds_a_ledger_under_the_configured_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_SENSE_WRITEBACK_DIR", str(tmp_path))
    ledger = ledger_from_environment()
    ledger.begin("key-1", adapter="qbo")
    assert (tmp_path / "writes.db").exists()


def test_ledger_from_environment_raises_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("LEDGER_SENSE_WRITEBACK_DIR", raising=False)
    with pytest.raises(RuntimeError, match="LEDGER_SENSE_WRITEBACK_DIR"):
        ledger_from_environment()
```

Add to `tests/writeback/test_audit.py`:

```python
def test_audit_log_from_environment_builds_a_log_under_the_configured_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_SENSE_WRITEBACK_DIR", str(tmp_path))
    audit = audit_log_from_environment()
    assert audit.verify_chain() == []
    assert (tmp_path / "audit.db").exists()


def test_audit_log_from_environment_raises_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("LEDGER_SENSE_WRITEBACK_DIR", raising=False)
    with pytest.raises(RuntimeError, match="LEDGER_SENSE_WRITEBACK_DIR"):
        audit_log_from_environment()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/writeback/test_idempotency.py tests/writeback/test_audit.py -k from_environment -v`
Expected: FAIL — `ImportError: cannot import name 'ledger_from_environment'` (and the audit equivalent).

- [ ] **Step 3: Write the minimal implementation**

In `ledger_sense/writeback/idempotency.py`, add near the top (after existing imports) and at the end of the file:

```python
import os
```

```python
_WRITEBACK_DIR_ENV = "LEDGER_SENSE_WRITEBACK_DIR"


def ledger_from_environment() -> IdempotencyLedger:
    """The deployment's idempotency ledger, at
    `<LEDGER_SENSE_WRITEBACK_DIR>/writes.db`.

    Built where writeback is wired (the writeback API routes), not as a
    module singleton — matching store/blobs.py::blob_store_from_environment.
    Raises if the directory is unset: a ledger written to a guessed path is
    a ledger nothing backs up.
    """
    root = os.environ.get(_WRITEBACK_DIR_ENV)
    if not root:
        raise RuntimeError(
            f"{_WRITEBACK_DIR_ENV} is unset: refusing to guess where the "
            f"writeback idempotency ledger lives.")
    os.makedirs(root, exist_ok=True)
    return IdempotencyLedger(os.path.join(root, "writes.db"))
```

In `ledger_sense/writeback/audit.py`, add `import os` alongside the existing imports and, at the end of the file:

```python
_WRITEBACK_DIR_ENV = "LEDGER_SENSE_WRITEBACK_DIR"


def audit_log_from_environment() -> AuditLog:
    """The deployment's audit log, at `<LEDGER_SENSE_WRITEBACK_DIR>/audit.db`.
    See idempotency.py::ledger_from_environment for the pattern this mirrors."""
    root = os.environ.get(_WRITEBACK_DIR_ENV)
    if not root:
        raise RuntimeError(
            f"{_WRITEBACK_DIR_ENV} is unset: refusing to guess where the "
            f"writeback audit log lives.")
    os.makedirs(root, exist_ok=True)
    return AuditLog(os.path.join(root, "audit.db"))
```

(`_WRITEBACK_DIR_ENV` is duplicated across the two modules deliberately — matching `store/retention.py`'s own `_aware()` docstring: "two copies of one line is cheaper than a cross-import; a third caller earns it a shared home.")

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/writeback/test_idempotency.py tests/writeback/test_audit.py -v`
Expected: PASS.

- [ ] **Step 5: Run mypy/ruff and commit**

Run: `uv run mypy --strict ledger_sense/writeback/idempotency.py ledger_sense/writeback/audit.py && uv run ruff check ledger_sense/writeback/idempotency.py ledger_sense/writeback/audit.py`

```bash
git add ledger_sense/writeback/idempotency.py ledger_sense/writeback/audit.py tests/writeback/test_idempotency.py tests/writeback/test_audit.py
git commit -m "feat(writeback): environment factories for the ledger and audit log

Mirrors store/blobs.py's blob_store_from_environment pattern: LEDGER_SENSE_WRITEBACK_DIR
names one directory holding both SQLite-backed stores, built where writeback
is wired rather than as a module singleton."
```

---

## Task 4: Environment factories for the QBO and export adapters

Completes the adapter-construction seam the spec (§2) calls for: `qbo_adapter_from_environment(ledger)` and `export_adapter_from_environment()`, both reading their configuration from the environment and raising loudly on a missing value, matching Task 3's factories.

**Files:**
- Modify: `ledger_sense/writeback/qbo.py` (add `qbo_adapter_from_environment`; promote the existing `TYPE_CHECKING`-only `TokenStore` import to a real one)
- Modify: `ledger_sense/writeback/export.py` (add `export_adapter_from_environment`)
- Test: `tests/writeback/test_qbo.py`, `tests/writeback/test_export.py`

**Interfaces:**
- Consumes: `ledger_sense.writeback.idempotency.IdempotencyLedger`, `ledger_sense.writeback.tokens.TokenStore`, `ledger_sense.store.blobs.kek_from_environment` (reused rather than inventing a second at-rest secret).
- Produces: `qbo_adapter_from_environment(ledger: IdempotencyLedger) -> QBOAdapter`, `export_adapter_from_environment() -> ExportAdapter`. Task 9/10/11 (`api/writeback.py`) call these to build the adapter named in a request body.

- [ ] **Step 1: Write the failing tests**

Add to `tests/writeback/test_qbo.py`:

```python
def test_qbo_adapter_from_environment_builds_a_real_adapter(tmp_path, monkeypatch, ledger) -> None:
    monkeypatch.setenv("LEDGER_SENSE_WRITEBACK_DIR", str(tmp_path))
    monkeypatch.setenv("QBO_REALM_ID", "realm-1")
    adapter = qbo_adapter_from_environment(ledger)
    assert isinstance(adapter, QBOAdapter)
    assert adapter.name == "qbo"


def test_qbo_adapter_from_environment_raises_when_realm_unset(tmp_path, monkeypatch, ledger) -> None:
    monkeypatch.setenv("LEDGER_SENSE_WRITEBACK_DIR", str(tmp_path))
    monkeypatch.delenv("QBO_REALM_ID", raising=False)
    with pytest.raises(RuntimeError, match="QBO_REALM_ID"):
        qbo_adapter_from_environment(ledger)
```

`test_qbo.py` already has a `ledger` fixture (see the existing `@pytest.fixture() def ledger(tmp_path): ...`) and already imports `pytest`; add `from cryptography.fernet import Fernet` and set `LEDGER_SENSE_KEK` via `monkeypatch.setenv("LEDGER_SENSE_KEK", Fernet.generate_key().decode())` in both new tests (`kek_from_environment()` requires it).

Add to `tests/writeback/test_export.py`:

```python
def test_export_adapter_from_environment_builds_a_real_adapter(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_SENSE_WRITEBACK_DIR", str(tmp_path))
    adapter = export_adapter_from_environment()
    assert isinstance(adapter, ExportAdapter)
    assert adapter.name == "export"


def test_export_adapter_from_environment_raises_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("LEDGER_SENSE_WRITEBACK_DIR", raising=False)
    with pytest.raises(RuntimeError, match="LEDGER_SENSE_WRITEBACK_DIR"):
        export_adapter_from_environment()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/writeback/test_qbo.py tests/writeback/test_export.py -k from_environment -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Write the minimal implementation**

In `ledger_sense/writeback/qbo.py`, move `TokenStore` out of the `TYPE_CHECKING` block into a real top-level import (it is now constructed at runtime, not just referenced in a type hint):

```python
from ledger_sense.store.blobs import kek_from_environment
from ledger_sense.writeback.tokens import TokenStore

if TYPE_CHECKING:
    from ledger_sense.writeback.interface import WritebackPort
```

Add near the bottom of the module, before the `if TYPE_CHECKING:` conformance-check block:

```python
_WRITEBACK_DIR_ENV = "LEDGER_SENSE_WRITEBACK_DIR"
_REALM_ID_ENV = "QBO_REALM_ID"
_QBO_SANDBOX_BASE_URL = "https://sandbox-quickbooks.api.intuit.com"


def qbo_adapter_from_environment(ledger: IdempotencyLedger) -> QBOAdapter:
    """The deployment's QBO adapter: HttpQBOClient against the sandbox,
    backed by an encrypted-at-rest TokenStore, wrapping `ledger`.

    Mirrors store/blobs.py's factory pattern. Reuses the deployment KEK
    (store.blobs.kek_from_environment) for the token store rather than
    inventing a second secret to manage — both protect at-rest client data
    the same way.
    """
    root = os.environ.get(_WRITEBACK_DIR_ENV)
    if not root:
        raise RuntimeError(
            f"{_WRITEBACK_DIR_ENV} is unset: refusing to guess where the "
            f"QBO token store lives.")
    realm_id = os.environ.get(_REALM_ID_ENV)
    if not realm_id:
        raise RuntimeError(
            f"{_REALM_ID_ENV} is unset: connect the QBO sandbox account "
            f"before writing.")
    os.makedirs(root, exist_ok=True)
    token_store = TokenStore(os.path.join(root, "tokens.db"),
                             fernet_key=kek_from_environment())
    client = HttpQBOClient(_QBO_SANDBOX_BASE_URL, realm_id, token_store)
    return QBOAdapter(client, ledger, realm_id=realm_id)
```

In `ledger_sense/writeback/export.py`, add `import os` to the existing import block and, near the bottom of the module before the `if TYPE_CHECKING:` block:

```python
_WRITEBACK_DIR_ENV = "LEDGER_SENSE_WRITEBACK_DIR"


def export_adapter_from_environment() -> ExportAdapter:
    """The deployment's CSV export adapter, writing to
    `<LEDGER_SENSE_WRITEBACK_DIR>/export/`. Mirrors store/blobs.py's factory
    pattern."""
    root = os.environ.get(_WRITEBACK_DIR_ENV)
    if not root:
        raise RuntimeError(
            f"{_WRITEBACK_DIR_ENV} is unset: refusing to guess where "
            f"exported bills should be written.")
    return ExportAdapter(os.path.join(root, "export"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/writeback/test_qbo.py tests/writeback/test_export.py -v`
Expected: PASS — including every pre-existing test in both files.

- [ ] **Step 5: Run mypy/ruff and commit**

Run: `uv run mypy --strict ledger_sense/writeback/qbo.py ledger_sense/writeback/export.py && uv run ruff check ledger_sense/writeback/qbo.py ledger_sense/writeback/export.py`

```bash
git add ledger_sense/writeback/qbo.py ledger_sense/writeback/export.py tests/writeback/test_qbo.py tests/writeback/test_export.py
git commit -m "feat(writeback): environment factories for the QBO and export adapters

qbo_adapter_from_environment and export_adapter_from_environment mirror
blob_store_from_environment's pattern, completing the adapter-selection seam
the Phase 6 spec calls for (adapter: qbo | export, chosen per request)."
```

---

## Task 5: `.env.example` documents the new variables

**Files:**
- Modify: `.env.example`

**Interfaces:** None — documentation only.

- [ ] **Step 1: Add the new variables**

Append to the "Accounting writeback" section of `.env.example`:

```
# Directory the idempotency ledger, audit log, OAuth token store, and CSV/IIF
# exports live in (ledger_sense/writeback/idempotency.py, audit.py, qbo.py,
# export.py). Reuses LEDGER_SENSE_KEK to encrypt tokens at rest.
LEDGER_SENSE_WRITEBACK_DIR=

# The QuickBooks Online sandbox company (realm) this deployment writes to.
QBO_REALM_ID=

# One tenant-wide default GL account applied to every posted bill line —
# real per-vendor/per-line account mapping is deferred (Phase 6 spec, §2).
LEDGER_SENSE_DEFAULT_ACCOUNT_REF=
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "docs: document the Phase 6 writeback environment variables"
```

---

## Task 6: `writeback/assemble.py` — `MissingClaimError` and `derive_idempotency_key`

The first half of the spec's §1 assembly module: the piece the `GET status` route (Task 9) needs on its own, without a `vendor_ref`.

**Files:**
- Create: `ledger_sense/writeback/assemble.py`
- Test: `tests/writeback/test_assemble.py`

**Interfaces:**
- Consumes: `ledger_sense.store.retention.claims`, `ledger_sense.store.retention.documents` (SQLAlchemy `Table`s), `ledger_sense.writeback.interface.build_idempotency_key`.
- Produces: `class MissingClaimError(Exception)` with a `.field: str` attribute; `derive_idempotency_key(conn: Connection, *, document_id: str) -> str`. Task 7 (`build_write_request`) and Task 9/10/11 (`api/writeback.py`) both import these.

- [ ] **Step 1: Write the failing tests**

Create `tests/writeback/test_assemble.py`:

```python
"""writeback/assemble.py: claims -> WriteRequest, and the idempotency key
derived independently of vendor identity (Ruling 5, Phase 6 spec)."""

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from ledger_sense.auth.models import create_tenant
from ledger_sense.boundary import Origin, Sensitivity
from ledger_sense.store import session as store_session
from ledger_sense.store.retention import claims, documents
from ledger_sense.writeback.assemble import MissingClaimError, derive_idempotency_key
from ledger_sense.writeback.interface import build_idempotency_key


@pytest.fixture()
def tenant_id() -> str:
    return create_tenant(f"tenant-{uuid.uuid4().hex[:8]}")


def _insert_document(conn: sa.Connection, tenant_id: str, *,
                     content_sha256: str | None = "abc123") -> str:
    document_id = uuid.uuid4().hex
    conn.execute(documents.insert().values(
        id=document_id, tenant_id=tenant_id, blob_id=None,
        received_at=datetime.now(UTC), sensitivity=Sensitivity.SYNTHETIC.value,
        origin=Origin.CORPUS.value, provenance="tests/writeback/test_assemble.py",
        filename="fixture.pdf", content_sha256=content_sha256))
    return document_id


def _insert_claim(conn: sa.Connection, tenant_id: str, document_id: str, *,
                  field: str, value: str, value_type: str = "str") -> None:
    conn.execute(claims.insert().values(
        id=uuid.uuid4().hex, tenant_id=tenant_id, document_id=document_id,
        field=field, value=value, value_type=value_type, method="deterministic",
        confidence=None, match_quality=1.0, page=0, x0=0.0, top=0.0, x1=1.0,
        bottom=1.0, caveat=None, observed_at=datetime.now(UTC)))


def test_derive_idempotency_key_matches_build_idempotency_key(tenant_id: str) -> None:
    with store_session.app_engine().begin() as conn:
        document_id = _insert_document(conn, tenant_id)
        _insert_claim(conn, tenant_id, document_id, field="contract_id", value="C-1")
        _insert_claim(conn, tenant_id, document_id, field="application_no",
                      value="2", value_type="int")
        key = derive_idempotency_key(conn, document_id=document_id)
    assert key == build_idempotency_key("abc123", "C-1", 2)


def test_derive_idempotency_key_is_stable_across_two_calls(tenant_id: str) -> None:
    with store_session.app_engine().begin() as conn:
        document_id = _insert_document(conn, tenant_id)
        _insert_claim(conn, tenant_id, document_id, field="contract_id", value="C-1")
        _insert_claim(conn, tenant_id, document_id, field="application_no",
                      value="2", value_type="int")
        first = derive_idempotency_key(conn, document_id=document_id)
        second = derive_idempotency_key(conn, document_id=document_id)
    assert first == second


def test_derive_idempotency_key_refuses_a_document_with_no_content_hash(tenant_id: str) -> None:
    with store_session.app_engine().begin() as conn:
        document_id = _insert_document(conn, tenant_id, content_sha256=None)
        with pytest.raises(MissingClaimError, match="content_sha256"):
            derive_idempotency_key(conn, document_id=document_id)


def test_derive_idempotency_key_refuses_a_document_missing_contract_id(tenant_id: str) -> None:
    with store_session.app_engine().begin() as conn:
        document_id = _insert_document(conn, tenant_id)
        _insert_claim(conn, tenant_id, document_id, field="application_no",
                      value="2", value_type="int")
        with pytest.raises(MissingClaimError, match="contract_id"):
            derive_idempotency_key(conn, document_id=document_id)


def test_derive_idempotency_key_picks_the_latest_claim_for_a_field(tenant_id: str) -> None:
    """The human-edit-supersedes-extraction rule (api/review.py::_value_type_for's
    convention): a later observed_at wins, so a reviewer's correction to
    contract_id changes which key a re-post derives."""
    with store_session.app_engine().begin() as conn:
        document_id = _insert_document(conn, tenant_id)
        conn.execute(claims.insert().values(
            id=uuid.uuid4().hex, tenant_id=tenant_id, document_id=document_id,
            field="contract_id", value="C-WRONG", value_type="str", method="deterministic",
            confidence=None, match_quality=1.0, page=0, x0=0.0, top=0.0, x1=1.0,
            bottom=1.0, caveat=None, observed_at=datetime(2026, 1, 1, tzinfo=UTC)))
        conn.execute(claims.insert().values(
            id=uuid.uuid4().hex, tenant_id=tenant_id, document_id=document_id,
            field="contract_id", value="C-CORRECT", value_type="str", method="human",
            confidence=None, match_quality=None, page=0, x0=0.0, top=0.0, x1=1.0,
            bottom=1.0, caveat=None, observed_at=datetime(2026, 1, 2, tzinfo=UTC)))
        _insert_claim(conn, tenant_id, document_id, field="application_no",
                      value="2", value_type="int")
        key = derive_idempotency_key(conn, document_id=document_id)
    assert key == build_idempotency_key("abc123", "C-CORRECT", 2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/writeback/test_assemble.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.writeback.assemble'`.

- [ ] **Step 3: Write the minimal implementation**

Create `ledger_sense/writeback/assemble.py`:

```python
"""Claims -> WriteRequest assembly.

Every value a bill needs comes from the `claims` table Phase 2/4 already
populate — nothing here re-derives a value from the document itself, and
nothing guesses one that was never extracted (B11). Where a field has more
than one claim (extraction, then a human accept/edit), the LATEST by
`observed_at` wins — the same rule api/review.py::_value_type_for uses, so a
reviewer's correction is picked up automatically.

The idempotency key (derive_idempotency_key) is deliberately independent of
vendor identity: it comes from `build_idempotency_key(document_hash,
contract_id, application_no)` only. Nothing extracts a vendor today, and a
caller-supplied vendor_ref must never change which remote object a retry
resolves to.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from ledger_sense.store.retention import claims, documents
from ledger_sense.writeback.interface import build_idempotency_key


class MissingClaimError(Exception):
    """No claim exists for a field this write needs. Never guessed (B11) —
    the money path refuses rather than defaulting."""

    def __init__(self, field: str) -> None:
        super().__init__(f"no claim for {field!r}")
        self.field = field


def _latest_claims(conn: Connection, tenant_id: str,
                   document_id: str) -> dict[str, sa.Row[Any]]:
    """One row per field: the claim with the latest observed_at wins."""
    rows = conn.execute(
        sa.select(claims)
        .where(claims.c.tenant_id == tenant_id, claims.c.document_id == document_id)
        .order_by(claims.c.observed_at.asc())
    ).all()
    latest: dict[str, sa.Row[Any]] = {}
    for row in rows:
        latest[row.field] = row  # a later observed_at overwrites an earlier one
    return latest


def _require(latest: dict[str, sa.Row[Any]], field: str) -> sa.Row[Any]:
    row = latest.get(field)
    if row is None:
        raise MissingClaimError(field)
    return row


def derive_idempotency_key(conn: Connection, *, document_id: str) -> str:
    """The document's idempotency key, independent of vendor identity.

    Reads only documents.content_sha256 plus the latest contract_id and
    application_no claims. A document with no content hash (a
    pre-migration-0005 row) or no contract_id/application_no claim raises
    MissingClaimError — there is no safe fallback for a key that must stay
    stable across retries.
    """
    doc = conn.execute(
        sa.select(documents.c.tenant_id, documents.c.content_sha256)
        .where(documents.c.id == document_id)
    ).one_or_none()
    if doc is None or doc.content_sha256 is None:
        raise MissingClaimError("content_sha256")
    latest = _latest_claims(conn, str(doc.tenant_id), document_id)
    contract_id = str(_require(latest, "contract_id").value)
    application_no = int(_require(latest, "application_no").value)
    return build_idempotency_key(str(doc.content_sha256), contract_id, application_no)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/writeback/test_assemble.py -v`
Expected: PASS.

- [ ] **Step 5: Run mypy/ruff and commit**

Run: `uv run mypy --strict ledger_sense/writeback/assemble.py && uv run ruff check ledger_sense/writeback/assemble.py`

```bash
git add ledger_sense/writeback/assemble.py tests/writeback/test_assemble.py
git commit -m "feat(writeback): derive_idempotency_key from persisted claims

The vendor-independent half of Phase 6's claims-to-WriteRequest assembly —
needed on its own by the status route, which has no vendor fields to work with."
```

---

## Task 7: `writeback/assemble.py` — `build_write_request`

The second half of §1: the full `WriteRequest` reconstruction.

**Files:**
- Modify: `ledger_sense/writeback/assemble.py`
- Test: `tests/writeback/test_assemble.py`

**Interfaces:**
- Consumes: `ledger_sense.writeback.interface.{LineItem, WriteRequest}`, `ledger_sense.domain.money.money`, `derive_idempotency_key` (Task 6), the `LEDGER_SENSE_DEFAULT_ACCOUNT_REF` environment variable.
- Produces: `build_write_request(conn: Connection, *, tenant_id: str, document_id: str, vendor_ref: str, vendor_invoice_number: str | None) -> WriteRequest`. Task 9/10 (`api/writeback.py`'s `post` route) is the only caller.

- [ ] **Step 1: Write the failing tests**

Add to `tests/writeback/test_assemble.py` (extend the imports at the top to add `date`, `os`, `LineItem`, `build_write_request`, `MissingClaimError` already imported, `money`, `WriteRequest`):

```python
_TOP_LEVEL_MONEY_FIELDS = ("total_completed_and_stored", "retainage_total",
                          "previous_payments", "change_orders_net")


def _seed_complete_document(conn: sa.Connection, tenant_id: str) -> str:
    document_id = _insert_document(conn, tenant_id)
    _insert_claim(conn, tenant_id, document_id, field="contract_id", value="C-1")
    _insert_claim(conn, tenant_id, document_id, field="application_no",
                  value="2", value_type="int")
    _insert_claim(conn, tenant_id, document_id, field="period_to",
                  value="2026-08-31", value_type="date")
    for field in _TOP_LEVEL_MONEY_FIELDS:
        _insert_claim(conn, tenant_id, document_id, field=field,
                      value="1000.00", value_type="money")
    _insert_claim(conn, tenant_id, document_id,
                  field="line[000].description", value="Sitework")
    _insert_claim(conn, tenant_id, document_id,
                  field="line[000].this_period", value="500.00", value_type="money")
    return document_id


def test_build_write_request_assembles_a_complete_request(tenant_id: str, monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_SENSE_DEFAULT_ACCOUNT_REF", "54")
    with store_session.app_engine().begin() as conn:
        document_id = _seed_complete_document(conn, tenant_id)
        request = build_write_request(
            conn, tenant_id=tenant_id, document_id=document_id,
            vendor_ref="V-9", vendor_invoice_number="INV-4471")

    assert request.doc_id == document_id
    assert request.vendor_ref == "V-9"
    assert request.vendor_invoice_number == "INV-4471"
    assert request.txn_date == date(2026, 8, 31)
    assert request.memo == "Pay application 2, contract C-1"
    assert len(request.line_items) == 1
    line = request.line_items[0]
    assert line.description == "Sitework"
    assert line.amount == money("500.00")
    assert line.account_ref == "54"
    assert request.idempotency_key == build_idempotency_key("abc123", "C-1", 2)


def test_build_write_request_picks_the_latest_claim_for_a_line(tenant_id: str, monkeypatch) -> None:
    """A reviewer's edit to this_period must be what actually bills, not the
    original extraction — the same latest-by-observed_at rule as every other
    field."""
    monkeypatch.setenv("LEDGER_SENSE_DEFAULT_ACCOUNT_REF", "54")
    with store_session.app_engine().begin() as conn:
        document_id = _seed_complete_document(conn, tenant_id)
        conn.execute(claims.insert().values(
            id=uuid.uuid4().hex, tenant_id=tenant_id, document_id=document_id,
            field="line[000].this_period", value="750.00", value_type="money",
            method="human", confidence=None, match_quality=None, page=0,
            x0=0.0, top=0.0, x1=1.0, bottom=1.0, caveat=None,
            observed_at=datetime(2099, 1, 1, tzinfo=UTC)))
        request = build_write_request(
            conn, tenant_id=tenant_id, document_id=document_id,
            vendor_ref="V-9", vendor_invoice_number=None)
    assert request.line_items[0].amount == money("750.00")


def test_build_write_request_refuses_a_line_missing_description(tenant_id: str, monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_SENSE_DEFAULT_ACCOUNT_REF", "54")
    with store_session.app_engine().begin() as conn:
        document_id = _insert_document(conn, tenant_id)
        _insert_claim(conn, tenant_id, document_id, field="contract_id", value="C-1")
        _insert_claim(conn, tenant_id, document_id, field="application_no",
                      value="2", value_type="int")
        _insert_claim(conn, tenant_id, document_id, field="period_to",
                      value="2026-08-31", value_type="date")
        for field in _TOP_LEVEL_MONEY_FIELDS:
            _insert_claim(conn, tenant_id, document_id, field=field,
                          value="1000.00", value_type="money")
        _insert_claim(conn, tenant_id, document_id,
                      field="line[000].this_period", value="500.00", value_type="money")
        with pytest.raises(MissingClaimError, match=r"line\[000\]\.description"):
            build_write_request(conn, tenant_id=tenant_id, document_id=document_id,
                                vendor_ref="V-9", vendor_invoice_number=None)


def test_build_write_request_refuses_a_missing_summary_field(tenant_id: str, monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_SENSE_DEFAULT_ACCOUNT_REF", "54")
    with store_session.app_engine().begin() as conn:
        document_id = _insert_document(conn, tenant_id)
        _insert_claim(conn, tenant_id, document_id, field="contract_id", value="C-1")
        _insert_claim(conn, tenant_id, document_id, field="application_no",
                      value="2", value_type="int")
        _insert_claim(conn, tenant_id, document_id, field="period_to",
                      value="2026-08-31", value_type="date")
        for field in ("total_completed_and_stored", "previous_payments", "change_orders_net"):
            _insert_claim(conn, tenant_id, document_id, field=field,
                          value="1000.00", value_type="money")
        _insert_claim(conn, tenant_id, document_id,
                      field="line[000].description", value="Sitework")
        _insert_claim(conn, tenant_id, document_id,
                      field="line[000].this_period", value="500.00", value_type="money")
        with pytest.raises(MissingClaimError, match="retainage_total"):
            build_write_request(conn, tenant_id=tenant_id, document_id=document_id,
                                vendor_ref="V-9", vendor_invoice_number=None)


def test_build_write_request_refuses_a_document_with_no_lines(tenant_id: str, monkeypatch) -> None:
    monkeypatch.setenv("LEDGER_SENSE_DEFAULT_ACCOUNT_REF", "54")
    with store_session.app_engine().begin() as conn:
        document_id = _insert_document(conn, tenant_id)
        _insert_claim(conn, tenant_id, document_id, field="contract_id", value="C-1")
        _insert_claim(conn, tenant_id, document_id, field="application_no",
                      value="2", value_type="int")
        _insert_claim(conn, tenant_id, document_id, field="period_to",
                      value="2026-08-31", value_type="date")
        for field in _TOP_LEVEL_MONEY_FIELDS:
            _insert_claim(conn, tenant_id, document_id, field=field,
                          value="1000.00", value_type="money")
        with pytest.raises(MissingClaimError, match="lines"):
            build_write_request(conn, tenant_id=tenant_id, document_id=document_id,
                                vendor_ref="V-9", vendor_invoice_number=None)


def test_build_write_request_raises_when_default_account_ref_unset(tenant_id: str, monkeypatch) -> None:
    monkeypatch.delenv("LEDGER_SENSE_DEFAULT_ACCOUNT_REF", raising=False)
    with store_session.app_engine().begin() as conn:
        document_id = _seed_complete_document(conn, tenant_id)
        with pytest.raises(RuntimeError, match="LEDGER_SENSE_DEFAULT_ACCOUNT_REF"):
            build_write_request(conn, tenant_id=tenant_id, document_id=document_id,
                                vendor_ref="V-9", vendor_invoice_number=None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/writeback/test_assemble.py -k build_write_request -v`
Expected: FAIL — `ImportError: cannot import name 'build_write_request'`.

- [ ] **Step 3: Write the minimal implementation**

Add to `ledger_sense/writeback/assemble.py`:

```python
import os
import re
from collections import defaultdict
from datetime import date

from ledger_sense.domain.money import Money, money
from ledger_sense.writeback.interface import LineItem, WriteRequest
```

```python
_ACCOUNT_REF_ENV = "LEDGER_SENSE_DEFAULT_ACCOUNT_REF"

_TOP_LEVEL_MONEY_FIELDS = ("total_completed_and_stored", "retainage_total",
                          "previous_payments", "change_orders_net")

_LINE_ITEM_FIELD = re.compile(r"\Aline\[(?P<item_no>[^\]]+)\]\.(?P<field>.+)\Z")


def _money_of(row: sa.Row[Any]) -> Money:
    return money(str(row.value))


def build_write_request(
    conn: Connection, *, tenant_id: str, document_id: str,
    vendor_ref: str, vendor_invoice_number: str | None,
) -> WriteRequest:
    """Reconstruct a WriteRequest from this document's persisted claims.

    account_ref is one tenant-wide default (LEDGER_SENSE_DEFAULT_ACCOUNT_REF),
    applied to every line — real per-vendor/per-line account mapping is
    deferred (Phase 6 spec, §2). Every required field's absence raises
    MissingClaimError rather than defaulting (B11).
    """
    idempotency_key = derive_idempotency_key(conn, document_id=document_id)
    account_ref = os.environ.get(_ACCOUNT_REF_ENV)
    if not account_ref:
        raise RuntimeError(
            f"{_ACCOUNT_REF_ENV} is unset: refusing to post a bill with no "
            f"default account.")

    latest = _latest_claims(conn, tenant_id, document_id)
    application_no = int(_require(latest, "application_no").value)
    contract_id = str(_require(latest, "contract_id").value)
    period_to = date.fromisoformat(str(_require(latest, "period_to").value))

    for field in _TOP_LEVEL_MONEY_FIELDS:
        _require(latest, field)  # presence only — extraction must have captured a total

    lines_by_item: dict[str, dict[str, sa.Row[Any]]] = defaultdict(dict)
    for field, row in latest.items():
        match = _LINE_ITEM_FIELD.match(field)
        if match is not None:
            lines_by_item[match.group("item_no")][match.group("field")] = row
    if not lines_by_item:
        raise MissingClaimError("lines")

    line_items: list[LineItem] = []
    for item_no in sorted(lines_by_item):
        fields = lines_by_item[item_no]
        if "description" not in fields:
            raise MissingClaimError(f"line[{item_no}].description")
        if "this_period" not in fields:
            raise MissingClaimError(f"line[{item_no}].this_period")
        line_items.append(LineItem(
            description=str(fields["description"].value),
            amount=_money_of(fields["this_period"]),
            account_ref=account_ref,
        ))

    memo = f"Pay application {application_no}, contract {contract_id}"
    return WriteRequest(
        idempotency_key=idempotency_key, doc_id=document_id, vendor_ref=vendor_ref,
        vendor_invoice_number=vendor_invoice_number, txn_date=period_to,
        line_items=line_items, memo=memo)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/writeback/test_assemble.py -v`
Expected: PASS — all tests in the file, including Task 6's.

- [ ] **Step 5: Run mypy/ruff and commit**

Run: `uv run mypy --strict ledger_sense/writeback/assemble.py && uv run ruff check ledger_sense/writeback/assemble.py`

```bash
git add ledger_sense/writeback/assemble.py tests/writeback/test_assemble.py
git commit -m "feat(writeback): build_write_request assembles a WriteRequest from claims

Line reconstruction, human-edit-supersedes-extraction, and MissingClaimError
on any required top-level field or line missing description/this_period —
nothing in the money path is guessed."
```

---

## Task 8: `api/writeback.py` — module skeleton and `GET .../status`

The simplest route first: no vendor fields, no gate, no adapter dispatch — just `derive_idempotency_key` plus an audit-log lookup.

**Files:**
- Create: `ledger_sense/api/writeback.py`
- Modify: `ledger_sense/api/app.py` (register the router — see below; not deferred to Task 12)
- Test: `tests/api/test_writeback.py`

**Interfaces:**
- Consumes: `ledger_sense.api.deps.{owned, require_session, register}`, `ledger_sense.store.retention.documents`, `ledger_sense.store.session.app_engine`, `ledger_sense.writeback.assemble.{MissingClaimError, derive_idempotency_key}`, `ledger_sense.writeback.audit.{AuditEntry, AuditLog, audit_log_from_environment}`.
- Produces: `router = APIRouter(prefix="/api/writeback")`, registered on `app` in this same task, with one route so far, `GET /api/writeback/documents/{doc_id}/status`; `_get_document(document_id, session, conn) -> sa.Row[Any]`; `_live_write_entries(audit: AuditLog, doc_id: str) -> dict[str, AuditEntry]`. Task 9-11 add more routes to the same already-registered router and reuse both helpers.

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_writeback.py`. It needs a tenant client that owns a real `documents` row with real `claims` — `tests/api/conftest.py`'s `TenantClient` has `create_document()` (a stub-table row, wrong shape) and `seed_exception()` (writes a real `documents` + one `findings` row, but no `claims`); extend `TenantClient` with a `seed_postable_document()` helper in `conftest.py` first:

```python
    def seed_postable_document(self) -> str:
        """A document with a complete claim set (ready to post) and no open
        exceptions — findings all 'accepted', matching what the review queue
        looks like once a reviewer clears it."""
        document_id = uuid.uuid4().hex
        with store_session.app_engine().begin() as conn:
            conn.execute(documents.insert().values(
                id=document_id, tenant_id=self.tenant_id, blob_id=None,
                received_at=datetime.now(UTC), sensitivity=Sensitivity.SYNTHETIC.value,
                origin=Origin.CORPUS.value, provenance="tests/api/conftest.py",
                filename="fixture.pdf", content_sha256="abc123"))
            conn.execute(findings.insert().values(
                id=uuid.uuid4().hex, tenant_id=self.tenant_id, document_id=document_id,
                rule_id="A2", field="line[000].total_completed_and_stored",
                message="cleared", expected=None, actual=None, page=None,
                x0=None, top=None, x1=None, bottom=None,
                state="accepted", created_at=datetime.now(UTC)))
            rows = [
                ("contract_id", "C-1", "str"), ("application_no", "2", "int"),
                ("period_to", "2026-08-31", "date"),
                ("total_completed_and_stored", "1000.00", "money"),
                ("retainage_total", "50.00", "money"),
                ("previous_payments", "0.00", "money"),
                ("change_orders_net", "0.00", "money"),
                ("line[000].description", "Sitework", "str"),
                ("line[000].this_period", "500.00", "money"),
            ]
            conn.execute(claims.insert(), [{
                "id": uuid.uuid4().hex, "tenant_id": self.tenant_id,
                "document_id": document_id, "field": field, "value": value,
                "value_type": value_type, "method": "deterministic",
                "confidence": None, "match_quality": 1.0, "page": 0,
                "x0": 0.0, "top": 0.0, "x1": 1.0, "bottom": 1.0,
                "caveat": None, "observed_at": datetime.now(UTC),
            } for field, value, value_type in rows])
        return document_id
```

Add `claims` to `conftest.py`'s `from ledger_sense.store.retention import documents, findings` import, and add a second small helper alongside `seed_postable_document` for the "never run" case Task 9 needs — `create_document()` (the existing stub-table helper) does not create a real `documents` row, so `_get_document`'s `owned()` check would 404 rather than let the gate's 422 fire:

```python
    def seed_unrun_document(self) -> str:
        """A real documents row with no findings and no claims — the
        'never run' case _require_cleared (Task 9) must refuse with 422,
        distinct from create_document()'s stub-table row, which owned()
        can't even see."""
        document_id = uuid.uuid4().hex
        with store_session.app_engine().begin() as conn:
            conn.execute(documents.insert().values(
                id=document_id, tenant_id=self.tenant_id, blob_id=None,
                received_at=datetime.now(UTC), sensitivity=Sensitivity.SYNTHETIC.value,
                origin=Origin.CORPUS.value, provenance="tests/api/conftest.py",
                filename="fixture.pdf", content_sha256=None))
        return document_id
```

Then create `tests/api/test_writeback.py`:

```python
"""Writeback API — the human-triggered seam from a cleared review queue to a
draft bill. tests/api/conftest.py's TenantClient.seed_postable_document()
gives every test here a document with a complete claim set and no open
exceptions, matching what the review queue looks like once cleared."""

from pathlib import Path

import pytest

from tests.api.conftest import TenantClient


@pytest.fixture(autouse=True)
def _writeback_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEDGER_SENSE_WRITEBACK_DIR", str(tmp_path / "writeback"))
    monkeypatch.setenv("LEDGER_SENSE_DEFAULT_ACCOUNT_REF", "54")


def test_status_reports_not_posted_before_any_post(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    alice, _bob = two_tenants
    doc_id = alice.seed_postable_document()
    response = alice.get(f"/api/writeback/documents/{doc_id}/status")
    assert response.status_code == 200, response.text
    assert response.json() == {"state": "not_posted", "remote_id": None}


def test_status_on_a_document_with_no_content_hash_is_422(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    alice, _bob = two_tenants
    doc_id = alice.seed_unrun_document()  # a real documents row, no content_sha256, no claims
    response = alice.get(f"/api/writeback/documents/{doc_id}/status")
    assert response.status_code == 422, response.text


def test_status_on_a_cross_tenant_document_is_404(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    alice, bob = two_tenants
    doc_id = alice.seed_postable_document()
    response = bob.get(f"/api/writeback/documents/{doc_id}/status")
    assert response.status_code == 404, response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_writeback.py -v`
Expected: FAIL — 404, because `ledger_sense/api/writeback.py` doesn't exist yet AND `app.py` doesn't register it yet. (Both are fixed together in Step 3 below — see the registration note after the module code, which explains why this task, not Task 12, must wire it into `app.py`.)

- [ ] **Step 3: Write the minimal implementation**

Create `ledger_sense/api/writeback.py`:

```python
"""Writeback API — the human-triggered seam from a cleared review queue to a
draft bill in the client's accounting system.

Never-rule #3: posting is a separate, later, human-triggered action, never a
step ledger_sense.run.document takes automatically. This router is the only
door a bill reaches an accounting system through.

The idempotency key (writeback.assemble.derive_idempotency_key) is
deliberately independent of vendor identity (Ruling 5, Phase 6 spec) — it
comes from the document alone. So "is this document already posted, and
under what vendor" is answered from the audit log, the one place both
QBOAdapter and ExportAdapter write through uniformly (AuditedAdapter),
rather than from IdempotencyLedger, which ExportAdapter never touches.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ledger_sense.api.deps import owned, require_session
from ledger_sense.auth.session import Session
from ledger_sense.store.retention import documents
from ledger_sense.store.session import app_engine
from ledger_sense.writeback.assemble import MissingClaimError, derive_idempotency_key
from ledger_sense.writeback.audit import AuditEntry, AuditLog, audit_log_from_environment

router = APIRouter(prefix="/api/writeback")

CurrentSession = Annotated[Session, Depends(require_session)]

AdapterName = Literal["qbo", "export"]


class StatusResponse(BaseModel):
    state: Literal["not_posted", "posted"]
    remote_id: str | None = None


def _get_document(document_id: str, session: Session, conn: sa.Connection) -> sa.Row[Any]:
    row = conn.execute(
        sa.select(documents.c.id, documents.c.tenant_id)
        .where(documents.c.id == document_id)).one_or_none()
    owned(session, None if row is None else str(row.tenant_id))
    assert row is not None  # owned() raised otherwise; for the type checker
    return row


def _live_write_entries(audit: AuditLog, doc_id: str) -> dict[str, AuditEntry]:
    """The currently-live 'write' entry per adapter for this document — one
    that has not since been reversed.

    Adapter-agnostic on purpose: ExportAdapter never touches
    IdempotencyLedger at all (its own CSV/IIF key-scan is its state), but
    both adapters write through AuditedAdapter uniformly, so the audit log
    is the one place "is this posted" can be answered without knowing which
    adapter posted it. Mirrors IdempotencyLedger.begin()'s own "a reversed
    record is treated as absent" rule (F-39).
    """
    live: dict[str, AuditEntry] = {}
    for entry in audit.for_document(doc_id):
        if entry.action == "write":
            live[entry.adapter] = entry
        elif entry.action == "reversal":
            current = live.get(entry.adapter)
            if current is not None and current.remote_id == entry.remote_id:
                del live[entry.adapter]
    return live


@router.get("/documents/{doc_id}/status")
def status(doc_id: str, session: CurrentSession) -> StatusResponse:
    from fastapi import HTTPException

    with app_engine().connect() as conn:
        _get_document(doc_id, session, conn)
        try:
            derive_idempotency_key(conn, document_id=doc_id)
        except MissingClaimError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"cannot check status: no claim for {exc.field!r}") from exc

    audit = audit_log_from_environment()
    live = _live_write_entries(audit, doc_id)
    if not live:
        return StatusResponse(state="not_posted", remote_id=None)
    entry = next(iter(live.values()))
    return StatusResponse(state="posted", remote_id=entry.remote_id)
```

(The `from fastapi import HTTPException` local import inside `status` is temporary scaffolding for this task only — Task 9 hoists it to a top-level import alongside the other exception-mapping routes it adds; leaving it local here keeps this task's diff minimal and self-contained.)

**Register the router now, not in Task 12.** `tests/api/conftest.py`'s `TenantClient` reaches every route through the one shared `TestClient(app)` built from `ledger_sense.api.app`'s module-level `app = build_app()`. If `register(app, writeback_router)` isn't called until Task 12, `/api/writeback/...` returns 404 "not found" for every test in Tasks 8-11 regardless of whether the route itself is correct — an unregistered path 404s identically to a route that doesn't exist, so Step 2's RED and Step 4's (attempted) GREEN would be indistinguishable failures. Because Python re-imports the whole module tree fresh for every pytest process, registering the (currently single-route) router now is safe: each later task (9, 10, 11) adds more `@router.post`/`@router.get` decorators to the same `ledger_sense/api/writeback.py` module, and since those decorators run at import time — before `register()` ever walks `router.routes` — every route defined in the file by that later commit is picked up automatically. No re-registration step is needed in any later task.

In `ledger_sense/api/app.py`, add the import alongside the other routers and register it in `build_app()`:

```python
from ledger_sense.api.writeback import router as writeback_router
```

```python
    register(app, baseline_router)
    register(app, inbox_router)
    register(app, review_router)
    register(app, reconciliation_router)
    register(app, writeback_router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_writeback.py -v`
Expected: PASS — now that the router is actually mounted on `app`.

- [ ] **Step 4b: Confirm no regression in the route-registration audit**

Run: `uv run pytest tests/api/test_route_registration.py tests/api/test_auth.py -v`
Expected: PASS — the new route must show up in the walkable route table with `require_session` in its dependencies, same as every other router `register()` mounts.

- [ ] **Step 5: Run mypy/ruff and commit**

Run: `uv run mypy --strict ledger_sense/api/writeback.py && uv run ruff check ledger_sense/api/writeback.py`

```bash
git add ledger_sense/api/writeback.py tests/api/test_writeback.py tests/api/conftest.py
git commit -m "feat(api): writeback status route

GET /api/writeback/documents/{doc_id}/status — the vendor-independent half
of the writeback surface, answering 'is this posted' from the audit log so
the answer works for either adapter."
```

---

## Task 9: `POST .../post` — happy path, gate, and replay

**Files:**
- Modify: `ledger_sense/api/writeback.py`
- Test: `tests/api/test_writeback.py`

**Interfaces:**
- Consumes: `ledger_sense.writeback.assemble.build_write_request`, `ledger_sense.writeback.audit.AuditedAdapter`, `ledger_sense.writeback.idempotency.ledger_from_environment`, `ledger_sense.writeback.qbo.qbo_adapter_from_environment`, `ledger_sense.writeback.export.export_adapter_from_environment`, `ledger_sense.auth.models.users`.
- Produces: `PostBody`, `PostResponse`, `POST /api/writeback/documents/{doc_id}/post`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/test_writeback.py`:

```python
def test_post_creates_a_bill_and_updates_status(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    alice, _bob = two_tenants
    doc_id = alice.seed_postable_document()
    response = alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                               {"adapter": "export", "vendor_ref": "V-9"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["adapter"] == "export"
    assert body["was_replay"] is False

    status_response = alice.get(f"/api/writeback/documents/{doc_id}/status")
    assert status_response.json() == {"state": "posted", "remote_id": body["remote_id"]}


def test_post_twice_with_the_same_vendor_replays(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    alice, _bob = two_tenants
    doc_id = alice.seed_postable_document()
    body = {"adapter": "export", "vendor_ref": "V-9"}
    first = alice.post_json(f"/api/writeback/documents/{doc_id}/post", body).json()
    second = alice.post_json(f"/api/writeback/documents/{doc_id}/post", body).json()
    assert second["was_replay"] is True
    assert second["remote_id"] == first["remote_id"]


def test_post_refuses_a_document_with_an_open_exception(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    alice, _bob = two_tenants
    doc_id = alice.seed_exception()  # ships with an OPEN finding by default
    response = alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                               {"adapter": "export", "vendor_ref": "V-9"})
    assert response.status_code == 422, response.text
    assert "open" in response.json()["detail"]


def test_post_refuses_a_document_never_run(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    alice, _bob = two_tenants
    doc_id = alice.seed_unrun_document()  # a real documents row, no findings at all
    response = alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                               {"adapter": "export", "vendor_ref": "V-9"})
    assert response.status_code == 422, response.text


def test_post_on_a_cross_tenant_document_is_404(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    alice, bob = two_tenants
    doc_id = alice.seed_postable_document()
    response = bob.post_json(f"/api/writeback/documents/{doc_id}/post",
                             {"adapter": "export", "vendor_ref": "V-9"})
    assert response.status_code == 404, response.text
```

`TenantClient` in `conftest.py` only has `get`/`post` (no-body); add a `post_json` helper alongside them:

```python
    def post_json(self, path: str, body: dict[str, object]) -> Response:
        return cast(Response, client.post(path, json=body, cookies={"session": self.token}))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_writeback.py -k "test_post" -v`
Expected: FAIL — 405 Method Not Allowed (the route does not exist yet).

- [ ] **Step 3: Write the minimal implementation**

In `ledger_sense/api/writeback.py`, replace the local `from fastapi import HTTPException` import in `status` with a top-level one, and add:

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StrictStr

from ledger_sense.auth.models import users
from ledger_sense.store.retention import findings
from ledger_sense.writeback.assemble import build_write_request
from ledger_sense.writeback.export import export_adapter_from_environment
from ledger_sense.writeback.idempotency import ledger_from_environment
from ledger_sense.writeback.interface import WritebackPort, WriteResult
from ledger_sense.writeback.qbo import qbo_adapter_from_environment
```

```python
class PostBody(BaseModel):
    adapter: AdapterName
    vendor_ref: StrictStr
    vendor_invoice_number: StrictStr | None = None


class PostResponse(BaseModel):
    remote_id: str
    idempotency_key: str
    adapter: str
    was_replay: bool


def _require_cleared(conn: sa.Connection, document_id: str, tenant_id: str) -> None:
    rows = conn.execute(
        sa.select(findings.c.state)
        .where(findings.c.document_id == document_id, findings.c.tenant_id == tenant_id)
    ).all()
    if not rows:
        raise HTTPException(status_code=422,
                            detail="document has never been run: no exceptions recorded")
    remaining = sum(1 for row in rows if row.state == "open")
    if remaining:
        raise HTTPException(status_code=422, detail=f"{remaining} exceptions still open")


def _approved_by(conn: sa.Connection, session: Session) -> str:
    row = conn.execute(sa.select(users.c.email).where(users.c.id == session.user_id)).one()
    return str(row.email)


def _inner_adapter(name: AdapterName, ledger: Any) -> WritebackPort:
    if name == "qbo":
        return qbo_adapter_from_environment(ledger)
    return export_adapter_from_environment()


@router.post("/documents/{doc_id}/post")
def post(doc_id: str, body: PostBody, session: CurrentSession) -> PostResponse:
    with app_engine().connect() as conn:
        _get_document(doc_id, session, conn)
        _require_cleared(conn, doc_id, session.tenant_id)
        approved_by = _approved_by(conn, session)
        try:
            request = build_write_request(
                conn, tenant_id=session.tenant_id, document_id=doc_id,
                vendor_ref=body.vendor_ref, vendor_invoice_number=body.vendor_invoice_number)
        except MissingClaimError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"cannot post: no claim for {exc.field!r}") from exc

    audit = audit_log_from_environment()
    ledger = ledger_from_environment()
    adapter = AuditedAdapter(_inner_adapter(body.adapter, ledger), audit, approved_by)
    result: WriteResult = adapter.create_unapproved_bill(request)
    return PostResponse(remote_id=result.remote_id, idempotency_key=result.idempotency_key,
                        adapter=result.adapter, was_replay=result.was_replay)
```

Add `from ledger_sense.writeback.audit import AuditedAdapter, ...` to the existing `writeback.audit` import line.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_writeback.py -v`
Expected: PASS — all tests added in Task 8 and this task.

- [ ] **Step 5: Run mypy/ruff and commit**

Run: `uv run mypy --strict ledger_sense/api/writeback.py && uv run ruff check ledger_sense/api/writeback.py`

```bash
git add ledger_sense/api/writeback.py tests/api/test_writeback.py tests/api/conftest.py
git commit -m "feat(api): writeback post route — gate, assemble, post, replay

POST /api/writeback/documents/{doc_id}/post gates on the review queue being
clear, reconstructs a WriteRequest from claims, and posts through an
AuditedAdapter-wrapped adapter. Error mapping and the vendor-conflict check
land in the next commit."
```

---

## Task 10: `POST .../post` — vendor-conflict 409 and the full error-mapping table

**Files:**
- Modify: `ledger_sense/api/writeback.py`
- Test: `tests/api/test_writeback.py`

**Interfaces:**
- Consumes: `ledger_sense.writeback.qbo.{ConcurrentWriteInProgressError, QBORejected}`, `ledger_sense.writeback.interface.ClosedPeriodError`, `_live_write_entries` (Task 8).
- Produces: the completed §5 error-mapping table on `post`, and the vendor-conflict 409 the two rulings settled.

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/test_writeback.py`:

```python
def test_post_with_a_different_vendor_ref_after_posting_is_409(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    alice, _bob = two_tenants
    doc_id = alice.seed_postable_document()
    alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                    {"adapter": "export", "vendor_ref": "V-9"})
    response = alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                               {"adapter": "export", "vendor_ref": "V-DIFFERENT"})
    assert response.status_code == 409, response.text
    assert "vendor" in response.json()["detail"]


def test_post_after_reversal_with_a_new_vendor_is_not_a_conflict(
        two_tenants: tuple[TenantClient, TenantClient]) -> None:
    """A reversal clears the way for a genuinely new post — including a
    changed vendor — the same way IdempotencyLedger.begin() already treats a
    `reversed` record as absent (F-39)."""
    alice, _bob = two_tenants
    doc_id = alice.seed_postable_document()
    first = alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                            {"adapter": "export", "vendor_ref": "V-9"}).json()
    alice.post_json(f"/api/writeback/documents/{doc_id}/reverse",
                    {"adapter": "export", "remote_id": first["remote_id"],
                     "idempotency_key": first["idempotency_key"]})
    response = alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                               {"adapter": "export", "vendor_ref": "V-DIFFERENT"})
    assert response.status_code == 200, response.text
```

The `ConcurrentWriteInProgressError`/`ClosedPeriodError`/`QBORejected` mapping rows are exercised at the unit level in `tests/api/test_writeback.py` via a monkeypatched adapter factory (an end-to-end sandbox call is out of scope here — the sandbox cassettes are the separate, credential-gated task the spec already carves out):

```python
def test_post_maps_concurrent_write_in_progress_to_409_with_retry_after(
        two_tenants: tuple[TenantClient, TenantClient], monkeypatch) -> None:
    from ledger_sense.writeback.qbo import ConcurrentWriteInProgressError
    import ledger_sense.api.writeback as writeback_mod

    def _raise(name, ledger):
        class _Raiser:
            name = "export"
            def create_unapproved_bill(self, request):
                raise ConcurrentWriteInProgressError("in progress")
            def is_period_closed(self, txn_date):
                return False
            def reverse(self, remote_id, idempotency_key):
                raise NotImplementedError
        return _Raiser()

    monkeypatch.setattr(writeback_mod, "_inner_adapter", _raise)
    alice, _bob = two_tenants
    doc_id = alice.seed_postable_document()
    response = alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                               {"adapter": "export", "vendor_ref": "V-9"})
    assert response.status_code == 409, response.text
    assert response.headers["retry-after"] == "2"


def test_post_maps_closed_period_to_422(
        two_tenants: tuple[TenantClient, TenantClient], monkeypatch) -> None:
    from ledger_sense.writeback.interface import ClosedPeriodError
    import ledger_sense.api.writeback as writeback_mod

    def _raise(name, ledger):
        class _Raiser:
            name = "export"
            def create_unapproved_bill(self, request):
                raise ClosedPeriodError("books closed through 2026-01-01")
            def is_period_closed(self, txn_date):
                return True
            def reverse(self, remote_id, idempotency_key):
                raise NotImplementedError
        return _Raiser()

    monkeypatch.setattr(writeback_mod, "_inner_adapter", _raise)
    alice, _bob = two_tenants
    doc_id = alice.seed_postable_document()
    response = alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                               {"adapter": "export", "vendor_ref": "V-9"})
    assert response.status_code == 422, response.text
    assert "books closed" in response.json()["detail"]


def test_post_maps_qbo_rejected_to_502_without_leaking_the_provider_body(
        two_tenants: tuple[TenantClient, TenantClient], monkeypatch) -> None:
    from ledger_sense.writeback.qbo import QBORejected
    import ledger_sense.api.writeback as writeback_mod

    def _raise(name, ledger):
        class _Raiser:
            name = "qbo"
            def create_unapproved_bill(self, request):
                raise QBORejected("QBO rejected: 400 {'bank_account': '...CLIENT_CONFIDENTIAL...'}")
            def is_period_closed(self, txn_date):
                return False
            def reverse(self, remote_id, idempotency_key):
                raise NotImplementedError
        return _Raiser()

    monkeypatch.setattr(writeback_mod, "_inner_adapter", _raise)
    alice, _bob = two_tenants
    doc_id = alice.seed_postable_document()
    response = alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                               {"adapter": "qbo", "vendor_ref": "V-9"})
    assert response.status_code == 502, response.text
    assert "CLIENT_CONFIDENTIAL" not in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_writeback.py -k "conflict or reversal or maps_" -v`
Expected: FAIL — the vendor-conflict tests get 200 instead of 409 (no check yet); the error-mapping tests raise an unhandled exception (500) instead of the mapped status.

- [ ] **Step 3: Write the minimal implementation**

In `ledger_sense/api/writeback.py`, import the two exceptions and wire the vendor-conflict check plus the try/except around `create_unapproved_bill`:

```python
from ledger_sense.writeback.interface import ClosedPeriodError, WritebackPort, WriteResult
from ledger_sense.writeback.qbo import ConcurrentWriteInProgressError, QBORejected, qbo_adapter_from_environment
```

Replace the tail of `post` (from `audit = audit_log_from_environment()` onward):

```python
    audit = audit_log_from_environment()
    live = _live_write_entries(audit, doc_id).get(body.adapter)
    if live is not None and (live.vendor_ref != body.vendor_ref
                             or live.vendor_invoice_number != body.vendor_invoice_number):
        raise HTTPException(status_code=409, detail=(
            "already posted with different vendor_ref/vendor_invoice_number; "
            "reverse and re-post to change vendor"))

    ledger = ledger_from_environment()
    adapter = AuditedAdapter(_inner_adapter(body.adapter, ledger), audit, approved_by)
    try:
        result: WriteResult = adapter.create_unapproved_bill(request)
    except ConcurrentWriteInProgressError as exc:
        raise HTTPException(status_code=409, detail=str(exc),
                            headers={"Retry-After": "2"}) from exc
    except ClosedPeriodError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except QBORejected as exc:
        raise HTTPException(status_code=502,
                            detail="the accounting system rejected the write") from exc

    return PostResponse(remote_id=result.remote_id, idempotency_key=result.idempotency_key,
                        adapter=result.adapter, was_replay=result.was_replay)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_writeback.py -v`
Expected: PASS — every test in the file.

- [ ] **Step 5: Run mypy/ruff and commit**

Run: `uv run mypy --strict ledger_sense/api/writeback.py && uv run ruff check ledger_sense/api/writeback.py`

```bash
git add ledger_sense/api/writeback.py tests/api/test_writeback.py
git commit -m "feat(api): writeback post — vendor-conflict 409 and error mapping

A second post with different vendor fields on an already-posted document is
a 409 conflict, not a silent replay (Ruling 5). ConcurrentWriteInProgressError
maps to 409+Retry-After, ClosedPeriodError to 422, QBORejected to 502 with
the provider's raw error body never returned to the client."
```

---

## Task 11: `POST .../reverse`

**Files:**
- Modify: `ledger_sense/api/writeback.py`
- Test: `tests/api/test_writeback.py`

**Interfaces:**
- Produces: `ReverseBody`, `ReverseResponse`, `POST /api/writeback/documents/{doc_id}/reverse`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/test_writeback.py`:

```python
def test_reverse_clears_the_posted_status(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    alice, _bob = two_tenants
    doc_id = alice.seed_postable_document()
    posted = alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                             {"adapter": "export", "vendor_ref": "V-9"}).json()
    response = alice.post_json(f"/api/writeback/documents/{doc_id}/reverse",
                               {"adapter": "export", "remote_id": posted["remote_id"],
                                "idempotency_key": posted["idempotency_key"]})
    assert response.status_code == 200, response.text
    assert response.json()["method"] == "deleted"

    status_response = alice.get(f"/api/writeback/documents/{doc_id}/status")
    assert status_response.json()["state"] == "not_posted"


def test_reverse_an_unknown_remote_id_is_422(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    alice, _bob = two_tenants
    doc_id = alice.seed_postable_document()
    response = alice.post_json(f"/api/writeback/documents/{doc_id}/reverse",
                               {"adapter": "export", "remote_id": "never-posted",
                                "idempotency_key": "LSbogus"})
    assert response.status_code == 422, response.text


def test_reverse_on_a_cross_tenant_document_is_404(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    alice, bob = two_tenants
    doc_id = alice.seed_postable_document()
    posted = alice.post_json(f"/api/writeback/documents/{doc_id}/post",
                             {"adapter": "export", "vendor_ref": "V-9"}).json()
    response = bob.post_json(f"/api/writeback/documents/{doc_id}/reverse",
                             {"adapter": "export", "remote_id": posted["remote_id"],
                              "idempotency_key": posted["idempotency_key"]})
    assert response.status_code == 404, response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_writeback.py -k reverse -v`
Expected: FAIL — 405 (the route does not exist yet).

- [ ] **Step 3: Write the minimal implementation**

Add to `ledger_sense/api/writeback.py`:

```python
class ReverseBody(BaseModel):
    adapter: AdapterName
    remote_id: StrictStr
    idempotency_key: StrictStr


class ReverseResponse(BaseModel):
    remote_id: str
    method: str


@router.post("/documents/{doc_id}/reverse")
def reverse(doc_id: str, body: ReverseBody, session: CurrentSession) -> ReverseResponse:
    with app_engine().connect() as conn:
        _get_document(doc_id, session, conn)
        approved_by = _approved_by(conn, session)

    audit = audit_log_from_environment()
    ledger = ledger_from_environment()
    adapter = AuditedAdapter(_inner_adapter(body.adapter, ledger), audit, approved_by)
    try:
        result = adapter.reverse(body.remote_id, body.idempotency_key)
    except ValueError as exc:
        # AuditedAdapter.reverse's own refusal: no audited write for this
        # remote id — nothing to reverse a record of.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ReverseResponse(remote_id=result.remote_id, method=result.method)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_writeback.py -v`
Expected: PASS — every test in the file.

- [ ] **Step 5: Run mypy/ruff and commit**

Run: `uv run mypy --strict ledger_sense/api/writeback.py && uv run ruff check ledger_sense/api/writeback.py`

```bash
git add ledger_sense/api/writeback.py tests/api/test_writeback.py
git commit -m "feat(api): writeback reverse route

POST /api/writeback/documents/{doc_id}/reverse, completing the three-route
writeback surface the Phase 6 spec defines."
```

---

## Task 12: Single-process constraint; methodology doc

Closes the second open Phase 5 exit item (Ruling 3): the single-process assumption the in-process `ConcurrentWriteInProgressError` guard depends on, pinned as a source-level fact rather than left as a comment. (The router registration originally planned for this task was moved forward into Task 8 — see that task's "Register the router now, not in Task 12" note. `ledger_sense/api/app.py` already registers `writeback_router`; this task does not touch it again.)

**Files:**
- Modify: `ledger_sense/__main__.py` (`WORKERS` constant + assertion)
- Modify: `docs/methodology.md` (document the constraint)
- Test: `tests/test_platform_skeleton.py` (new regression test for `WORKERS`)

**Interfaces:** None new — this task wires together interfaces already produced by Tasks 8-11.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_platform_skeleton.py`:

```python
def test_ledger_sense_runs_as_a_single_process() -> None:
    """Phase 6 Ruling 3: writeback/qbo.py's in-process ConcurrentWriteInProgressError
    guard (an in-memory, per-process set) only closes the concurrent-write
    race within one process. Pinning WORKERS as a source-level constant means
    raising it later is a deliberate, reviewed change — not silent drift from
    an unenforced comment."""
    from ledger_sense.__main__ import WORKERS
    assert WORKERS == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_platform_skeleton.py::test_ledger_sense_runs_as_a_single_process -v`
Expected: FAIL — `ImportError: cannot import name 'WORKERS'`.

- [ ] **Step 3: Write the minimal implementation**

In `ledger_sense/__main__.py`, add above `main()`:

```python
# Phase 6 Ruling 3: writeback/qbo.py's ConcurrentWriteInProgressError guard is
# an in-memory, per-process set — it closes the concurrent-write race
# (writeback/idempotency.py's module docstring) only within one process.
# Raising this requires a cross-process lock in the idempotency ledger first
# (see docs/methodology.md); until then this is asserted, not just commented.
WORKERS = 1
assert WORKERS == 1, (
    "raising WORKERS requires a cross-process lock in the idempotency "
    "ledger first — see docs/methodology.md's single-process constraint "
    "section before changing this.")
```

And change the `uvicorn.run` call:

```python
    uvicorn.run("ledger_sense.api.app:app", host="0.0.0.0", port=8000, workers=WORKERS)
```

In `docs/methodology.md`, append a new section:

```markdown
## Single-process constraint

`ledger_sense/__main__.py` runs uvicorn with a single worker (`WORKERS = 1`),
asserted at that call site. The writeback path's in-process
`ConcurrentWriteInProgressError` guard (`ledger_sense/writeback/qbo.py`) is
an in-memory, per-process set — it closes the concurrent-write race
described in `writeback/idempotency.py`'s module docstring only within one
process. Running more than one worker (or more than one container) would
silently reopen the duplicate-bill race `IdempotencyLedger`'s
`BEGIN IMMEDIATE` transaction alone cannot close. Raising the worker count
requires first replacing that in-process guard with a cross-process lock in
the idempotency ledger itself — deferred until the deployment topology
actually needs more than one process.
```

- [ ] **Step 4: Run tests to verify everything passes**

Run: `uv run pytest tests/test_platform_skeleton.py tests/api/ -v`
Expected: PASS — including `tests/api/test_route_registration.py::test_the_audit_walk_covers_every_served_route` and `test_every_protected_route_carries_the_guard_in_its_dependencies`, which now also cover the three new writeback routes automatically (they walk `app.routes`, not a hand-maintained list).

- [ ] **Step 5: Run mypy/ruff and commit**

Run: `uv run mypy --strict ledger_sense/__main__.py && uv run ruff check ledger_sense/__main__.py`

```bash
git add ledger_sense/__main__.py docs/methodology.md tests/test_platform_skeleton.py
git commit -m "feat: pin the single-process constraint

WORKERS = 1 in __main__.py, asserted at the uvicorn.run call site and
documented in docs/methodology.md — closing Phase 5's second open exit item.
(The writeback router itself was registered in Task 8, not here.)"
```

---

## Task 13: Extend the writeback import-boundary guard to `api/writeback.py`

**Files:**
- Modify: `tests/boundary/test_structure.py`

**Interfaces:** None — test-only.

- [ ] **Step 1: Write the failing test (by tightening an existing one)**

Modify `test_writeback_never_imports_the_document_pipeline_or_the_gateway` in `tests/boundary/test_structure.py`:

```python
def test_writeback_never_imports_the_document_pipeline_or_the_gateway() -> None:
    """Task 37 review (recommended hardening), extended for Phase 6:
    ledger_sense/writeback/'s provider-client exemption rests on writeback
    never seeing a raw document. ledger_sense/api/writeback.py inherits that
    same assumption — it reconstructs a WriteRequest from persisted claims
    (writeback/assemble.py), never from the pipeline directly — so it is
    checked by the same guard rather than a second, easily-forgotten copy."""
    offenders = []
    for path, tree in _sources():
        if not (PACKAGE / "writeback" in path.parents
                or path == PACKAGE / "api" / "writeback.py"):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            if any(name == forbidden or name.startswith(forbidden + ".")
                   for name in names for forbidden in WRITEBACK_FORBIDDEN_IMPORTS):
                offenders.append(f"{path}:{node.lineno}")
    assert offenders == [], (
        f"writeback (or its API router) imported the document pipeline or "
        f"the gateway, invalidating its provider-client exemption: {offenders}")
```

This is deliberately not "add a test that currently fails" in the RED sense — it is a guard the plan's own Tasks 6-11 must already satisfy (none of them import `extract`/`ingest`/`gateway`), so tightening it here is verification that the earlier tasks stayed inside the boundary, not new behavior to build toward.

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/boundary/test_structure.py::test_writeback_never_imports_the_document_pipeline_or_the_gateway -v`
Expected: PASS immediately — confirms Tasks 6-11 never reached into `extract`/`ingest`/`gateway`. If this fails, it means an earlier task's implementation imported one of those forbidden modules; fix that task's file before proceeding, per `superpowers:systematic-debugging` (the cause is a specific import statement, not ambiguous).

- [ ] **Step 3: Run the full suite once**

Run: `uv run pytest -v`
Expected: PASS — every test in the repository, confirming this plan's tasks compose cleanly with everything Phase 1-5 already built.

- [ ] **Step 4: Run mypy/ruff across the whole package**

Run: `uv run mypy --strict ledger_sense && uv run ruff check ledger_sense tests`

- [ ] **Step 5: Commit**

```bash
git add tests/boundary/test_structure.py
git commit -m "test(boundary): extend the writeback import guard to api/writeback.py

api/writeback.py inherits the same never-touches-a-raw-document assumption
writeback/ already relies on for its provider-client exemption — checked by
the same AST guard rather than a second copy that could silently drift."
```

---

## Self-review notes (from the writing-plans skill's own checklist)

**Spec coverage:** §1 (Task 6-7), §2 (Task 4), §3 (Task 9), §4 (Task 8-11), §5 (Task 10-11), §6 (Task 12), Ruling 5's vendor-conflict resolution (Task 2, 10), the extended import-boundary test (Task 13, per the spec's Testing bar). The spec's "Out of scope" items (vendor entity/merge, per-line account mapping, cross-process locking, QBO sandbox cassette recording) are correctly left untouched by every task above.

**Placeholder scan:** no task above says "add error handling" or "similar to Task N" without showing the code; every step has a real, pasteable code block.

**Type consistency:** `WriteRequest`/`LineItem`/`WriteResult`/`ReversalResult` are used with the exact field names `writeback/interface.py` already defines throughout; `AuditEntry.vendor_ref`/`vendor_invoice_number` (Task 2) match the names `_live_write_entries` (Task 8) and the conflict check (Task 10) read; `MissingClaimError.field` (Task 6) is the same attribute every `except MissingClaimError as exc: ... exc.field` call site (Task 8, 9) reads.
