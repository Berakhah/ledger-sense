# Inbox Route Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `POST /api/inbox` accepts a PDF through the labelled door and stores it; `GET /api/inbox` and `GET /api/inbox/{id}` read it back for the owning tenant.

**Architecture:** One thin router (`ledger_sense/api/inbox.py`) composing `intake()` → `accept_document()` → `store_document()`, mounted through `deps.register()`. Three supporting changes: a `kind` on `RejectedDocument` so the route maps 413/422 without parsing strings; a `documents.filename` column (migration 0004); blob-store wiring from the environment. The demo seeder is moved through the same door so the demo-profile proof runs against the real table.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy Core + alembic (SQLite in tests, Postgres in deployment), `python-multipart`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-15-inbox-route-design.md`

## Global Constraints

- `uv run ruff check .` and `uv run mypy --strict ledger_sense tests` clean after every task (Never #2 lint discipline applies to all `.py`).
- `uv` is invoked by full path on this machine: `/c/Users/berek/AppData/Roaming/Python/Python313/Scripts/uv.exe` (Bash tool; no `$VAR` names, no heredocs — use the Write tool for files).
- Every route mounts through `deps.register()`; `tests/api/test_auth.py::test_every_api_route_requires_a_session` must stay green.
- `Labelled` is constructed only in `ingest/intake.py` and `store/documents.py`; `documents.insert()` is called only in `store/documents.py` (`tests/boundary/test_structure.py`). The route and the seeder call those, never the table.
- No client-controlled string reaches provenance. Provenance grammar: `upload://{tenant_id}/{user_id}`, `corpus://seed/{tenant_id}/{user_id}`.
- Migrations write enum values as literals; a migration is a frozen record.
- No secrets in the repo; `LEDGER_SENSE_KEK` stays in `.env` (git-ignored). Test KEKs are generated per run.
- Commit after every task with a conventional message; end with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

---

### Task 1: `RejectedDocument.kind`

**Files:**
- Modify: `ledger_sense/ingest/limits.py:62-70` (dataclass) and the seven `RejectedDocument(...)` sites at lines 129, 136, 150, 166, 168, 179, 184
- Test: `tests/ingest/test_limits.py`

**Interfaces:**
- Produces: `class Rejection(str, Enum)` with members `NOT_PDF, TOO_LARGE, TOO_MANY_PAGES, BOMB, TIMEOUT, UNPARSEABLE`; `RejectedDocument(reason: str, kind: Rejection)` (both required, positional order `reason, kind`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/ingest/test_limits.py`:

```python
from ledger_sense.ingest.limits import Rejection


def test_a_rejection_names_its_kind_not_just_a_sentence() -> None:
    """The route maps 413/422 on `kind`; substring-matching `reason` would
    break the day the wording changes."""
    assert parse_within_limits(b"MZ\x90\x00", Limits()) == RejectedDocument(
        "rejected: not a PDF (bad magic bytes)", Rejection.NOT_PDF)
    oversized = parse_within_limits(b"%PDF-1.4" + b"\x00" * 26_000_000, Limits())
    assert isinstance(oversized, RejectedDocument) and oversized.kind is Rejection.TOO_LARGE
    bomb = parse_within_limits((MALFORMED / "many_pages.pdf").read_bytes(), Limits())
    assert isinstance(bomb, RejectedDocument) and bomb.kind is Rejection.TOO_MANY_PAGES


def test_a_parse_failure_is_unparseable() -> None:
    result = parse_within_limits((MALFORMED / "corrupted_header.pdf").read_bytes(), Limits())
    assert isinstance(result, RejectedDocument) and result.kind is Rejection.UNPARSEABLE
```

If `corrupted_header.pdf` does not begin with `%PDF` (check with `head -c 4`), pick another file in `tests/ingest/fixtures/malformed/` that does and is not `many_pages.pdf`; the assertion is on `UNPARSEABLE`.

- [ ] **Step 2: Run to verify RED**

Run: `uv run pytest tests/ingest/test_limits.py -q -p no:cacheprovider`
Expected: ImportError on `Rejection`.

- [ ] **Step 3: Implement**

In `ledger_sense/ingest/limits.py`, add `from enum import Enum` and, above `RejectedDocument`:

```python
class Rejection(str, Enum):
    """Why a payload was refused. The route maps HTTP status on this, never
    on the wording of `reason`."""

    NOT_PDF = "NOT_PDF"
    TOO_LARGE = "TOO_LARGE"
    TOO_MANY_PAGES = "TOO_MANY_PAGES"
    BOMB = "BOMB"
    TIMEOUT = "TIMEOUT"
    UNPARSEABLE = "UNPARSEABLE"
```

Add `kind: Rejection` as the second field of `RejectedDocument` (after `reason`). Then name each site:

| line | kind |
|---|---|
| decompression bomb (`_decompression_bomb`) | `Rejection.BOMB` |
| page count | `Rejection.TOO_MANY_PAGES` |
| `could not parse` | `Rejection.UNPARSEABLE` |
| bad magic | `Rejection.NOT_PDF` |
| size cap | `Rejection.TOO_LARGE` |
| wall-clock timeout | `Rejection.TIMEOUT` |
| parser exited without a result | `Rejection.UNPARSEABLE` |

Run `grep -rn "RejectedDocument(" ledger_sense tests` and fix any other constructor call (there should be none outside `limits.py`).

- [ ] **Step 4: Run to verify GREEN**

Run: `uv run pytest tests/ingest -q -p no:cacheprovider && uv run ruff check . && uv run mypy --strict ledger_sense tests`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/ingest/limits.py tests/ingest/test_limits.py
git commit -m "feat(ingest): RejectedDocument carries a kind so callers map on it, not on wording"
```

---

### Task 2: `documents.filename` — migration 0004 and the one writer

**Files:**
- Create: `migrations/versions/0004_filename.py`
- Modify: `ledger_sense/store/retention.py:44-64` (add column), `ledger_sense/store/documents.py:36-77` (`filename=`)
- Modify (raw inserts that must now supply a filename): `tests/store/conftest.py` (two `documents.insert()` sites), `tests/store/test_migrations.py::_raw_insert`
- Test: `tests/store/test_migrations.py`, `tests/store/test_documents.py`

**Interfaces:**
- Produces: `store_document(conn, *, tenant_id, doc, blobs, received_at, filename: str) -> str`. `documents.c.filename` (`Text`, NOT NULL, no default).

- [ ] **Step 1: Write the failing tests**

In `tests/store/test_migrations.py`, add after `test_0003_round_trips_on_a_populated_database`:

```python
def test_0004_backfills_a_filename_and_then_refuses_to_invent_one(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-0004 row gets 'unknown:pre-0004' — honest, not blank — and the
    default is dropped so a raw INSERT without a filename is refused."""
    database = (tmp_path / "populated.db").as_posix()
    monkeypatch.setenv("LEDGER_SENSE_DB_URL", f"sqlite:///{database}")
    store_session.reset_app_engine()
    try:
        config = _alembic_config()
        command.upgrade(config, "0003_sensitivity")
        with store_session.app_engine().begin() as conn:
            _seed_a_pre_0003_document(conn)
            conn.execute(sa.text(
                "INSERT INTO documents (id, tenant_id, blob_id, received_at, sensitivity, "
                "origin, provenance) VALUES ('doc-pre-0004', :tenant_id, NULL, :received_at, "
                "'SYNTHETIC', 'CORPUS', 'tests')"),
                {"tenant_id": _PRE_0003_ROW["tenant_id"],
                 "received_at": _PRE_0003_ROW["received_at"]})
        command.upgrade(config, "head")
        with store_session.app_engine().connect() as conn:
            filename = conn.execute(sa.text(
                "SELECT filename FROM documents WHERE id = 'doc-pre-0004'")).scalar()
        assert filename == "unknown:pre-0004"
        with pytest.raises(sa.exc.IntegrityError), store_session.app_engine().begin() as conn:
            _raw_insert(conn, _PRE_0003_ROW["tenant_id"], document_id="doc-no-name",
                        sensitivity="SYNTHETIC", origin="CORPUS", filename=None)
        command.downgrade(config, "0003_sensitivity")
        columns = {c["name"] for c in sa.inspect(store_session.app_engine())
                   .get_columns("documents")}
        assert "filename" not in columns
    finally:
        store_session.reset_app_engine()
```

Look at `_seed_a_pre_0003_document` and `_PRE_0003_ROW` (defined near line 100 of the same file) to confirm the key names `tenant_id` / `received_at`; adjust the dict keys above if they differ.

Change `_raw_insert` to:

```python
def _raw_insert(conn: sa.Connection, tenant_id: str, *, document_id: str,
                sensitivity: str, origin: str, filename: str | None = "raw.pdf") -> None:
    conn.execute(sa.text(
        "INSERT INTO documents (id, tenant_id, blob_id, received_at, sensitivity, origin, "
        "provenance, filename) VALUES (:id, :tenant_id, NULL, :received_at, :sensitivity, "
        ":origin, 'tests/store/test_migrations.py', :filename)"),
        {"id": document_id, "tenant_id": tenant_id, "received_at": _PRE_0003_ROW["received_at"],
         "sensitivity": sensitivity, "origin": origin, "filename": filename})
```

In `tests/store/test_documents.py`, add:

```python
def test_the_row_carries_the_filename_separately_from_the_label(
    seeded_documents: SeededDocuments, blobs: BlobStore
) -> None:
    """Filename is a display attribute the client chose; provenance is who put
    it through the door. They are different columns on purpose."""
    with store_session.app_engine().begin() as conn:
        document_id = store_document(conn, tenant_id=seeded_documents.tenant_id,
                                     doc=_labelled(Origin.UPLOAD), blobs=blobs,
                                     received_at=RECEIVED_AT, filename="payapp-07.pdf")
    (row,) = seeded_documents.documents_for(document_id)
    assert (row.filename, row.provenance) == ("payapp-07.pdf", "test:UPLOAD")
```

and add `filename="test.pdf"` to every other `store_document(` call in that file (seven sites).

In `tests/store/conftest.py`, add `filename="tests/store/conftest.py"` to both `documents.insert().values(...)` calls.

- [ ] **Step 2: Run to verify RED**

Run: `uv run pytest tests/store -q -p no:cacheprovider`
Expected: `test_0004_...` fails (no such revision); `store_document` tests fail on the unexpected `filename` kwarg; `test_the_migration_builds_the_schema_the_code_queries` still passes (column not yet on the Table either).

- [ ] **Step 3: Implement**

`migrations/versions/0004_filename.py`:

```python
"""filename: documents.filename

Revision ID: 0004_filename
Revises: 0003_sensitivity
Create Date: 2026-09-15

The inbox lists documents by the name the client gave them. That name is a
display attribute and nothing more: it is never provenance (who put the file
through the door — see 0003) and never a path (store/documents.py's caller
strips it to a basename before it gets here).

Same two decisions as 0003, for the same reasons:

1. Server default, then dropped. A row that predates this column is
   backfilled 'unknown:pre-0004' — named, not blank, so the inbox shows what
   it is rather than an empty cell. The default is then dropped so no raw
   INSERT can omit the name and have one invented.

2. Two batch blocks. On SQLite a single block that adds NOT NULL and drops
   the default would copy existing rows with nothing to fill.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_filename"
down_revision: str | None = "0003_sensitivity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("filename", sa.Text(), nullable=False,
                                   server_default="unknown:pre-0004"))
    with op.batch_alter_table("documents") as batch:
        batch.alter_column("filename", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("filename")
```

`ledger_sense/store/retention.py`: after the `provenance` column add

```python
    # 0004: the name the client gave the file. Display only — never a path,
    # never provenance. NOT NULL, no default, same discipline as the label.
    sa.Column("filename", sa.Text, nullable=False),
```

`ledger_sense/store/documents.py`: add `filename: str` to `store_document`'s keyword parameters (after `received_at`), and `filename=filename,` to the `.values(...)`. Update the docstring: "Encrypt the payload, insert the row with its label and filename, return the new id."

- [ ] **Step 4: Run to verify GREEN**

Run: `uv run pytest tests/store tests/boundary -q -p no:cacheprovider && uv run ruff check . && uv run mypy --strict ledger_sense tests`
Expected: all pass, including the 0003 named-CHECK reflection (batch recreate must keep `ck_documents_*`).

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0004_filename.py ledger_sense/store/retention.py ledger_sense/store/documents.py tests/store
git commit -m "feat(store): documents.filename — migration 0004, written by the one writer"
```

---

### Task 3: Blob store from the environment, multipart, compose

**Files:**
- Modify: `ledger_sense/store/blobs.py:145-160` (add `blob_store_from_environment`)
- Modify: `pyproject.toml` (`python-multipart`), `uv.lock` (via `uv add`)
- Modify: `docker-compose.yml` (`LEDGER_SENSE_BLOB_ROOT`, a `client_blobs` volume for `app`, tmpfs path for `app-demo`), `.env.example`
- Modify: `tests/api/conftest.py::_database` (set `LEDGER_SENSE_KEK` and `LEDGER_SENSE_BLOB_ROOT`)
- Test: `tests/store/test_blobs.py`

**Interfaces:**
- Produces: `blob_store_from_environment() -> BlobStore` reading `LEDGER_SENSE_BLOB_ROOT` (required, raises `RuntimeError` if unset) and `kek_from_environment()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/store/test_blobs.py`:

```python
def test_the_blob_store_is_wired_from_the_environment(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEDGER_SENSE_KEK", Fernet.generate_key().decode())
    monkeypatch.setenv("LEDGER_SENSE_BLOB_ROOT", str(tmp_path / "blobs"))
    store = blob_store_from_environment()
    blob_id = store.put("t1", b"%PDF-1.4 x")
    assert store.get("t1", blob_id) == b"%PDF-1.4 x"
    assert (tmp_path / "blobs" / blob_id).is_file()


def test_an_unset_blob_root_refuses_to_start_rather_than_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEDGER_SENSE_KEK", Fernet.generate_key().decode())
    monkeypatch.delenv("LEDGER_SENSE_BLOB_ROOT", raising=False)
    with pytest.raises(RuntimeError, match="LEDGER_SENSE_BLOB_ROOT"):
        blob_store_from_environment()
```

Add the imports the file lacks (`pathlib`, `pytest`, `Fernet`, `blob_store_from_environment`) — check its header first.

- [ ] **Step 2: Run to verify RED**

Run: `uv run pytest tests/store/test_blobs.py -q -p no:cacheprovider`
Expected: ImportError on `blob_store_from_environment`.

- [ ] **Step 3: Implement**

Append to `ledger_sense/store/blobs.py`:

```python
def blob_store_from_environment() -> BlobStore:
    """The deployment's blob store: `LEDGER_SENSE_BLOB_ROOT` + the KEK.

    Built where storage is wired (the inbox route, the demo seeder), not as a
    module singleton, for the reason kek_from_environment gives. Raises if the
    root is unset: a document written to a guessed directory is a document
    nobody backs up.
    """
    root = os.environ.get("LEDGER_SENSE_BLOB_ROOT")
    if not root:
        raise RuntimeError(
            "LEDGER_SENSE_BLOB_ROOT is unset: refusing to guess where client "
            "documents live (see docs/security.md)."
        )
    return BlobStore(root=root, kek=kek_from_environment())
```

Add the dependency: `uv add python-multipart` (MIT; `tests/test_licence_audit.py` reads the real environment and must stay green).

`docker-compose.yml`:
- In `app` (client) `environment:` add `LEDGER_SENSE_BLOB_ROOT: /var/lib/ledger-sense/blobs` and a `volumes:` list with `- client_blobs:/var/lib/ledger-sense/blobs`; add `client_blobs:` under the top-level `volumes:`.
- In `app-demo` `environment:` add `LEDGER_SENSE_BLOB_ROOT: /app/scratch/blobs` (already tmpfs via the `x-app` anchor; SYNTHETIC data, ephemeral by design — same stance as `postgres-demo`).
- A comment on the client volume: the KEK in `.env` is what makes this volume ciphertext; back the two up separately (docs/security.md).

`.env.example`: below `LEDGER_SENSE_KEK`, add

```
# Directory the encrypted blobs live in. docker-compose.yml sets this per
# profile (named volume for client, tmpfs for demo); set it only for a bare
# `uv run` outside compose.
LEDGER_SENSE_BLOB_ROOT=
```

`tests/api/conftest.py::_database`: inside the `MonkeyPatch.context()`, before `reset_app_engine()`:

```python
        patch.setenv("LEDGER_SENSE_KEK", Fernet.generate_key().decode())
        patch.setenv("LEDGER_SENSE_BLOB_ROOT", str(tmp_path_factory.mktemp("blobs")))
```

with `from cryptography.fernet import Fernet` added to the imports.

- [ ] **Step 4: Run to verify GREEN**

Run: `uv run pytest tests/store/test_blobs.py tests/test_licence_audit.py tests/api -q -p no:cacheprovider && uv run ruff check . && uv run mypy --strict ledger_sense tests && docker compose config -q`
Expected: all pass; `docker compose config` validates (skip that command if Docker is not installed and say so in the report).

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/store/blobs.py tests/store/test_blobs.py pyproject.toml uv.lock docker-compose.yml .env.example tests/api/conftest.py
git commit -m "feat(store): blob store wired from LEDGER_SENSE_BLOB_ROOT; multipart dependency; compose volume"
```

---

### Task 4: `POST /api/inbox`

**Files:**
- Create: `ledger_sense/api/inbox.py`
- Modify: `ledger_sense/api/app.py` (import and `register(app, inbox_router)` before the stub loop), `ledger_sense/api/_stub_routes.py` (delete `_read_inbox_document` and the `if prefix == "/api/inbox"` branch; remove `"/api/inbox"` from `PREFIXES`; trim the docstring's "six prefixes" to "five")
- Test: `tests/api/test_inbox.py` (create)

**Interfaces:**
- Consumes: `intake(payload) -> ParsedDocument | RejectedDocument` (`RejectedDocument.kind: Rejection` from Task 1), `accept_document(payload, *, origin, provenance)`, `store_document(..., filename=)` (Task 2), `blob_store_from_environment()` (Task 3), `require_session`, `owned`, `demo_profile_enabled`, `app_engine`.
- Produces: `router: APIRouter` (prefix `/api/inbox`); `sanitise_filename(name: str | None) -> str`; `_row(...) -> dict` response shape `{id, filename, sensitivity, origin, received_at}` used by Task 5.

Note: removing the inbox stub makes `tests/api/test_fixtures.py::test_seeded_documents_are_labelled_synthetic` and `..._refused_to_an_anonymous_caller` fail until Task 6 moves the seeder. Task 4 and Task 5 run the suite with `--deselect tests/api/test_fixtures.py` and say so; Task 6 restores it.

- [ ] **Step 1: Write the failing tests**

`tests/api/test_inbox.py`:

```python
"""POST /api/inbox is the labelled door with an HTTP front. It composes
intake → accept_document → store_document and adds nothing of its own."""

import ast
import pathlib
from typing import cast

import pypdfium2 as pdfium
import pytest
import sqlalchemy as sa
from httpx import Response

from ledger_sense.store import session as store_session
from ledger_sense.store.retention import documents
from tests.api.conftest import TenantClient, client


def _pdf(pages: int = 1) -> bytes:
    doc = pdfium.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(612, 792)
    out = pathlib.Path(__file__).parent / f"_inbox_{pages}.pdf"
    doc.save(out)
    payload = out.read_bytes()
    out.unlink()
    return payload


def _upload(tenant: TenantClient, payload: bytes, filename: str = "payapp.pdf",
            origin: str | None = None) -> Response:
    data = {} if origin is None else {"origin": origin}
    return cast(Response, client.post(
        "/api/inbox", files={"file": (filename, payload, "application/pdf")},
        data=data, cookies={"session": tenant.token}))


def _row(document_id: str) -> sa.Row[tuple[object, ...]]:
    with store_session.app_engine().connect() as conn:
        return conn.execute(sa.select(documents).where(documents.c.id == document_id)).one()


def test_an_upload_is_stored_confidential_from_an_upload(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    response = _upload(a, _pdf())
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"id", "filename", "sensitivity", "origin", "received_at"}
    assert (body["sensitivity"], body["origin"], body["filename"]) == (
        "CLIENT_CONFIDENTIAL", "UPLOAD", "payapp.pdf")
    row = _row(body["id"])
    assert row.tenant_id == a.tenant_id
    assert row.provenance.startswith(f"upload://{a.tenant_id}/")
    assert row.provenance.count("/") == 3 and "payapp" not in row.provenance


def test_a_forgotten_origin_is_the_most_restrictive_label(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    assert _upload(a, _pdf(), origin=None).json()["sensitivity"] == "CLIENT_CONFIDENTIAL"


def test_corpus_origin_is_refused_outside_the_demo_profile(
    two_tenants: tuple[TenantClient, TenantClient], monkeypatch: pytest.MonkeyPatch,
) -> None:
    a, _ = two_tenants
    monkeypatch.delenv("LEDGER_SENSE_PROFILE", raising=False)
    response = _upload(a, _pdf(), origin="CORPUS")
    assert response.status_code == 422
    assert "demo" in response.json()["detail"]


def test_corpus_origin_is_accepted_under_the_demo_profile(
    two_tenants: tuple[TenantClient, TenantClient], monkeypatch: pytest.MonkeyPatch,
) -> None:
    a, _ = two_tenants
    monkeypatch.setenv("LEDGER_SENSE_PROFILE", "demo")
    body = _upload(a, _pdf(), origin="CORPUS").json()
    assert (body["sensitivity"], body["origin"]) == ("SYNTHETIC", "CORPUS")
    assert _row(body["id"]).provenance.startswith(f"corpus://{a.tenant_id}/")


def test_redacted_upload_is_refused_in_plain_words(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    response = _upload(a, _pdf(), origin="REDACTED_UPLOAD")
    assert response.status_code == 422
    assert "redaction" in response.json()["detail"].lower()


def test_an_unknown_origin_is_a_schema_error(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    assert _upload(a, _pdf(), origin="EMAIL").status_code == 422


def test_a_missing_or_empty_file_is_refused(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    assert client.post("/api/inbox", cookies={"session": a.token}).status_code == 422
    assert _upload(a, b"").status_code == 422


def test_an_oversized_payload_is_413_and_nothing_is_stored(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    before = _count(a.tenant_id)
    response = _upload(a, b"%PDF-1.4" + b"\x00" * 26_000_000)
    assert response.status_code == 413
    assert "size" in response.json()["detail"]
    assert _count(a.tenant_id) == before


def test_a_non_pdf_is_422_and_nothing_is_stored(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    before = _count(a.tenant_id)
    response = _upload(a, b"MZ\x90\x00", filename="payapp.pdf")
    assert response.status_code == 422
    assert "not a PDF" in response.json()["detail"]
    assert _count(a.tenant_id) == before


def test_a_page_bomb_is_422(two_tenants: tuple[TenantClient, TenantClient]) -> None:
    a, _ = two_tenants
    bomb = pathlib.Path("tests/ingest/fixtures/malformed/many_pages.pdf").read_bytes()
    response = _upload(a, bomb)
    assert response.status_code == 422 and "page" in response.json()["detail"]


def test_the_filename_is_a_basename_with_no_path_or_control_characters(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    assert _upload(a, _pdf(), filename="../../etc/passwd").json()["filename"] == "passwd"
    assert _upload(a, _pdf(), filename="C:\\Users\\x\\pay\x00app.pdf").json()["filename"] == "payapp.pdf"
    assert _upload(a, _pdf(), filename="").json()["filename"] == "document.pdf"
    assert len(_upload(a, _pdf(), filename="a" * 400 + ".pdf").json()["filename"]) == 255


def test_an_anonymous_upload_is_refused() -> None:
    assert client.post("/api/inbox", files={"file": ("x.pdf", _pdf())}).status_code == 401


def test_every_upload_passes_through_the_intake_door() -> None:
    """The Task 43 guard, kept: the handler calls accept_document. The
    structural tests already forbid building a Labelled any other way."""
    src = pathlib.Path("ledger_sense/api/inbox.py").read_text(encoding="utf-8")
    called = {n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
              for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)}
    assert {"accept_document", "intake", "store_document"} <= called


def _count(tenant_id: str) -> int:
    with store_session.app_engine().connect() as conn:
        return conn.execute(sa.select(sa.func.count()).select_from(documents)
                            .where(documents.c.tenant_id == tenant_id)).scalar_one()
```

- [ ] **Step 2: Run to verify RED**

Run: `uv run pytest tests/api/test_inbox.py -q -p no:cacheprovider`
Expected: every test fails — 404 from the stub (no POST) or FileNotFoundError on `inbox.py`.

- [ ] **Step 3: Implement**

`ledger_sense/api/inbox.py`:

```python
"""The inbox: the labelled door with an HTTP front.

POST composes three functions that already exist and are already guarded —
intake() (resource caps), accept_document() (the only Labelled constructor),
store_document() (the only documents.insert()) — and adds nothing of its own.
No extraction, no rules, no gateway call: those come later and read the row
this route writes.

Mounted through api.deps.register() in api/app.py, so require_session is
applied there; depended on here too because the handlers need the tenant and
user off the session.
"""

import unicodedata
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ledger_sense.api.deps import require_session
from ledger_sense.api.fixtures import demo_profile_enabled
from ledger_sense.auth.session import Session
from ledger_sense.boundary import Origin, SensitivityViolation
from ledger_sense.ingest.intake import accept_document, intake
from ledger_sense.ingest.limits import RejectedDocument, Rejection
from ledger_sense.store.blobs import blob_store_from_environment
from ledger_sense.store.documents import store_document
from ledger_sense.store.session import app_engine

router = APIRouter(prefix="/api/inbox")

CurrentSession = Annotated[Session, Depends(require_session)]

FALLBACK_FILENAME = "document.pdf"
MAX_FILENAME = 255

# Only TOO_LARGE is the client's payload being too big for the server (413).
# Everything else is a payload the server cannot process as sent (422).
_STATUS_OF: dict[Rejection, int] = {Rejection.TOO_LARGE: 413}


def sanitise_filename(name: str | None) -> str:
    """A display name, never a path: basename only (either slash), control
    characters dropped, bounded length, a fallback if nothing survives."""
    stem = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    clean = "".join(ch for ch in stem if unicodedata.category(ch)[0] != "C").strip()
    return clean[:MAX_FILENAME] or FALLBACK_FILENAME


def _provenance(origin: Origin, session: Session) -> str:
    # Who put it through the door — never the client-controlled filename.
    return f"{origin.value.lower()}://{session.tenant_id}/{session.user_id}"


def _row(document_id: str, *, filename: str, sensitivity: str, origin: str,
         received_at: datetime) -> dict[str, Any]:
    return {"id": document_id, "filename": filename, "sensitivity": sensitivity,
            "origin": origin, "received_at": received_at.isoformat()}


@router.post("", status_code=201)
async def upload(
    session: CurrentSession,
    file: Annotated[UploadFile, File()],
    origin: Annotated[Origin, Form()] = Origin.UPLOAD,
) -> dict[str, Any]:
    # Route-level policy, not the door's: CORPUS means "our generator made
    # this", which only the demo profile has any business asserting.
    if origin is Origin.CORPUS and not demo_profile_enabled():
        raise HTTPException(
            status_code=422,
            detail="origin CORPUS is accepted only under the demo profile "
                   "(LEDGER_SENSE_PROFILE=demo); a client deployment labels every "
                   "upload CLIENT_CONFIDENTIAL")
    if origin is Origin.REDACTED_UPLOAD:
        raise HTTPException(
            status_code=422,
            detail="REDACTED_UPLOAD needs a verified redaction pass and no verifier "
                   "ships yet; upload the original and it will be labelled "
                   "CLIENT_CONFIDENTIAL")

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=422, detail="file is empty")

    parsed = intake(payload)
    if isinstance(parsed, RejectedDocument):
        raise HTTPException(status_code=_STATUS_OF.get(parsed.kind, 422), detail=parsed.reason)

    try:
        doc = accept_document(payload, origin=origin, provenance=_provenance(origin, session))
    except SensitivityViolation as exc:
        # Belt and braces: the two checks above should make this unreachable,
        # but the door's refusal must never surface as a 500 traceback.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    filename = sanitise_filename(file.filename)
    received_at = datetime.now(UTC)
    with app_engine().begin() as conn:
        document_id = store_document(conn, tenant_id=session.tenant_id, doc=doc,
                                     blobs=blob_store_from_environment(),
                                     received_at=received_at, filename=filename)
    return _row(document_id, filename=filename, sensitivity=doc.sensitivity.value,
                origin=doc.origin.value, received_at=received_at)
```

`app.py`: `from ledger_sense.api.inbox import router as inbox_router` and `register(app, inbox_router)` right after `register(app, baseline_router)`; update the comment above it ("/api/baseline and /api/inbox are not…"). `_stub_routes.py`: remove the inbox handler, branch and prefix as listed in **Files**.

`tests/api/test_route_registration.py` may enumerate `PREFIXES` or the stub route count — run it and adjust any assertion that counted six stub prefixes.

- [ ] **Step 4: Run to verify GREEN**

Run: `uv run pytest tests/api tests/boundary -q -p no:cacheprovider --deselect tests/api/test_fixtures.py::test_seeded_documents_are_labelled_synthetic --deselect tests/api/test_fixtures.py::test_a_seeded_document_is_still_refused_to_an_anonymous_caller && uv run ruff check . && uv run mypy --strict ledger_sense tests`
Expected: all pass. Note the oversized test builds a 26 MB body; it should take a few seconds, not minutes.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/api/inbox.py ledger_sense/api/app.py ledger_sense/api/_stub_routes.py tests/api/test_inbox.py tests/api/test_route_registration.py
git commit -m "feat(api): POST /api/inbox — intake, label at the door, store; nothing else"
```

---

### Task 5: `GET /api/inbox` and `GET /api/inbox/{id}`

**Files:**
- Modify: `ledger_sense/api/inbox.py`
- Test: `tests/api/test_inbox.py`

**Interfaces:**
- Consumes: `_row(...)` from Task 4, `owned`.
- Produces: `GET /api/inbox?limit=&before_received_at=&before_id=` → `{items: [...]}`; `GET /api/inbox/{document_id}` → row.

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_inbox.py`:

```python
def test_the_list_is_the_tenants_own_documents_newest_first(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, b = two_tenants
    first = _upload(a, _pdf(), filename="first.pdf").json()["id"]
    second = _upload(a, _pdf(), filename="second.pdf").json()["id"]
    _upload(b, _pdf(), filename="theirs.pdf")
    items = a.get("/api/inbox").json()["items"]
    assert [i["id"] for i in items][:2] == [second, first]
    assert all(set(i) == {"id", "filename", "sensitivity", "origin", "received_at"}
               for i in items)
    assert "theirs.pdf" not in {i["filename"] for i in items}


def test_the_list_pages_by_keyset_and_caps_the_page(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, _ = two_tenants
    ids = [_upload(a, _pdf()).json()["id"] for _ in range(3)]
    page = a.get("/api/inbox?limit=2").json()["items"]
    assert [i["id"] for i in page] == [ids[2], ids[1]]
    last = page[-1]
    rest = a.get(f"/api/inbox?limit=2&before_received_at={last['received_at']}"
                 f"&before_id={last['id']}").json()["items"]
    assert [i["id"] for i in rest] == [ids[0]]
    assert a.get("/api/inbox?limit=201").status_code == 422
    assert a.get("/api/inbox?limit=0").status_code == 422
    assert a.get(f"/api/inbox?before_id={last['id']}").status_code == 422


def test_a_document_reads_back_for_its_owner_and_404s_for_anyone_else(
    two_tenants: tuple[TenantClient, TenantClient],
) -> None:
    a, b = two_tenants
    body = _upload(a, _pdf(), filename="mine.pdf").json()
    assert a.get(f"/api/inbox/{body['id']}").json() == body
    assert b.get(f"/api/inbox/{body['id']}").status_code == 404
    assert a.get("/api/inbox/nope").status_code == 404
```

- [ ] **Step 2: Run to verify RED**

Run: `uv run pytest tests/api/test_inbox.py -q -p no:cacheprovider -k "list or reads_back"`
Expected: 404/405 on the GETs.

- [ ] **Step 3: Implement**

Add to `ledger_sense/api/inbox.py` (imports: `Query` from fastapi, `sqlalchemy as sa`, `owned` from deps, `documents` from `ledger_sense.store.retention`):

```python
DEFAULT_PAGE = 50
MAX_PAGE = 200

_COLUMNS = (documents.c.id, documents.c.filename, documents.c.sensitivity,
            documents.c.origin, documents.c.received_at)


def _row_of(row: sa.Row[Any]) -> dict[str, Any]:
    return _row(row.id, filename=row.filename, sensitivity=row.sensitivity,
                origin=row.origin, received_at=row.received_at)


@router.get("")
def list_documents(
    session: CurrentSession,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = DEFAULT_PAGE,
    before_received_at: datetime | None = None,
    before_id: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    # Keyset on (received_at, id): a page boundary on received_at alone drops
    # every row that shares the boundary's timestamp.
    if (before_received_at is None) != (before_id is None):
        raise HTTPException(status_code=422,
                            detail="before_received_at and before_id go together")
    query = (sa.select(*_COLUMNS)
             .where(documents.c.tenant_id == session.tenant_id)
             .order_by(documents.c.received_at.desc(), documents.c.id.desc())
             .limit(limit))
    if before_received_at is not None and before_id is not None:
        query = query.where(sa.tuple_(documents.c.received_at, documents.c.id)
                            < sa.tuple_(before_received_at, before_id))
    with app_engine().connect() as conn:
        rows = conn.execute(query).all()
    return {"items": [_row_of(r) for r in rows]}


@router.get("/{document_id}")
def read_document(document_id: str, session: CurrentSession) -> dict[str, Any]:
    with app_engine().connect() as conn:
        row = conn.execute(
            sa.select(*_COLUMNS, documents.c.tenant_id)
            .where(documents.c.id == document_id)).one_or_none()
    owned(session, None if row is None else str(row.tenant_id))
    assert row is not None  # owned() raised otherwise; for the type checker
    return _row_of(row)
```

SQLite stores `DateTime(timezone=True)` naively; if the keyset comparison misbehaves on the tz-aware `before_received_at`, normalise it with `before_received_at.astimezone(UTC).replace(tzinfo=None)` only when the engine dialect is sqlite (`app_engine().dialect.name == "sqlite"`), and say so in a comment. Also confirm `received_at` reads back tz-aware on SQLite; if it comes back naive, attach `UTC` in `_row_of` so the JSON carries an offset on both engines.

- [ ] **Step 4: Run to verify GREEN**

Run: `uv run pytest tests/api tests/boundary -q -p no:cacheprovider --deselect tests/api/test_fixtures.py::test_seeded_documents_are_labelled_synthetic --deselect tests/api/test_fixtures.py::test_a_seeded_document_is_still_refused_to_an_anonymous_caller && uv run ruff check . && uv run mypy --strict ledger_sense tests`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/api/inbox.py tests/api/test_inbox.py
git commit -m "feat(api): GET /api/inbox list (keyset paged) and GET /api/inbox/{id}, owned()"
```

---

### Task 6: The demo seeder goes through the door

**Files:**
- Modify: `ledger_sense/api/fixtures.py` (document row via `accept_document` → `store_document`; exception rows unchanged), its module docstring
- Modify: `tests/api/test_fixtures.py` (the two inbox-reading tests now read the real row)
- Modify: `docs/security.md` (one paragraph: the inbox route is the door's HTTP front; the seeder uses the same door)
- Test: `tests/api/test_fixtures.py`, `tests/boundary/test_structure.py` (must still pass — the seeder calls `accept_document`, never `Labelled(...)` or `documents.insert`)

**Interfaces:**
- Consumes: `accept_document`, `store_document(filename=)`, `blob_store_from_environment`, `ledger_sense.corpus.generate.generate(seed, "clean") -> (Contract, PayApplication, bytes)`.

- [ ] **Step 1: Write the failing tests**

In `tests/api/test_fixtures.py`, replace `test_seeded_documents_are_labelled_synthetic` with:

```python
def test_seeded_documents_are_labelled_synthetic(monkeypatch: pytest.MonkeyPatch) -> None:
    """F-49 depends on this: a synthetic session must never produce field
    labels. Read back through the real inbox route, off the real documents
    table — the seeder stores through the same door as an upload."""
    client = _client(monkeypatch, "demo")
    body = client.post("/api/fixtures/seed", json={"doc_id": "doc-3", "exceptions": 3}).json()
    row = client.get(f"/api/inbox/{body['doc_id']}").json()
    assert (row["sensitivity"], row["origin"]) == ("SYNTHETIC", "CORPUS")
    assert row["filename"] == "doc-3.pdf"


def test_the_seeded_row_records_who_seeded_it(monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlalchemy as sa

    from ledger_sense.store import session as store_session
    from ledger_sense.store.retention import documents

    client = _client(monkeypatch, "demo")
    body = client.post("/api/fixtures/seed", json={"doc_id": "doc-4", "exceptions": 0}).json()
    with store_session.app_engine().connect() as conn:
        provenance = conn.execute(sa.select(documents.c.provenance)
                                  .where(documents.c.id == body["doc_id"])).scalar_one()
    assert provenance.startswith(f"corpus://seed/{body['tenant_id']}/")
```

The anonymous-caller test needs no change beyond reading `["doc_id"]` from the seed body, which it already does.

- [ ] **Step 2: Run to verify RED**

Run: `uv run pytest tests/api/test_fixtures.py -q -p no:cacheprovider`
Expected: the two inbox-reading tests 404 (seeded id is not in `documents`).

- [ ] **Step 3: Implement**

In `ledger_sense/api/fixtures.py`:

- Imports: `from datetime import UTC, datetime`; `from ledger_sense.boundary import Origin`; `from ledger_sense.corpus.generate import generate`; `from ledger_sense.ingest.intake import accept_document`; `from ledger_sense.store.blobs import blob_store_from_environment`; `from ledger_sense.store.documents import store_document`. Keep the `SYNTHETIC` constant — the exception rows still write it.
- After `user_id = create_user(...)`, replace the document row in `rows` and the insert block:

```python
    # The document goes through the same door as an upload: accept_document
    # labels it from its origin (CORPUS → SYNTHETIC), store_document writes
    # the only documents row. No stub row for the document any more — the
    # demo-profile proof now runs against the real table. The exception rows
    # stay in stub_resources until /api/review is real.
    _, _, payload = generate(seed=0, error="clean")
    doc = accept_document(payload, origin=Origin.CORPUS,
                          provenance=f"corpus://seed/{tenant_id}/{user_id}")
    with engine.begin() as conn:
        document_id = store_document(conn, tenant_id=tenant_id, doc=doc,
                                     blobs=blob_store_from_environment(),
                                     received_at=datetime.now(UTC),
                                     filename=f"{body.doc_id}.pdf")
        conn.execute(stub_resources.delete().where(
            stub_resources.c.id.startswith(f"{body.doc_id}:exc:", autoescape=True)))
        if exception_rows:
            conn.execute(stub_resources.insert(), exception_rows)
```

where `exception_rows = [{"id": _exception_id(body.doc_id, i), "tenant_id": tenant_id, "sensitivity": SYNTHETIC} for i in range(body.exceptions)]`. Return `{"doc_id": document_id, ...}` — the stored id, which `e2e/seed.ts` already reads. Update the `SeedRequest.doc_id` comment: it is now the exception-key prefix and the filename stem; the response's `doc_id` is the stored document's id.

`docs/security.md`, in the section that describes the labelled door (grep `accept_document`), add:

> `POST /api/inbox` is that door's HTTP front: `intake()` bounds the parse, `accept_document()` labels from the caller's declared origin (defaulting to `UPLOAD`, the most restrictive), and `store_document()` writes the one row. `CORPUS` is accepted only under the demo profile; `REDACTED_UPLOAD` is refused until a verifier ships. The demo seeder stores through the same three calls, so "the demo holds no client data" is proven against the real `documents` table.

- [ ] **Step 4: Run to verify GREEN — full suite**

Run: `uv run pytest -q -p no:cacheprovider && uv run ruff check . && uv run mypy --strict ledger_sense tests`
Expected: all pass, no deselects. `tests/boundary/test_structure.py` in particular: fixtures.py must not show up as a `Labelled` constructor or a `documents.insert` caller.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/api/fixtures.py tests/api/test_fixtures.py docs/security.md
git commit -m "feat(fixtures): the demo seeder stores through the labelled door, not a stub row"
```

---

## Self-review

- **Spec coverage:** §1 → Task 4 (module, mount, stub removal). §2 POST → Task 4 (file, origin default/gating, provenance, filename, 413/422 via `kind`, nothing stored). §2 GET list/keyset/cap → Task 5. §2 GET {id}/owned → Task 5. §3 `kind` → Task 1; `filename` → Task 2; blob wiring/multipart/compose → Task 3; seeder → Task 6. Out-of-scope items are not planned.
- **Placeholders:** none; every step has its code.
- **Type consistency:** `store_document(..., filename=)` introduced in Task 2 and used identically in Tasks 4 and 6; `Rejection`/`RejectedDocument.kind` from Task 1 used in Task 4; `blob_store_from_environment` from Task 3 used in Tasks 4 and 6; `_row` from Task 4 used by Task 5.
