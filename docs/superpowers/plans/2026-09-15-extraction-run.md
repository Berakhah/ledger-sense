# Extraction Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stored inbox document is run through the deterministic pipeline (classify → assemble → arithmetic rules), its Claims and Findings are persisted with an honest status, and the inbox exposes that status — with no queue, no model call, and no confidently-wrong finding.

**Architecture:** Migration 0005 adds `documents.status` (+ hash, doc_type, notes, attempt counter), gives the `claims` stub its value/provenance columns, and creates `findings`. One idempotent runner, `ledger_sense/run/document.py::run_document(engine, id)`, owns its transactions, takes the row with a write (so the lock holds on SQLite), and replaces that document's claims/findings in place. The inbox route schedules it via `BackgroundTasks` after commit; `python -m ledger_sense extract --pending` re-runs anything stuck.

**Tech Stack:** Python 3.12, SQLAlchemy Core + alembic (SQLite in tests via `batch_alter_table`, Postgres in compose), FastAPI `BackgroundTasks`, existing `pdfplumber`-based `extract/` package, `rules.engine`.

**Spec:** `docs/superpowers/specs/2026-09-15-extraction-run-design.md` — read it first; the plan argues from it.

## Global Constraints

- **Never #1:** `ledger_sense/run/` imports nothing from `ledger_sense.gateway`, `ledger_sense.extract.ocr`, or any provider client. The existing AST guard (`tests/boundary/test_structure.py`, `tests/test_degraded_mode.py`) covers every `.py` under `ledger_sense/`; a new test pins the runner specifically.
- **Never #2:** money is `Decimal` (`ledger_sense.domain.money.money`) or TEXT in the store. No `float` in any money path. `scripts/check_no_float.py` runs over the new package; `confidence`/`match_quality`/bbox are the only `REAL` columns and are scores/geometry.
- **One writer, one reader:** `documents.insert` only in `store/documents.py` (AST-pinned). `Labelled` constructed only in `ingest/intake.py` and `store/documents.py` (AST-pinned). The runner only UPDATEs `documents` and only *consumes* the `Labelled` from `load_document`.
- **Tenancy:** every `claims`/`findings` row carries `tenant_id`; duplicate matching is `WHERE tenant_id = :tenant_id` in SQL.
- **`run_note` never carries document content:** exception class name only; the message goes to the log with `document_id`/`tenant_id`, never the filename.
- **Toolchain:** `uv run pytest`, `uv run ruff check .`, `uv run mypy --strict ledger_sense`. `uv` is not on PATH on this machine — see memory `uv-toolchain-location` (`%APPDATA%\Python\Python313\Scripts\uv.exe`). Every task ends with all three green.
- **TDD:** every step is RED → GREEN → commit. Test names describe behaviour, not the function under test.
- **Commit trailer:** `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

## File Structure

| File | Responsibility |
|---|---|
| `migrations/versions/0005_extraction_run.py` | **Create.** Status/hash/doc_type/notes/attempts on `documents`; value columns on `claims`; `findings` table. |
| `ledger_sense/store/retention.py` | **Modify.** Table metadata for the above; purge deletes `findings`; `PurgeReport.findings_purged`. |
| `ledger_sense/store/documents.py` | **Modify.** `store_document` writes `content_sha256`. |
| `ledger_sense/rules/engine.py` | **Modify.** `run_rules(..., exclude=frozenset())`; `CONTRACT_DEPENDENT_RULES`. |
| `ledger_sense/run/__init__.py` | **Create.** Package marker + docstring. |
| `ledger_sense/run/document.py` | **Create.** `RunOutcome`, `run_document`, `run_pending`, `run_in_background`, the tenant-default contract, claim/finding serialisation. |
| `ledger_sense/api/inbox.py` | **Modify.** `BackgroundTasks` on `upload`; `status`/`doc_type`/`run_note`/counts on rows; `?status=` filter. |
| `ledger_sense/__main__.py` | **Modify.** `extract --pending [--limit N]`, `extract --document <id>`. |
| `docs/security.md` | **Modify.** Hash-of-plaintext note; run_note policy. |
| `tests/run/conftest.py` | **Create.** Migrated SQLite + blob store via env; `RunDb` helper. |
| `tests/run/test_document.py`, `tests/run/test_pending.py` | **Create.** Runner behaviour. |
| `tests/store/test_migrations.py`, `tests/store/test_retention.py`, `tests/store/test_documents.py`, `tests/store/conftest.py` | **Modify.** |
| `tests/rules/test_engine.py`, `tests/rules/test_contract_surface.py` | **Modify / Create.** |
| `tests/api/test_inbox.py`, `tests/test_main.py`, `tests/boundary/test_structure.py`, `tests/test_degraded_mode.py` | **Modify.** |

---

### Task 1: Migration 0005 and table metadata

**Files:**
- Create: `migrations/versions/0005_extraction_run.py`
- Modify: `ledger_sense/store/retention.py:44-79` (table definitions)
- Modify: `tests/store/conftest.py:150-154` (the stub `claims` seed must now supply every NOT NULL column)
- Test: `tests/store/test_migrations.py`

**Interfaces:**
- Produces: `ledger_sense.store.retention.documents` with columns `status, content_sha256, doc_type, run_note, rounding_policy_source, run_attempts`; `ledger_sense.store.retention.claims` with `field, value, value_type, method, confidence, match_quality, page, x0, top, x1, bottom, caveat, observed_at`; new `ledger_sense.store.retention.findings`; constants `DOCUMENT_STATUSES`, `DOC_TYPES`, `FINDING_STATES`, `CLAIM_VALUE_TYPES`.

- [ ] **Step 1: Write the failing migration test**

Append to `tests/store/test_migrations.py`:

```python
def test_0005_adds_the_run_columns_the_findings_table_and_named_checks(
    tmp_path: pathlib.Path,
) -> None:
    database = (tmp_path / "m.db").as_posix()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("LEDGER_SENSE_DB_URL", f"sqlite:///{database}")
        store_session.reset_app_engine()
        config = _alembic_config()
        command.upgrade(config, "head")
        inspector = sa.inspect(store_session.app_engine())

        doc_cols = {c["name"] for c in inspector.get_columns("documents")}
        assert {"status", "content_sha256", "doc_type", "run_note",
                "rounding_policy_source", "run_attempts"} <= doc_cols
        claim_cols = {c["name"] for c in inspector.get_columns("claims")}
        assert {"field", "value", "value_type", "method", "confidence", "match_quality",
                "page", "x0", "top", "x1", "bottom", "caveat", "observed_at"} <= claim_cols
        assert "findings" in inspector.get_table_names()

        doc_checks = {c["name"] for c in inspector.get_check_constraints("documents")}
        assert {"ck_documents_status", "ck_documents_doc_type"} <= doc_checks
        finding_checks = {c["name"] for c in inspector.get_check_constraints("findings")}
        assert "ck_findings_state" in finding_checks
        claim_checks = {c["name"] for c in inspector.get_check_constraints("claims")}
        assert "ck_claims_value_type" in claim_checks

        claim_indexes = {i["name"]: i for i in inspector.get_indexes("claims")}
        assert claim_indexes["ix_claims_document_field"]["unique"] is False
        finding_indexes = {i["name"] for i in inspector.get_indexes("findings")}
        assert "ix_findings_document_order" in finding_indexes

        # Downgrade one step removes everything 0005 added and nothing else.
        command.downgrade(config, "0004_filename")
        inspector = sa.inspect(store_session.app_engine())
        assert "findings" not in inspector.get_table_names()
        assert "status" not in {c["name"] for c in inspector.get_columns("documents")}
        assert "field" not in {c["name"] for c in inspector.get_columns("claims")}
        assert "filename" in {c["name"] for c in inspector.get_columns("documents")}
    store_session.reset_app_engine()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/store/test_migrations.py -k 0005 -v`
Expected: FAIL — `"status"` not in `doc_cols`.

- [ ] **Step 3: Write the migration**

Create `migrations/versions/0005_extraction_run.py`:

```python
"""extraction run: documents.status & friends, claims value columns, findings

Revision ID: 0005_extraction_run
Revises: 0004_filename
Create Date: 2026-09-15

Spec: docs/superpowers/specs/2026-09-15-extraction-run-design.md §1.

`documents.status` keeps its server default ('received'): a row that exists
is, by definition, received. `run_attempts` keeps its default (0) for the
same reason. Every other new column is nullable — it is written by the run,
and NULL honestly means "no run has said anything yet".

`claims` had no value columns and no writer before this revision, so the
table is empty in every deployment; the NOT NULL columns are added with a
throwaway server default and the default is then dropped (0003's two-block
pattern — on SQLite one block that adds NOT NULL and drops the default would
copy rows with nothing to fill).

`ix_claims_document_field` is deliberately NOT unique: Phase 4 writes a
HUMAN-method claim on the same field for every accept/edit.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_extraction_run"
down_revision: str | None = "0004_filename"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors ledger_sense.store.retention.DOCUMENT_STATUSES / DOC_TYPES /
# FINDING_STATES / CLAIM_VALUE_TYPES. Written out on purpose, as 0003 does.
_STATUSES = ("received", "extracted", "cannot_extract", "duplicate", "failed")
_DOC_TYPES = ("pay_application", "continuation_sheet", "invoice", "lien_waiver", "unknown")
_STATES = ("open", "accepted", "edited", "rejected")
_VALUE_TYPES = ("money", "date", "int", "str")


def _one_of(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("status", sa.Text(), nullable=False,
                                   server_default="received"))
        batch.add_column(sa.Column("content_sha256", sa.Text(), nullable=True))
        batch.add_column(sa.Column("doc_type", sa.Text(), nullable=True))
        batch.add_column(sa.Column("run_note", sa.Text(), nullable=True))
        batch.add_column(sa.Column("rounding_policy_source", sa.Text(), nullable=True))
        batch.add_column(sa.Column("run_attempts", sa.Integer(), nullable=False,
                                   server_default="0"))
        batch.create_check_constraint("ck_documents_status", _one_of("status", _STATUSES))
        batch.create_check_constraint(
            "ck_documents_doc_type",
            f"doc_type IS NULL OR {_one_of('doc_type', _DOC_TYPES)}")
        batch.create_index(
            "ix_documents_tenant_sha", ["tenant_id", "content_sha256"],
            sqlite_where=sa.text("content_sha256 IS NOT NULL"),
            postgresql_where=sa.text("content_sha256 IS NOT NULL"))

    with op.batch_alter_table("claims") as batch:
        batch.add_column(sa.Column("field", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("value", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("value_type", sa.Text(), nullable=False,
                                   server_default="str"))
        batch.add_column(sa.Column("method", sa.Text(), nullable=False,
                                   server_default="deterministic"))
        batch.add_column(sa.Column("confidence", sa.Float(), nullable=True))
        batch.add_column(sa.Column("match_quality", sa.Float(), nullable=True))
        batch.add_column(sa.Column("page", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("x0", sa.Float(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("top", sa.Float(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("x1", sa.Float(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("bottom", sa.Float(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("caveat", sa.Text(), nullable=True))
        batch.add_column(sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False,
                                   server_default="1970-01-01 00:00:00"))
        batch.create_check_constraint("ck_claims_value_type",
                                      _one_of("value_type", _VALUE_TYPES))
        batch.create_index("ix_claims_document_field", ["document_id", "field"])

    with op.batch_alter_table("claims") as batch:
        for column in ("field", "value", "value_type", "method", "page",
                       "x0", "top", "x1", "bottom", "observed_at"):
            batch.alter_column(column, server_default=None)

    op.create_table(
        "findings",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("document_id", sa.Text(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("expected", sa.Text(), nullable=True),
        sa.Column("actual", sa.Text(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("x0", sa.Float(), nullable=True),
        sa.Column("top", sa.Float(), nullable=True),
        sa.Column("x1", sa.Float(), nullable=True),
        sa.Column("bottom", sa.Float(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_one_of("state", _STATES), name="ck_findings_state"),
    )
    op.create_index("ix_findings_document_order", "findings",
                    ["document_id", "page", "top", "x0"])
    op.create_index("ix_findings_tenant_id_id", "findings", ["tenant_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_findings_tenant_id_id", table_name="findings")
    op.drop_index("ix_findings_document_order", table_name="findings")
    op.drop_table("findings")

    with op.batch_alter_table("claims") as batch:
        batch.drop_index("ix_claims_document_field")
        batch.drop_constraint("ck_claims_value_type", type_="check")
        for column in ("observed_at", "caveat", "bottom", "x1", "top", "x0", "page",
                       "match_quality", "confidence", "method", "value_type", "value",
                       "field"):
            batch.drop_column(column)

    with op.batch_alter_table("documents") as batch:
        batch.drop_index("ix_documents_tenant_sha")
        batch.drop_constraint("ck_documents_doc_type", type_="check")
        batch.drop_constraint("ck_documents_status", type_="check")
        for column in ("run_attempts", "rounding_policy_source", "run_note", "doc_type",
                       "content_sha256", "status"):
            batch.drop_column(column)
```

- [ ] **Step 4: Update the table metadata to match**

In `ledger_sense/store/retention.py`, replace the `documents` and `claims` definitions (lines 44–79) with:

```python
DOCUMENT_STATUSES = ("received", "extracted", "cannot_extract", "duplicate", "failed")
DOC_TYPES = ("pay_application", "continuation_sheet", "invoice", "lien_waiver", "unknown")
FINDING_STATES = ("open", "accepted", "edited", "rejected")
CLAIM_VALUE_TYPES = ("money", "date", "int", "str")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


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
    # 0004: the name the client gave the file. Display only — never a path,
    # never provenance. NOT NULL, no default, same discipline as the label.
    sa.Column("filename", sa.Text, nullable=False),
    # 0005: the run. `status` and `run_attempts` keep server defaults (a row
    # that exists is received, zero times tried); the rest is written by
    # ledger_sense.run.document and NULL until it has something to say.
    sa.Column("status", sa.Text, nullable=False, server_default="received"),
    sa.Column("content_sha256", sa.Text, nullable=True),
    sa.Column("doc_type", sa.Text, nullable=True),
    sa.Column("run_note", sa.Text, nullable=True),
    sa.Column("rounding_policy_source", sa.Text, nullable=True),
    sa.Column("run_attempts", sa.Integer, nullable=False, server_default="0"),
    # Named to match the migrations exactly: SQLite batch recreate keeps named CHECKs.
    sa.CheckConstraint(
        "sensitivity IN (" + ", ".join(f"'{s.value}'" for s in Sensitivity) + ")",
        name="ck_documents_sensitivity"),
    sa.CheckConstraint(
        "origin IN (" + ", ".join(f"'{o.value}'" for o in Origin) + ")",
        name="ck_documents_origin"),
    sa.CheckConstraint(_one_of("status", DOCUMENT_STATUSES), name="ck_documents_status"),
    sa.CheckConstraint(f"doc_type IS NULL OR {_one_of('doc_type', DOC_TYPES)}",
                       name="ck_documents_doc_type"),
    sa.Index("ix_documents_tenant_id_id", "tenant_id", "id"),
    sa.Index("ix_documents_tenant_sha", "tenant_id", "content_sha256",
             sqlite_where=sa.text("content_sha256 IS NOT NULL"),
             postgresql_where=sa.text("content_sha256 IS NOT NULL")),
)

claims = sa.Table(
    "claims",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("tenant_id", sa.Text, sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("document_id", sa.Text, sa.ForeignKey("documents.id"), nullable=False),
    # 0005: one row per Claim the run produced. `value` is TEXT on purpose —
    # Money never leaves as a float. confidence/match_quality/bbox are scores
    # and geometry, the only REAL columns in the store.
    sa.Column("field", sa.Text, nullable=False),
    sa.Column("value", sa.Text, nullable=False),
    sa.Column("value_type", sa.Text, nullable=False),
    sa.Column("method", sa.Text, nullable=False),
    sa.Column("confidence", sa.Float, nullable=True),
    sa.Column("match_quality", sa.Float, nullable=True),
    sa.Column("page", sa.Integer, nullable=False),
    sa.Column("x0", sa.Float, nullable=False),
    sa.Column("top", sa.Float, nullable=False),
    sa.Column("x1", sa.Float, nullable=False),
    sa.Column("bottom", sa.Float, nullable=False),
    sa.Column("caveat", sa.Text, nullable=True),
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(_one_of("value_type", CLAIM_VALUE_TYPES), name="ck_claims_value_type"),
    sa.Index("ix_claims_tenant_id_id", "tenant_id", "id"),
    sa.Index("ix_claims_document_id", "document_id"),
    # NOT unique: Phase 4 writes a HUMAN claim on the same field per accept/edit.
    sa.Index("ix_claims_document_field", "document_id", "field"),
)

findings = sa.Table(
    "findings",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("tenant_id", sa.Text, sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("document_id", sa.Text, sa.ForeignKey("documents.id"), nullable=False),
    sa.Column("rule_id", sa.Text, nullable=False),
    sa.Column("field", sa.Text, nullable=False),
    sa.Column("message", sa.Text, nullable=False),
    sa.Column("expected", sa.Text, nullable=True),
    sa.Column("actual", sa.Text, nullable=True),
    sa.Column("page", sa.Integer, nullable=True),
    sa.Column("x0", sa.Float, nullable=True),
    sa.Column("top", sa.Float, nullable=True),
    sa.Column("x1", sa.Float, nullable=True),
    sa.Column("bottom", sa.Float, nullable=True),
    sa.Column("state", sa.Text, nullable=False, server_default="open"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(_one_of("state", FINDING_STATES), name="ck_findings_state"),
    sa.Index("ix_findings_document_order", "document_id", "page", "top", "x0"),
    sa.Index("ix_findings_tenant_id_id", "tenant_id", "id"),
)
```

Keep the existing `retention_audit_log` table and the comment about `audit` below it unchanged.

- [ ] **Step 5: Fix the store conftest's stub claim seed**

In `tests/store/conftest.py`, the `claims.insert()` inside `seeded_documents` (around line 150) now needs every NOT NULL column:

```python
            conn.execute(claims.insert().values(
                id=uuid.uuid4().hex,
                tenant_id=tenant_id,
                document_id=EXPIRED_DOCUMENT_ID,
                field="total_completed_and_stored",
                value="0.00",
                value_type="money",
                method="deterministic",
                page=0, x0=0.0, top=0.0, x1=0.0, bottom=0.0,
                observed_at=datetime.now(UTC),
            ))
```

- [ ] **Step 6: Run the store tests**

Run: `uv run pytest tests/store -v`
Expected: PASS, including the existing `_assert_migration_builds_the_schema_the_code_queries` (metadata ⊆ migrated columns for every table) and the new 0005 test.

- [ ] **Step 7: Lint, type-check, commit**

Run: `uv run ruff check . && uv run mypy --strict ledger_sense`
Expected: clean.

```bash
git add migrations/versions/0005_extraction_run.py ledger_sense/store/retention.py tests/store/conftest.py tests/store/test_migrations.py
git commit -m "feat(store): 0005 — documents.status, claims value columns, findings table

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Retention purge deletes findings

**Files:**
- Modify: `ledger_sense/store/retention.py:100-108` (`PurgeReport`), `:150-216` (`purge_expired`)
- Test: `tests/store/test_retention.py`

**Interfaces:**
- Consumes: `findings` table from Task 1.
- Produces: `PurgeReport.findings_purged: int`.

- [ ] **Step 1: Write the failing test**

Append to `tests/store/test_retention.py` (reuse the module's existing imports; add `from ledger_sense.store.retention import findings` and `import uuid` if not present):

```python
def test_purge_removes_the_findings_derived_from_an_expired_document(
    seeded_documents: SeededDocuments,
) -> None:
    with store_session.app_engine().begin() as conn:
        conn.execute(findings.insert().values(
            id=uuid.uuid4().hex, tenant_id=seeded_documents.tenant_id,
            document_id=EXPIRED_DOCUMENT_ID, rule_id="A1", field="total_completed_and_stored",
            message="does not foot", expected="1.00", actual="2.00",
            page=0, x0=1.0, top=1.0, x1=2.0, bottom=2.0, created_at=datetime.now(UTC)))
    report = purge_expired(now=datetime.now(UTC), blobs=None)
    assert report.findings_purged == 1
    with store_session.app_engine().connect() as conn:
        left = conn.execute(
            sa.select(findings).where(findings.c.document_id == EXPIRED_DOCUMENT_ID)).all()
    assert left == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/store/test_retention.py -k findings -v`
Expected: FAIL — `PurgeReport` has no attribute `findings_purged`.

- [ ] **Step 3: Implement**

In `ledger_sense/store/retention.py`:

```python
@dataclass(frozen=True)
class PurgeReport:
    """What one run actually removed. Reported, not inferred — `00`'s standing
    objection to claiming an outcome nobody measured."""

    documents_purged: int = 0
    claims_purged: int = 0
    blobs_deleted: int = 0
    findings_purged: int = 0
```

In `purge_expired`, initialise `findings_purged = 0` beside the others, and inside the loop, immediately before `deleted_claims = ...`:

```python
            deleted_findings = conn.execute(
                findings.delete().where(findings.c.document_id == row.id)
            ).rowcount
```

after `claims_purged += deleted_claims` add `findings_purged += deleted_findings`, and return
`PurgeReport(documents_purged=purged, claims_purged=claims_purged, blobs_deleted=blobs_deleted, findings_purged=findings_purged)`.

Update the module docstring's chain line (`the encrypted original -> the claims derived from it -> the row itself`) to `-> the claims and findings derived from it ->`.

- [ ] **Step 4: Run, lint, commit**

Run: `uv run pytest tests/store -v && uv run ruff check . && uv run mypy --strict ledger_sense`
Expected: PASS / clean.

```bash
git add ledger_sense/store/retention.py tests/store/test_retention.py
git commit -m "feat(store): purge deletes findings with the document and reports it

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `store_document` writes the plaintext hash

**Files:**
- Modify: `ledger_sense/store/documents.py:36-78`
- Test: `tests/store/test_documents.py`

**Interfaces:**
- Produces: `documents.content_sha256` populated (hex, lowercase) for every row the one writer inserts.

- [ ] **Step 1: Write the failing tests**

Append to `tests/store/test_documents.py` (module already imports `sa`, `store_session`, `documents`, `store_document`, `accept_document`, `Origin`, and defines `blobs`, `RECEIVED_AT`, `_labelled`):

```python
import hashlib


def test_the_row_carries_the_sha256_of_the_plaintext(
    seeded_documents: SeededDocuments, blobs: BlobStore,
) -> None:
    doc = _labelled(Origin.CORPUS)
    with store_session.app_engine().begin() as conn:
        document_id = store_document(conn, tenant_id=seeded_documents.tenant_id, doc=doc,
                                     blobs=blobs, received_at=RECEIVED_AT, filename="a.pdf")
    row = seeded_documents.documents_for(document_id)[0]
    assert row.content_sha256 == hashlib.sha256(doc.payload).hexdigest()


def test_the_same_bytes_twice_are_two_rows_with_one_hash(
    seeded_documents: SeededDocuments, blobs: BlobStore,
) -> None:
    doc = _labelled(Origin.CORPUS)
    with store_session.app_engine().begin() as conn:
        first = store_document(conn, tenant_id=seeded_documents.tenant_id, doc=doc,
                               blobs=blobs, received_at=RECEIVED_AT, filename="a.pdf")
        second = store_document(conn, tenant_id=seeded_documents.tenant_id, doc=doc,
                                blobs=blobs, received_at=RECEIVED_AT, filename="a.pdf")
    assert first != second
    rows = seeded_documents.documents_for(first) + seeded_documents.documents_for(second)
    assert len({r.content_sha256 for r in rows}) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/store/test_documents.py -k sha256 -v`
Expected: FAIL — `row.content_sha256` is `None`.

- [ ] **Step 3: Implement**

In `store_document`, add `import hashlib` at the top of the module and `content_sha256=hashlib.sha256(doc.payload).hexdigest(),` to the `documents.insert().values(...)` call. Extend the docstring: `"""Encrypt the payload, insert the row with its label, filename and the SHA-256 of the plaintext, return the new id. The hash is of plaintext on purpose: it is how the run recognises a byte-identical re-submission within a tenant, and it reveals nothing but equality."""`

- [ ] **Step 4: Run, lint, commit**

Run: `uv run pytest tests/store tests/api/test_inbox.py -v && uv run ruff check . && uv run mypy --strict ledger_sense`
Expected: PASS / clean.

```bash
git add ledger_sense/store/documents.py tests/store/test_documents.py
git commit -m "feat(store): the one writer records the plaintext sha256 of every document

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `run_rules(exclude=...)` and the contract-surface guard

**Files:**
- Modify: `ledger_sense/rules/engine.py:17-33`
- Create: `tests/rules/test_contract_surface.py`
- Test: `tests/rules/test_engine.py`

**Interfaces:**
- Produces: `run_rules(app, contract, *, rule_classes=None, exclude: Iterable[str] = frozenset()) -> list[Finding]`; constant `ledger_sense.rules.engine.CONTRACT_DEPENDENT_RULES: frozenset[str] = frozenset({"A7"})`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/rules/test_engine.py` (it already has `_contract()`, `_app()`, imports `run_rules`, `register`, `Finding`, `money`):

```python
def test_exclude_skips_exactly_the_named_rules() -> None:
    @register("T_excluded", "arithmetic")
    def _excluded(app: PayApplication, contract: Contract) -> list[Finding]:
        return [Finding("T_excluded", "x", "fired", money("0"), money("1"))]

    @register("T_kept", "arithmetic")
    def _kept(app: PayApplication, contract: Contract) -> list[Finding]:
        return [Finding("T_kept", "x", "fired", money("0"), money("1"))]

    fired = {f.rule_id for f in run_rules(_app(), _contract(), exclude={"T_excluded"})}
    assert "T_kept" in fired
    assert "T_excluded" not in fired
```

Create `tests/rules/test_contract_surface.py`:

```python
"""Which contract fields each registered rule reads — pinned by AST.

The extraction run supplies a tenant-default Contract whose only real field
is `rounding` (spec §2 step 5). Any rule that reads more than that must be
listed in CONTRACT_DEPENDENT_RULES so the run excludes it; otherwise it runs
against placeholder values and reports a confident, wrong finding.
"""

import ast
import inspect
import textwrap

from ledger_sense.rules.arithmetic import foot, lines, payment  # noqa: F401 — registers
from ledger_sense.rules.engine import CONTRACT_DEPENDENT_RULES
from ledger_sense.rules.registry import registered_rules

# Methods on PayApplication that themselves read the contract beyond `rounding`.
_CONTRACT_READING_METHODS = {"adjusted_contract_sum"}


def _contract_reads(fn: object) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))  # type: ignore[arg-type]
    func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
    param = func.args.args[1].arg  # (app, contract)
    reads: set[str] = set()
    for node in ast.walk(func):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == param):
            reads.add(node.attr)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _CONTRACT_READING_METHODS):
            reads.add(f"via {node.func.attr}")
    return reads


def test_every_rule_the_run_executes_reads_only_rounding() -> None:
    offenders = {}
    for rule_id, rule in registered_rules().items():
        if rule_id.startswith("T_"):
            continue  # test-registered rules from other modules
        reads = _contract_reads(rule.fn) - {"rounding"}
        if reads and rule_id not in CONTRACT_DEPENDENT_RULES:
            offenders[rule_id] = reads
    assert offenders == {}, f"rules reading contract fields the run cannot supply: {offenders}"


def test_a7_is_the_contract_dependent_rule() -> None:
    assert "A7" in CONTRACT_DEPENDENT_RULES
    assert "via adjusted_contract_sum" in _contract_reads(registered_rules()["A7"].fn)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/rules/test_engine.py::test_exclude_skips_exactly_the_named_rules tests/rules/test_contract_surface.py -v`
Expected: FAIL — `run_rules() got an unexpected keyword argument 'exclude'`; `ImportError: CONTRACT_DEPENDENT_RULES`.

- [ ] **Step 3: Implement**

In `ledger_sense/rules/engine.py`:

```python
# Rules that read Contract fields an uploaded document cannot supply until a
# contracts table exists. The extraction run excludes them; a rule that runs
# against a placeholder original_sum reports a confident, wrong finding.
# tests/rules/test_contract_surface.py pins this set against the rule bodies.
CONTRACT_DEPENDENT_RULES: frozenset[str] = frozenset({"A7"})


def run_rules(
    app: PayApplication,
    contract: Contract,
    *,
    rule_classes: Iterable[RuleClass] | None = None,
    exclude: Iterable[str] = frozenset(),
) -> list[Finding]:
    """Run every registered rule (or only the given rule classes, minus `exclude`)
    and collect findings.

    Replaces the `ALL_RULES = [...]` list that was being hand-built at each call
    site. A rule that trusts a defaulted Money("0.00") still reports "consistent"
    here — this function answers "did the rules agree", not "was the data real".
    See `unverifiable_money_fields` for the second question.
    """
    wanted = None if rule_classes is None else frozenset(rule_classes)
    skipped = frozenset(exclude)
    findings: list[Finding] = []
    for rule in registered_rules().values():
        if wanted is not None and rule.rule_class not in wanted:
            continue
        if rule.rule_id in skipped:
            continue
        findings.extend(rule.fn(app, contract))
    return findings
```

- [ ] **Step 4: Run, lint, commit**

Run: `uv run pytest tests/rules -v && uv run ruff check . && uv run mypy --strict ledger_sense`
Expected: PASS / clean.

```bash
git add ledger_sense/rules/engine.py tests/rules/test_engine.py tests/rules/test_contract_surface.py
git commit -m "feat(rules): run_rules(exclude=); A7 pinned as the contract-dependent rule

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Runner package — outcomes, the row guard, the tenant-default contract

**Files:**
- Create: `ledger_sense/run/__init__.py`, `ledger_sense/run/document.py`
- Create: `tests/run/__init__.py` (empty), `tests/run/conftest.py`, `tests/run/test_document.py`

**Interfaces:**
- Consumes: `documents`, `claims`, `findings` tables (Task 1); `load_document` (`store/documents.py`); `blob_store_from_environment` (`store/blobs.py`).
- Produces:
  ```python
  class RunOutcome(NamedTuple):
      document_id: str
      status: str          # row status after the call, or "missing"
      note: str | None
      claims_written: int
      findings_written: int

  RUNNABLE = frozenset({"received", "failed"})
  def run_document(engine: Engine, document_id: str, *, blobs: BlobStore | None = None) -> RunOutcome
  ```
  Later tasks fill in `_execute()`; this task ships the guard and the "already"/"missing"/"human decisions" outcomes with `_execute` returning a placeholder `cannot_extract` (replaced in Task 6).

- [ ] **Step 1: Write the run-test fixtures**

Create `tests/run/conftest.py`:

```python
"""Fixtures for the runner tests.

A real SQLite database built by `alembic upgrade head` (the same override
tests/api and tests/store use) plus a real BlobStore reached the way the
runner reaches it — LEDGER_SENSE_KEK / LEDGER_SENSE_BLOB_ROOT — so the test
exercises load_document's decrypt path, not a stub.

`RunDb.store_pdf` puts bytes through the labelled door as CORPUS/SYNTHETIC
(the only label a test may assert) and the one writer; it is the only way a
test here gets a document row.
"""

import pathlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from sqlalchemy import Row
from sqlalchemy.engine import Engine

from ledger_sense.auth.models import create_tenant
from ledger_sense.boundary import Origin
from ledger_sense.ingest.intake import accept_document
from ledger_sense.store import session as store_session
from ledger_sense.store.blobs import blob_store_from_environment
from ledger_sense.store.documents import store_document
from ledger_sense.store.retention import claims, documents, findings

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


class RunDb:
    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self.engine = engine
        self.tenant_id = tenant_id

    def store_pdf(self, pdf: bytes, *, tenant_id: str | None = None,
                  received_at: datetime | None = None, filename: str = "payapp.pdf") -> str:
        doc = accept_document(pdf, origin=Origin.CORPUS, provenance="tests/run/conftest.py")
        with self.engine.begin() as conn:
            return store_document(
                conn, tenant_id=tenant_id or self.tenant_id, doc=doc,
                blobs=blob_store_from_environment(),
                received_at=received_at or datetime.now(UTC), filename=filename)

    def document(self, document_id: str) -> Row[tuple[object, ...]]:
        with self.engine.connect() as conn:
            return conn.execute(
                sa.select(documents).where(documents.c.id == document_id)).one()

    def claims_for(self, document_id: str) -> list[Row[tuple[object, ...]]]:
        with self.engine.connect() as conn:
            return list(conn.execute(
                sa.select(claims).where(claims.c.document_id == document_id)).all())

    def findings_for(self, document_id: str) -> list[Row[tuple[object, ...]]]:
        with self.engine.connect() as conn:
            return list(conn.execute(
                sa.select(findings).where(findings.c.document_id == document_id)
                .order_by(findings.c.rule_id, findings.c.field)).all())

    def new_tenant(self) -> str:
        return create_tenant(f"tenant-{uuid.uuid4().hex[:8]}")


@pytest.fixture
def db(tmp_path: pathlib.Path) -> Iterator[RunDb]:
    from alembic import command
    from alembic.config import Config

    database = (tmp_path / "run.db").as_posix()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("LEDGER_SENSE_DB_URL", f"sqlite:///{database}")
        patch.setenv("LEDGER_SENSE_KEK", Fernet.generate_key().decode())
        patch.setenv("LEDGER_SENSE_BLOB_ROOT", str(tmp_path / "blobs"))
        store_session.reset_app_engine()
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        command.upgrade(config, "head")
        engine = store_session.app_engine()
        yield RunDb(engine, create_tenant(f"tenant-{uuid.uuid4().hex[:8]}"))
    store_session.reset_app_engine()
```

Create `tests/run/__init__.py` (empty). Check `create_tenant`'s signature in `ledger_sense/auth/models.py` — the api conftest calls it as `create_tenant("seed")` and gets an id back; if it also needs an engine or connection, pass `engine`.

- [ ] **Step 2: Write the failing guard tests**

Create `tests/run/test_document.py`:

```python
"""run_document: the guard. Extraction behaviour arrives in later tests."""

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from ledger_sense.corpus.generate import generate
from ledger_sense.run.document import RunOutcome, run_document
from ledger_sense.store.retention import documents, findings
from tests.run.conftest import RunDb


def _clean_pdf(seed: int = 1) -> bytes:
    return generate(seed=seed, error="clean")[2]


def test_a_missing_document_is_reported_not_raised(db: RunDb) -> None:
    outcome = run_document(db.engine, "nope")
    assert outcome == RunOutcome("nope", "missing", None, 0, 0)


def test_a_document_that_is_not_runnable_is_left_alone(db: RunDb) -> None:
    document_id = db.store_pdf(_clean_pdf())
    with db.engine.begin() as conn:
        conn.execute(documents.update().where(documents.c.id == document_id)
                     .values(status="extracted", run_attempts=3))
    outcome = run_document(db.engine, document_id)
    assert outcome.status == "extracted"
    assert outcome.note == "already extracted"
    assert db.document(document_id).run_attempts == 3


def test_every_run_increments_the_attempt_counter(db: RunDb) -> None:
    document_id = db.store_pdf(_clean_pdf())
    run_document(db.engine, document_id)
    assert db.document(document_id).run_attempts == 1


def test_a_document_with_a_human_decision_is_never_overwritten(db: RunDb) -> None:
    document_id = db.store_pdf(_clean_pdf())
    with db.engine.begin() as conn:
        conn.execute(documents.update().where(documents.c.id == document_id)
                     .values(status="failed"))
        conn.execute(findings.insert().values(
            id="f1", tenant_id=db.tenant_id, document_id=document_id, rule_id="A1",
            field="total_completed_and_stored", message="m", state="accepted",
            created_at=datetime.now(UTC)))
    outcome = run_document(db.engine, document_id)
    assert outcome.status == "failed"
    assert outcome.note == "human decisions present; refusing to overwrite"
    with db.engine.connect() as conn:
        assert conn.execute(sa.select(findings.c.state)
                            .where(findings.c.document_id == document_id)).scalar() == "accepted"
```

(`pytest` is imported now because Task 6's tests in the same module use `monkeypatch`.)

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/run -v`
Expected: FAIL — `ModuleNotFoundError: ledger_sense.run`.

- [ ] **Step 4: Implement the package and the guard**

Create `ledger_sense/run/__init__.py`:

```python
"""The run: a stored document through the deterministic pipeline, persisted.

Sits above extract/, rules/ and store/ and below api/. Nothing in
extract/ or rules/ imports this package. Deterministic only — no gateway,
no provider client, no OCR escalation (tests/boundary pins it).
"""
```

Create `ledger_sense/run/document.py`:

```python
"""run_document: one stored document → Claims + Findings + an honest status.

Spec: docs/superpowers/specs/2026-09-15-extraction-run-design.md §2.

The runner owns its transactions (it needs two on failure), so it takes an
Engine. Its first statement is a WRITE — `run_attempts = run_attempts + 1
WHERE status IN (runnable)` — because that is what takes the row lock on
Postgres AND the database write lock on SQLite; `SELECT ... FOR UPDATE` is a
no-op on SQLite and two runners could both read `received`.

Nothing here raises for a document-level problem: the document ends up
`cannot_extract`, `duplicate` or `failed` with a note. Infrastructure
failures (DB, blob store, KEK) propagate.
"""

import logging
from typing import NamedTuple

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from ledger_sense.domain.models import Contract, RetainageTerms
from ledger_sense.domain.money import RoundingPolicy, money
from ledger_sense.store.blobs import BlobStore, blob_store_from_environment
from ledger_sense.store.retention import documents, findings

log = logging.getLogger(__name__)

RUNNABLE = frozenset({"received", "failed"})
HUMAN_DECISIONS_NOTE = "human decisions present; refusing to overwrite"
ROUNDING_POLICY_SOURCE = "tenant_default"


class RunOutcome(NamedTuple):
    document_id: str
    status: str
    """The row's status after the call — or "missing", which is an outcome, not a status."""
    note: str | None
    claims_written: int
    findings_written: int


def _tenant_default_contract() -> Contract:
    """The Contract the arithmetic rules are run against until a contracts
    table exists. Only `rounding` is real; every rule the run executes reads
    nothing else (tests/rules/test_contract_surface.py), and A7 — which does —
    is excluded. Never persisted."""
    return Contract(
        id="tenant-default",
        original_sum=money("0.00"),
        rounding=RoundingPolicy(money_places=2, percent_places=2),
        retainage=RetainageTerms(base_rate=money("0.00")),
    )


def _take_row(conn: Connection, document_id: str) -> RunOutcome | sa.Row[tuple[object, ...]]:
    taken = conn.execute(
        documents.update()
        .where(documents.c.id == document_id, documents.c.status.in_(RUNNABLE))
        .values(run_attempts=documents.c.run_attempts + 1)
    ).rowcount
    row = conn.execute(
        sa.select(documents).where(documents.c.id == document_id)).one_or_none()
    if row is None:
        return RunOutcome(document_id, "missing", None, 0, 0)
    if taken == 0:
        return RunOutcome(document_id, str(row.status), f"already {row.status}", 0, 0)
    decided = conn.execute(
        sa.select(sa.func.count()).select_from(findings)
        .where(findings.c.document_id == document_id, findings.c.state != "open")
    ).scalar_one()
    if decided:
        return RunOutcome(document_id, str(row.status), HUMAN_DECISIONS_NOTE, 0, 0)
    return row


def run_document(engine: Engine, document_id: str, *,
                 blobs: BlobStore | None = None) -> RunOutcome:
    """Run one document. Safe to call again: a row that is not `received` or
    `failed`, or that carries a human decision, is left exactly as it was."""
    store = blobs if blobs is not None else blob_store_from_environment()
    with engine.begin() as conn:
        taken = _take_row(conn, document_id)
        if isinstance(taken, RunOutcome):
            return taken
        return _execute(conn, taken, store)


def _execute(conn: Connection, row: sa.Row[tuple[object, ...]], store: BlobStore) -> RunOutcome:
    # Filled in by the extraction tasks. Until then every runnable document is
    # honestly "not extracted" rather than silently "extracted".
    conn.execute(documents.update().where(documents.c.id == row.id).values(
        status="cannot_extract", run_note="runner not yet implemented"))
    return RunOutcome(str(row.id), "cannot_extract", "runner not yet implemented", 0, 0)
```

Check `RetainageTerms`'s required fields in `ledger_sense/domain/models.py:46-58` before running — if `base_rate` is the only required field the above is right; otherwise supply the others with zero values.

- [ ] **Step 5: Run to verify passing**

Run: `uv run pytest tests/run -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Lint, type-check, commit**

Run: `uv run ruff check . && uv run mypy --strict ledger_sense`
Expected: clean.

```bash
git add ledger_sense/run tests/run
git commit -m "feat(run): runner package — write-first row guard, outcomes, tenant-default contract

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: The honest refusals — no text layer, wrong doc type, failure

**Files:**
- Modify: `ledger_sense/run/document.py` (`run_document`, `_execute`)
- Test: `tests/run/test_document.py`

**Interfaces:**
- Consumes: `has_text_layer` (`extract/textlayer.py`), `classify` (`ingest/classify.py`), `assemble_pay_application` (`extract/assemble.py`), `load_document`.
- Produces: `_execute(conn, row, store)` structure; `_RunFailure(cause, doc_type)`; `_mark_failed(engine, document_id, cause, doc_type)`; `NO_TEXT_LAYER_NOTE`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/run/test_document.py`:

```python
import io

import pypdfium2 as pdfium


def _blank_pdf() -> bytes:
    doc = pdfium.PdfDocument.new()
    doc.new_page(612, 792)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _invoice_pdf() -> bytes:
    # The corpus generator's private text helpers, used the way generate.py
    # itself uses them (ledger_sense/corpus/generate.py:63-107). Check the
    # positional order of _insert_text there before running.
    from ledger_sense.corpus.generate import _insert_text
    doc = pdfium.PdfDocument.new()
    page = doc.new_page(612, 792)
    _insert_text(page, doc, "INVOICE 1042", 72, 700, 12)
    _insert_text(page, doc, "Remit to: Acme Concrete", 72, 680, 12)
    page.gen_content()
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def test_a_pdf_with_no_text_layer_is_refused_honestly(db: RunDb) -> None:
    document_id = db.store_pdf(_blank_pdf())
    outcome = run_document(db.engine, document_id)
    assert outcome.status == "cannot_extract"
    row = db.document(document_id)
    assert row.status == "cannot_extract"
    assert row.doc_type is None
    assert row.run_note.startswith("no text layer")
    assert db.claims_for(document_id) == []


def test_a_document_that_is_not_a_pay_application_is_refused_with_its_type(db: RunDb) -> None:
    document_id = db.store_pdf(_invoice_pdf())
    outcome = run_document(db.engine, document_id)
    assert outcome.status == "cannot_extract"
    row = db.document(document_id)
    assert row.doc_type == "invoice"
    assert row.run_note == "doc_type=invoice: only pay applications are extracted in v1"
    assert db.claims_for(document_id) == []


def test_an_exception_inside_extraction_marks_failed_with_the_class_only(
    db: RunDb, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ledger_sense.run.document as run_module

    def _boom(pdf: bytes, source_path: str) -> object:
        raise ValueError("page text: Retainage 10% of $1,234.56")
    monkeypatch.setattr(run_module, "assemble_pay_application", _boom)
    document_id = db.store_pdf(_clean_pdf())
    outcome = run_document(db.engine, document_id)
    assert outcome.status == "failed"
    row = db.document(document_id)
    assert row.status == "failed"
    assert row.run_note == "failed: ValueError"
    assert "1,234.56" not in row.run_note
    assert row.doc_type == "pay_application"  # classified before the failure
    assert db.claims_for(document_id) == []
    assert row.run_attempts == 1


def test_a_failed_document_is_runnable_again(db: RunDb, monkeypatch: pytest.MonkeyPatch) -> None:
    import ledger_sense.run.document as run_module
    document_id = db.store_pdf(_clean_pdf())

    def _boom(pdf: bytes, source_path: str) -> object:
        raise RuntimeError("x")
    monkeypatch.setattr(run_module, "assemble_pay_application", _boom)
    assert run_document(db.engine, document_id).status == "failed"
    monkeypatch.undo()
    outcome = run_document(db.engine, document_id)
    assert outcome.status != "failed"
    assert db.document(document_id).run_attempts == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/run -v`
Expected: the four new tests FAIL (`run_note == "runner not yet implemented"`; `assemble_pay_application` not an attribute of the module).

- [ ] **Step 3: Implement**

In `ledger_sense/run/document.py`, add imports:

```python
from ledger_sense.extract.assemble import assemble_pay_application
from ledger_sense.extract.textlayer import has_text_layer
from ledger_sense.ingest.classify import classify
from ledger_sense.store.documents import load_document
```

Add the failure wrapper and note constant:

```python
NO_TEXT_LAYER_NOTE = "no text layer; scanned documents route to review until OCR routing ships"


class _RunFailure(Exception):
    """Carries what was learned before the failure (the doc_type) out of the
    transaction that is about to roll back."""

    def __init__(self, cause: Exception, doc_type: str | None) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.doc_type = doc_type
```

Replace `run_document`:

```python
def run_document(engine: Engine, document_id: str, *,
                 blobs: BlobStore | None = None) -> RunOutcome:
    """Run one document. Safe to call again: a row that is not `received` or
    `failed`, or that carries a human decision, is left exactly as it was."""
    store = blobs if blobs is not None else blob_store_from_environment()
    try:
        with engine.begin() as conn:
            taken = _take_row(conn, document_id)
            if isinstance(taken, RunOutcome):
                return taken
            return _execute(conn, taken, store)
    except _RunFailure as failure:
        return _mark_failed(engine, document_id, failure.cause, failure.doc_type)


def _mark_failed(engine: Engine, document_id: str, cause: Exception,
                 doc_type: str | None) -> RunOutcome:
    # The transaction above has rolled back: no partial claims survive, and
    # the attempt increment rolled back with it — re-applied here. The
    # message may carry page text, so it goes to the log; the row gets the
    # class only. If the guarded UPDATE matches nothing the row changed
    # under us, which means the lock did not hold — re-raise.
    note = f"failed: {type(cause).__name__}"
    with engine.begin() as conn:
        tenant = conn.execute(sa.select(documents.c.tenant_id)
                              .where(documents.c.id == document_id)).scalar_one_or_none()
        log.warning("run failed document_id=%s tenant_id=%s: %r", document_id, tenant, cause)
        updated = conn.execute(
            documents.update()
            .where(documents.c.id == document_id, documents.c.status.in_(RUNNABLE))
            .values(status="failed", run_note=note, doc_type=doc_type,
                    run_attempts=documents.c.run_attempts + 1)
        ).rowcount
    if updated == 0:
        raise cause
    return RunOutcome(document_id, "failed", note, 0, 0)
```

Replace `_execute`:

```python
def _refuse(conn: Connection, row: sa.Row[tuple[object, ...]], note: str,
            doc_type: str | None) -> RunOutcome:
    conn.execute(documents.update().where(documents.c.id == row.id).values(
        status="cannot_extract", run_note=note, doc_type=doc_type))
    return RunOutcome(str(row.id), "cannot_extract", note, 0, 0)


def _execute(conn: Connection, row: sa.Row[tuple[object, ...]], store: BlobStore) -> RunOutcome:
    document_id, tenant_id = str(row.id), str(row.tenant_id)
    pdf = load_document(conn, tenant_id=tenant_id, document_id=document_id, blobs=store).payload
    if not has_text_layer(pdf):
        return _refuse(conn, row, NO_TEXT_LAYER_NOTE, None)
    doc_type = classify(pdf)
    if doc_type != "pay_application":
        return _refuse(conn, row, f"doc_type={doc_type}: only pay applications are "
                                  "extracted in v1", doc_type)
    conn.execute(documents.update().where(documents.c.id == document_id)
                 .values(doc_type=doc_type))
    try:
        app = assemble_pay_application(pdf, document_id)
        # Claims, findings and the final status arrive in the next two tasks.
        conn.execute(documents.update().where(documents.c.id == document_id).values(
            status="cannot_extract", run_note="persistence not yet implemented"))
        return RunOutcome(document_id, "cannot_extract", "persistence not yet implemented", 0, 0)
    except Exception as exc:  # noqa: BLE001 — every document-level error becomes `failed`
        raise _RunFailure(exc, doc_type) from exc
```

Infrastructure errors raised *before* the `try` (`load_document` → `DocumentNotFound`, blob store, KEK) propagate as they should; a `DocumentNotFound` for a row that has no blob is exactly that kind of error.

- [ ] **Step 4: Run to verify passing**

Run: `uv run pytest tests/run -v`
Expected: PASS. The guard tests still pass (`_execute`'s placeholder now says "persistence not yet implemented"; the "runnable again" test only asserts `!= "failed"`).

- [ ] **Step 5: Lint, type-check, commit**

Run: `uv run ruff check . && uv run mypy --strict ledger_sense`
Expected: clean. (`app` is assigned and unused in the placeholder — ruff F841; add `del app` or `_ = app` for this commit only; Task 7 uses it.)

```bash
git add ledger_sense/run/document.py tests/run/test_document.py
git commit -m "feat(run): honest refusals — no text layer, wrong doc type, failure with class-only note

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Persist Claims and X0 findings

**Files:**
- Modify: `ledger_sense/run/document.py`
- Test: `tests/run/test_document.py`

**Interfaces:**
- Consumes: `Claim`, `FileSource` (`ledger_sense/provenance.py`), `UnextractableField` (`extract/assemble.py`), `PayApplication` (`domain/models.py`).
- Produces: `_claim_rows(tenant_id, document_id, app) -> list[dict[str, object]]`, `_x0_rows(tenant_id, document_id, app, now) -> list[dict[str, object]]`, `_value_of(value) -> tuple[str, str]` (`(text, value_type)`), `_write(conn, row, claim_rows, finding_rows) -> RunOutcome`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/run/test_document.py`:

```python
from decimal import Decimal

from ledger_sense.extract.assemble import UnextractableField, assemble_pay_application
from ledger_sense.provenance import Claim


def test_a_clean_document_is_extracted_with_one_claim_per_provenance_entry(db: RunDb) -> None:
    pdf = _clean_pdf()
    document_id = db.store_pdf(pdf)
    outcome = run_document(db.engine, document_id)
    assert outcome.status == "extracted"
    row = db.document(document_id)
    assert row.status == "extracted"
    assert row.doc_type == "pay_application"
    assert row.run_note is None
    assert row.rounding_policy_source == "tenant_default"

    expected = {k: v for k, v in assemble_pay_application(pdf, document_id).provenance.items()
                if not k.startswith("_")}
    stored = {c.field: c for c in db.claims_for(document_id)}
    assert set(stored) == set(expected)
    assert outcome.claims_written == len(expected)
    for field, claim in expected.items():
        assert isinstance(claim, Claim)
        s = stored[field]
        assert s.tenant_id == db.tenant_id
        assert s.method == "deterministic"
        assert s.page == claim.source.page
        assert (s.x0, s.top, s.x1, s.bottom) == claim.source.bbox
        if isinstance(claim.value, Decimal):
            assert s.value_type == "money"
            assert Decimal(s.value) == claim.value


def test_claims_keep_money_as_text_never_float(db: RunDb) -> None:
    document_id = db.store_pdf(_clean_pdf())
    run_document(db.engine, document_id)
    money_claims = [c for c in db.claims_for(document_id) if c.value_type == "money"]
    assert money_claims
    assert all(isinstance(c.value, str) for c in money_claims)


def test_an_unextractable_field_becomes_an_x0_finding_without_a_box(
    db: RunDb, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ledger_sense.run.document as run_module

    real = run_module.assemble_pay_application

    def _drop_previous_payments(pdf: bytes, source_path: str) -> object:
        app = real(pdf, source_path)
        app.provenance.pop("previous_payments")
        app.provenance["_unextractable"].append(
            UnextractableField("previous_payments", "label not found on page"))
        return app
    monkeypatch.setattr(run_module, "assemble_pay_application", _drop_previous_payments)

    document_id = db.store_pdf(_clean_pdf())
    outcome = run_document(db.engine, document_id)
    assert outcome.status == "extracted"
    x0 = [f for f in db.findings_for(document_id) if f.rule_id == "X0"]
    assert [f.field for f in x0] == ["previous_payments"]
    assert x0[0].message == "could not extract: label not found on page"
    assert x0[0].page is None and x0[0].x0 is None
    assert x0[0].state == "open"
    assert "previous_payments" not in {c.field for c in db.claims_for(document_id)}
```

`PayApplication` is a pydantic model; `app.provenance` is a plain `dict` so `pop`/`append` on it work in place.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/run -v -k "claim or x0"`
Expected: FAIL — status is `cannot_extract` / "persistence not yet implemented".

- [ ] **Step 3: Implement**

In `ledger_sense/run/document.py` add imports `import uuid`, `from datetime import UTC, date, datetime`, `from decimal import Decimal`, `from ledger_sense.domain.models import PayApplication` (extend the existing `domain.models` import), `from ledger_sense.provenance import Claim, FileSource`, and extend the retention import to `claims, documents, findings`. Then:

```python
def _value_of(value: object) -> tuple[str, str]:
    """(text, value_type). Money first — a Decimal is never a float on the way out."""
    if isinstance(value, Decimal):
        return str(value), "money"
    if isinstance(value, date):
        return value.isoformat(), "date"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value), "int"
    return str(value), "str"


def _claim_rows(tenant_id: str, document_id: str, app: PayApplication) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for field, claim in app.provenance.items():
        if field.startswith("_") or not isinstance(claim, Claim):
            continue
        source = claim.source
        if not isinstance(source, FileSource):
            continue  # the run only ever produces file-sourced claims
        text, value_type = _value_of(claim.value)
        x0, top, x1, bottom = source.bbox
        rows.append({
            "id": uuid.uuid4().hex, "tenant_id": tenant_id, "document_id": document_id,
            "field": field, "value": text, "value_type": value_type,
            "method": claim.method.value, "confidence": claim.confidence,
            "match_quality": claim.match_quality, "page": source.page,
            "x0": x0, "top": top, "x1": x1, "bottom": bottom,
            "caveat": claim.caveat, "observed_at": claim.observed_at,
        })
    return rows


def _x0_rows(tenant_id: str, document_id: str, app: PayApplication,
             now: datetime) -> list[dict[str, object]]:
    entries = app.provenance.get("_unextractable", [])
    if not isinstance(entries, list):
        return []
    return [{
        "id": uuid.uuid4().hex, "tenant_id": tenant_id, "document_id": document_id,
        "rule_id": "X0", "field": entry.field,
        "message": f"could not extract: {entry.reason}",
        "expected": None, "actual": None,
        "page": None, "x0": None, "top": None, "x1": None, "bottom": None,
        "state": "open", "created_at": now,
    } for entry in entries]


def _write(conn: Connection, row: sa.Row[tuple[object, ...]],
           claim_rows: list[dict[str, object]],
           finding_rows: list[dict[str, object]]) -> RunOutcome:
    document_id = str(row.id)
    # Replace in place: everything here is `open` (the guard checked), and a
    # re-run's answer supersedes the previous one entirely.
    conn.execute(findings.delete().where(findings.c.document_id == document_id))
    conn.execute(claims.delete().where(claims.c.document_id == document_id))
    if claim_rows:
        conn.execute(claims.insert(), claim_rows)
    if finding_rows:
        conn.execute(findings.insert(), finding_rows)
    updated = conn.execute(
        documents.update()
        .where(documents.c.id == document_id, documents.c.status.in_(RUNNABLE))
        .values(status="extracted", run_note=None,
                rounding_policy_source=ROUNDING_POLICY_SOURCE)
    ).rowcount
    if updated == 0:
        raise RuntimeError(f"document {document_id} changed status during the run")
    return RunOutcome(document_id, "extracted", None, len(claim_rows), len(finding_rows))
```

Replace the placeholder body of `_execute`'s `try` block with:

```python
    try:
        app = assemble_pay_application(pdf, document_id)
        now = datetime.now(UTC)
        claim_rows = _claim_rows(tenant_id, document_id, app)
        finding_rows = _x0_rows(tenant_id, document_id, app, now)
        return _write(conn, row, claim_rows, finding_rows)
    except Exception as exc:  # noqa: BLE001 — every document-level error becomes `failed`
        raise _RunFailure(exc, doc_type) from exc
```

- [ ] **Step 4: Run to verify passing**

Run: `uv run pytest tests/run -v`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, no-float lint, commit**

Run: `uv run ruff check . && uv run mypy --strict ledger_sense && uv run python scripts/check_no_float.py`
Expected: clean. (If `check_no_float.py` takes paths, pass `ledger_sense/run`.)

```bash
git add ledger_sense/run/document.py tests/run/test_document.py
git commit -m "feat(run): persist every Claim as a row, every unextractable field as an X0 finding

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Arithmetic findings — suppression on unverifiable fields, A7 excluded, boxed from the claim

**Files:**
- Modify: `ledger_sense/run/document.py`
- Test: `tests/run/test_document.py`

**Interfaces:**
- Consumes: `run_rules(exclude=)`, `CONTRACT_DEPENDENT_RULES`, `Finding`, `unverifiable_money_fields` (`rules/engine.py`); the three `rules.arithmetic` modules must be imported so their `@register` decorators run.
- Produces: `_rule_rows(tenant_id, document_id, app, claim_rows, now) -> list[dict[str, object]]`, `_keep(finding, unverifiable) -> bool`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/run/test_document.py`:

```python
from ledger_sense.corpus.errors import ErrorClass
from ledger_sense.domain.money import money


def test_a_clean_document_produces_no_findings(db: RunDb) -> None:
    document_id = db.store_pdf(_clean_pdf())
    outcome = run_document(db.engine, document_id)
    assert outcome.findings_written == 0
    assert db.findings_for(document_id) == []


@pytest.mark.parametrize("error,rule_id", [
    ("A1_footing", "A1"), ("A2_line_sum", "A2"), ("A8_retainage", "A8")])
def test_an_injected_error_becomes_a_finding_boxed_at_its_claim(
    db: RunDb, error: ErrorClass, rule_id: str,
) -> None:
    document_id = db.store_pdf(generate(seed=1, error=error)[2])
    run_document(db.engine, document_id)
    hits = [f for f in db.findings_for(document_id) if f.rule_id == rule_id]
    assert hits, f"{rule_id} did not fire"
    claims_by_field = {c.field: c for c in db.claims_for(document_id)}
    for f in hits:
        assert f.state == "open"
        assert f.expected is not None and f.actual is not None
        assert Decimal(f.expected) != Decimal(f.actual)
        if f.field in claims_by_field:
            c = claims_by_field[f.field]
            assert (f.page, f.x0, f.top, f.x1, f.bottom) == (c.page, c.x0, c.top, c.x1, c.bottom)
        else:
            assert f.page is None


def test_arithmetic_is_not_run_on_a_field_that_could_not_be_extracted(
    db: RunDb, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """assemble defaults an unextractable total to 0.00 so the model validates;
    a footing rule run on that zero is a confident lie. X0 says the truth."""
    import ledger_sense.run.document as run_module

    real = run_module.assemble_pay_application

    def _scrub_total(pdf: bytes, source_path: str) -> object:
        app = real(pdf, source_path)
        app.provenance.pop("total_completed_and_stored")
        app.provenance["_unextractable"].append(
            UnextractableField("total_completed_and_stored", "label not found on page"))
        return app.model_copy(update={"total_completed_and_stored": money("0.00")})
    monkeypatch.setattr(run_module, "assemble_pay_application", _scrub_total)

    document_id = db.store_pdf(_clean_pdf())
    run_document(db.engine, document_id)
    rules_fired = {f.rule_id for f in db.findings_for(document_id)}
    assert rules_fired == {"X0"}, rules_fired


def test_a7_never_fires_because_no_uploaded_document_has_a_contract(
    db: RunDb, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ledger_sense.run.document as run_module

    real = run_module.assemble_pay_application

    def _negative_change_orders(pdf: bytes, source_path: str) -> object:
        return real(pdf, source_path).model_copy(update={"change_orders_net": money("-1.00")})
    monkeypatch.setattr(run_module, "assemble_pay_application", _negative_change_orders)
    document_id = db.store_pdf(_clean_pdf())
    run_document(db.engine, document_id)
    assert "A7" not in {f.rule_id for f in db.findings_for(document_id)}
```

`model_copy` keeps `provenance` (a shared dict reference) so the claims and `_unextractable` written above survive the copy.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/run -v -k "finding or a7 or arithmetic"`
Expected: FAIL — no A-findings are written yet (`hits == []`).

- [ ] **Step 3: Implement**

In `ledger_sense/run/document.py` add:

```python
from ledger_sense.rules.arithmetic import foot, lines, payment  # noqa: F401 — @register runs on import
from ledger_sense.rules.engine import (
    CONTRACT_DEPENDENT_RULES, Finding, run_rules, unverifiable_money_fields,
)

# A5/A6 are derived from the summary totals; if any total is unverifiable the
# derivation is too, and neither has a claim of its own to box.
_SUMMARY_DERIVED_RULES = frozenset({"A5", "A6"})
_SUMMARY_FIELDS = frozenset({"total_completed_and_stored", "retainage_total",
                             "previous_payments", "change_orders_net"})


def _keep(finding: Finding, unverifiable: frozenset[str]) -> bool:
    if finding.field in unverifiable:
        return False
    if finding.field.startswith("line[") and "lines" in unverifiable:
        return False
    if finding.rule_id in _SUMMARY_DERIVED_RULES and unverifiable & _SUMMARY_FIELDS:
        return False
    return True


def _rule_rows(tenant_id: str, document_id: str, app: PayApplication,
               claim_rows: list[dict[str, object]], now: datetime) -> list[dict[str, object]]:
    unverifiable = frozenset(unverifiable_money_fields(app))
    boxes = {str(c["field"]): c for c in claim_rows}
    rows: list[dict[str, object]] = []
    for finding in run_rules(app, _tenant_default_contract(),
                             rule_classes={"arithmetic"}, exclude=CONTRACT_DEPENDENT_RULES):
        if not _keep(finding, unverifiable):
            continue
        box = boxes.get(finding.field)
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

In `_execute`, after `finding_rows = _x0_rows(...)`, add `finding_rows += _rule_rows(tenant_id, document_id, app, claim_rows, now)`.

- [ ] **Step 4: Run to verify passing**

Run: `uv run pytest tests/run tests/test_degraded_mode.py -v`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, commit**

Run: `uv run ruff check . && uv run mypy --strict ledger_sense`
Expected: clean.

```bash
git add ledger_sense/run/document.py tests/run/test_document.py
git commit -m "feat(run): arithmetic findings boxed at their claim; none on unverifiable fields; A7 excluded

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Byte-identical duplicates → `duplicate` + D0, no extraction

**Files:**
- Modify: `ledger_sense/run/document.py`
- Test: `tests/run/test_document.py`

**Interfaces:**
- Produces: `_earlier_twin(conn, row) -> str | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/run/test_document.py`:

```python
from datetime import timedelta


def test_a_byte_identical_resubmission_is_flagged_not_re_extracted(db: RunDb) -> None:
    pdf = _clean_pdf()
    t0 = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)
    first = db.store_pdf(pdf, received_at=t0)
    second = db.store_pdf(pdf, received_at=t0 + timedelta(minutes=5))
    assert run_document(db.engine, first).status == "extracted"
    outcome = run_document(db.engine, second)
    assert outcome.status == "duplicate"
    assert outcome.findings_written == 1
    row = db.document(second)
    assert row.run_note == f"duplicate of {first}"
    assert db.claims_for(second) == []
    d0 = db.findings_for(second)
    assert [(f.rule_id, f.field) for f in d0] == [("D0", "document")]
    assert first in d0[0].message
    assert d0[0].page is None


def test_the_original_of_a_pair_is_decided_by_received_at_then_id(db: RunDb) -> None:
    pdf = _clean_pdf()
    same_instant = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)
    a = db.store_pdf(pdf, received_at=same_instant)
    b = db.store_pdf(pdf, received_at=same_instant)
    original, twin = sorted([a, b])
    assert run_document(db.engine, twin).status == "duplicate"
    assert run_document(db.engine, original).status == "extracted"


def test_the_same_bytes_from_another_tenant_are_not_a_duplicate(db: RunDb) -> None:
    pdf = _clean_pdf()
    other = db.new_tenant()
    mine = db.store_pdf(pdf)
    theirs = db.store_pdf(pdf, tenant_id=other)
    assert run_document(db.engine, mine).status == "extracted"
    assert run_document(db.engine, theirs).status == "extracted"


def test_a_row_without_a_hash_is_never_matched(db: RunDb) -> None:
    pdf = _clean_pdf()
    legacy = db.store_pdf(pdf, received_at=datetime(2020, 1, 1, tzinfo=UTC))
    with db.engine.begin() as conn:
        conn.execute(documents.update().where(documents.c.id == legacy)
                     .values(content_sha256=None))
    later = db.store_pdf(pdf)
    assert run_document(db.engine, later).status == "extracted"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/run -v -k "duplicate or original or another_tenant or without_a_hash"`
Expected: the first two FAIL (second document is `extracted`, not `duplicate`).

- [ ] **Step 3: Implement**

In `ledger_sense/run/document.py`:

```python
def _earlier_twin(conn: Connection, row: sa.Row[tuple[object, ...]]) -> str | None:
    """The id of the earliest document on the same tenant with the same
    plaintext hash, if this row is not it. Tenant-scoped in SQL: another
    tenant's bytes are never a match. Rows without a hash (pre-0005) never
    match in either direction. `row.received_at` is compared in the form the
    same engine handed it back, so the naive-vs-aware SQLite trap
    (api/inbox.py list_documents) does not arise here."""
    if row.content_sha256 is None:
        return None
    twin = conn.execute(
        sa.select(documents.c.id)
        .where(documents.c.tenant_id == row.tenant_id,
               documents.c.content_sha256 == row.content_sha256,
               documents.c.id != row.id,
               sa.tuple_(documents.c.received_at, documents.c.id)
               < sa.tuple_(sa.literal(row.received_at), sa.literal(row.id)))
        .order_by(documents.c.received_at, documents.c.id)
        .limit(1)
    ).scalar_one_or_none()
    return None if twin is None else str(twin)


def _flag_duplicate(conn: Connection, row: sa.Row[tuple[object, ...]], twin: str) -> RunOutcome:
    document_id, tenant_id = str(row.id), str(row.tenant_id)
    note = f"duplicate of {twin}"
    conn.execute(findings.delete().where(findings.c.document_id == document_id))
    conn.execute(claims.delete().where(claims.c.document_id == document_id))
    conn.execute(findings.insert().values(
        id=uuid.uuid4().hex, tenant_id=tenant_id, document_id=document_id,
        rule_id="D0", field="document",
        message=f"byte-identical to document {twin}, received earlier",
        expected=None, actual=None, page=None, x0=None, top=None, x1=None, bottom=None,
        state="open", created_at=datetime.now(UTC)))
    conn.execute(documents.update().where(documents.c.id == document_id)
                 .values(status="duplicate", run_note=note))
    return RunOutcome(document_id, "duplicate", note, 0, 1)
```

At the top of `_execute`, before `load_document`:

```python
    twin = _earlier_twin(conn, row)
    if twin is not None:
        return _flag_duplicate(conn, row, twin)
```

- [ ] **Step 4: Run to verify passing**

Run: `uv run pytest tests/run -v`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, commit**

Run: `uv run ruff check . && uv run mypy --strict ledger_sense`
Expected: clean.

```bash
git add ledger_sense/run/document.py tests/run/test_document.py
git commit -m "feat(run): a byte-identical resubmission is flagged D0 and not re-extracted

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: `run_pending`, `run_in_background`, and the concurrency proof

**Files:**
- Modify: `ledger_sense/run/document.py`
- Create: `tests/run/test_pending.py`

**Interfaces:**
- Produces:
  ```python
  def run_pending(engine: Engine, *, limit: int = 100, blobs: BlobStore | None = None) -> list[RunOutcome]
  def run_in_background(document_id: str, tenant_id: str) -> None   # never raises
  ```

- [ ] **Step 1: Write the failing tests**

Create `tests/run/test_pending.py`:

```python
"""run_pending and the concurrency guard."""

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import OperationalError

from ledger_sense.corpus.generate import generate
from ledger_sense.run.document import run_document, run_in_background, run_pending
from ledger_sense.store.retention import documents
from tests.run.conftest import RunDb


def _pdf(seed: int = 1) -> bytes:
    return generate(seed=seed, error="clean")[2]


def test_pending_runs_oldest_first_and_honours_the_limit(db: RunDb) -> None:
    t0 = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)
    ids = [db.store_pdf(_pdf(seed), received_at=t0 + timedelta(minutes=i))
           for i, seed in enumerate((1, 2, 3))]
    outcomes = run_pending(db.engine, limit=2)
    assert [o.document_id for o in outcomes] == ids[:2]
    assert db.document(ids[2]).status == "received"
    assert [o.document_id for o in run_pending(db.engine)] == [ids[2]]
    assert run_pending(db.engine) == []


def test_pending_picks_up_failed_documents_too(db: RunDb) -> None:
    document_id = db.store_pdf(_pdf())
    with db.engine.begin() as conn:
        conn.execute(documents.update().where(documents.c.id == document_id)
                     .values(status="failed", run_note="failed: RuntimeError"))
    assert [o.status for o in run_pending(db.engine)] == ["extracted"]


def test_one_failing_document_does_not_stop_the_batch(
    db: RunDb, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ledger_sense.run.document as run_module
    bad = db.store_pdf(_pdf(), received_at=datetime(2026, 1, 1, tzinfo=UTC))
    good = db.store_pdf(_pdf(2), received_at=datetime(2026, 1, 2, tzinfo=UTC))
    real = run_module.assemble_pay_application

    def _fail_bad(pdf: bytes, source_path: str) -> object:
        if source_path == bad:
            raise RuntimeError("x")
        return real(pdf, source_path)
    monkeypatch.setattr(run_module, "assemble_pay_application", _fail_bad)
    outcomes = {o.document_id: o.status for o in run_pending(db.engine)}
    assert outcomes == {bad: "failed", good: "extracted"}


def test_two_runners_on_one_document_write_exactly_one_result(
    db: RunDb, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The step-1 UPDATE takes the write lock. On SQLite the second runner
    either waits (busy timeout, 5 s by default) and then finds the row
    `extracted`, or hits the timeout and raises OperationalError — an
    infrastructure error, which propagates. Either way exactly one set of
    claims exists and the row was never `failed`."""
    import ledger_sense.run.document as run_module
    document_id = db.store_pdf(_pdf())
    real = run_module.assemble_pay_application
    entered = threading.Event()

    def _slow(pdf: bytes, source_path: str) -> object:
        if not entered.is_set():
            entered.set()
            time.sleep(1.0)  # hold the lock until the other thread is queued on its UPDATE
        return real(pdf, source_path)
    monkeypatch.setattr(run_module, "assemble_pay_application", _slow)

    results: list[object] = []

    def _run() -> None:
        try:
            results.append(run_document(db.engine, document_id))
        except OperationalError as exc:
            results.append(exc)

    threads = [threading.Thread(target=_run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    statuses = [getattr(r, "status", "locked") for r in results]
    assert "extracted" in statuses
    assert "failed" not in statuses
    assert db.document(document_id).status == "extracted"
    expected_claims = len({k for k in real(_pdf(), document_id).provenance
                           if not k.startswith("_")})
    assert len(db.claims_for(document_id)) == expected_claims


def test_run_in_background_swallows_and_logs_everything(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    import ledger_sense.run.document as run_module

    def _boom(engine: object, document_id: str, **kw: object) -> object:
        raise RuntimeError("db")
    monkeypatch.setattr(run_module, "run_document", _boom)
    monkeypatch.setattr(run_module, "app_engine", lambda: object())
    with caplog.at_level("ERROR"):
        run_in_background("doc-1", "tenant-1")  # must not raise
    assert any("doc-1" in r.message and "tenant-1" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/run/test_pending.py -v`
Expected: FAIL — `ImportError: run_pending`.

- [ ] **Step 3: Implement**

In `ledger_sense/run/document.py`:

```python
from ledger_sense.store.session import app_engine


def run_pending(engine: Engine, *, limit: int = 100,
                blobs: BlobStore | None = None) -> list[RunOutcome]:
    """Run every `received`/`failed` document, oldest first, each in its own
    transaction. One failure is one `failed` row, not a stopped batch."""
    with engine.connect() as conn:
        ids = [str(i) for i in conn.execute(
            sa.select(documents.c.id)
            .where(documents.c.status.in_(RUNNABLE))
            .order_by(documents.c.received_at, documents.c.id)
            .limit(limit)
        ).scalars()]
    store = blobs if blobs is not None else blob_store_from_environment()
    return [run_document(engine, document_id, blobs=store) for document_id in ids]


def run_in_background(document_id: str, tenant_id: str) -> None:
    """The BackgroundTasks entry point. A failure here has nowhere to go —
    the 201 has been sent — so it is logged with the ids (never the
    client-chosen filename) and swallowed."""
    try:
        run_document(app_engine(), document_id)
    except Exception:  # noqa: BLE001 — by design: nothing may escape a background task
        log.exception("background run crashed document_id=%s tenant_id=%s",
                      document_id, tenant_id)
```

- [ ] **Step 4: Run to verify passing**

Run: `uv run pytest tests/run -v`
Expected: PASS. If the concurrency test is flaky on this machine, raise the sleep to 2.0 s — do not weaken the assertions.

- [ ] **Step 5: Lint, type-check, commit**

Run: `uv run ruff check . && uv run mypy --strict ledger_sense`
Expected: clean.

```bash
git add ledger_sense/run/document.py tests/run/test_pending.py
git commit -m "feat(run): run_pending oldest-first; run_in_background never raises; two runners, one result

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: The inbox route schedules the run and shows its status

**Files:**
- Modify: `ledger_sense/api/inbox.py` (`upload`, `_row`, `_row_of`, `_COLUMNS`, `list_documents`, `read_document`, module docstring)
- Test: `tests/api/test_inbox.py`

**Interfaces:**
- Consumes: `run_in_background` (Task 10); `DOCUMENT_STATUSES`, `claims`, `findings` (Task 1).
- Produces: inbox rows carry `status`, `doc_type`, `run_note`, `finding_count`, `claim_count`; `GET /api/inbox?status=<s>`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_inbox.py` (it already defines `_pdf`, `_upload`, imports `TenantClient`, `client`, `documents`, `store_session`, `sa`, `pytest`):

```python
from ledger_sense.corpus.generate import generate


def _corpus_pdf(error: str = "clean") -> bytes:
    return generate(seed=1, error=error)[2]  # type: ignore[arg-type]


def test_an_upload_answers_received_and_is_extracted_by_the_time_it_is_read_back(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    response = _upload(a, _corpus_pdf())
    assert response.status_code == 201
    assert response.json()["status"] == "received"
    # TestClient runs BackgroundTasks before returning, so the run is done.
    body = a.get(f"/api/inbox/{response.json()['id']}").json()
    assert body["status"] == "extracted"
    assert body["doc_type"] == "pay_application"
    assert body["run_note"] is None
    assert body["claim_count"] > 0
    assert body["finding_count"] == 0


def test_a_footing_error_shows_up_as_an_open_finding_count(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    document_id = _upload(a, _corpus_pdf("A1_footing")).json()["id"]
    assert a.get(f"/api/inbox/{document_id}").json()["finding_count"] >= 1


def test_a_blank_pdf_is_stored_and_honestly_cannot_extract(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    document_id = _upload(a, _pdf()).json()["id"]
    body = a.get(f"/api/inbox/{document_id}").json()
    assert body["status"] == "cannot_extract"
    assert body["run_note"].startswith("no text layer")


def test_a_crashing_background_run_leaves_the_row_received(
    two_tenants: tuple[TenantClient, TenantClient], monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ledger_sense.api.inbox as inbox_module

    def _boom(document_id: str, tenant_id: str) -> None:
        raise RuntimeError("x")
    monkeypatch.setattr(inbox_module, "run_in_background", _boom)
    a, _ = two_tenants
    # Starlette's TestClient re-raises a background task's exception (a real
    # server only logs it); the 201 was already built and the row committed.
    with pytest.raises(RuntimeError):
        _upload(a, _corpus_pdf())
    with store_session.app_engine().connect() as conn:
        latest = conn.execute(sa.select(documents.c.status)
                              .where(documents.c.tenant_id == a.tenant_id)
                              .order_by(documents.c.received_at.desc(), documents.c.id.desc())
                              ).scalar()
    assert latest == "received"


def test_the_list_filters_by_status_and_rejects_unknown_statuses(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    extracted = _upload(a, _corpus_pdf()).json()["id"]
    blank = _upload(a, _pdf()).json()["id"]
    ids = {d["id"] for d in a.get("/api/inbox?status=cannot_extract").json()["items"]}
    assert blank in ids and extracted not in ids
    assert a.get("/api/inbox?status=bogus").status_code == 422


def test_counts_are_per_tenant(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, b = two_tenants
    mine = _upload(a, _corpus_pdf("A1_footing")).json()["id"]
    assert a.get(f"/api/inbox/{mine}").json()["finding_count"] >= 1
    assert b.get(f"/api/inbox/{mine}").status_code == 404
    assert all(d["finding_count"] == 0 for d in b.get("/api/inbox").json()["items"])
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/api/test_inbox.py -v -k "received_and_is_extracted or footing_error or blank_pdf or crashing_background or filters_by_status or per_tenant"`
Expected: FAIL — `KeyError: 'status'`.

- [ ] **Step 3: Implement**

In `ledger_sense/api/inbox.py`:

Imports: add `BackgroundTasks` to the `fastapi` import; `from ledger_sense.run.document import run_in_background`; change the retention import to `from ledger_sense.store.retention import DOCUMENT_STATUSES, claims, documents, findings`.

Update the module docstring's sentence "No extraction, no rules, no gateway call: those come later and read the row this route writes." to: "No extraction inline: after the row commits, `run_in_background` is scheduled through BackgroundTasks and reads the row this route wrote. The 201 says `received`; the run's answer arrives on the next read."

Replace `_row` (keep the existing comment about `"Z"` above `stamp`):

```python
def _row(document_id: str, *, filename: str, sensitivity: str, origin: str,
         received_at: datetime, status: str = "received", doc_type: str | None = None,
         run_note: str | None = None, finding_count: int = 0,
         claim_count: int = 0) -> dict[str, Any]:
    stamp = received_at.isoformat().replace("+00:00", "Z")
    return {"id": document_id, "filename": filename, "sensitivity": sensitivity,
            "origin": origin, "received_at": stamp, "status": status,
            "doc_type": doc_type, "run_note": run_note,
            "finding_count": finding_count, "claim_count": claim_count}
```

In `upload`, add the parameter `background: BackgroundTasks` (after `session`) and, after the `with app_engine().begin() as conn:` block and before `return _row(...)`:

```python
    # Scheduled only once the row is committed: the runner reads it back.
    background.add_task(run_in_background, document_id, session.tenant_id)
```

Replace `_COLUMNS` and `_row_of` (keep the SQLite-naive comment inside `_row_of`):

```python
_OPEN_FINDINGS = (sa.select(sa.func.count()).select_from(findings)
                  .where(findings.c.document_id == documents.c.id, findings.c.state == "open")
                  .correlate(documents).scalar_subquery().label("finding_count"))
_CLAIMS = (sa.select(sa.func.count()).select_from(claims)
           .where(claims.c.document_id == documents.c.id)
           .correlate(documents).scalar_subquery().label("claim_count"))

_COLUMNS = (documents.c.id, documents.c.filename, documents.c.sensitivity,
            documents.c.origin, documents.c.received_at, documents.c.status,
            documents.c.doc_type, documents.c.run_note, _OPEN_FINDINGS, _CLAIMS)


def _row_of(row: sa.Row[Any]) -> dict[str, Any]:
    received_at = row.received_at
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=UTC)
    return _row(row.id, filename=row.filename, sensitivity=row.sensitivity,
                origin=row.origin, received_at=received_at, status=row.status,
                doc_type=row.doc_type, run_note=row.run_note,
                finding_count=int(row.finding_count), claim_count=int(row.claim_count))
```

In `list_documents`, add a parameter `status: str | None = None` and, before building `query`:

```python
    if status is not None and status not in DOCUMENT_STATUSES:
        raise HTTPException(status_code=422,
                            detail=f"status must be one of {', '.join(DOCUMENT_STATUSES)}")
```

and after the tenant `.where(...)`:

```python
    if status is not None:
        query = query.where(documents.c.status == status)
```

- [ ] **Step 4: Run to verify passing**

Run: `uv run pytest tests/api -v`
Expected: PASS, including every pre-existing inbox test (they read only the keys they always did).

- [ ] **Step 5: Lint, type-check, commit**

Run: `uv run ruff check . && uv run mypy --strict ledger_sense`
Expected: clean.

```bash
git add ledger_sense/api/inbox.py tests/api/test_inbox.py
git commit -m "feat(api): inbox schedules the run after commit and reports status, type, note, counts

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: CLI — `extract --pending`, `extract --document`

**Files:**
- Modify: `ledger_sense/__main__.py:63-78` (`_extract`)
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `run_pending`, `run_document`, `app_engine`.
- Produces: `python -m ledger_sense extract --pending [--limit N]`, `python -m ledger_sense extract --document <id>`; unchanged `extract <path>`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_main.py` (it already imports `pytest`, `pathlib`, `Fernet`):

```python
import json

from ledger_sense.__main__ import _extract
from ledger_sense.corpus.generate import generate
from tests.run.conftest import RunDb, db  # noqa: F401 — the `db` fixture


def test_extract_pending_runs_every_runnable_document_and_prints_one_line_each(
    db: RunDb, capsys: pytest.CaptureFixture[str],
) -> None:
    ids = [db.store_pdf(generate(seed=s, error="clean")[2]) for s in (1, 2)]
    assert _extract(["ledger_sense", "extract", "--pending"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert {line["document_id"] for line in lines} == set(ids)
    assert all(line["status"] == "extracted" for line in lines)


def test_extract_pending_honours_limit(db: RunDb, capsys: pytest.CaptureFixture[str]) -> None:
    for s in (1, 2, 3):
        db.store_pdf(generate(seed=s, error="clean")[2])
    assert _extract(["ledger_sense", "extract", "--pending", "--limit", "2"]) == 0
    assert len(capsys.readouterr().out.strip().splitlines()) == 2


def test_extract_document_runs_exactly_one(db: RunDb, capsys: pytest.CaptureFixture[str]) -> None:
    document_id = db.store_pdf(generate(seed=1, error="clean")[2])
    other = db.store_pdf(generate(seed=2, error="clean")[2])
    assert _extract(["ledger_sense", "extract", "--document", document_id]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "extracted"
    assert db.document(other).status == "received"


def test_extract_usage_is_still_an_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert _extract(["ledger_sense", "extract"]) == 2
    assert "usage" in capsys.readouterr().err
```

Importing the fixture function `db` by name (the `noqa` line) is what makes pytest see it outside `tests/run/`. If ruff's F811/F401 handling makes that awkward, the alternative is a line `pytest_plugins = ["tests.run.conftest"]` in a top-level `tests/conftest.py` (create it if absent) and drop `db` from the import.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_main.py -v -k extract`
Expected: FAIL — `--pending` treated as a path; `FileNotFoundError`.

- [ ] **Step 3: Implement**

Replace `_extract` in `ledger_sense/__main__.py`:

```python
def _extract(argv: list[str]) -> int:
    """`extract <path>`                — stateless probe: parse a file, print a summary.
    `extract --pending [--limit N]`   — run every received/failed document, oldest first.
    `extract --document <id>`        — run one stored document."""
    usage = ("usage: python -m ledger_sense extract <path> | --pending [--limit N] "
             "| --document <id>")
    args = argv[2:]
    if not args:
        print(usage, file=sys.stderr)
        return 2
    if args[0] == "--pending":
        limit = 100
        if len(args) == 3 and args[1] == "--limit" and args[2].isdigit():
            limit = int(args[2])
        elif len(args) != 1:
            print(usage, file=sys.stderr)
            return 2
        for outcome in run_pending(app_engine(), limit=limit):
            print(json.dumps(outcome._asdict()))
        return 0
    if args[0] == "--document":
        if len(args) != 2:
            print(usage, file=sys.stderr)
            return 2
        print(json.dumps(run_document(app_engine(), args[1])._asdict()))
        return 0
    if len(args) != 1:
        print(usage, file=sys.stderr)
        return 2
    path = pathlib.Path(args[0])
    pdf = path.read_bytes()
    app = assemble_pay_application(pdf, str(path))
    unextractable = app.provenance["_unextractable"]
    assert isinstance(unextractable, list)
    print(json.dumps({
        "doc_type": classify(pdf),
        "lines": len(app.lines),
        "total_completed_and_stored": str(app.total_completed_and_stored),
        "unextractable": [u.field for u in unextractable],
    }, indent=2))
    return 0
```

Add imports: `from ledger_sense.run.document import run_document, run_pending` and `from ledger_sense.store.session import app_engine` (check whether `app_engine` is already imported for `_migrate`; if so, do not duplicate).

- [ ] **Step 4: Run to verify passing**

Run: `uv run pytest tests/test_main.py -v`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, commit**

Run: `uv run ruff check . && uv run mypy --strict ledger_sense`
Expected: clean.

```bash
git add ledger_sense/__main__.py tests/test_main.py
git commit -m "feat(cli): extract --pending / --document re-run stored documents

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Boundary pin, degraded-mode end to end, security note

**Files:**
- Modify: `tests/boundary/test_structure.py`, `tests/test_degraded_mode.py`, `docs/security.md`

**Interfaces:** none new.

- [ ] **Step 1: Write the boundary test and prove it bites**

Append to `tests/boundary/test_structure.py` (it has `PACKAGE`, `PROVIDER_CLIENTS`, `_sources()`, `ast`):

```python
# The runner is the first code to touch a CLIENT_CONFIDENTIAL document after
# the door. It must be deterministic all the way down: no gateway, no OCR
# escalation (which is where a model lane would hang), no provider client.
_RUNNER_FORBIDDEN = ("ledger_sense.gateway", "ledger_sense.extract.ocr", *PROVIDER_CLIENTS)


def test_the_runner_imports_nothing_that_could_reach_a_provider() -> None:
    offenders = []
    for path, tree in _sources():
        if "run" not in path.parts:
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if any(name == f or name.startswith(f + ".") for f in _RUNNER_FORBIDDEN):
                    offenders.append(f"{path}:{node.lineno} imports {name}")
    assert offenders == [], offenders
```

Run: `uv run pytest tests/boundary -v -k runner` — expected PASS immediately (the runner is already clean). To prove the test bites, temporarily add `import ledger_sense.extract.ocr  # noqa` to `ledger_sense/run/document.py`, run again (expected FAIL naming the line), then remove it.

- [ ] **Step 2: Degraded mode end to end**

Append to `tests/test_degraded_mode.py` (it has `every_provider_disabled`, `generate`):

```python
from ledger_sense.run.document import run_document
from tests.run.conftest import RunDb, db  # noqa: F401 — the `db` fixture


def test_a_stored_document_is_run_end_to_end_with_every_provider_disabled(
    every_provider_disabled: None, db: RunDb,
) -> None:
    document_id = db.store_pdf(generate(seed=3, error="A1_footing")[2])
    outcome = run_document(db.engine, document_id)
    assert outcome.status == "extracted"
    assert "A1" in {f.rule_id for f in db.findings_for(document_id)}
```

(Same fixture-import note as Task 12; use whichever mechanism Task 12 settled on.)

Run: `uv run pytest tests/test_degraded_mode.py -v` — expected PASS.

- [ ] **Step 3: Security note**

In `docs/security.md`, add a section (match the document's existing heading level and voice):

```markdown
## The extraction run

`ledger_sense/run/document.py` is the first code to open a stored document after
the door. It is deterministic only — `tests/boundary/test_structure.py` pins that
it imports no gateway, OCR escalation or provider client — so a
`CLIENT_CONFIDENTIAL` document is extracted the same way a `SYNTHETIC` one is:
locally, in memory, with the decrypted bytes never written to disk.

**`documents.content_sha256`** is the SHA-256 of the *plaintext*. It exists so a
byte-identical re-submission within a tenant can be flagged without decrypting
anything; it reveals equality and nothing else, and duplicate matching is
tenant-scoped in SQL, so one tenant can never learn that another holds the same
bytes.

**`documents.run_note`** never carries document content. A failed run records the
exception's class name only; the message (which can quote page text) goes to the
application log with the document and tenant ids — never the client-chosen
filename.

**Claims and findings hold extracted values** — including any payment details a
document carries. They are deleted with the document by the retention purge
(`PurgeReport.findings_purged` reports it), and every row carries `tenant_id`.
```

- [ ] **Step 4: Full suite, lint, type-check, coverage floor**

Run: `uv run pytest --cov=ledger_sense --cov-fail-under=75 -q && uv run ruff check . && uv run mypy --strict ledger_sense && uv run python scripts/check_no_float.py && uv run python scripts/aia_guard.py`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add tests/boundary/test_structure.py tests/test_degraded_mode.py docs/security.md
git commit -m "test(boundary): the runner reaches no provider; degraded-mode run end to end; security note

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage.** §1 schema → Task 1 (+ purge in Task 2, hash writer in Task 3). §2 steps 1–7 → Tasks 5 (step 1 guard), 6 (steps 3, 7), 7 (4, 6), 8 (5), 9 (2); `run_pending` → Task 10. §3 trigger, hash at the door, read side, filter, CLI → Tasks 11, 3, 11, 11, 12. §4 boundary/tenancy/notes → Tasks 13, 9, 6. §5 test list → every bullet has a test above.

**Known deviation from the spec, stated.** Spec §2 step 1 says the attempt counter still increments on the "human decisions present" refusal — Task 5 does that (the UPDATE runs before the findings check). But in the concurrency case the *loser's* UPDATE matches zero rows and does **not** increment, so the spec's §5 line "`run_attempts == 2`" is wrong; Task 10's test asserts the truth (one `extracted`, no `failed`, one claim set). Correct that spec line while executing Task 10.

**Placeholders.** None. Task 6's placeholder return ("persistence not yet implemented") is deliberate scaffolding replaced in Task 7, and Task 5's ("runner not yet implemented") is replaced in Task 6; neither survives the plan.

**Type consistency.** `run_document(engine, document_id, *, blobs=None)` in Tasks 5/6/10/12; `_execute(conn, row, store)` throughout (Task 6's `_RunFailure` carries `doc_type` out so `_execute` never needs the engine); `RunOutcome` fields identical in Tasks 5/10/12; `_row(... status, doc_type, run_note, finding_count, claim_count)` matches `_row_of` and the tests' keys; `run_rules(exclude=)` name matches between Tasks 4 and 8; `RUNNABLE`, `ROUNDING_POLICY_SOURCE`, `NO_TEXT_LAYER_NOTE` defined once (Tasks 5, 5, 6) and used by name after.
