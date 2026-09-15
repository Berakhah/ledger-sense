# Sensitivity Gateway — design (Never #1, Task 13 without `bok-core`)

**Date:** 2026-09-15 · **Status:** approved in brainstorming (§1–§5), plan at
`docs/superpowers/plans/2026-09-15-sensitivity-gateway.md`.

## Why this exists

`00` R4 and `02` Never #1: no client document reaches a free-tier model, and the
code — not discipline — is what stops it (`00` B2: "you will absolutely, at 2am
in week 11, paste a real client invoice into a test script"). Task 13 of the
Phase 1 plan implemented this against `bok_core.boundary` and `bok_core.gateway`.
`bok-core` is not on PyPI (404, verified 2026-09-14). Option 2, chosen in
brainstorming: **label + door + local gateway seam**, following the F-61 local-
protocol precedent (`ledger_sense/provenance.py`, `rules/provenance.py`). Nothing
here is deleted when `bok-core` ships; the seam is where it plugs in.

## The four questions this design answers

| Question | Answer | Where |
|---|---|---|
| Where does the label attach? | At the door, derived from **where the bytes came from** (`Origin`), never passed in by hand | §2 |
| Where is it judged? | At the gateway, **before** any provider state is consulted | §3 |
| How does it survive a restart? | Stored as columns with CHECKs; rebuilt into the same `Labelled` on load | §4 |
| How do we know it stays true? | Behavioural tests on the gateway, structural (AST) tests on the codebase | §5 |

## §1 — Boundary types: `ledger_sense/boundary.py`

A leaf module (stdlib imports only) so anything can import it without cycles.

```python
class Sensitivity(str, Enum):        # 00 R4 vocabulary, verbatim
    PUBLIC = "PUBLIC"
    SYNTHETIC = "SYNTHETIC"
    REDACTED = "REDACTED"
    CLIENT_CONFIDENTIAL = "CLIENT_CONFIDENTIAL"

class Origin(str, Enum):             # the three ways bytes enter THIS product
    UPLOAD = "UPLOAD"                    # a client's file
    CORPUS = "CORPUS"                    # our synthetic generator
    REDACTED_UPLOAD = "REDACTED_UPLOAD"  # a client's file after a verified redaction pass

class SensitivityViolation(Exception): ...

@dataclass(frozen=True)
class Labelled[U]:
    payload: U
    sensitivity: Sensitivity
    origin: Origin
    provenance: str
```

`ledger_sense/provenance.py` re-exports `Labelled` from here (its current
`sensitivity: object` definition is replaced) so `from ledger_sense.provenance
import Labelled` — the F-61 spelling Phase 2/4 tasks use — stays valid.

`PUBLIC` is in the enum because it is `00`'s vocabulary and the column CHECK
carries it; **nothing in Ledger Sense produces it** (no `Origin` maps to it).

## §2 — The door: `ledger_sense/ingest/intake.py`

```python
@dataclass(frozen=True)
class RedactionVerdict:
    verified: bool
    verifier: str          # who said so — bok-core's pass, when it exists
    detail: str = ""

def accept_document(payload: bytes, *, origin: Origin, provenance: str,
                    redaction: RedactionVerdict | None = None) -> Labelled[bytes]
```

- `UPLOAD → CLIENT_CONFIDENTIAL`, `CORPUS → SYNTHETIC`, `REDACTED_UPLOAD → REDACTED`.
  The mapping is a dict keyed by `Origin`; a test asserts it covers every member.
- `REDACTED_UPLOAD` without a `RedactionVerdict(verified=True)` raises
  `SensitivityViolation` (message contains "verif"). No verifier ships in this
  task — `bok-core`'s redaction pass is the only one the brief accepts — so in
  v1 every real upload is `CLIENT_CONFIDENTIAL`. That is the honest state.
- A `RedactionVerdict` supplied with a non-redacted origin raises: a verdict does
  not lift an `UPLOAD` to `REDACTED`, and a caller who thinks it does is the
  2am case.
- The existing `intake(payload, limits)` pass-through (Rule 2, the only caller
  of `parse_within_limits`) is untouched.

Safety-by-structure over safety-by-discipline (S19): a caller cannot mislabel a
client upload as `SYNTHETIC` because the function has no sensitivity parameter.

## §3 — The seam: `ledger_sense/gateway.py`

```python
class Lane(str, Enum):               # 00 R1 lane names; code asks for a lane, never a model
    LOCAL_FALLBACK = "local-fallback"    # deterministic, no provider (must exist for every lane)
    EXTRACT_VISION = "extract-vision"
    @property
    def is_model(self) -> bool: return self is not Lane.LOCAL_FALLBACK

@dataclass(frozen=True)
class ProviderPolicy:
    enabled: bool             # BOK_PROVIDERS_DISABLED / no key / no models.yaml → False
    redacted_opt_in: bool     # the client's written opt-in, a per-tenant fact the caller supplies

class ProviderDisabled(Exception): ...

class Transport(Protocol):
    def send(self, lane: Lane, payload: bytes) -> bytes: ...

def admit(lane: Lane, doc: Labelled[bytes], *, policy: ProviderPolicy,
          transport: Transport) -> bytes
```

Order inside `admit`, and the order is the point:

1. Local lane → skip to 4. The deterministic path takes everything (degraded mode, `00` R3).
2. **Label check.** `SYNTHETIC` passes. `REDACTED` passes only with
   `policy.redacted_opt_in`. `PUBLIC` and `CLIENT_CONFIDENTIAL` raise
   `SensitivityViolation`. This runs first so that no configuration change —
   enabling a provider, adding a key — can open the door.
3. **Provider check.** `not policy.enabled` → `ProviderDisabled`.
4. `transport.send(lane, doc.payload)`.

Decisions recorded:

- **`PUBLIC` is refused at the model lane.** `00` R4's table admits it; `02`
  Never #1 and `docs/security.md` say "SYNTHETIC or REDACTED only". The brief
  wins for this product, and nothing produces `PUBLIC` anyway.
- **`00` R4(b) — `CLIENT_CONFIDENTIAL` to a `trains_on_data: false` provider on a
  client-paid key — is deliberately not implemented.** `02` forbids it in v1.
  `ProviderPolicy` therefore carries no `trains_on_data`/`commercial_use`
  fields; adding them is the day that decision is reversed, not before.
- No real `Transport` ships. The seam is the deliverable; the first transport
  arrives with `models.yaml` and a working free-tier account (`00` B1).
- **Known limitation (final review, 2026-09-15):** `admit(Lane.LOCAL_FALLBACK,
  doc, ..., transport=t)` follows the same §3 steps 1→4 as any lane and ends
  by calling `t.send(LOCAL_FALLBACK, doc.payload)` — so once a real HTTP
  `Transport` exists, confidential bytes on the local lane stay off the wire
  only because that transport's own implementation happens to branch on
  `LOCAL_FALLBACK` before making a request, not because `admit` enforces it.
  This is a spec defect, ruled out of scope for this task and deferred to the
  first `Transport` task: the local lane must not go through the provider
  `Transport` at all — e.g. a separate local callable outside `Transport`, or
  a `Transport.send` typed to accept only model lanes — plus a test asserting
  a `Transport` is never called with `Lane.LOCAL_FALLBACK`. Close this note
  when that lands.

## §4 — Persistence

Migration `0003_sensitivity` adds to `documents`: `sensitivity TEXT NOT NULL`,
`origin TEXT NOT NULL`, `provenance TEXT NOT NULL`, with **named** CHECKs
`ck_documents_sensitivity` / `ck_documents_origin` listing the values as
literals (a migration imports nothing from the package — 0002's rule). Server
defaults `'CLIENT_CONFIDENTIAL'` / `'UPLOAD'` / `'unknown:pre-0003'` backfill any
row that predates the label — confidential-from-an-upload is the only honest
guess — and are dropped **in a second `batch_alter_table` block**: SQLite batch
mode recreates the table once per block, and a block that both adds the column
and drops its default copies existing rows into a NOT NULL column with nothing
to fill it. The CHECKs are named because SQLite batch recreate silently drops
unnamed ones, and 0002 promises Phase 2 and 4 will ALTER `documents` again.

`ledger_sense/store/documents.py` — one writer, one reader:

```python
class DocumentNotFound(LookupError): ...

def store_document(conn: Connection, *, tenant_id: str, doc: Labelled[bytes],
                   blobs: BlobStore, received_at: datetime) -> str
def load_document(conn: Connection, *, tenant_id: str, document_id: str,
                  blobs: BlobStore) -> Labelled[bytes]
```

- `store_document` takes `Labelled[bytes]`, never `bytes`: a row cannot exist
  without a label because the function cannot be called without one. It is the
  only `documents.insert()` under `ledger_sense/` (guarded). The blob is written
  before the row and deleted again if the insert raises — purge is row-driven
  and would never find an orphan.
- `load_document` rebuilds `Labelled(Sensitivity(row.sensitivity),
  Origin(row.origin), row.provenance)` — the label survives a restart without
  being re-labelled by hand. Wrong tenant, unknown id, `blob_id IS NULL`, or a
  purged blob all raise `DocumentNotFound` — the 404-not-403 pattern.
- `retention.py`'s `documents` Table grows the same three columns and the same
  two named CheckConstraints; the existing metadata-vs-migration test polices
  the columns.
- `tests/store/conftest.py`'s two raw inserts (`blob_id=None` rows that
  `store_document` cannot produce) gain the three columns explicitly.
- `api/fixtures.py`: `SYNTHETIC = Sensitivity.SYNTHETIC.value` — one spelling.

Out of scope: an upload route (Phase 1 ingest). This gives it the only function
it will be allowed to call.

## §5 — Guard tests: `tests/boundary/`

F-60: the feature is cuttable, the tests are not. The spy transport doubles the
*transport*; `admit` is always the real one.

Behavioural:
- Exhaustive allow-matrix: `list(Sensitivity)` against an explicit table; a fifth
  value fails until someone puts it in the table.
- Ordering: `CLIENT_CONFIDENTIAL` with the provider enabled *and* disabled both
  raise `SensitivityViolation` (never `ProviderDisabled`), `send` never called.
  `SYNTHETIC` with the provider disabled raises `ProviderDisabled` — proves step 3
  exists and is second.
- `REDACTED` ± opt-in. Local lane admits every label with providers disabled.
- Persistence: `store_document → load_document → admit` still refuses
  `CLIENT_CONFIDENTIAL`.
- Migration: CHECK refuses `'FREE_TIER_OK'`, accepts every enum value;
  upgrade→downgrade→upgrade on an empty DB and on a DB with a pre-0003 row.

Structural, via `tests/_ast_guard.py::calls_to` (the F-32 alias-resolving shape,
extracted once for this plan, Phase 2 Task 18, Phase 3 Task 26, Phase 5 Task 36):
- `Labelled(...)` constructed only in `ingest/intake.py` and `store/documents.py`.
- `documents.insert(` only in `store/documents.py`.
- Provider SDK / HTTP client imports (`httpx`, `requests`, `openai`, `anthropic`,
  `google.generativeai`, `groq`, `mistralai`, `cohere`) nowhere under
  `ledger_sense/` except `gateway.py` — no path to a provider around `admit`.
- The deterministic packages (`extract`, `rules`, `ingest`, `domain`, `corpus`,
  `baseline`) never import `ledger_sense.gateway`.
