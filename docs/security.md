# Security

How Ledger Sense handles client data, what it protects that data against, and
what it does not. `SECURITY.md` at the repository root is the short policy for
reporting a vulnerability; this document is the longer engineering account
behind it.

We become a data processor the moment a client sends the first pay
application. Everything below was built before that document arrived, not
after — a retention clause in a signed agreement with no code honouring it is
a promise, not a control.

## Threat model

What we are defending client documents against, in the order we consider it
likely to matter:

- **A copied disk, backup, or container volume.** A pay application names a
  subcontractor's bank account. The `client` compose profile keeps Postgres in
  a local named volume and document originals on a local filesystem, so the
  realistic loss is a copy of that storage rather than a network breach.
  Answered by envelope encryption of every stored document (below): the volume
  alone yields ciphertext.
- **One tenant reading another tenant's documents.** Answered structurally:
  every table carries `tenant_id NOT NULL`, `api.deps.owned()` is the single
  ownership boundary and compares against the tenant on the server-side
  session rather than anything the caller sends, and a stored blob is
  cryptographically bound to its tenant so a guessed or leaked blob id does
  not open it.
- **A client document reaching a third-party model provider.** Free-tier
  inference may train on its inputs. `CLIENT_CONFIDENTIAL` hard-fails at the
  model gateway; the model lane accepts `SYNTHETIC` or `REDACTED` only, and
  `REDACTED` additionally requires the client's written opt-in and a passing
  redaction verification. See rule 1 in `CLAUDE.md`.
  The label is a type (`ledger_sense/boundary.py`), attached at the door
  (`ingest/intake.py::accept_document`, derived from the document's origin —
  there is no parameter through which an upload can be called synthetic),
  judged at the seam (`gateway.py::admit`, before any provider state is
  consulted) and persisted with a CHECK constraint (migration 0003). The
  tests in `tests/boundary/` prove the refusal, its ordering, and that the
  codebase has no second path.
  A client who declines the model lane entirely still gets a working product.
  No `Transport` ships in v1; when one does, the local-fallback lane must be
  routed around it rather than through it (see the design spec §3 known
  limitation).

  **The inbox route.** `POST /api/inbox` is that door's HTTP front: `intake()`
  bounds the parse, `accept_document()` labels from the caller's declared
  origin (defaulting to `UPLOAD`, the most restrictive), and `store_document()`
  writes the one row. `CORPUS` is accepted only under the demo profile;
  `REDACTED_UPLOAD` is refused until a verifier ships. The demo seeder stores
  through the same three calls, so "the demo holds no client data" is proven
  against the real `documents` table.
- **A hostile PDF.** Uploads are untrusted input parsed by a library that has
  had memory-safety history. Parsing runs under explicit resource caps
  (`ingest/limits.py`), in a container that is read-only, non-root, memory-
  and CPU-capped, with `no-new-privileges`. The app itself caps the request
  body before Starlette's multipart parser spools a byte of it to disk (a
  plain ASGI middleware on `POST /api/inbox`, `api/app.py`); a fronting
  reverse proxy in front of the container must enforce its own body-size
  cap too, since the app's cap only starts working once bytes reach it.
- **Credential and session theft.** Passwords are stored as Argon2id digests;
  session tokens are 256 random bits stored only as a SHA-256 digest, so a
  database dump does not hand over live sessions.

Explicitly outside the model: a compromised operator workstation with the
key-encryption key in memory, a malicious insider with production database and
KEK access, and nation-state-grade adversaries. Encryption at rest does not
defend against an attacker who already holds the key.

## Data we hold

- **Client documents.** Progress billing applications, continuation sheets,
  subcontractor invoices, change orders, lien waivers, and their scanned
  variants. These routinely contain vendor bank details, contract sums,
  retainage positions and named individuals.
- **Data derived from them.** Extracted field values with their provenance
  (`Claim` rows: method, confidence, source coordinates), validation
  exceptions, vendor records and payment-detail history.
- **Account data.** Tenant name, user email address, Argon2id password digest,
  session token digests, and the tenant's agreed retention term.
- **Operational records.** The retention audit log described below, and the
  writeback audit log owned by the accounting-integration phase.

We do not hold payment card data, government identifiers, or health data, and
nothing in the product asks for them.

## Encryption

**At rest, application layer.** Every document original is stored through
`ledger_sense/store/blobs.py` using envelope encryption:

- Each blob gets a **freshly generated Fernet data key**. Fernet is
  AES-128-CBC for confidentiality with HMAC-SHA256 for integrity, over a
  random IV, from the `cryptography` package.
- That data key encrypts the payload. The deployment's **key-encryption key
  (KEK)** encrypts the data key. The wrapped key is stored in the same file as
  the ciphertext it opens, so a deletion cannot half-succeed and leave a
  readable document behind.
- Per-blob data keys mean two identical documents produce unrelated
  ciphertext — the store leaks no "these two clients uploaded the same file"
  signal — and a KEK rotation rewrites one short wrapped key per blob rather
  than re-encrypting every document.
- The envelope plaintext is `sha256(tenant_id)` followed by the payload, so
  the owning tenant is inside authenticated ciphertext rather than implied by
  a directory name. Moving a file between directories does not change who can
  read it.
- Blob files are created with mode `0600` and `O_EXCL`, and flushed with
  `fsync` before the database row that points at them is committed.

**The KEK** comes from the `LEDGER_SENSE_KEK` environment variable, supplied
the same way the `POSTGRES_*` variables are — through the deployment's `.env`,
which is git-ignored. It is a Fernet key, generated once per deployment with
`Fernet.generate_key()`. It is never committed: `.env.example` carries a
placeholder only, and `gitleaks` runs in pre-commit and in CI against the
working tree. `kek_from_environment()` raises when the variable is unset
rather than substituting a fallback, and `BlobStore` takes the key as a
required constructor argument with no default — so there is no code path that
stores a document unencrypted, by configuration error or otherwise. Losing the
KEK means losing every stored document; it is backed up separately from the
storage it protects, because a backup containing both is a backup of
plaintext.

**At rest, below the application.** Full-disk encryption on any machine that
touches client documents, including the development machine, is an operator
requirement rather than something this codebase can enforce. Database columns
are not separately encrypted: the extracted field values live in Postgres
inside the encrypted volume, and adding a second key-management surface for
them is a decision the first design partner's agreement should drive.

**In transit.** The application speaks HTTP on port 8000 inside the compose
network; TLS termination is the deployment's reverse proxy. Session cookies
are issued `Secure` and `HttpOnly`, so a deployment that serves the product
over plain HTTP will find sessions do not work — deliberately.

## Retention and deletion

**The period is a term in the client's signed data agreement, never a number
we chose.** It is transcribed onto the tenant row as `tenants.retention_days`
at onboarding and read back by `retention_days(tenant_id)`. There is no global
constant to override it. A tenant row created before that transcription
carries a 365-day backstop, which exists so that a missed onboarding step
cannot turn into indefinite retention; erring short is the safer direction,
because the client holds the originals and we hold copies.

**The purge.** `purge_expired(now)` in `ledger_sense/store/retention.py` finds
every document whose `received_at` plus its tenant's term has passed, and for
each one deletes, in this order:

1. the encrypted original from the blob store,
2. every `Claim` derived from it — the extracted bank details live there, and
   a deletion that leaves them behind is not a deletion,
3. the document row itself.

The blob is deleted first on purpose. If a run dies halfway, the ciphertext is
already gone and the surviving row is purged on the next pass; the opposite
order would leave an unreferenced encrypted document on disk that nothing
knows to look for. For the same reason the job refuses to start rather than
delete rows it cannot match to a reachable blob store. The run is idempotent —
a document already gone is not re-purged — so a failed run is retried, never
reconciled by hand.

**The audit trail survives the deletion.** Each purged document leaves one
`retention_audit_log` row: the tenant, the document id, the action, the actor
(the schedule, or the named operator behind a manual erasure request), the
reason including the term applied and the date it expired, how many claims
went with it, whether the ciphertext was deleted, and when. That record is
what lets us answer a client asking what happened to a specific document
months later, and it is deliberately not itself deletable by the purge. It
names identifiers and dates, never document content.

**A client erasure request** is the same code path with a different actor and
without waiting for the term to expire: delete the documents in scope through
the same purge routine so the same audit entries are written. Deletion of the
tenant's account data (users, sessions, tenant row) follows once no document
rows remain.

**What deletion does not yet cover, stated plainly.** Database backups and
filesystem snapshots taken before a purge still contain the deleted rows until
those backups age out; a deployment's backup retention must therefore be set
no longer than the shortest client retention term it holds, and that is an
operator decision the compose file does not make. The purge itself is a
library function today — the schedule that calls it nightly is not yet wired
into `docker-compose.yml`, so a deployment must invoke it until that lands.
Nothing in this section is claimed to run automatically that does not.

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

## Coordinated disclosure

Report a suspected vulnerability by email to the maintainer at
77killuazoldic@gmail.com, the same address given in `SECURITY.md`. Include a
description, steps to reproduce, and the impact you believe it has. Do not
open a public issue for a suspected vulnerability, and do not test against a
deployment holding another party's documents.

We aim to acknowledge within 5 business days, agree a disclosure timeline with
you, and credit you when the fix ships unless you would rather we did not.
Reports touching the `CLIENT_CONFIDENTIAL` model boundary, the tenant
ownership check, blob encryption, or writeback are treated as highest
priority.
