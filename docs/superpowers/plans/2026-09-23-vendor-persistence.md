# Vendor Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the vendor a real identity — a corpus-generated name, a deterministic extraction claim, tenant-scoped SQL persistence, the already-written I2 fuzzy-match rule wired live into the run pipeline, a reviewer confirmation action distinct from accept/edit/reject, and a read-only vendor ledger screen — so Task 44's ledger screen has real data instead of a stub.

**Architecture:** `VendorLedger` (`ledger_sense/domain/vendor.py`) already holds every fraud-control invariant this needs (B5 hard-stop bank-detail change, B6 unrecoverable-merge confirmation, HMAC comparison) and stays exactly as it is — nothing here touches its mutating methods. What's missing is entirely around it: a fourth corpus/extraction header field (`vendor_name`, following `contract_id`'s exact pattern), three new tenant-scoped SQLite/Postgres tables with a single-door repository (`store/vendors.py`, mirroring `store/documents.py`), the I2 rule called read-only from `run/document.py`, a new `confirm-vendor` review action (`confirmed_by` comes from `Session.user_id`, never the request body), and a `GET /api/vendors/{id}/ledger` read model the frontend renders. Bank-detail history stays an honestly empty list — there is no payment-detail ingest path yet, and this plan does not add one; the UI states that plainly instead of hiding the section.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy Core (no ORM — this codebase does not use one), Alembic, Pydantic v2 (`strict=True, extra="forbid"`), Next.js 15 client components, Vitest + Testing Library, pytest + `hypothesis` where money is touched (none is, in this plan).

**Spec:** `../../../CLAUDE.md` (project root) — validation-layer, confidence-routing, and Never-rule sections apply throughout. No separate design doc exists beyond this plan; the architecture was worked out interactively against the live codebase (see `git log` on this branch's parent commit `168a52a` for the Phase 6 base) rather than written up first.

## Global Constraints

- `mypy --strict` and `ruff` clean on every task — no exceptions (CLAUDE.md Never #2's enforcement style applies project-wide).
- Every new/modified table carries `tenant_id TEXT NOT NULL REFERENCES tenants.id` — `tests/boundary/test_structure.py::test_no_table_escapes_the_tenancy_rule` iterates `auth.models.metadata.tables` generically and fails on any table missing it. Register new tables on that same `metadata` object (imported from `ledger_sense.auth.models`), never a second one.
- `confirmed_by` on every vendor mutation is `Session.user_id` — sourced from the sessions row via `require_session`, never accepted from a request body (CLAUDE.md Never #6: "surface, require human confirmation").
- `PaymentDetail.account_hmac` is never serialized into an API response. It is a fraud-detection secret, not display data.
- No AIA form artifact, no float in the money path (this plan touches no Money arithmetic — fuzzy-match scores stay `float` per the existing documented exception in `domain/vendor.py`), no hardcoded model names, no client document to a free-tier model (none of this plan touches the model lane).
- Every new writer of `vendors`/`vendor_aliases`/`payment_details` rows lives in exactly one module (`ledger_sense/store/vendors.py`) — enforced the same way `tests/boundary/test_structure.py` already enforces it for `documents.insert()`.
- Commit after every green test, per CLAUDE.md's TDD workflow (RED → GREEN → REFACTOR, no exceptions).

---

### Task 1: Corpus — a fourth document-header field

**Files:**
- Modify: `ledger_sense/corpus/layouts.py:78-82` (`DOC_HEADER_FIELDS`)
- Modify: `ledger_sense/corpus/generate.py:55` (trade pool, for the sibling vendor-name pool) and `ledger_sense/corpus/generate.py:110-133` (`_clean`), `ledger_sense/corpus/generate.py:189-198` (`header_values` in `_render`)
- Test: `tests/corpus/test_generate.py`

**Interfaces:**
- Consumes: nothing new — follows the existing `DOC_HEADER_FIELDS: tuple[tuple[str, str], ...]` pattern already used by `contract_id`/`application_no`/`period_to`.
- Produces: every corpus-generated PDF now carries a fourth header row labelled `"Contractor"`, rendered at `DOC_HEADER_VALUE_X_PT` on the row after `"Period Ending"`. `generate(seed, error, layout)` still returns `tuple[Contract, PayApplication, bytes]`; `PayApplication.vendor_name` (added in Task 2) is now populated on the returned `app`, and the rendered PDF's text layer carries the same string at a known label/value position for Task 3's extractor to find.

- [ ] **Step 1: Write the failing test — the corpus fixture renders a vendor name that is not a trade or a summary label**

```python
# tests/corpus/test_generate.py — add near the existing header-field tests
def test_the_document_header_carries_a_vendor_name() -> None:
    _, app, pdf = generate(seed=1, error="clean")
    assert app.vendor_name is not None
    assert app.vendor_name.strip() != ""
    # F-09's lesson, extended: the vendor name must not collide with any
    # continuation-grid header, summary label, or the other three header
    # labels, or a label-anchor test could bind to the wrong text.
    words = {w.text for w in extract_words(pdf)}
    assert "Contractor" in words
```

(Add the necessary import: `from ledger_sense.extract.textlayer import extract_words` at the top of the test file if not already present — check first with `grep -n "^import\|^from" tests/corpus/test_generate.py`.)

- [ ] **Step 2: Run it to confirm it fails**

Run: `uv run pytest tests/corpus/test_generate.py::test_the_document_header_carries_a_vendor_name -v`
Expected: FAIL — `AttributeError: 'PayApplication' object has no attribute 'vendor_name'` (this also proves Task 2 is a real prerequisite; if this task is executed before Task 2 lands, do Task 2's model change first, then return here).

- [ ] **Step 3: Add the vendor-name pool and wire it through**

In `ledger_sense/corpus/generate.py`, near `_TRADES` (line 55), add a sibling pool:

```python
# A closed pool, same discipline as _TRADES: deterministic per seed via
# rng.choice, never a fabricated one-off that could collide with a
# hand-typed test fixture name.
_VENDOR_NAMES = [
    "Acme Steel Company",
    "Zenith Glazing",
    "Harborview Concrete",
    "Meridian Electrical",
    "Cascade Roofing",
    "Ironclad Mechanical",
]
```

In `_clean(seed)` (line 110), after `contract = Contract(...)`:

```python
    app = PayApplication(
        contract_id=contract.id, application_no=rng.randint(1, 12),
        period_to=date(2026, 8, 31), lines=lines, change_orders_net=money("0.00"),
        retainage_total=money("0.00"), previous_payments=money("0.00"),
        total_completed_and_stored=grand_total,
        vendor_name=rng.choice(_VENDOR_NAMES))
```

In `ledger_sense/corpus/layouts.py`, extend `DOC_HEADER_FIELDS`:

```python
DOC_HEADER_FIELDS: tuple[tuple[str, str], ...] = (
    ("Contract Number",     "contract_id"),
    ("Application Number",  "application_no"),
    ("Period Ending",       "period_to"),
    ("Contractor",          "vendor_name"),
)
```

In `ledger_sense/corpus/generate.py`'s `_render` (line 189), extend `header_values`:

```python
    header_values: dict[str, str] = {
        "contract_id": contract.id,
        "application_no": str(app.application_no),
        "period_to": str(app.period_to),
        "vendor_name": app.vendor_name or "",
    }
```

(The `or ""` is defensive only — Task 2 makes `vendor_name` required with no default for corpus-generated documents; `_clean` above always sets it. A real uploaded document with no vendor name renders nothing, which is not this generator's concern.)

- [ ] **Step 4: Run it to confirm it passes**

Run: `uv run pytest tests/corpus/test_generate.py -v`
Expected: PASS, all tests in the file, not just the new one — confirms the fourth header row didn't collide with the grid header, the summary block, or the other three header labels (F-09's check, done empirically by the existing tests in this file that already scan for label collisions).

- [ ] **Step 5: Run the no-floats and AST guard suites (cheap, catches accidental collateral damage early)**

Run: `uv run pytest tests/test_no_floats.py tests/test_ast_guard.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add ledger_sense/corpus/layouts.py ledger_sense/corpus/generate.py tests/corpus/test_generate.py
git commit -m "feat(corpus): render a fourth document-header field, vendor name"
```

---

### Task 2: Domain model — `PayApplication.vendor_name`

**Files:**
- Modify: `ledger_sense/domain/models.py:136-151` (`PayApplication`)
- Test: `tests/domain/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `PayApplication.vendor_name: str | None = None` — a fourth OCR-sourced, possibly-omitted header scalar, same discipline as `contract_id`/`application_no`/`period_to` (F-15: `None` means "not extracted", never a guessed value). Task 1 and Task 3 both depend on this field existing.

- [ ] **Step 1: Write the failing test**

```python
# tests/domain/test_models.py
def test_vendor_name_defaults_to_none_and_is_never_guessed() -> None:
    app = PayApplication(
        change_orders_net=money("0.00"), retainage_total=money("0.00"),
        previous_payments=money("0.00"), total_completed_and_stored=money("0.00"))
    assert app.vendor_name is None


def test_vendor_name_accepts_an_extracted_string() -> None:
    app = PayApplication(
        vendor_name="Acme Steel Company",
        change_orders_net=money("0.00"), retainage_total=money("0.00"),
        previous_payments=money("0.00"), total_completed_and_stored=money("0.00"))
    assert app.vendor_name == "Acme Steel Company"
```

(Check the file's existing imports first — `money` and `PayApplication` are almost certainly already imported for the surrounding tests; add only what's missing.)

- [ ] **Step 2: Run to confirm it fails**

Run: `uv run pytest tests/domain/test_models.py -k vendor_name -v`
Expected: FAIL — `extra="forbid"` on `_Base` means passing `vendor_name=` to a model that doesn't declare it raises a Pydantic `ValidationError`, and the no-arg case fails with `AttributeError` on the `.vendor_name` access.

- [ ] **Step 3: Add the field**

In `ledger_sense/domain/models.py`, in `PayApplication` (after `period_to`, before `lines`):

```python
class PayApplication(_Base):
    # None means "not extracted", recorded in provenance["_unextractable"].
    # Defaulting these to "", 0 and date.today() was F-15: a guessed period_to
    # makes C12 flag every waiver on a document processed a month late.
    contract_id: str | None = None
    application_no: int | None = None
    period_to: date | None = None
    vendor_name: str | None = None
    lines: list[ContinuationLine] = Field(default_factory=list)
```

- [ ] **Step 4: Run to confirm it passes**

Run: `uv run pytest tests/domain/test_models.py -v`
Expected: PASS, whole file (confirms `model_rebuild()` at the bottom of the module still succeeds and nothing else in the file depended on `PayApplication`'s exact field set).

- [ ] **Step 5: Run mypy strict on the touched file**

Run: `uv run mypy --strict ledger_sense/domain/models.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add ledger_sense/domain/models.py tests/domain/test_models.py
git commit -m "feat(domain): PayApplication.vendor_name, extracted-or-omitted like the other header scalars"
```

---

### Task 3: Extraction — a `vendor_name` claim

**Files:**
- Modify: `ledger_sense/extract/assemble.py:47-52` (`_DOCUMENT_HEADER_LABELS`), `ledger_sense/extract/assemble.py:112-123` (`extract_document_header`'s `predicates`/`parsers`), `ledger_sense/extract/assemble.py:294-325` (the header-hit loop and `PayApplication(...)` construction)
- Test: `tests/extract/test_assemble.py`

**Interfaces:**
- Consumes: `PayApplication.vendor_name` (Task 2).
- Produces: `assemble_pay_application(pdf, source_path)` now populates `app.vendor_name` and `app.provenance["vendor_name"]` (a `Claim[str]` with `method=DETERMINISTIC`, `match_quality`, and a `FileSource` bbox) whenever the `"Contractor"` label is found exactly once on the page with a non-empty value after it — omitted (recorded in `provenance["_unextractable"]`) otherwise, never guessed. This is what Task 6's rule wiring and Task 7's confirm-vendor route both read from `claims` by `field == "vendor_name"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/extract/test_assemble.py — add near the existing header-field tests
# (contract_id/application_no/period_to already have equivalents; mirror them)

def test_vendor_name_is_extracted_with_a_claim() -> None:
    _, app, pdf = generate(seed=1, error="clean")
    result = assemble_pay_application(pdf, "test-source")
    assert result.vendor_name == app.vendor_name
    claim = result.provenance["vendor_name"]
    assert claim.value == app.vendor_name
    assert claim.method == Method.DETERMINISTIC
    assert claim.source.bbox is not None


def test_a_missing_vendor_name_label_is_omitted_not_guessed() -> None:
    pdf = generate(seed=1, error="clean")[2]
    result = assemble_pay_application(pdf, "test-source", _drop_labels=frozenset({"Contractor"}))
    assert result.vendor_name is None
    unextractable_fields = {e.field for e in result.provenance["_unextractable"]}
    assert "vendor_name" in unextractable_fields
```

Check the file's top imports first (`grep -n "^from\|^import" tests/extract/test_assemble.py`) — `generate`, `assemble_pay_application`, and `Method` are almost certainly already imported for the sibling `contract_id` tests; reuse those exact import lines rather than guessing paths.

- [ ] **Step 2: Run to confirm they fail**

Run: `uv run pytest tests/extract/test_assemble.py -k vendor_name -v`
Expected: FAIL — `result.vendor_name` doesn't exist as an extracted value yet (it stays `None` from Task 2's default, so the first test's `claim = result.provenance["vendor_name"]` raises `KeyError`).

- [ ] **Step 3: Wire the fourth header field through extraction**

In `ledger_sense/extract/assemble.py`, extend `_DOCUMENT_HEADER_LABELS` (line 49):

```python
_DOCUMENT_HEADER_LABELS: dict[str, str] = {
    "Contract Number": "contract_id",
    "Application Number": "application_no",
    "Period Ending": "period_to",
    "Contractor": "vendor_name",
}
```

In `extract_document_header` (line 112), extend both dicts — `vendor_name` follows exactly `contract_id`'s predicate (any non-empty text) and parser (`str`):

```python
    predicates: dict[str, Callable[[Word], bool]] = {
        "contract_id": lambda w: bool(w.text.strip()),
        "application_no": lambda w: bool(_INTEGER_TOKEN.fullmatch(w.text)),
        "period_to": lambda w: bool(_DATE_TOKEN.fullmatch(w.text)),
        "vendor_name": lambda w: bool(w.text.strip()),
    }
    parsers: dict[str, Callable[[str], object]] = {
        "contract_id": str,
        "application_no": int,
        "period_to": lambda text: date.fromisoformat(text),
        "vendor_name": str,
    }
```

In `assemble_pay_application` (around line 300), extend the header-field loop and the `assert` block:

```python
    header = extract_document_header(words)
    contract_id = header["contract_id"].value if "contract_id" in header else None
    application_no = header["application_no"].value if "application_no" in header else None
    period_to = header["period_to"].value if "period_to" in header else None
    vendor_name = header["vendor_name"].value if "vendor_name" in header else None
    for field_name in ("contract_id", "application_no", "period_to", "vendor_name"):
        header_hit = header.get(field_name)
        if header_hit is None:
            unextractable.append(UnextractableField(field_name, "not found on page"))
        else:
            provenance[field_name] = _claim(
                source_path, header_hit.page, header_hit.bbox, header_hit.value,
                match_quality=match_quality(label_exact=True, value_unique=True,
                                            column_unambiguous=True, profile_hit=False))
    assert contract_id is None or isinstance(contract_id, str)
    assert application_no is None or isinstance(application_no, int)
    assert period_to is None or isinstance(period_to, date)
    assert vendor_name is None or isinstance(vendor_name, str)
```

And pass it into the returned `PayApplication`:

```python
    return PayApplication(
        contract_id=contract_id,
        application_no=application_no,
        period_to=period_to,
        vendor_name=vendor_name,
        lines=lines, provenance=provenance,
        change_orders_net=totals["change_orders_net"],
        retainage_total=totals["retainage_total"],
        previous_payments=totals["previous_payments"],
        total_completed_and_stored=totals["total_completed_and_stored"])
```

Note: `assemble_pay_application`'s `_drop_labels: frozenset[str]` parameter (used by the second test above) already exists — it filters words at the top of the function (`words: list[Word] = [w for w in extract_words(pdf) if w.text not in _drop_labels]`), so dropping `"Contractor"` before extraction is already supported with no further change.

- [ ] **Step 4: Run to confirm both pass, then the whole file**

Run: `uv run pytest tests/extract/test_assemble.py -v`
Expected: PASS — all tests, confirming the fourth field didn't disturb the existing three or the line/summary extraction.

- [ ] **Step 5: Run mypy strict and the AST/no-floats guards**

Run: `uv run mypy --strict ledger_sense/extract/assemble.py && uv run pytest tests/test_no_floats.py tests/test_ast_guard.py -q`
Expected: no errors, PASS

- [ ] **Step 6: Commit**

```bash
git add ledger_sense/extract/assemble.py tests/extract/test_assemble.py
git commit -m "feat(extract): a vendor_name claim, same anchor-plus-geometry discipline as contract_id"
```

---

### Task 4: Persistence schema — three tables, one new column, migration 0007

**Security-sensitive — this task creates the `payment_details` table.** Per CLAUDE.md's skill map ("`Sensitivity` gateway, redaction pass, OAuth token storage, `PaymentDetail`  → `security-review`. Run before anything else touches it"), invoke the `security-review` skill against the diff from this task before marking it complete, in addition to the standard review this plan's workflow already runs. The single check that matters most: no column here can hold a full account/routing number, and `account_hmac` is the only comparable value derived from one.

**Files:**
- Modify: `ledger_sense/store/retention.py` (add `vendors`, `vendor_aliases`, `payment_details` tables; add `vendor_id` column to `documents`)
- Create: `migrations/versions/0007_vendor_persistence.py`
- Test: `tests/store/test_migrations.py` (extend, following the `test_0005_adds_the_run_columns_the_findings_table_and_named_checks` pattern)

**Interfaces:**
- Consumes: nothing new.
- Produces: three `sa.Table` objects — `vendors`, `vendor_aliases`, `payment_details` — importable from `ledger_sense.store.retention`, registered on `auth.models.metadata`. `documents.c.vendor_id` (nullable `TEXT`, FK to `vendors.id`). Task 5's repository is the only code permitted to `insert()`/`update()`/`delete()` against the three new tables.

**Table shapes** (columns only — types/nullability mirror the domain Pydantic fields in `ledger_sense/domain/vendor.py` exactly):

```
vendors:          id TEXT PK, tenant_id TEXT NOT NULL FK->tenants.id, name TEXT NOT NULL
vendor_aliases:    id TEXT PK, tenant_id TEXT NOT NULL FK->tenants.id, vendor_id TEXT NOT NULL FK->vendors.id,
                   name TEXT NOT NULL, first_seen DATETIME NOT NULL, source_document TEXT NOT NULL,
                   confirmed_by TEXT NOT NULL, merged_from TEXT NULL
payment_details:   id TEXT PK, tenant_id TEXT NOT NULL FK->tenants.id, vendor_id TEXT NOT NULL FK->vendors.id,
                   account_last4 TEXT NOT NULL, routing_last4 TEXT NOT NULL, account_hmac TEXT NOT NULL,
                   observed_at DATETIME NOT NULL, source_document TEXT NOT NULL,
                   confirmed_by TEXT NULL, confirmed_at DATETIME NULL
documents:         + vendor_id TEXT NULL FK->vendors.id
```

- [ ] **Step 1: Write the failing migration test**

```python
# tests/store/test_migrations.py — append
def test_0007_adds_vendor_tables_and_the_documents_vendor_id_column(
    tmp_path: pathlib.Path,
) -> None:
    database = (tmp_path / "m.db").as_posix()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("LEDGER_SENSE_DB_URL", f"sqlite:///{database}")
        store_session.reset_app_engine()
        config = _alembic_config()
        command.upgrade(config, "head")
        inspector = sa.inspect(store_session.app_engine())

        assert {"vendors", "vendor_aliases", "payment_details"} <= set(
            inspector.get_table_names())
        vendor_cols = {c["name"] for c in inspector.get_columns("vendors")}
        assert vendor_cols == {"id", "tenant_id", "name"}
        alias_cols = {c["name"] for c in inspector.get_columns("vendor_aliases")}
        assert alias_cols == {"id", "tenant_id", "vendor_id", "name", "first_seen",
                              "source_document", "confirmed_by", "merged_from"}
        detail_cols = {c["name"] for c in inspector.get_columns("payment_details")}
        assert detail_cols == {"id", "tenant_id", "vendor_id", "account_last4",
                               "routing_last4", "account_hmac", "observed_at",
                               "source_document", "confirmed_by", "confirmed_at"}
        doc_cols = {c["name"] for c in inspector.get_columns("documents")}
        assert "vendor_id" in doc_cols

        # Downgrade one step removes everything 0007 added and nothing else.
        command.downgrade(config, "0006_review_reject_reason")
        inspector = sa.inspect(store_session.app_engine())
        assert "vendors" not in inspector.get_table_names()
        assert "vendor_aliases" not in inspector.get_table_names()
        assert "payment_details" not in inspector.get_table_names()
        assert "vendor_id" not in {c["name"] for c in inspector.get_columns("documents")}
    store_session.reset_app_engine()
```

- [ ] **Step 2: Run to confirm it fails**

Run: `uv run pytest tests/store/test_migrations.py::test_0007_adds_vendor_tables_and_the_documents_vendor_id_column -v`
Expected: FAIL — revision `"0007_vendor_persistence"` does not exist / tables not found.

- [ ] **Step 3: Add the tables to `store/retention.py`**

Near the existing `documents`/`claims`/`findings` table definitions (after `retention_audit_log`, before the module's functions), add:

```python
vendors = sa.Table(
    "vendors", metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("tenant_id", sa.Text, sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("name", sa.Text, nullable=False),
)

vendor_aliases = sa.Table(
    "vendor_aliases", metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("tenant_id", sa.Text, sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("vendor_id", sa.Text, sa.ForeignKey("vendors.id"), nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
    sa.Column("source_document", sa.Text, nullable=False),
    sa.Column("confirmed_by", sa.Text, nullable=False),
    sa.Column("merged_from", sa.Text, nullable=True),
)

payment_details = sa.Table(
    "payment_details", metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("tenant_id", sa.Text, sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("vendor_id", sa.Text, sa.ForeignKey("vendors.id"), nullable=False),
    sa.Column("account_last4", sa.Text, nullable=False),
    sa.Column("routing_last4", sa.Text, nullable=False),
    # HMAC of the full account+routing under LEDGER_SENSE_PAYMENT_HMAC_KEY
    # (domain/vendor.py:payment_hmac) — never the plaintext account number.
    sa.Column("account_hmac", sa.Text, nullable=False),
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("source_document", sa.Text, nullable=False),
    sa.Column("confirmed_by", sa.Text, nullable=True),
    sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
)
```

Add `vendors`, `vendor_aliases`, `payment_details` to this module's exports if it has an `__all__` — check with `grep -n "__all__" ledger_sense/store/retention.py`; if present, add the three names; if absent, no change needed.

`documents`'s existing `sa.Table` definition (line 54) needs one more column added directly to its definition:

```python
sa.Column("vendor_id", sa.Text, sa.ForeignKey("vendors.id"), nullable=True),
```

(Add it near the other nullable, run-produced columns like `doc_type`/`run_note` — check exact current column order with `sed -n '54,93p' ledger_sense/store/retention.py` before editing, and append after the last existing column rather than reordering.)

- [ ] **Step 4: Write migration `0007_vendor_persistence.py`**

```python
"""vendor persistence: vendors, vendor_aliases, payment_details, documents.vendor_id

Revision ID: 0007_vendor_persistence
Revises: 0006_review_reject_reason
Create Date: 2026-09-23

I2 (vendor resolution) and I3 (bank-detail hard stop) have been written and
tested since the initial vendor module but never had a table to persist
against — VendorLedger lived only in-memory, per-process, for the duration
of a single test. This gives the ledger real storage: one row per canonical
vendor, one per confirmed alias (provenance: who confirmed it and from what
document, per B6), and one per payment-detail observation (never the full
account number — account_hmac only, per B5's "only last four digits are
ever stored" rule in domain/vendor.py).

documents.vendor_id is nullable and set only when a reviewer confirms a
vendor match via the review-queue's confirm-vendor action — never inferred,
never defaulted.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_vendor_persistence"
down_revision: str | None = "0006_review_reject_reason"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vendors",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
    )
    op.create_table(
        "vendor_aliases",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("vendor_id", sa.Text(), sa.ForeignKey("vendors.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_document", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.Text(), nullable=False),
        sa.Column("merged_from", sa.Text(), nullable=True),
    )
    op.create_table(
        "payment_details",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("vendor_id", sa.Text(), sa.ForeignKey("vendors.id"), nullable=False),
        sa.Column("account_last4", sa.Text(), nullable=False),
        sa.Column("routing_last4", sa.Text(), nullable=False),
        sa.Column("account_hmac", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_document", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.Text(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("documents") as batch:
        batch.add_column(
            sa.Column("vendor_id", sa.Text(), sa.ForeignKey("vendors.id"), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("vendor_id")
    op.drop_table("payment_details")
    op.drop_table("vendor_aliases")
    op.drop_table("vendors")
```

- [ ] **Step 5: Run to confirm the new migration test passes**

Run: `uv run pytest tests/store/test_migrations.py -v`
Expected: PASS, whole file — including `test_the_migration_builds_the_schema_the_code_queries` and `test_no_table_escapes_the_tenancy_rule`, both of which iterate `metadata.tables` generically and will now cover the three new tables automatically.

- [ ] **Step 6: Run the full store + boundary suites, plus mypy strict**

Run: `uv run pytest tests/store tests/boundary -q && uv run mypy --strict ledger_sense/store/retention.py`
Expected: PASS, no errors

- [ ] **Step 7: Invoke `security-review` on the diff (see the note at the top of this task) before continuing**

- [ ] **Step 8: Commit**

```bash
git add ledger_sense/store/retention.py migrations/versions/0007_vendor_persistence.py tests/store/test_migrations.py
git commit -m "feat(store): vendors/vendor_aliases/payment_details tables, documents.vendor_id (migration 0007)"
```

---

### Task 5: Persistence repository — `store/vendors.py`, the single door

**Files:**
- Modify: `ledger_sense/domain/vendor.py` (two small additive methods on `VendorLedger`)
- Create: `ledger_sense/store/vendors.py`
- Modify: `tests/boundary/test_structure.py` (generalize the single-door AST guard)
- Test: `tests/store/test_vendors.py`

**Interfaces:**
- Consumes: `vendors`/`vendor_aliases`/`payment_details` tables (Task 4); `VendorLedger`, `CanonicalVendor`, `VendorAlias`, `PaymentDetail` from `ledger_sense.domain.vendor`.
- Produces: `load_vendor_ledger(conn: Connection, tenant_id: str) -> VendorLedger` and `save_vendor_ledger(conn: Connection, tenant_id: str, ledger: VendorLedger) -> None`. Task 6 (read-only `load_vendor_ledger` call), Task 7 (`load_vendor_ledger` + `save_vendor_ledger`), and Task 8 (`load_vendor_ledger`) all depend on these two exact signatures.

**Design note on the snapshot-replace strategy:** `VendorLedger` keeps no dirty-tracking (by design — see its docstring), and at this project's demo scale a tenant has at most a handful of vendors. `save_vendor_ledger` therefore deletes every one of the tenant's rows across all three tables and reinserts the ledger's current in-memory state wholesale, the same "replace in place" idiom `run/document.py::_write` already uses for `claims`/`findings`. This is simpler and harder to get wrong than incremental diffing, and safe here because nothing outside a single request holds a `VendorLedger` across two `save_vendor_ledger` calls.

- [ ] **Step 1: Write the failing repository tests**

```python
# tests/store/test_vendors.py
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from ledger_sense.domain.vendor import PaymentDetail, VendorLedger, payment_hmac
from ledger_sense.store import session as store_session
from ledger_sense.store.retention import vendors
from ledger_sense.store.vendors import load_vendor_ledger, save_vendor_ledger
from tests.store.conftest import SeededDocuments

OBSERVED_AT = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)


def test_an_empty_tenant_round_trips_to_an_empty_ledger(
    seeded_documents: SeededDocuments,
) -> None:
    with store_session.app_engine().connect() as conn:
        ledger = load_vendor_ledger(conn, seeded_documents.tenant_id)
    assert ledger.get("nope") is None


def test_a_vendor_with_an_alias_and_a_payment_detail_round_trips(
    seeded_documents: SeededDocuments, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEDGER_SENSE_PAYMENT_HMAC_KEY", "test-key")
    ledger = VendorLedger()
    vendor_id = ledger.create("Acme Steel Company")
    ledger.confirm_alias(vendor_id, "Acme Steel Co.", source_document="doc-1",
                         confirmed_by="reviewer-1")
    ledger.record_payment_detail(
        vendor_id,
        PaymentDetail(account_last4="1234", routing_last4="5678",
                      account_hmac=payment_hmac("00001234", "00005678"),
                      observed_at=OBSERVED_AT, source_document="doc-1"),
        confirmed_by="")

    with store_session.app_engine().begin() as conn:
        save_vendor_ledger(conn, seeded_documents.tenant_id, ledger)
    with store_session.app_engine().connect() as conn:
        reloaded = load_vendor_ledger(conn, seeded_documents.tenant_id)

    vendor = reloaded.get(vendor_id)
    assert vendor is not None
    assert vendor.name == "Acme Steel Company"
    assert [a.name for a in vendor.aliases] == ["Acme Steel Co."]
    assert vendor.aliases[0].confirmed_by == "reviewer-1"
    assert len(vendor.payment_history) == 1
    assert vendor.payment_history[0].account_last4 == "1234"


def test_another_tenants_vendors_are_invisible(
    seeded_documents: SeededDocuments,
) -> None:
    ledger = VendorLedger()
    ledger.create("Acme Steel Company")
    with store_session.app_engine().begin() as conn:
        save_vendor_ledger(conn, seeded_documents.tenant_id, ledger)

    with store_session.app_engine().connect() as conn:
        other_tenants_ledger = load_vendor_ledger(conn, "someone-else")
        # sanity: the row does exist, just not for "someone-else"
        assert conn.execute(sa.select(vendors)).all() != []
    assert other_tenants_ledger.get("nope") is None
    assert other_tenants_ledger.all_vendors() == []


def test_resaving_replaces_rather_than_accumulates(
    seeded_documents: SeededDocuments,
) -> None:
    ledger = VendorLedger()
    vendor_id = ledger.create("Acme Steel Company")
    with store_session.app_engine().begin() as conn:
        save_vendor_ledger(conn, seeded_documents.tenant_id, ledger)
        save_vendor_ledger(conn, seeded_documents.tenant_id, ledger)
    with store_session.app_engine().connect() as conn:
        reloaded = load_vendor_ledger(conn, seeded_documents.tenant_id)
    assert len(reloaded.all_vendors()) == 1
    assert reloaded.get(vendor_id) is not None
```

- [ ] **Step 2: Run to confirm they fail**

Run: `uv run pytest tests/store/test_vendors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.store.vendors'`

- [ ] **Step 3: Add the two small hydration methods to `VendorLedger`**

In `ledger_sense/domain/vendor.py`, in the `VendorLedger` class, right after `__init__`:

```python
    def all_vendors(self) -> list[CanonicalVendor]:
        """Every canonical vendor this ledger currently holds — the whole
        state a repository snapshot-replace needs to persist. Read-only:
        callers get the live CanonicalVendor objects, not copies, matching
        `get()`'s existing behaviour."""
        return list(self._vendors.values())

    @classmethod
    def from_vendors(cls, vendors: list[CanonicalVendor]) -> "VendorLedger":
        """Rehydrate a ledger from a repository read. The inverse of
        `all_vendors()` — round-trips exactly, including each vendor's
        aliases and payment_history, since CanonicalVendor already carries
        both."""
        ledger = cls()
        ledger._vendors = {v.id: v for v in vendors}
        return ledger
```

Neither touches an existing method — `create`, `resolve`, `confirm_alias`, `merge`, `unmerge`, `record_payment_detail`, `seen_before` are all unchanged.

- [ ] **Step 4: Write `ledger_sense/store/vendors.py`**

```python
"""The vendors/vendor_aliases/payment_details tables' one writer and one
reader (§4, same discipline as store/documents.py).

`load_vendor_ledger` hydrates every vendor a tenant has ever recorded into an
in-memory VendorLedger; `save_vendor_ledger` replaces that tenant's rows
across all three tables wholesale. VendorLedger keeps no dirty-tracking of
its own (domain/vendor.py's docstring) and a tenant's vendor count is small
at this project's scale, so a snapshot replace is simpler — and harder to
get subtly wrong — than incremental diffing against three tables at once.

tests/boundary/test_structure.py enforces that vendors.insert()/
vendor_aliases.insert()/payment_details.insert() appear nowhere else under
ledger_sense/ — this is the only door.
"""

from __future__ import annotations

from collections import defaultdict

import sqlalchemy as sa
from sqlalchemy import Connection

from ledger_sense.domain.vendor import CanonicalVendor, PaymentDetail, VendorAlias, VendorLedger
from ledger_sense.store.retention import payment_details, vendor_aliases, vendors

__all__ = ["load_vendor_ledger", "save_vendor_ledger"]


def load_vendor_ledger(conn: Connection, tenant_id: str) -> VendorLedger:
    vendor_rows = conn.execute(
        sa.select(vendors).where(vendors.c.tenant_id == tenant_id)).all()

    aliases_by_vendor: dict[str, list[VendorAlias]] = defaultdict(list)
    alias_rows = conn.execute(
        sa.select(vendor_aliases)
        .where(vendor_aliases.c.tenant_id == tenant_id)
        .order_by(vendor_aliases.c.first_seen)
    ).all()
    for row in alias_rows:
        aliases_by_vendor[str(row.vendor_id)].append(VendorAlias(
            id=str(row.id), name=row.name, first_seen=row.first_seen,
            source_document=row.source_document, confirmed_by=row.confirmed_by,
            merged_from=row.merged_from))

    details_by_vendor: dict[str, list[PaymentDetail]] = defaultdict(list)
    detail_rows = conn.execute(
        sa.select(payment_details)
        .where(payment_details.c.tenant_id == tenant_id)
        .order_by(payment_details.c.observed_at)
    ).all()
    for row in detail_rows:
        details_by_vendor[str(row.vendor_id)].append(PaymentDetail(
            id=str(row.id), account_last4=row.account_last4, routing_last4=row.routing_last4,
            account_hmac=row.account_hmac, observed_at=row.observed_at,
            source_document=row.source_document, confirmed_by=row.confirmed_by,
            confirmed_at=row.confirmed_at))

    canon = [
        CanonicalVendor(
            id=str(row.id), name=row.name,
            aliases=aliases_by_vendor[str(row.id)],
            payment_history=details_by_vendor[str(row.id)])
        for row in vendor_rows
    ]
    return VendorLedger.from_vendors(canon)


def save_vendor_ledger(conn: Connection, tenant_id: str, ledger: VendorLedger) -> None:
    conn.execute(payment_details.delete().where(payment_details.c.tenant_id == tenant_id))
    conn.execute(vendor_aliases.delete().where(vendor_aliases.c.tenant_id == tenant_id))
    conn.execute(vendors.delete().where(vendors.c.tenant_id == tenant_id))

    canon = ledger.all_vendors()
    if not canon:
        return

    conn.execute(vendors.insert(), [
        {"id": v.id, "tenant_id": tenant_id, "name": v.name} for v in canon])

    alias_rows = [
        {"id": a.id, "tenant_id": tenant_id, "vendor_id": v.id, "name": a.name,
         "first_seen": a.first_seen, "source_document": a.source_document,
         "confirmed_by": a.confirmed_by, "merged_from": a.merged_from}
        for v in canon for a in v.aliases]
    if alias_rows:
        conn.execute(vendor_aliases.insert(), alias_rows)

    detail_rows = [
        {"id": d.id, "tenant_id": tenant_id, "vendor_id": v.id,
         "account_last4": d.account_last4, "routing_last4": d.routing_last4,
         "account_hmac": d.account_hmac, "observed_at": d.observed_at,
         "source_document": d.source_document, "confirmed_by": d.confirmed_by,
         "confirmed_at": d.confirmed_at}
        for v in canon for d in v.payment_history]
    if detail_rows:
        conn.execute(payment_details.insert(), detail_rows)
```

- [ ] **Step 5: Run to confirm the repository tests pass**

Run: `uv run pytest tests/store/test_vendors.py -v`
Expected: PASS

- [ ] **Step 6: Extend the single-door boundary guard**

Read `tests/boundary/test_structure.py:45-121` first (the existing `documents.insert()` guard) to match its exact AST-walking helper functions. Generalize or duplicate the pattern for the three new tables — the simplest correct approach is a second, near-identical test function rather than trying to parametrize the existing one if its helpers are hardcoded to `documents`:

```python
# tests/boundary/test_structure.py — append, mirroring
# test_documents_insert_is_called_only_by_store_document immediately above

def _vendor_table_insert_calls(tree: ast.AST) -> list[ast.Call]:
    """Matches `vendors.insert(...)`, `vendor_aliases.insert(...)`,
    `payment_details.insert(...)`, with or without an import alias or an
    attribute-chain prefix — same shape as _documents_insert_calls above,
    generalized to three table names instead of one."""
    names = {"vendors", "vendor_aliases", "payment_details"}
    calls = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "insert"):
            target = node.func.value
            if isinstance(target, ast.Name) and target.id in names:
                calls.append(node)
            elif isinstance(target, ast.Attribute) and target.attr in names:
                calls.append(node)
    return calls


def test_vendor_table_inserts_are_called_only_by_store_vendors() -> None:
    offenders = []
    for path in (path for path in PACKAGE.rglob("*.py")
                 if path != PACKAGE / "store" / "vendors.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if _vendor_table_insert_calls(tree):
            offenders.append(str(path))
    assert offenders == [], f"vendor table insert outside store/vendors.py: {offenders}"


def test_the_store_does_call_vendor_table_inserts() -> None:
    site = PACKAGE / "store" / "vendors.py"
    tree = ast.parse(site.read_text(encoding="utf-8"))
    assert _vendor_table_insert_calls(tree), f"{site} no longer calls insert() on a vendor table"
```

(`PACKAGE` and `ast` are already imported at the top of this file for the existing `documents` guard — reuse them, don't re-import.)

- [ ] **Step 7: Run the full boundary suite**

Run: `uv run pytest tests/boundary -v`
Expected: PASS, including both new tests and every pre-existing one (confirms the generalization didn't accidentally start matching `documents.insert()` too, or stop matching it).

- [ ] **Step 8: Run mypy strict on both new/modified modules**

Run: `uv run mypy --strict ledger_sense/domain/vendor.py ledger_sense/store/vendors.py`
Expected: no errors

- [ ] **Step 9: Invoke `security-review` on this task's diff too (it touches `PaymentDetail` persistence directly)**

- [ ] **Step 10: Commit**

```bash
git add ledger_sense/domain/vendor.py ledger_sense/store/vendors.py tests/store/test_vendors.py tests/boundary/test_structure.py
git commit -m "feat(store): single-door VendorLedger repository, load_vendor_ledger/save_vendor_ledger"
```

---

### Task 6: Rule wiring — I2 runs live, read-only, inside `run_document`

**Files:**
- Modify: `ledger_sense/rules/integrity/vendors.py` (one-line field-name fix — see below)
- Modify: `tests/rules/integrity/test_vendors.py` (pin the corrected field name)
- Modify: `ledger_sense/run/document.py` (`_execute`, a new `_vendor_rows` helper)
- Test: `tests/run/test_document.py`

**Interfaces:**
- Consumes: `load_vendor_ledger` (Task 5, read-only — `run_document` never calls `save_vendor_ledger`; `resolve()` is a pure read, so nothing here mutates the ledger), `check_vendor_resolution` (already exists, unused until now), `app.vendor_name` (Task 3).
- Produces: a document whose extracted `vendor_name` doesn't cleanly resolve against the tenant's existing vendors now gets an `I2` row in `findings`, with the same geometry (`page`/`x0`/`top`/`x1`/`bottom`) as its `vendor_name` claim — exactly the pattern `_rule_rows` already uses via `boxes.get(finding.field)`. A document with `vendor_name is None` (label not found — Task 3's omission path) produces no I2 finding; that gap is already reported as an `X0` finding by the existing `_x0_rows` path, and I2 has nothing to resolve against.

**A pre-existing naming bug to fix first:** `check_vendor_resolution` in `ledger_sense/rules/integrity/vendors.py` currently builds its `Finding` with `field="vendor.name"` (a dot-separated pseudo-path that matches no real claim field). Every other field this codebase's findings reference is a bare claim field name (`"contract_id"`, `"line[010].description"`, etc.) so that `_value_type_for`/`_match_quality_for`-style lookups by `(document_id, field)` work. Task 3 produces a claim at `field == "vendor_name"` (no dot). Fix the mismatch now, before anything depends on the wrong one:

```python
# ledger_sense/rules/integrity/vendors.py — change both occurrences
return [Finding("I2", "vendor_name", ...)]   # was "vendor.name"
```

- [ ] **Step 1: Update the existing I2 rule tests to pin the corrected field name**

In `tests/rules/integrity/test_vendors.py`, add one assertion line to each of the two tripping tests (they currently only check `.rule_id`):

```python
def test_check_vendor_resolution_flags_an_unrecognised_name(ledger: VendorLedger) -> None:
    findings = check_vendor_resolution(ledger, "Zenith Glazing")
    assert [f.rule_id for f in findings] == ["I2"]
    assert findings[0].field == "vendor_name"


def test_check_vendor_resolution_flags_a_name_needing_confirmation(
        ledger: VendorLedger) -> None:
    findings = check_vendor_resolution(ledger, "Acme Steel Co.")
    assert [f.rule_id for f in findings] == ["I2"]
    assert findings[0].field == "vendor_name"
```

Run: `uv run pytest tests/rules/integrity/test_vendors.py -v`
Expected: FAIL on the two new assertion lines (field is still `"vendor.name"`), then apply the one-line fix above and re-run — Expected: PASS, whole file.

- [ ] **Step 2: Write the failing run-pipeline test**

```python
# tests/run/test_document.py — add near the other extraction-behaviour tests
from ledger_sense.store.vendors import load_vendor_ledger, save_vendor_ledger
from ledger_sense.domain.vendor import VendorLedger as _VendorLedger


def test_an_unresolved_vendor_name_produces_an_i2_finding(db: RunDb) -> None:
    document_id = db.store_pdf(_clean_pdf())
    run_document(db.engine, document_id)
    findings = db.findings_for(document_id)
    i2 = [f for f in findings if f.rule_id == "I2"]
    assert len(i2) == 1
    assert i2[0].field == "vendor_name"
    # boxed at the vendor_name claim's own geometry, same as every other
    # rule finding _rule_rows produces.
    assert i2[0].page is not None


def test_a_vendor_name_that_exactly_matches_an_existing_vendor_produces_no_i2_finding(
    db: RunDb,
) -> None:
    _, app, pdf = generate(seed=1, error="clean")
    assert app.vendor_name is not None
    with db.engine.begin() as conn:
        ledger = _VendorLedger()
        ledger.create(app.vendor_name)
        save_vendor_ledger(conn, db.tenant_id, ledger)

    document_id = db.store_pdf(pdf)
    run_document(db.engine, document_id)
    findings = db.findings_for(document_id)
    assert [f for f in findings if f.rule_id == "I2"] == []


def test_i2_never_touches_another_tenants_vendor_ledger(db: RunDb) -> None:
    _, app, pdf = generate(seed=1, error="clean")
    assert app.vendor_name is not None
    other_tenant = db.new_tenant()
    with db.engine.begin() as conn:
        ledger = _VendorLedger()
        ledger.create(app.vendor_name)
        save_vendor_ledger(conn, other_tenant, ledger)  # a DIFFERENT tenant

    document_id = db.store_pdf(pdf)  # stored under db.tenant_id
    run_document(db.engine, document_id)
    findings = db.findings_for(document_id)
    # db.tenant_id's own ledger is still empty, so the name is unresolved.
    assert [f.rule_id for f in findings if f.rule_id == "I2"] == ["I2"]
```

(`generate` is already imported at the top of `tests/run/test_document.py` per the file's existing `_clean_pdf` helper — reuse it, don't re-import.)

- [ ] **Step 3: Run to confirm they fail**

Run: `uv run pytest tests/run/test_document.py -k i2 -v` (and `-k vendor` if the first pattern misses any)
Expected: FAIL — no I2 findings are produced yet; `run/document.py` never calls `check_vendor_resolution`.

- [ ] **Step 4: Wire I2 into `_execute`**

In `ledger_sense/run/document.py`, add the imports:

```python
from ledger_sense.rules.integrity.vendors import check_vendor_resolution
from ledger_sense.store.vendors import load_vendor_ledger
```

Add a helper near `_rule_rows` (same file, follows its exact shape — `boxes` keyed by claim field, geometry pulled from the matching claim row):

```python
def _vendor_rows(conn: Connection, tenant_id: str, document_id: str,
                 app: PayApplication, claim_rows: list[dict[str, object]],
                 now: datetime) -> list[dict[str, object]]:
    """I2, read-only: resolve() never mutates the ledger, so this never
    calls save_vendor_ledger — there is nothing here to persist back."""
    if app.vendor_name is None:
        return []  # X0 already reports the omission; nothing to resolve.
    ledger = load_vendor_ledger(conn, tenant_id)
    boxes = {str(c["field"]): c for c in claim_rows}
    box = boxes.get("vendor_name")
    rows: list[dict[str, object]] = []
    for finding in check_vendor_resolution(ledger, app.vendor_name):
        rows.append({
            "id": uuid.uuid4().hex, "tenant_id": tenant_id, "document_id": document_id,
            "rule_id": finding.rule_id, "field": finding.field, "message": finding.message,
            "expected": str(finding.expected), "actual": str(finding.actual),
            "page": box["page"] if box else None,
            "x0": box["x0"] if box else None, "top": box["top"] if box else None,
            "x1": box["x1"] if box else None, "bottom": box["bottom"] if box else None,
            "state": "open", "created_at": now,
        })
    return rows
```

In `_execute` (find the block that currently builds `finding_rows` — around where `_rule_rows` is called, before `_write`), extend it:

```python
        claim_rows = _claim_rows(tenant_id, document_id, app)
        finding_rows = _x0_rows(tenant_id, document_id, app, now)
        finding_rows += _rule_rows(tenant_id, document_id, app, claim_rows, now)
        finding_rows += _vendor_rows(conn, tenant_id, document_id, app, claim_rows, now)
```

(Match this against the file's real current line order with `grep -n "finding_rows" ledger_sense/run/document.py` before editing — the exact variable name and whether `now`/`claim_rows` are already in scope at that point must be confirmed against the live file rather than assumed from this plan's earlier read.)

- [ ] **Step 5: Run to confirm the new tests pass, then the whole run suite**

Run: `uv run pytest tests/run/test_document.py -v`
Expected: PASS, whole file — confirms I2 wiring didn't disturb the arithmetic-rule or duplicate-detection tests already in this file.

- [ ] **Step 6: Run mypy strict and the boundary/no-floats guards**

Run: `uv run mypy --strict ledger_sense/run/document.py ledger_sense/rules/integrity/vendors.py && uv run pytest tests/boundary tests/test_no_floats.py -q`
Expected: no errors, PASS

(The boundary run matters here specifically: `run/document.py` has its own AST guard, `test_the_runner_imports_nothing_that_could_reach_a_provider` — confirm the new imports don't trip it. `load_vendor_ledger`/`check_vendor_resolution` reach no provider client, so this should pass without further changes, but verify rather than assume.)

- [ ] **Step 7: Commit**

```bash
git add ledger_sense/rules/integrity/vendors.py ledger_sense/run/document.py tests/rules/integrity/test_vendors.py tests/run/test_document.py
git commit -m "feat(run): I2 vendor resolution runs live, read-only, per document"
```

---

### Task 7: Review API — the `confirm-vendor` action

**Files:**
- Modify: `ledger_sense/api/review.py`
- Test: `tests/api/test_review.py`

**Interfaces:**
- Consumes: `load_vendor_ledger`/`save_vendor_ledger` (Task 5), `VendorLedger.confirm_alias`/`.create`/`.get` (unchanged, existing), `documents.c.vendor_id` (Task 4), `Session.user_id`, `_get_finding`/`_require_open`/`_close` (unchanged, existing).
- Produces: `POST /api/review/exceptions/{exc_id}/confirm-vendor`, body `{"vendor_id": "..."}` XOR `{"new_vendor_name": "..."}`. On success: the finding closes to a new `"confirmed"` state, `documents.vendor_id` is set, and the vendor ledger gains a new confirmed alias (existing vendor) or a brand-new canonical vendor plus its first confirmed alias (new vendor) — `confirmed_by` is always `session.user_id`.

- [ ] **Step 1: Write the failing API tests**

Read `tests/api/test_review.py:1-60` first to copy its exact fixture setup (how a document/finding gets seeded for the `accept`/`edit`/`reject` tests already in that file, and the `TenantClient`-style test client). Match that helper's shape exactly rather than inventing a new one — the sketch below is a structural guide to the required assertions, not literal final code; the actual implementer adapts fixture names to whatever the file already provides.

```python
# tests/api/test_review.py — add near the accept/edit/reject tests

def test_confirm_vendor_links_an_existing_vendor(...) -> None:
    # seed a document with a vendor_name claim and an open I2 finding, and a
    # pre-existing canonical vendor to confirm against
    response = client.post(f"/api/review/exceptions/{exc_id}/confirm-vendor",
                           json={"vendor_id": vendor_id})
    assert response.status_code == 200
    assert response.json() == {"status": "confirmed", "vendor_id": vendor_id}
    exceptions = client.get(f"/api/review/documents/{doc_id}/exceptions").json()
    assert next(e for e in exceptions if e["id"] == exc_id)["state"] == "confirmed"


def test_confirm_vendor_creates_a_new_vendor(...) -> None:
    response = client.post(f"/api/review/exceptions/{exc_id}/confirm-vendor",
                           json={"new_vendor_name": "Brand New Roofing Co."})
    assert response.status_code == 200
    new_vendor_id = response.json()["vendor_id"]
    with app_engine().connect() as conn:
        ledger = load_vendor_ledger(conn, tenant_id)
    assert ledger.get(new_vendor_id).name == "Brand New Roofing Co."


def test_confirm_vendor_rejects_both_fields_at_once(...) -> None:
    response = client.post(f"/api/review/exceptions/{exc_id}/confirm-vendor",
                           json={"vendor_id": "v1", "new_vendor_name": "X"})
    assert response.status_code == 422


def test_confirm_vendor_rejects_neither_field(...) -> None:
    assert client.post(f"/api/review/exceptions/{exc_id}/confirm-vendor",
                       json={}).status_code == 422


def test_confirm_vendor_refuses_a_non_i2_exception(...) -> None:
    # against an A1/arithmetic finding's exc_id instead of an I2 one
    response = client.post(f"/api/review/exceptions/{other_exc_id}/confirm-vendor",
                           json={"new_vendor_name": "X"})
    assert response.status_code == 422


def test_confirm_vendor_is_404_for_another_tenants_vendor_id(...) -> None:
    response = client.post(f"/api/review/exceptions/{exc_id}/confirm-vendor",
                           json={"vendor_id": "belongs-to-another-tenant"})
    assert response.status_code == 404


def test_confirm_vendor_twice_is_409(...) -> None:
    client.post(f"/api/review/exceptions/{exc_id}/confirm-vendor",
               json={"new_vendor_name": "X"})
    response = client.post(f"/api/review/exceptions/{exc_id}/confirm-vendor",
                           json={"new_vendor_name": "Y"})
    assert response.status_code == 409
```

- [ ] **Step 2: Run to confirm they fail**

Run: `uv run pytest tests/api/test_review.py -k confirm_vendor -v`
Expected: FAIL — `404 Not Found` (no such route yet) on every one.

- [ ] **Step 3: Implement the route**

In `ledger_sense/api/review.py`, add to the imports:

```python
from ledger_sense.store.vendors import load_vendor_ledger, save_vendor_ledger
```

(`documents`, `claims`, `findings` are already imported from `ledger_sense.store.retention`; no change needed there.)

Extend `ExceptionState`:

```python
ExceptionState = Literal["open", "accepted", "edited", "rejected", "confirmed"]
```

Add `model_validator` to the existing `from pydantic import ...` line, then add the body model near `EditBody`/`RejectBody`:

```python
class ConfirmVendorBody(BaseModel):
    vendor_id: str | None = None
    new_vendor_name: StrictStr | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "ConfirmVendorBody":
        if (self.vendor_id is None) == (self.new_vendor_name is None):
            raise ValueError("exactly one of vendor_id or new_vendor_name is required")
        return self
```

Add a small helper next to `_value_type_for`/`_match_quality_for` (same lookup-by-`(document_id, field)` pattern):

```python
def _observed_vendor_name(conn: sa.Connection, document_id: str) -> str | None:
    row = conn.execute(
        sa.select(claims.c.value)
        .where(claims.c.document_id == document_id, claims.c.field == "vendor_name")
        .order_by(claims.c.observed_at.desc())
        .limit(1)
    ).one_or_none()
    return None if row is None else row.value
```

Add the route, after `reject`:

```python
@router.post("/exceptions/{exc_id}/confirm-vendor")
def confirm_vendor(exc_id: str, body: ConfirmVendorBody,
                   session: CurrentSession) -> dict[str, str | None]:
    with app_engine().begin() as conn:
        row = _get_finding(exc_id, session, conn)
        _require_open(row)
        if row.rule_id != "I2":
            raise HTTPException(status_code=422,
                                detail="confirm-vendor only applies to an I2 exception")
        observed_name = _observed_vendor_name(conn, row.document_id)
        if observed_name is None:
            raise HTTPException(status_code=422, detail="no extracted vendor name to confirm")

        ledger = load_vendor_ledger(conn, session.tenant_id)
        if body.vendor_id is not None:
            if ledger.get(body.vendor_id) is None:
                raise HTTPException(status_code=404, detail="not found")
            vendor_id = body.vendor_id
        else:
            assert body.new_vendor_name is not None  # the validator guarantees one is set
            vendor_id = ledger.create(body.new_vendor_name)
        ledger.confirm_alias(vendor_id, observed_name, source_document=row.document_id,
                             confirmed_by=session.user_id)

        save_vendor_ledger(conn, session.tenant_id, ledger)
        conn.execute(documents.update().where(documents.c.id == row.document_id)
                     .values(vendor_id=vendor_id))
        _close(conn, exc_id, "confirmed")
    return {"status": "confirmed", "vendor_id": vendor_id}
```

- [ ] **Step 4: Run to confirm the new tests pass, then the whole file**

Run: `uv run pytest tests/api/test_review.py -v`
Expected: PASS, whole file — confirms `accept`/`edit`/`reject`/`progress`/`list_exceptions` are all untouched.

- [ ] **Step 5: Confirm the session-required audit still covers the new route**

Run: `uv run pytest tests/api/test_auth.py -v`
Expected: PASS — `test_every_api_route_requires_a_session` walks the live route table, so a new route mounted correctly (`review_router` is registered as a whole in `api/app.py`, unchanged) is covered automatically. Confirm rather than assume: check the test's output names the new route.

- [ ] **Step 6: Run mypy strict**

Run: `uv run mypy --strict ledger_sense/api/review.py`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add ledger_sense/api/review.py tests/api/test_review.py
git commit -m "feat(review): confirm-vendor action links or creates a vendor, confirmed_by=session.user_id"
```

---

### Task 8: Vendor ledger read API — `GET /api/vendors/{id}/ledger`

**Files:**
- Create: `ledger_sense/api/vendors.py`
- Modify: `ledger_sense/api/app.py` (register the router)
- Test: `tests/api/test_vendors.py`

**Interfaces:**
- Consumes: `load_vendor_ledger` (Task 5).
- Produces: `GET /api/vendors/{vendor_id}/ledger` → `{"id": str, "name": str, "aliases": [...], "payment_history": [...]}`. `payment_history` is always `[]` in this plan (no ingest path exists — see the plan's Architecture note); the shape is still fully typed for forward compatibility, and `account_hmac` is never included in the response under any circumstance.

- [ ] **Step 1: Write the failing API tests**

Read `tests/api/test_review.py`'s top for the test-client/session-cookie fixture pattern this codebase uses (it will be reused here verbatim — every route in this project requires a session).

```python
# tests/api/test_vendors.py
from ledger_sense.domain.vendor import VendorLedger
from ledger_sense.store.session import app_engine
from ledger_sense.store.vendors import save_vendor_ledger
# ... plus whatever session/test-client fixtures test_review.py uses


def test_vendor_ledger_returns_name_and_aliases(...) -> None:
    ledger = VendorLedger()
    vendor_id = ledger.create("Acme Steel Company")
    ledger.confirm_alias(vendor_id, "Acme Steel Co.", source_document="doc-1",
                         confirmed_by="reviewer-1")
    with app_engine().begin() as conn:
        save_vendor_ledger(conn, tenant_id, ledger)

    response = client.get(f"/api/vendors/{vendor_id}/ledger")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == vendor_id
    assert body["name"] == "Acme Steel Company"
    assert [a["name"] for a in body["aliases"]] == ["Acme Steel Co."]


def test_vendor_ledger_payment_history_is_an_honest_empty_list(...) -> None:
    # no payment_details rows exist anywhere yet — this plan adds no ingest path.
    response = client.get(f"/api/vendors/{vendor_id}/ledger")
    assert response.json()["payment_history"] == []


def test_vendor_ledger_never_includes_the_account_hmac(...) -> None:
    # seeded a payment_details row directly (bypassing the ledger API) to
    # prove the response model excludes account_hmac regardless of how the
    # row got there
    body = client.get(f"/api/vendors/{vendor_id}/ledger").json()
    assert "account_hmac" not in str(body)


def test_an_unknown_vendor_id_is_404(...) -> None:
    assert client.get("/api/vendors/nope/ledger").status_code == 404


def test_another_tenants_vendor_is_404_not_403(...) -> None:
    # a vendor created under a different tenant's ledger
    response = client.get(f"/api/vendors/{other_tenants_vendor_id}/ledger")
    assert response.status_code == 404
```

- [ ] **Step 2: Run to confirm they fail**

Run: `uv run pytest tests/api/test_vendors.py -v`
Expected: FAIL — `404`/`ModuleNotFoundError`, the route doesn't exist.

- [ ] **Step 3: Implement `ledger_sense/api/vendors.py`**

```python
"""Read-only vendor ledger API — Task 44's screen reads from here.

payment_history is always [] until a payment-detail ingest path exists (none
does yet — see docs/superpowers/plans/2026-09-23-vendor-persistence.md's
Architecture note). The response model is still fully typed rather than a
bare [] placeholder, so the frontend and any future ingest path share one
contract; account_hmac is never a field on PaymentDetailOut — it must never
leave the server.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ledger_sense.api.deps import require_session
from ledger_sense.auth.session import Session
from ledger_sense.store.session import app_engine
from ledger_sense.store.vendors import load_vendor_ledger

router = APIRouter(prefix="/api/vendors")

CurrentSession = Annotated[Session, Depends(require_session)]


class VendorAliasOut(BaseModel):
    id: str
    name: str
    first_seen: datetime
    source_document: str
    confirmed_by: str


class PaymentDetailOut(BaseModel):
    account_last4: str
    routing_last4: str
    observed_at: datetime
    source_document: str
    confirmed_by: str | None
    confirmed_at: datetime | None


class VendorLedgerOut(BaseModel):
    id: str
    name: str
    aliases: list[VendorAliasOut]
    payment_history: list[PaymentDetailOut]


@router.get("/{vendor_id}/ledger")
def vendor_ledger(vendor_id: str, session: CurrentSession) -> VendorLedgerOut:
    with app_engine().connect() as conn:
        ledger = load_vendor_ledger(conn, session.tenant_id)
    vendor = ledger.get(vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="not found")
    return VendorLedgerOut(
        id=vendor.id, name=vendor.name,
        aliases=[VendorAliasOut(id=a.id, name=a.name, first_seen=a.first_seen,
                                source_document=a.source_document,
                                confirmed_by=a.confirmed_by) for a in vendor.aliases],
        payment_history=[PaymentDetailOut(
            account_last4=p.account_last4, routing_last4=p.routing_last4,
            observed_at=p.observed_at, source_document=p.source_document,
            confirmed_by=p.confirmed_by, confirmed_at=p.confirmed_at)
            for p in vendor.payment_history],
    )
```

Register it in `ledger_sense/api/app.py`: add `from ledger_sense.api.vendors import router as vendors_router` alongside the other router imports, and `register(app, vendors_router)` alongside the other `register(app, ...)` calls (currently lines 235-239).

- [ ] **Step 4: Run to confirm the new tests pass**

Run: `uv run pytest tests/api/test_vendors.py -v`
Expected: PASS

- [ ] **Step 5: Confirm session enforcement and mypy strict**

Run: `uv run pytest tests/api/test_auth.py -v && uv run mypy --strict ledger_sense/api/vendors.py ledger_sense/api/app.py`
Expected: PASS, no errors

- [ ] **Step 6: Full API suite, to catch any router-registration collateral**

Run: `uv run pytest tests/api -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add ledger_sense/api/vendors.py ledger_sense/api/app.py tests/api/test_vendors.py
git commit -m "feat(api): GET /api/vendors/{id}/ledger — name, aliases, honest empty payment history"
```

---

### Task 9: Frontend — the vendor ledger screen

**Files:**
- Create: `ledger_sense/web/app/vendors/[vendorId]/page.tsx`
- Create: `ledger_sense/web/app/vendors/[vendorId]/VendorLedger.tsx`
- Test: `ledger_sense/web/app/vendors/[vendorId]/VendorLedger.test.tsx`

**Interfaces:**
- Consumes: `GET /api/vendors/{vendorId}/ledger` (Task 8).
- Produces: a screen at `/vendors/{vendorId}` rendering the vendor's canonical name, its confirmed aliases (name, first seen, source document, confirmed by), and an explicit, honest statement when `payment_history` is empty — never a hidden or silently-omitted section (CLAUDE.md Never #8: publish the straight-through rate including the misses; the same "say the miss out loud" posture applies to an unbuilt feature, not just a routing statistic).

- [ ] **Step 1: Write the failing component test**

```tsx
// ledger_sense/web/app/vendors/[vendorId]/VendorLedger.test.tsx
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { VendorLedger } from './VendorLedger'

const LEDGER = {
  id: 'vendor-1',
  name: 'Acme Steel Company',
  aliases: [
    {
      id: 'alias-1',
      name: 'Acme Steel Co.',
      first_seen: '2026-09-15T10:00:00Z',
      source_document: 'doc-1',
      confirmed_by: 'reviewer-1',
    },
  ],
  payment_history: [],
}

function stubFetchOnce(body: unknown, ok = true): void {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok,
      status: ok ? 200 : 404,
      statusText: ok ? 'OK' : 'Not Found',
      json: async () => body,
    }),
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('VendorLedger', () => {
  it('renders the canonical name and confirmed aliases', async () => {
    stubFetchOnce(LEDGER)
    render(<VendorLedger vendorId="vendor-1" />)
    await waitFor(() => expect(screen.getByText('Acme Steel Company')).toBeInTheDocument())
    expect(screen.getByText('Acme Steel Co.')).toBeInTheDocument()
    expect(screen.getByText('reviewer-1')).toBeInTheDocument()
  })

  it('states plainly that no payment details are on file, rather than hiding the section', async () => {
    stubFetchOnce(LEDGER)
    render(<VendorLedger vendorId="vendor-1" />)
    await waitFor(() =>
      expect(screen.getByText(/no payment details on file/i)).toBeInTheDocument(),
    )
  })

  it('surfaces a 404 as an explicit not-found state, not a silent blank screen', async () => {
    stubFetchOnce({ detail: 'not found' }, false)
    render(<VendorLedger vendorId="nope" />)
    await waitFor(() => expect(screen.getByText(/not found/i)).toBeInTheDocument())
  })
})
```

- [ ] **Step 2: Run to confirm it fails**

Run: `npx vitest run ledger_sense/web/app/vendors/[vendorId]/VendorLedger.test.tsx`
Expected: FAIL — `Cannot find module './VendorLedger'`

- [ ] **Step 3: Write `VendorLedger.tsx`**

```tsx
'use client'

import { useEffect, useState } from 'react'

// Same-origin-split convention as Reconciliation.tsx / ReviewQueue.tsx.
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

interface VendorAlias {
  id: string
  name: string
  first_seen: string
  source_document: string
  confirmed_by: string
}

interface PaymentDetail {
  account_last4: string
  routing_last4: string
  observed_at: string
  source_document: string
  confirmed_by: string | null
  confirmed_at: string | null
}

interface VendorLedgerData {
  id: string
  name: string
  aliases: VendorAlias[]
  payment_history: PaymentDetail[]
}

async function api<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { credentials: 'include' })
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // A non-JSON error body: the status line above is the best we have.
    }
    throw new Error(detail)
  }
  return (await response.json()) as T
}

export function VendorLedger({ vendorId }: { vendorId: string }) {
  const [data, setData] = useState<VendorLedgerData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api<VendorLedgerData>(`/api/vendors/${vendorId}/ledger`)
      .then((result) => {
        if (!cancelled) setData(result)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [vendorId])

  if (error !== null) {
    return (
      <main className="p-6">
        <p role="alert">{error}</p>
      </main>
    )
  }
  if (data === null) {
    return (
      <main className="p-6">
        <p>Loading…</p>
      </main>
    )
  }

  return (
    <main className="p-6">
      <h1 className="text-xl font-semibold">{data.name}</h1>

      <section className="mt-6">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-muted">
          Confirmed aliases
        </h2>
        {data.aliases.length === 0 ? (
          <p className="mt-2 text-ink-muted">No confirmed aliases on file for this vendor.</p>
        ) : (
          <table className="mt-2 w-full text-left">
            <thead>
              <tr>
                <th>Name</th>
                <th>First seen</th>
                <th>Source document</th>
                <th>Confirmed by</th>
              </tr>
            </thead>
            <tbody>
              {data.aliases.map((alias) => (
                <tr key={alias.id}>
                  <td>{alias.name}</td>
                  <td>{alias.first_seen}</td>
                  <td>{alias.source_document}</td>
                  <td>{alias.confirmed_by}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="mt-6">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-muted">
          Payment detail history
        </h2>
        {data.payment_history.length === 0 ? (
          // Never-rule #8's spirit: an unbuilt feature is stated plainly,
          // never a hidden or silently-empty section.
          <p className="mt-2 text-ink-muted">
            No payment details on file for this vendor. Payment-detail tracking is not yet
            connected to any document ingest path.
          </p>
        ) : (
          <table className="mt-2 w-full text-left">
            <thead>
              <tr>
                <th>Account</th>
                <th>Routing</th>
                <th>Observed</th>
                <th>Source document</th>
                <th>Confirmed by</th>
              </tr>
            </thead>
            <tbody>
              {data.payment_history.map((detail, i) => (
                <tr key={i}>
                  <td>····{detail.account_last4}</td>
                  <td>····{detail.routing_last4}</td>
                  <td>{detail.observed_at}</td>
                  <td>{detail.source_document}</td>
                  <td>{detail.confirmed_by ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </main>
  )
}
```

- [ ] **Step 4: Write the thin route wrapper `page.tsx`**

```tsx
import { VendorLedger } from './VendorLedger'

// Thin on purpose, mirrors app/reconciliation/[docId]/page.tsx: awaits the
// dynamic segment, hands it to the client component that does the fetching.
export default async function VendorLedgerPage({
  params,
}: {
  params: Promise<{ vendorId: string }>
}) {
  const { vendorId } = await params
  return <VendorLedger key={vendorId} vendorId={vendorId} />
}
```

- [ ] **Step 5: Run to confirm the component tests pass**

Run: `npx vitest run ledger_sense/web/app/vendors/[vendorId]/VendorLedger.test.tsx`
Expected: PASS

- [ ] **Step 6: Run the full frontend test suite and typecheck**

Run: `npm test && npx tsc --noEmit`
Expected: PASS, no type errors

- [ ] **Step 7: Run axe / accessibility check on the new screen, per CLAUDE.md's testing bar**

Follow whatever this project's existing accessibility convention is for a new screen (check how `app/inbox/` or `app/reconciliation/` satisfied this bar) and confirm zero serious/critical violations on `/vendors/{vendorId}`.

- [ ] **Step 8: Commit**

```bash
git add ledger_sense/web/app/vendors
git commit -m "feat(ui): vendor ledger screen — canonical name, confirmed aliases, honest empty payment history"
```

---

## Self-Review Notes (from the plan author, for the executor's awareness)

- **Task ordering is load-bearing.** Tasks 1→3 must land before Task 6 (which reads `app.vendor_name`), Task 4→5 before Task 6/7/8 (which all call `load_vendor_ledger`/`save_vendor_ledger`), Task 6 before Task 7 isn't strictly required but keeps I2's field-name fix landing before anything else depends on `"vendor_name"` as the lookup key. Execute in the numbered order.
- **`security-review` gates, not blocks.** Tasks 4 and 5 both touch `PaymentDetail` persistence directly (schema and repository respectively) — CLAUDE.md's skill map requires that review "before anything else touches it." Both tasks call it out explicitly; don't skip it because the table stays empty in this plan — the schema and the repository are real, reachable code regardless of whether any row exists yet.
- **No task in this plan adds payment-detail ingest.** `payment_details` exists as a table and a repository field, and the API/UI both handle it correctly when empty — but nothing writes to it. That gap was a deliberate, user-confirmed scope decision (see the plan's Architecture note), not an oversight to "complete" mid-execution.
- **The I2 field-name fix in Task 6 touches an already-reviewed, already-tested module.** It's a one-line, well-justified change (aligning `Finding.field` with the real claim field name Task 3 introduces), but flag it for extra attention in code review precisely because it modifies code that previously passed review under a different (in hindsight, incomplete) assumption.
