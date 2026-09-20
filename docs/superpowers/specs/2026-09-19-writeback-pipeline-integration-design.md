# Writeback pipeline integration — design

**Date:** 2026-09-19
**Status:** approved in brainstorming, awaiting plan
**Builds on:** `2026-09-15-extraction-run-design.md` (claims/findings schema, runner),
Phase 5 writeback (`ledger_sense/writeback/`, merged at `d9b48f2`)

## Problem

Phase 5 built the writeback stack — `WritebackPort`, `QBOAdapter`, `CSVExportAdapter`,
`IdempotencyLedger`, `AuditedAdapter` — and proved it safe under concurrency and crash-retry.
Nothing calls into it. There is no HTTP surface, no path from a document's cleared review-queue
exceptions to a draft bill, and no reconstruction of a `WriteRequest` from the `claims` table.
Two Phase 5 exit items are also still open: `ConcurrentWriteInProgressError` has no HTTP mapping,
and the in-process concurrency guard's single-process assumption was never asserted anywhere.

This slice adds **the writeback seam**: a human-triggered API surface that gates on review-queue
completion, reconstructs a `WriteRequest` from stored claims, and posts through whichever adapter
the caller names.

## Rulings (settled in brainstorming)

1. **Trigger.** Explicit human action only — a new API route the frontend calls, never an
   automatic step inside `run_document`/`run_pending`. The runner's job stays extraction and
   validation; posting to the accounting system is a separate, later, human-triggered action.
   This keeps Never-rule #3's human checkpoint literal.
2. **Field-mapping scope.** Minimal mapping only. `vendor_ref` and `vendor_invoice_number` are
   supplied by the caller in the request body (nothing extracts a vendor today — there is no
   vendor entity in `domain/models.py` at all). `account_ref` is one tenant-wide default constant
   read from the environment, applied to every line. Real vendor fuzzy-resolution, vendor merge,
   and per-line/per-vendor account mapping are explicitly deferred to a later phase.
3. **Multi-process concurrency.** The in-process `ConcurrentWriteInProgressError` guard (Phase 5,
   C-1) only holds within one process. The deployment is already single-process (`uvicorn.run`
   with no worker count in `ledger_sense/__main__.py`; one container in `docker-compose.yml`), so
   this slice documents the constraint and asserts it (see §6) rather than building a cross-process
   lock for a topology that does not exist yet.
4. **Adapter selection.** Per-request, explicit (`adapter: "qbo" | "export"` in the body) — not a
   stored tenant setting (no config store exists yet either). Matches the "CSV/IIF is a peer
   adapter, not a fallback" framing from the brief.
5. **Idempotency key / no new document state.** The key is deterministic
   (`build_idempotency_key(document_hash, contract_id, application_no)`, already built in
   `writeback/interface.py`), so re-posting the same document always derives the same key. "Has
   this been posted" is answered by looking that key up in the `IdempotencyLedger` — no new
   `documents` columns, no migration. The key deliberately excludes vendor identity, so the ledger
   entry itself must carry the `vendor_ref`/`vendor_invoice_number` the first post used (already
   captured in the stored `WriteRequest`/`WriteResult`) — the route compares against these on every
   later call to distinguish a true replay from a vendor conflict (§4, §5).

## 1. Claims → WriteRequest assembly

New module `ledger_sense/writeback/assemble.py`.

```python
def derive_idempotency_key(conn: Connection, *, document_id: str) -> str: ...

def build_write_request(
    conn: Connection, *, tenant_id: str, document_id: str,
    vendor_ref: str, vendor_invoice_number: str | None,
) -> WriteRequest: ...
```

`derive_idempotency_key` reads only `documents.content_sha256` plus the latest `contract_id` and
`application_no` claims — nothing vendor-related — and calls the existing
`build_idempotency_key(document_hash, contract_id, application_no)`. A document with no
`content_sha256` (pre-migration-0005 row) or no `contract_id`/`application_no` claim raises
`MissingClaimError(field)` (new, in `assemble.py`) — there is no safe fallback for a key that must
stay stable across retries. This is the only piece the `GET status` route (§4) needs, so it works
without a `vendor_ref`.

`build_write_request` calls `derive_idempotency_key` internally and then fills in the rest:

- Reads every claim row for `document_id`, grouped by `field`. Where a field has more than one
  claim (extraction, then a human accept/edit/reject-derived claim), the **latest by
  `observed_at`** wins — the same rule `api/review.py::_value_type_for` already uses. This means a
  reviewer's correction is picked up automatically with no separate "resolved value" table.
- Top-level fields consumed: `contract_id`, `application_no`, `period_to`,
  `total_completed_and_stored`, `retainage_total`, `previous_payments`, `change_orders_net`.
- Line items: grouped by the `line[{item_no}]` prefix (matching `extract/assemble.py`'s provenance
  key convention). Each line's `LineItem.amount` is its `this_period` claim (the amount billed on
  *this* application — the number a bill should actually carry, not the running total).
  `description` from the line's own claim. `account_ref` is the same tenant-wide default constant
  for every line (§2).
- `txn_date` = `period_to`. `memo` is generated: `f"Pay application {application_no}, contract
  {contract_id}"`.
- Any required top-level field or any line missing its `description`/`this_period` claim raises
  `MissingClaimError(field)` — nothing is guessed or defaulted silently in the money path.

## 2. Adapter construction

`writeback/qbo.py` gains `qbo_adapter_from_environment(ledger: IdempotencyLedger) -> QBOAdapter`,
`writeback/export.py` gains `export_adapter_from_environment(ledger: IdempotencyLedger) ->
CSVExportAdapter` — both mirror the existing `blob_store_from_environment()` factory pattern
(`store/blobs.py`). Each returned adapter is wrapped in `AuditedAdapter` before use. A new env var,
`LEDGER_SENSE_DEFAULT_ACCOUNT_REF`, supplies §1's default account; missing at process start is a
hard failure (fail fast, not a silent empty string reaching QBO).

## 3. Review-queue gate

Before assembling, the route requires `findings.state != 'open'` for every row on that document
(`remaining == 0`), reusing `api/review.py::progress`'s counting query. A document with open
exceptions, or with no findings rows at all (never run), refuses with 422 and a message naming how
many exceptions remain.

## 4. API surface — `ledger_sense/api/writeback.py`

Mounted through `register()` like every other router (session-guarded, tenant-owned, subject to
`test_every_api_route_requires_a_session`).

| Route | Body | Behavior |
|---|---|---|
| `POST /api/writeback/documents/{doc_id}/post` | `{adapter, vendor_ref, vendor_invoice_number?}` | Gate (§3) → assemble (§1) → `create_unapproved_bill` → `WriteResult`. Idempotent: a second call for the same document whose `vendor_ref`/`vendor_invoice_number` match the ones recorded on the original post returns that `WriteResult` with `was_replay=True`. A second call with *different* vendor fields is a conflict, not a replay (§5) — the idempotency key doesn't cover vendor identity, so silently honoring the first call's data while a caller believes they corrected it would bury a real divergence. |
| `POST /api/writeback/documents/{doc_id}/reverse` | `{adapter, remote_id, idempotency_key}` | `reverse(remote_id, idempotency_key)` → `ReversalResult`. |
| `GET /api/writeback/documents/{doc_id}/status` | — | Calls `derive_idempotency_key` (§1, no gate, no vendor fields needed) and looks it up in the ledger; `{"state": "not_posted"}` if absent. |

Every route uses `deps.owned()` against the document's `tenant_id` first (404, not 403, on a
cross-tenant id) before touching writeback state.

## 5. Error handling

| Exception | HTTP | Detail |
|---|---|---|
| `ConcurrentWriteInProgressError` | 409 | fixed `Retry-After: 2` header (a same-process in-flight race, not a rate limit — a short fixed hint is correct, not a computed backoff) |
| vendor-field mismatch on an already-posted document | 409 | `"already posted with different vendor_ref/vendor_invoice_number; reverse and re-post to change vendor"` — no `Retry-After` (retrying with the same mismatched body will never resolve it) |
| `ClosedPeriodError` | 422 | the refusal message verbatim (already client-safe text) |
| `MissingClaimError` | 422 | `"cannot post: no claim for <field>"` |
| review-queue gate failure | 422 | `"N exceptions still open"` |
| `QBORejected` / adapter HTTP failure | 502 | generic `"the accounting system rejected the write"` — the provider's raw error body is logged, never returned, since it can carry `CLIENT_CONFIDENTIAL` data echoed back from the request |

`ApprovedTransactionError` is not caught specially: this route only ever calls
`create_unapproved_bill`, so it can only fire on an adapter-internal invariant violation, which
should surface as an uncaught 500.

## 6. Single-process constraint (Ruling 3)

`ledger_sense/__main__.py` calls `uvicorn.run(...)` with a hardcoded worker count today (no CLI
flag, no env var reads it). The assertion is pinned to that call site, not to runtime process
inspection: a module-level constant `WORKERS = 1` passed as `uvicorn.run(..., workers=WORKERS)`,
with an `assert WORKERS == 1, "..."` immediately above it explaining that raising this requires the
cross-process idempotency lock described below. This makes the constraint a source-level fact a
regression test can import and assert on (`from ledger_sense.__main__ import WORKERS; assert
WORKERS == 1`) rather than something that has to be inferred from how the process was launched. A
line in `docs/methodology.md` states the constraint and what upgrading it requires (a cross-process
lock in the idempotency ledger, deferred).

## Testing bar

- `assemble.py`: line reconstruction from claims, human-edit-supersedes-extraction (write an
  extraction claim then a later HUMAN claim for the same field, assert the human value wins),
  missing-field refusal, idempotency-key stability across two assembly calls for the same document.
- Route tests per endpoint: gate refusal (open exceptions), successful post, replay-returns-same-
  result, replay-with-different-vendor-fields-returns-409, reversal, each row of the §5
  error-mapping table, cross-tenant 404.
- Extend the existing writeback import-boundary guard test to also assert
  `ledger_sense.api.writeback` does not import `ledger_sense.extract`/`ledger_sense.ingest`
  directly (it goes through `assemble.py`, which reads persisted claims, not the pipeline).
- Single-process assertion has its own regression test (§6).

## Out of scope

- Vendor entity, vendor fuzzy-resolution, vendor merge (Never-rule #6) — later phase.
- Per-vendor / per-line account mapping — later phase; one tenant-wide default account for now.
- Cross-process idempotency locking — deferred per Ruling 3 until the deployment topology changes.
- QBO sandbox cassette recording (`tests/writeback/cassettes/`) — separate, credential-gated task
  tracked outside this spec.
