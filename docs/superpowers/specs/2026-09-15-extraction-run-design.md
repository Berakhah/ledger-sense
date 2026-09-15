# Extraction run — design

**Date:** 2026-09-15
**Status:** approved in brainstorming, awaiting plan
**Builds on:** `2026-09-15-inbox-route-design.md`, `2026-09-15-sensitivity-gateway-design.md`,
`2025-03-11-ledger-sense-design.md` §4.3 (extraction), §6 (rules)

## Problem

The extraction pipeline (`ingest.classify` → `extract.document.parse` → `extract.assemble`)
and the arithmetic rules (`rules.engine.run_rules`, A1–A8) exist and are tested, but the only
caller is the stateless CLI probe. A document stored by `POST /api/inbox` is never run, and
nothing persists a `Claim` or a `Finding`. The `claims` table is a retention stub with no value
columns. Phase 4's review queue therefore has nothing real to read.

This slice adds **the run seam**: take a stored document row, run the deterministic pipeline,
persist its Claims and Findings with a status, and expose that status through the inbox.

## Rulings (settled in brainstorming)

1. **Trigger.** A `status` column on `documents` plus one idempotent runner. The inbox route
   schedules the runner in-process via FastAPI `BackgroundTasks` after the row commits; a CLI
   command re-runs anything stuck. No queue infrastructure, no `running` state, no poller.
2. **Byte-identical duplicates.** Store the second upload as its own row (audit trail of who
   sent what, when), flag it as a `duplicate` finding routed to review, and do not re-extract.
3. **Run scope.** Classify → assemble → arithmetic rules A1–A8, persisting Claims and Findings.
   Contract terms and contract-dependent rules (retainage, change orders, vendors) are a later
   slice with a `contracts` table. Only `contract.rounding` is read by the registered rules, so
   the run supplies a tenant-default `RoundingPolicy(2, 2)` and **records that it did**.
4. **History.** Results replace in place; no `runs` table. The one thing worth keeping from the
   history alternative — how the run was configured — lives on the document row.

## 1. Schema — migration 0005

### `documents` gains

| column | type | notes |
|---|---|---|
| `status` | `TEXT NOT NULL DEFAULT 'received'` | named CHECK `ck_documents_status` ∈ `received, extracted, cannot_extract, duplicate, failed` |
| `content_sha256` | `TEXT NULL` | hex digest of the **plaintext** bytes, written by `store_document`. NULL only for rows that predate 0005; such rows are excluded from duplicate matching. Partial index `ix_documents_tenant_sha` on `(tenant_id, content_sha256) WHERE content_sha256 IS NOT NULL`. |
| `doc_type` | `TEXT NULL` | classifier's answer: `pay_application, continuation_sheet, invoice, lien_waiver, unknown` (named CHECK, NULL allowed) |
| `run_note` | `TEXT NULL` | human-readable reason for `cannot_extract` / `duplicate` / `failed`. Error class + first 200 chars of message. Never a traceback, never document content. Cleared on `extracted`. |
| `rounding_policy_source` | `TEXT NULL` | `'tenant_default'` in v1. Set by the run alongside `extracted`. |

### `claims` gains

Existing: `id, tenant_id, document_id`. Added:

| column | type | notes |
|---|---|---|
| `field` | `TEXT NOT NULL` | the provenance key, e.g. `total_completed_and_stored`, `line[3].this_period` |
| `value` | `TEXT NOT NULL` | Money / date / int / str rendered as string. Decimal never leaves as float. |
| `value_type` | `TEXT NOT NULL` | CHECK ∈ `money, date, int, str` |
| `method` | `TEXT NOT NULL` | `provenance.Method` value |
| `confidence` | `REAL NULL` | uncalibrated; NULL for the text layer |
| `match_quality` | `REAL NULL` | |
| `page` | `INTEGER NOT NULL` | |
| `x0, top, x1, bottom` | `REAL NOT NULL` | top-left origin, per `extract/document.py` contract |
| `caveat` | `TEXT NULL` | |
| `observed_at` | `TIMESTAMPTZ NOT NULL` | |

Unique `uq_claims_document_field (document_id, field)`. Retention purge already deletes by
`document_id`; unchanged.

`confidence`/`match_quality`/bbox are geometry and scores, not money — `REAL` is acceptable
there. Money is `TEXT`. The no-float lint covers the store path exactly as it covers rules.

### New `findings`

| column | type | notes |
|---|---|---|
| `id` | `TEXT PK` | |
| `tenant_id` | `TEXT NOT NULL FK tenants` | |
| `document_id` | `TEXT NOT NULL FK documents` | |
| `rule_id` | `TEXT NOT NULL` | `A1`–`A8`, `X0` (unextractable field), `D0` (byte duplicate) |
| `field` | `TEXT NOT NULL` | `"document"` for D0 |
| `message` | `TEXT NOT NULL` | |
| `expected`, `actual` | `TEXT NULL` | Money as string; NULL for X0/D0 |
| `page` | `INTEGER NULL` | from the claim for `field`, NULL when there is none |
| `x0, top, x1, bottom` | `REAL NULL` | likewise |
| `state` | `TEXT NOT NULL DEFAULT 'open'` | named CHECK ∈ `open, accepted, edited, rejected` — Phase 4's `ExceptionState` |
| `created_at` | `TIMESTAMPTZ NOT NULL` | |

Indexes: `ix_findings_document_order (document_id, page, top, x0)` — Phase 4's queue order —
and `ix_findings_tenant_id_id (tenant_id, id)` matching the claims convention.

Retention purge deletes `findings` by `document_id` and `PurgeReport` gains `findings_purged`.

Downgrade drops `findings`, the added `claims` columns and the added `documents` columns.

## 2. Runner — `ledger_sense/run/document.py`

```python
class RunOutcome(NamedTuple):
    document_id: str
    status: str          # the row's status after the call
    note: str | None
    claims_written: int
    findings_written: int

def run_document(engine: Engine, document_id: str) -> RunOutcome: ...
def run_pending(engine: Engine, *, limit: int = 100) -> list[RunOutcome]: ...
```

`run_document` never raises for a document-level problem. Infrastructure failures (DB
unreachable, blob store misconfigured, KEK missing) propagate.

The runner owns its transactions (it needs two on failure), which is why it takes an `Engine`, not a connection. Steps, inside one `engine.begin()`:

1. **Take the row.** `SELECT … FOR UPDATE` on `documents` (real lock on Postgres, no-op on
   SQLite where writers serialise anyway). Missing row → `RunOutcome(status="missing")` (an outcome, not a row status).
   `status ∉ {received, failed}` → return the current status with `note="already <status>"`,
   touching nothing. Any `findings` row for this document with `state != 'open'` → return with
   `note="human decisions present; refusing to overwrite"`, touching nothing. This is the
   idempotency guard: a BackgroundTask and the CLI racing on one id cannot double-write.
2. **Duplicate.** If `content_sha256` is set and another row **on the same tenant** shares it
   with an earlier `(received_at, id)` → write `status='duplicate'`,
   `run_note="duplicate of <id>"`, one finding `D0` (`field="document"`, message names the
   earlier id). No blob load, no extraction. Ties on `received_at` break by `id`, so exactly one
   of a pair is the original.
3. **Load and classify.** `load_document` (the one reader) decrypts to bytes in memory.
   `classify(pdf)` → write `doc_type`. Not `pay_application` → `status='cannot_extract'`,
   `run_note="doc_type=<t>: only pay applications are extracted in v1"`, no claims. A
   standalone continuation sheet gets the same honest refusal; the corpus emits both pages in
   one PDF.
4. **Extract.** `assemble_pay_application(pdf, source_path=document_id)`. Every `Claim` in
   `app.provenance` whose key does not start with `_` → one `claims` row. Every entry in
   `provenance["_unextractable"]` → an `X0` finding with message `"could not extract"`, no
   bbox.
5. **Rules.** `run_rules(app, contract=_tenant_default_contract())`. The helper builds a
   `Contract` whose only meaningful field is `rounding=RoundingPolicy(2, 2)`, `id="tenant-default"`;
   nothing persists it. Each `Finding` → a `findings` row; `page`/bbox are looked up from the
   claim written for `finding.field` (absent → NULLs). `rounding_policy_source='tenant_default'`
   is written on the row.
6. **Write.** Delete this document's existing claims and findings (all `open` by step 1),
   insert the new set, set `status='extracted'`, `run_note=NULL`.
7. **Any exception in steps 3–5** (`pdfminer`/`pdfplumber` errors, `AmbiguousColumnError`,
   `MissingColumnError`, `AmbiguousLabelError`, decode failures, …): the transaction rolls back
   so no partial claims survive; a fresh short transaction writes `status='failed'`,
   `run_note=f"{type(e).__name__}: {str(e)[:200]}"`. Nothing reaches the request that
   scheduled the run.

`run_pending(engine, limit=100)` selects `received`/`failed` ids oldest-first (by
`received_at, id`), up to `limit`, and calls `run_document` for each in its own transaction. One
failure does not stop the batch. This is what the CLI calls and what any later poller would call.

Module placement: `ledger_sense/run/` is a new package (`__init__.py`, `document.py`). It sits
above `extract`, `rules` and `store` and below `api`; nothing in `extract`/`rules` imports it.

## 3. Trigger and API surface

**Inbox route.** `upload()` takes a `BackgroundTasks` parameter. After the
`with app_engine().begin()` block exits (row committed), it calls
`background.add_task(run_in_background, document_id)`. `run_in_background` (in
`ledger_sense/run/document.py`) opens its own connection and calls `run_document`; it catches
and logs every exception with `document_id` and `tenant_id` — never the client-chosen
filename — because a BackgroundTask failure after the 201 has nowhere else to go. Starlette's
`TestClient` runs background tasks before returning, so route tests assert post-run state
without threads.

The 201 body is unchanged except it now carries `status: "received"`.

**Hash at the door.** `store_document` computes `content_sha256` over the plaintext bytes it
already holds and writes it in the same insert. `accept_document` and `intake` are untouched.

**Read side.** `GET /api/inbox` and `GET /api/inbox/{id}` rows gain `status`, `doc_type`,
`run_note`, `finding_count` (findings with `state='open'`), `claim_count`. Counts are computed in
the same `owned()`-scoped query. `GET /api/inbox?status=<s>` filters, validated against the
CHECK set (422 otherwise). No new endpoints: the queue read
(`GET /api/review/documents/{id}/exceptions`) is Phase 4's and reads the `findings` table this
slice creates.

**CLI.** `python -m ledger_sense extract --pending [--limit N]` calls `run_pending` and prints
one JSON line per `RunOutcome`. `python -m ledger_sense extract --document <id>` runs one.
The existing `extract <path>` stays as the stateless dev probe.

**Deliberately absent:** poller loop, retry backoff, `running` state. If the process dies
mid-run the row is still `received` and `--pending` picks it up.

## 4. Boundary, tenancy, security

- **Never #1.** The runner is deterministic-only. It imports `classify`, `parse`/`assemble`,
  `run_rules`, `load_document` and nothing from `gateway` or any provider client. A structural
  test pins that `ledger_sense/run/` imports neither `ledger_sense.gateway` nor
  `ledger_sense.extract.ocr`. `CLIENT_CONFIDENTIAL` documents are extracted locally exactly like
  `SYNTHETIC` ones.
- **Tenancy.** Every write carries the document's `tenant_id`. Duplicate matching is
  tenant-scoped in SQL: tenant B uploading tenant A's bytes is never told about A's row.
- **Bytes stay in memory.** Plaintext is never spooled to disk by the runner.
- **Claims hold extracted values** — this was already retention's premise ("the claims are where
  the bank details live"). Purge now covers `findings` too.
- **`content_sha256` is of plaintext.** It reveals only equality, only within a tenant. Noted in
  `docs/security.md`.
- **`load_document` allowlist.** The boundary guard's set of permitted callers gains
  `ledger_sense/run/document.py`.

## 5. Testing

- `tests/store/test_migrations.py` — 0005 up/down; named CHECKs present; `uq_claims_document_field`
  and `ix_findings_document_order` exist; downgrade removes everything it added.
- `tests/store/test_documents.py` — `store_document` writes `content_sha256`; same bytes twice →
  same hash, two rows.
- `tests/run/test_document.py` (corpus PDFs, SQLite, generated in-process):
  - clean seed → `extracted`, `claims` count equals non-underscore provenance entries, zero
    findings, `rounding_policy_source='tenant_default'`;
  - each injected error class in `corpus/errors.py` that A1–A8 cover → the expected `rule_id`
    with page/bbox equal to the matching claim's;
  - an unextractable field → `X0` finding, no bbox;
  - non-pay-application bytes → `cannot_extract`, `doc_type` set, note set, zero claims;
  - corrupt bytes → `failed`, note starts with the exception class, zero claims (rollback);
  - second run on `extracted` → no-op, row and counts unchanged;
  - run after a finding is set to `accepted` → refused, nothing touched;
  - byte duplicate → `duplicate`, one `D0`, zero claims; ties on `received_at` resolved by id;
  - same bytes on another tenant → `extracted` normally.
- `tests/run/test_pending.py` — oldest first, `limit` honoured, one failing document does not
  stop the batch.
- `tests/api/test_inbox.py` — 201 body has `status: received`; after the background task the
  GET shows `extracted` with counts; a failing run never alters the 201; `?status=failed`
  filter and 422 on an unknown status; another tenant's counts are not visible.
- `tests/test_main.py` — `extract --pending`, `--document`, and the unchanged `extract <path>`.
- `tests/boundary/` — `ledger_sense/run/` imports no gateway/provider/OCR module; `load_document`
  caller allowlist updated and still pinned.
- `tests/store/test_retention.py` — purge deletes findings, reports `findings_purged`.
- `tests/test_degraded_mode.py` — upload → run → findings with every provider env var unset.
- No-float lint runs over `ledger_sense/run/` unchanged. No new arithmetic, so no new
  `hypothesis` tests.

## Out of scope

`contracts` table and contract-dependent rules · review-queue endpoints (Phase 4) · a poller or
retry policy · run history · model-lane escalation · OCR for the scanned tail (the runner treats
`has_text_layer() == False` as whatever `assemble` does today; OCR routing is its own slice).
