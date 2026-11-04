# Inbox route — design

The first route that accepts a document. It composes three functions that already
exist and are already guarded; it adds no business logic of its own.

## §1 Architecture

One new module, `ledger_sense/api/inbox.py`, mounted via `register()` like
`baseline.py`. It replaces the `/api/inbox` entries in `_stub_routes.py`
(deleting `_read_inbox_document` and the inbox prefix; the stub file stays until
the other prefixes are real).

```
POST /api/inbox
  session ─▶ require_session (tenant, user)
  bytes   ─▶ intake()            parse_within_limits: bombs, size, pages, timeout
          ─▶ accept_document()   origin → label; the only Labelled constructor
          ─▶ store_document()    the only documents.insert()
          ─▶ 201 {id, filename, sensitivity, origin, received_at}
```

No extraction, no rules, no gateway call. The existing AST guards already ensure
the route cannot build a `Labelled` or insert a row any other way; the new guard
is that `inbox.py` must call `accept_document` (the Task 43 test, kept).

## §2 Contract

### `POST /api/inbox` — `multipart/form-data`

- `file` — required. 422 if missing or empty.
- `origin` — optional, default `UPLOAD`. Declared as the `Origin` enum, so an
  unknown string is a 422 from the schema. `UPLOAD` always accepted. `CORPUS`
  accepted only when `demo_profile_enabled()`; otherwise 422 with a message that
  says why. `REDACTED_UPLOAD` → 422 always (no verifier ships). The route never
  lets a `SensitivityViolation` escape as a 500.
- Default `UPLOAD` is the fail-closed direction: a caller who forgets the field
  gets `CLIENT_CONFIDENTIAL`, never anything looser.
- Provenance written to the row: `upload://{tenant_id}/{user_id}` — who put it
  through the door, in the same grammar as the existing `corpus://…` convention.
  Never the client-controlled filename.
- Filename is stored in its own column, sanitised: basename only (both slash
  kinds stripped), control characters removed, capped at 255 characters,
  `document.pdf` if nothing survives.
- Limits rejections (`RejectedDocument`) map on `kind`, not on the reason text:
  `TOO_LARGE` → 413; `NOT_PDF`, `TOO_MANY_PAGES`, `BOMB`, `TIMEOUT`, `UNPARSEABLE`
  → 422. `detail` carries the reason from `limits.py`. Nothing is stored on
  rejection. `UNPARSEABLE` is refused *for now*; once the inbox has a status
  column it becomes a stored-and-flagged document (B11: an honest "cannot
  extract" a reviewer can see), not a refusal.

### `GET /api/inbox`

List for the session's tenant, newest first:
`{items: [{id, filename, sensitivity, origin, received_at}]}`.
Paged by `?limit=` (default 50, max 200) and keyset `?before_received_at=&before_id=`
(both or neither) so rows sharing a timestamp are not dropped. No `status`,
`confidence_band` or `flag_count`: those columns do not exist, and placeholders
would be a lie on the inbox screen.

### `GET /api/inbox/{id}`

Same row shape. `owned()` → 404 for another tenant's id.

## §3 Consequences elsewhere

- **`RejectedDocument.kind`** — a `Rejection` enum on the dataclass; each of the
  seven construction sites in `limits.py` names its kind.
- **`documents.filename`** — migration `0004_filename`, `TEXT NOT NULL`, same
  add-with-default-then-drop pattern as 0003. `store_document` takes
  `filename=`; `load_document` is unchanged (filename is not part of the label).
- **Blob store wiring** — `LEDGER_SENSE_BLOB_ROOT` joins `LEDGER_SENSE_KEK`;
  `blob_store_from_environment()` builds the `BlobStore`. Compose gives the
  client profile a named volume and the demo profile tmpfs. `python-multipart`
  is added (MIT) for form parsing.
- **The demo seeder goes through the door.** `api/fixtures.py` stores its
  document via `accept_document(origin=CORPUS)` → `store_document`, with a
  corpus-generated PDF as payload and provenance `corpus://seed/{tenant_id}/{user_id}`.
  Its exception rows stay in `stub_resources` until `/api/review` is real. The
  request's `doc_id` becomes the exception-key prefix only; the response's
  `doc_id` is the stored document's id, which is what `e2e/seed.ts` already reads.

## Out of scope

Duplicate detection (`content_hash`), status, extraction on upload, the inbox
screen. Each is its own plan.
