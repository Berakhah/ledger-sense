# Ledger Sense: system blind spots and completion audit

Date: 15 October 2026 (Africa/Nairobi)  
Reviewed revision: `50e64ff` (`Merge branch 'feat/erase-command'`), plus the working tree, which was clean when review began.  
Deliverable: findings and proposed fixes only. No application code was changed.

## Assessment

**The repository is a substantial engineering prototype, but the normal client workflow and several central accounting controls are incomplete. It should not yet be treated as ready for client accounting writeback or as a complete implementation of the construction-validation brief.**

The most urgent defect is a reversal request that can delete another tenant's exported transaction. Other locally reproduced problems include cross-tenant idempotency collisions, duplicate and incomplete exports, incorrect payable amounts, invalid human edits reaching writeback, valid PDFs timing out, and baseline measurements being lost or producing invalid reports.

The central architectural blind spot is **local correctness without connected product correctness**: a rule, adapter, screen, or calibration function often has tests while the real upload-to-review-to-write path never invokes it. Several browser tests manufacture the state they need through demo seed endpoints, including a reconciliation store separate from actual uploaded documents.

This assessment does not imply that every document is processed incorrectly. It means that the current safeguards and tests do not establish the end-to-end guarantees stated in the README and original brief.

## Scope and evidence

Reviewed the original foundation/project briefs, README, CLAUDE.md, design slices, Python services, schema/migrations, Next.js screens, tests, Docker configuration, and CI. Independent reviews of the pipeline, writeback, and frontend used `gpt-6-astra` with high reasoning effort, followed by consolidation and checks in the primary session.

Evidence labels used below:

- **Reproduced:** observed in an isolated local probe against actual implementation code. API probes used a freshly migrated temporary SQLite database and local TestClient; no accounting service was contacted.
- **Source-confirmed:** demonstrated by the code's control flow, schema, or missing integration. No claim of live production reproduction.
- **Deferred:** explicitly scoped out by a later design slice; still an incomplete original requirement, rather than necessarily an accidental regression.

Severity is based on impact: **Critical** for a demonstrated cross-tenant destructive operation; **High** for accounting-integrity failures, missing core controls, or blocked essential workflows; **Medium** for narrower correctness, reliability, or completeness gaps. A dormant library defect is identified as such.

No real client documents, stored secrets, production systems, live QBO writes, or external vulnerability databases were used. This is a broad product/code audit, not a claim of exhaustive security certification or legal/accounting advice. Line references refer to the reviewed revision.

## Priority findings

| ID | Severity | Finding | Evidence |
|---|---|---|---|
| LS-01 | Critical | Reversal can delete another document's/tenant's export | Reproduced |
| LS-02 | High | Writeback identity and storage are not tenant-scoped | Reproduced |
| LS-03 | High | Export retries/concurrency can duplicate or truncate bills | Reproduced |
| LS-04 | High | Writeback amount ignores the payable reconciliation | Reproduced |
| LS-05 | High | Review completion is not a validated writeback authorization | Reproduced/source |
| LS-06 | High | Incorrect printed payment totals can pass validation | Reproduced |
| LS-07 | High | Incomplete table rows silently disappear | Reproduced |
| LS-08 | High | “Draft-only” promise differs from QBO implementation | Source-confirmed |
| LS-09 | High | Confidence routing is disconnected from actual processing | Source-confirmed |
| LS-10 | High | Bank-change and major construction controls are not active | Source-confirmed/deferred |
| LS-11 | High | Actual extraction runs outside the parser's resource boundary | Source-confirmed |
| LS-12 | High | Parser queue handling rejects valid text-rich PDFs | Reproduced |
| LS-13 | High | Normal browser workflow cannot start or finish | Source-confirmed |
| LS-14 | High | Reconciliation reads demo-only state instead of uploaded documents | Source-confirmed |
| LS-15 | High | Baseline studies are not durable or resumable | Source-confirmed/deferred |
| LS-34 | High | QBO reversal lacks durable recovery and local period/state guards | Source-confirmed |

### LS-01 — Reversal checks the remote ID but trusts an unrelated deletion key

**Critical · Reproduced.** Evidence: `ledger_sense/api/writeback.py:206`, `ledger_sense/writeback/audit.py:362`, `ledger_sense/writeback/export.py:142` and `:163`.

The API verifies that `remote_id` belongs to the path document. It then forwards an independently supplied `idempotency_key`. Export reversal deletes rows by that key, rather than by the ownership-checked remote ID. The shared export file makes this cross-tenant.

Local reproduction: create Alice's export A and Bob's export B. Submit a reversal under Alice's own document using A's remote ID and B's key. Response: **200**; A still has one CSV row, B has zero. Audit-derived status says A is `not_posted` and B is `posted`, the opposite of file contents. This requires knowing the target key; it does not permit arbitrary filesystem deletion.

**Suggested fix:** resolve a live transaction server-side by `(tenant, document, adapter, remote ID)`, obtain its key from that record, and never use a caller-selected deletion key. Scope audit lookups by adapter/realm as well as tenant. Refuse stale, mismatched, and already-reversed identities consistently.

**Acceptance check:** cross-tenant and same-tenant mismatched keys/adapters must leave both transactions unchanged; audit, status, and file contents must agree after valid reversal.

### LS-02 — Identical documents in different tenants collide in writeback

**High · Reproduced.** Evidence: `writeback/interface.py:46`, `writeback/assemble.py:73`, `writeback/audit.py:44`, `:172`, `:255`, and its environment factory; `writeback/export.py:203`.

The key derives from document hash, contract ID, and application number without tenant identity. Environment factories share one export directory, one ledger, a default audit tenant, and one configured QBO realm. Application authentication is multi-tenant; these stores are not.

Reproduction: two tenants submit documents with the same identity inputs. Alice receives `was_replay=False`; Bob receives `was_replay=True` with the same remote ID. Alice's later reversal returns **404** because the latest global audited write now associates that ID with Bob's document.

**Suggested fix:** propagate tenant and accounting-connection identity through every factory, key, ledger, export, audit, status, and reversal lookup. Provide a migration strategy for existing records. If deployment is intentionally single-tenant, enforce that invariant during provisioning rather than exposing unrestricted multi-tenant account creation.

**Acceptance check:** identical documents in two tenants create isolated records and can each be replayed/reversed independently; equal remote IDs in different adapters/realms cannot alias.

### LS-03 — Export is neither atomic nor safely idempotent under concurrency/crashes

**High · Reproduced.** Evidence: `writeback/export.py:89-139` and `:142-197`.

The adapter checks existing keys, then appends rows without locking or transactional staging. A key on any row is treated as proof that an entire bill was exported. Reversal rewrites the same shared file directly.

Reproductions: two concurrent same-key, two-line requests both return `was_replay=False` and produce **four rows instead of two**. Simulating a partial two-line export, then retrying, returns `was_replay=True` with **one row still missing**. These defects exist with one API process because synchronous handlers can run concurrently in its thread pool.

**Suggested fix:** store export intents and complete transaction contents in a tenant-scoped transactional database; render files from committed records using a lock and atomic replacement. Record content hashes/line counts and completion state. Reversal must operate on transaction identity and preserve crash recovery.

**Acceptance check:** concurrent same/different keys, crashes after every row, simultaneous create/reverse, and interruption during file replacement must produce complete, nonduplicated transactions with matching audit state.

### LS-04 — The amount written is current work, not a reconciled payable amount

**High · Reproduced.** Evidence: `writeback/assemble.py:97` and `:144-164`.

Assembly requires several summary Claims to exist but never uses them to reconcile the output. Each exported/QBO line amount is simply `this_period`. Stored-material movements, retainage, and prior-payment reconciliation are not incorporated into a documented posting model.

Reproduction: completed work 1,000, retainage 50, previous payments 0, and this-period work 1,000 yield a write total of **1,000**, although the corresponding current payment calculation is **950**.

**Suggested fix:** agree the accounting representation with a construction accountant, persist all reported/computed totals, and show an explicit posting preview. Represent retainage/material changes in appropriate accounts or adjustments and enforce reconciliation to the reviewed payable under that policy. Do not blindly subtract historical totals from each current line.

**Acceptance check:** first/subsequent applications, stored-to-installed transfers, retainage releases, previous payments, approved changes, and credit adjustments must produce the agreed amount and balanced allocation.

### LS-05 — Closing findings does not establish that a document is safe to write

**High · Reproduced/source-confirmed.** Evidence: `api/writeback.py:130-143`, `api/review.py:298-339`, `writeback/assemble.py:53`.

The gate asks only whether findings exist and none remain `open`. A clean, successfully extracted document with zero findings is refused as “never been run” (**422**, reproduced). Conversely, accepting an incorrect arithmetic value or rejecting a finding closes it without establishing a consistent, validated document. Edits are arbitrary strings stored under the previous value type, with no typed validation or dependent-rule rerun.

Reproduction: edit a money field to `not money` → **200 / edited**; subsequent writeback reaches `decimal.InvalidOperation`. A rejected extraction/duplicate finding can also count as “cleared” even though rejection is not inherently approval to pay. Generic accept is available to special rule types and copies their `actual` payload into a Claim.

**Suggested fix:** separate extraction status, field verification, finding disposition, document rejection, and explicit posting authorization. Validate edits as finite money/date/integer/string values before closing findings; rerun affected rules against a versioned effective document. Require the appropriate vendor/bank confirmation action for those rule types. Gate on a reviewed, reconciled version rather than a count of closed rows.

**Acceptance check:** clean documents follow an explicit review path; invalid edits return 422 without state changes; unresolved arithmetic and rejected/unextractable documents cannot accidentally become payable. Reopening/changing a reviewed version invalidates prior posting authorization.

### LS-06 — Printed current payment and other summary amounts are discarded

**High · Reproduced.** Evidence: `extract/assemble.py:43`, `extract/schema.py:58`, `rules/arithmetic/payment.py:26` and `:36`.

Anchors recognize more summary fields than assembly retains. Reported current payment due and earned-less-retainage are discarded. A5/A6 check negative computed amounts, not disagreement with the printed totals.

Probe: replacing only the synthetic PDF's Current Payment Due word with `9999999.00` yields **zero findings and zero unextractable fields**.

**Suggested fix:** retain independently reported summary values and their boxes, compute expected values separately, and compare them under the documented contract convention. Missing reported values must be explicitly unverifiable.

**Acceptance check:** independently corrupt each summary total while keeping its component inputs unchanged; the matching finding must point to the printed cell.

### LS-07 — One unreadable cell can silently remove a whole continuation row

**High · Reproduced.** Evidence: `extract/tables.py:222-223`, `extract/assemble.py:198-206`.

Rows missing a required column are discarded as presumed non-data. Assembly reports missing lines only when no rows survive. Removing one stored-material cell from a clean five-line PDF produces four lines, `_unextractable=[]`, and an incidental A1 mismatch rather than an extraction warning. Other values from the row are lost to review.

**Suggested fix:** distinguish probable item rows from headings/totals, preserve partial rows, and emit explicit missing-cell/row diagnostics. Suppress aggregate “complete” arithmetic when line coverage is uncertain.

**Acceptance check:** missing/illegible single cells, blank descriptions, and missing page sections must yield explicit X0 evidence; a subset of rows must never masquerade as the complete schedule.

### LS-08 — QBO behavior does not fulfill the stated draft-only contract

**High · Source-confirmed; no live QBO verification.** Evidence: README “Writeback is draft-only”; CLAUDE.md Never-rule 3; `writeback/qbo.py:3-6`, `:129`, and `:185`.

The adapter explicitly describes creating an unpaid Bill stamped “AWAITING REVIEW,” with approval equated to later payment. It does not implement a separate draft approval state. A note and absence of BillPayment do not prove the README's stronger claim that only drafts are written.

**Suggested fix:** reconcile the product contract with actual sandbox behavior before enabling client writeback. Prefer local draft/staging plus reviewed export unless an integration can establish the required approval semantics. If unpaid Bills are the intended scope, document that concrete effect consistently and obtain the product/accounting decision; do not silently treat “unpaid” as “draft.”

**Acceptance check:** demonstrate the exact object/state created and its effect in sandbox reports, approval/payment workflow, and reversal; align tests, UI confirmation, docs, and adapter naming.

### LS-09 — The real pipeline does not invoke confidence routing

**High · Source-confirmed.** Evidence: `run/document.py:344-376`, `api/review.py:281`, `route/router.py`, `route/calibration.py:379`; `docs/methodology.md:120`.

Production processing has no construction/call of `Router.decide`, no operational curve publication caller, and no drift-withdrawal caller. Review lists rule exceptions; it does not create review requirements for every uncalibrated extracted field. Thus clearing the few exceptions does not prove the other fields were verified, despite the stated “until calibrated, everything routes to review” policy.

**Suggested fix:** persist field routing/review requirements and the curve/extractor version used. Implement fit, independent validation, publish, audit holdback, and withdraw workflows, then enforce those decisions at writeback. Keep all uncalibrated fields review-required.

**Acceptance check:** a clean uncalibrated upload cannot reach posting without field review; eligible calibrated fields and held-back audit samples take demonstrably different paths; drift withdrawal affects subsequent decisions.

### LS-10 — Central fraud/construction controls are not connected or implemented

**High · Source-confirmed; several explicit deferrals.** Evidence: `run/document.py:237-281`, `:344-376`, `api/vendors.py:3-6`, `rules/registry.py:33`; original brief §§4-6.

The runner invokes a subset of arithmetic plus vendor resolution and same-byte duplicate detection. It does not extract/persist payment details or invoke I3 bank-change checks. Semantic duplicate history is not used. Contract-aware retainage rate/stepping, stored-material transfer/backup, approved change orders, tax, and waiver checks are incomplete. A placeholder contract is used and A7 is excluded. Vendor confirmation alone is not bank-change confirmation.

**Suggested fix:** make these unsupported controls explicit in product scope immediately. Implement evidence-backed contract/project history, payment-detail extraction and independent confirmation, then activate each supported rule through the actual runner. Obtain domain authority for convention rules rather than inventing assumptions to satisfy a checklist.

**Acceptance check:** real ingestion fixtures must trigger each advertised control by its intended rule, with a clean counterpart; bank changes remain a hard stop until the specific confirmation succeeds.

### LS-11 — The expensive extraction is outside the capped child process

**High · Source-confirmed.** Evidence: `ingest/limits.py:164`, `extract/document.py:52-67`, `run/document.py:123`, `:355-363`.

Intake performs a capped preliminary PDFium parse. The runner later reparses the same bytes with pdfplumber in the API process, while holding a database transaction. Passing the first parser does not bound the second parser's CPU, memory, or wall time. Container-wide limits can kill the entire API rather than only the document job.

**Suggested fix:** run actual classification/extraction/OCR in a killable, capped worker and commit resulting Claims/findings in a short transaction. Bound concurrency and preserve an explicit timeout/failure state.

**Acceptance check:** deliberately stalled/expensive extraction times out without killing the API, leaking a lock, or preventing other documents from being reviewed.

### LS-12 — Join-before-read deadlocks the parser on larger results

**High · Reproduced.** Evidence: `ingest/limits.py:195-205`.

The parent joins the child before reading its multiprocessing Queue. A text-rich result can fill the queue pipe; the child cannot exit until the parent drains it, and the parent reports a timeout instead.

Independent probe: a valid **10-page, 9,901-byte PDF**, returning **29,130 text characters**, times out after **10.20 seconds**. The same `_parse_child` with result read before join succeeds in **1.31 seconds**. A one-page control succeeds. A 100-page variant also timed out; these are below the 200-page/25 MB intake caps.

**Suggested fix:** consume the result while the child is alive within a single monotonic deadline, then join/clean up the process and queue. Prefer returning only the information intake actually needs; concatenated page text is unnecessarily large here.

**Acceptance check:** ordinary PDFs with output above pipe capacity must parse promptly; timeout, child crash, queue cleanup, and repeated requests must remain bounded.

### LS-13 — A normal browser user cannot perform the product journey

**High · Source-confirmed.** Evidence: `web/app/page.tsx:6`, `web/app/layout.tsx:18`, `web/app/inbox/page.tsx:69` and `:136`; `api/deps.py:21`; `api/writeback.py:156` and `:206`.

There is no login page/caller, upload control, working inbox-to-review link, or writeback/export/reversal UI. Home links to a hardcoded `/review/doc-50`. Core screens require a session that tests manufacture via demo seeding. User provisioning/logout/session-expiry recovery also lack a complete supported product flow.

**Suggested fix:** connect provisioning/login, upload, processing status, document navigation, review, reconciliation, explicit posting/export preview, download, and reversal. Add shared navigation and session-expiry handling. Keep accounting actions explicit and human-triggered.

**Acceptance check:** a fresh browser completes that journey using ordinary endpoints, without fixture-issued sessions, direct SQL, or manufactured document IDs.

### LS-14 — Reconciliation cannot read actual uploaded documents

**High · Source-confirmed/deferred.** Evidence: `api/reconciliation.py:65-83`, `:116`, `:159`; `api/inbox.py:199`.

Reconciliation reads process-local `_DOCS` and `_DOC_TENANTS`, populated by a dedicated corpus seeder. Real uploads persist in Postgres and have no path into those dictionaries. A successfully extracted upload therefore cannot use the demonstrated reconciliation screen.

**Suggested fix:** reconstruct reconciliation from the same tenant-scoped persisted document, effective Claims, contract, and findings used by review/writeback. Include human corrections and explicit incomplete-data diagnostics.

**Acceptance check:** upload → extraction → reconciliation works without seeding; editing a Claim changes the effective reconciliation; restarting the API preserves access.

### LS-15 — The billable baseline instrument loses state across sessions/restarts

**High · Source-confirmed/deferred.** Evidence: `api/baseline.py:37-38`, `web/app/baseline/page.tsx:39`.

Studies/samples/rates live in process dictionaries, and the screen keeps study ID/count only in component state. Browser reload loses the reference; API restart loses the study. No list/retrieve/resume workflow restores it. This is explicitly postponed in code, but conflicts with a baseline collected over a client's working week.

**Suggested fix:** persist studies, samples, metadata, rates, and exception notes; provide tenant-scoped retrieval/resume and a study URL. Make sample submission idempotent and preserve report inputs/version.

**Acceptance check:** browser reload and API restart during a multiday study retain exactly the acknowledged observations and reproduce the same report.

## Additional defects and blind spots

### LS-16 — Overlapping first samples create different baseline studies

**Medium · Reproduced.** Evidence: `web/app/baseline/page.tsx:80`, `:139`.

`ensureStudy()` shares the finished ID but not an in-flight creation promise. Delaying creation while filing two samples creates two studies, sends `doc-1` and `doc-2` to different IDs, and displays two samples as though they belong to one study.

**Fix:** share one creation promise, use stable sample IDs, track pending/acknowledged counts, and derive report readiness from server state. **Check:** delayed first creation still produces one study with both samples.

### LS-17 — Baseline validation accepts impossible inputs; empty reports crash

**Medium · Reproduced.** Evidence: `api/baseline.py:41-56`, `:68-103`, `baseline/study.py:64-100`, `baseline/report.py:18-19`.

Negative touch times/exception counts/population, empty frames, reversed dates, duplicate samples, and invalid rate ranges lack adequate domain validation. A probe with eight negative 60-second samples, population -1, and reversed dates successfully generates a **-$1.67/document** report. `render_report()` computes the point before checking sample adequacy; zero samples raise `decimal.InvalidOperation`, which the API's `ValueError` handler does not catch.

**Fix:** validate finite nonnegative rates, valid sample counts/times, study dates/frame/population, ordered uncertainty ranges, and sample identity. Check adequacy before any division and return a clear client error. Either support the requested confidence level or reject non-95% values; `confidence_interval(level=...)` currently ignores its argument.

**Check:** empty/invalid studies return structured 4xx responses and never produce an authoritative-looking report; valid study calculations remain unchanged.

### LS-18 — Failed sample submission loses the measured observation

**Medium · Source-confirmed.** Evidence: `web/app/baseline/page.tsx:122-165`.

The screen clears elapsed time and pending exceptions before posting. Failure rolls back the count but does not restore a retryable payload. Session/network loss discards evidence that cannot be accurately re-created later.

**Fix:** retain an immutable pending sample until acknowledgment; expose retry/discard and use server idempotency for ambiguous responses. **Check:** fail, reconnect, and retry preserves the original time/exceptions exactly once.

### LS-19 — Typing spaces in baseline metadata controls the timer

**Medium · Reproduced.** Evidence: `web/app/baseline/page.tsx:169-200`; `web/e2e/test_baseline_screen.spec.ts:23`.

The global shortcut deliberately excludes only buttons and the note input. Space in Sampling frame is prevented and starts/stops timing. A jsdom probe against the actual component observed `prevented=true`, `timerRunning=true`. Playwright `.fill()` bypasses actual typing and misses it.

**Fix:** defer shortcuts in every editable control, offer explicit timer/file buttons, and scope shortcuts appropriately. **Check:** real keyboard entry preserves multiword metadata and does not affect timing.

### LS-20 — Report metadata can disagree with the visible baseline form

**Medium · Source-confirmed.** Evidence: `web/app/baseline/page.tsx:92-94`, `:215`, `:248-259`.

Population/frame are submitted only at study creation but remain editable afterward. Producing a report updates only the rate. Collection dates are silently set to today/today. Subsequent corrections and multiday collection are therefore misrepresented.

**Fix:** persist metadata edits or lock finalized values clearly; expose actual collection dates. **Check:** the report exactly matches the saved form and dates, including edits after sample one.

### LS-21 — Corrected review values disappear from the displayed evidence

**Medium · Source-confirmed.** Evidence: `api/review.py:126`, `:326`; `web/app/review/[docId]/ReviewQueue.tsx:159`, `:252`.

Edit creates a new effective Claim, but exception serialization still returns the original finding's `actual`. Revisiting/reloading can show “Edited” next to the old value while writeback uses the newer Claim.

**Fix:** expose original and effective values with review actor/time/source, and render the server-acknowledged correction. **Check:** edit 100 → 125, navigate/reload, and see 125 consistently in review, reconciliation, and posting preview.

### LS-22 — Inbox hides older work and does not follow processing updates

**Medium · Source-confirmed.** Evidence: `web/app/inbox/page.tsx:69`; `api/inbox.py:208`, `:239-255`.

The UI makes one request, while the API defaults to 50 rows. There is no cursor UI or indication of omitted documents, and no processing-status refresh. Older failures/open exceptions can disappear from practical access despite the “Every document” copy.

**Fix:** wire cursor pagination, a visible continuation state, and bounded refresh/retry for pending work. **Check:** 51+ documents with equal timestamps remain reachable, including an older unresolved item; received status updates after extraction.

### LS-23 — Calibration math is order-dependent for tied scores and crashes at one label

**Medium · Reproduced; currently dormant in the production route.** Evidence: `route/calibration.py:151-174`, `:241`, `:256-269`; `route/router.py:95`.

Equal-score evidence is not pooled before isotonic fitting. Scores `[.75,.75]` with outcomes `[False,True]` produce different confidence from the reversed ordering (0 versus .5 at .75). A one-label fit creates no fitting knots; evaluation raises `IndexError`.

**Fix:** aggregate equal scores with weights before PAVA, define interpolation over distinct scores, and handle insufficient data as explicitly uncalibrated. **Check:** permutation invariance, independent reference cases, and zero/one/two-label behavior through Router and persistence.

### LS-24 — Calibration evidence lacks population identity and transaction integrity

**Medium · Source-confirmed; resolve before enabling automation.** Evidence: `route/calibration.py:323-340`, `route/deps.py:36`, `api/review.py:248-278`.

The deployment-wide SQLite store lacks tenant/extractor-version scope and evidence uniqueness. Exact item-specific names such as `line[010].stored` become field types. Labels refer to the newly created human Claim rather than the original prediction. The label commit is separate from the enclosing review transaction, so a later rollback can leave evidence for a review that never committed. Subsequent same-field actions may find a HUMAN Claim with no extraction score and omit a label.

**Fix:** use normalized field types and versioned population scope; link immutable original prediction and review identities; enforce distinct evidence; publish via a transaction/outbox. Define whether cross-tenant pooling is permitted and statistically valid instead of pooling accidentally.

**Check:** rollback/retry creates no orphan/duplicate label; different tenants and extractor versions remain separated; multiple item numbers contribute to the intended field population.

### LS-25 — Extraction is too coupled to the synthetic layout

**Medium · Reproduced/source-confirmed.** Evidence: `extract/tables.py:109`, `:182`, `extract/assemble.py:208`, `extract/anchors.py:208`, `extract/schema.py:40-65`.

Header matching/global Y cutoffs assume table and summary arrangement across pages. Some ordinary summary vocabulary requires the generator's `Summary —` prefix. Rearranging otherwise identical words into a summary-first page and continuation page causes lost grid/ambiguity rather than successful extraction.

**Fix:** page-aware coherent regions, repeated-header handling, and contextual summary labels. **Check:** independently designed original-layout fixtures, summary before/after continuation, varying page positions, and ordinary unprefixed labels. Treat low extraction coverage as explicit review work, never guessed values.

### LS-26 — Percentage and balance-to-finish rules cannot receive uploaded values

**Medium · Source-confirmed/probed.** Evidence: `extract/schema.py:15-24`, `extract/assemble.py:41-42`, `rules/arithmetic/lines.py:36`, `:69`.

The extractor does not capture reported percentage/balance columns. Domain fields remain `None`, so A3/A4 skip them. Hand-constructed rule tests do not establish uploaded-document coverage.

**Fix:** add reported fields, Claims, vocabulary, geometry, and explicit missing-data handling. **Check:** upload PDFs with independently incorrect percentage/balance and require corresponding findings, including zero scheduled values.

### LS-27 — Missing line fingerprints falsely establish duplicates

**Medium · Reproduced; semantic duplicate integration is currently dormant.** Evidence: `rules/integrity/duplicates.py:61`; `extract/assemble.py`, `line_hash()`.

Equal empty hashes match. Same vendor, different amounts, dates a year apart, no invoice numbers, and `line_hash=''` on both records yield “identical line-item fingerprint.”

**Fix:** require populated comparable fingerprints; normalize missing invoice identifiers similarly. **Check:** absence alone never establishes duplication, while genuine populated fingerprints still match.

### LS-28 — Money arithmetic depends on ambient Decimal settings

**Medium · Reproduced.** Evidence: `domain/money.py:33-45`; `rules/arithmetic/lines.py:15`, `payment.py:18-22`, `foot.py:11`.

`money()` applies an explicit context after callers have already performed arithmetic under the ambient Decimal context. Under precision 3 / ROUND_DOWN, a previously clean seed produces five false A2 findings; one expected value becomes `7.51E+4` instead of `75135.00`. This is not evidence that normal values under default precision already fail.

**Fix:** execute complete monetary operations under `localcontext(MONEY_CONTEXT)` or explicit context helpers. **Check:** rules and totals remain invariant under deliberately hostile ambient precision/rounding.

### LS-29 — Retention does not cover document-derived side stores

**Medium · Source-confirmed.** Evidence: `store/retention.py:274-373`; `writeback/export.py:26`, `:98-139`; `writeback/audit.py:219-252`; `route/calibration.py:323`.

Purge/erase remove originals, Claims, findings, and linked vendor history, but do not reconcile CSV/IIF exports, writeback audit/ledger state, or calibration labels. Exports contain descriptions, vendor references, amounts, and memos; audit records include operator email and invoice/vendor references. Deleting the document also removes the normal API entry point needed to inspect/reverse its recorded write. Account-data/backups are already acknowledged as manual limitations; these side-store implications need similarly explicit handling.

**Fix:** inventory data and retention purpose per store. Implement tenant/document lineage and cleanup or documented justified retention, including a way to manage accounting records after source deletion. Preserve necessary audit evidence without claiming that all derived content was erased; do not automatically delete remote accounting entries as a side effect of document retention.

**Check:** erase a document after export/review and inspect every store; retained information and recovery operations must match the agreed policy.

### LS-30 — A rollback or crash can strand encrypted blobs indefinitely

**Medium · Source-confirmed and explicitly acknowledged in code.** Evidence: `store/documents.py:63-79`; `store/blobs.py:88-105`; `store/retention.py:405-422`.

Blob storage precedes the database commit. Immediate insert errors have cleanup, but a later transaction rollback/commit failure or process crash leaves a blob with no document row. Purge is row-driven and cannot discover it. The code assigns this responsibility to callers, but upload has no durable reconciliation mechanism.

**Fix:** staged blob intents plus commit finalization, or a conservative orphan reconciler with age/grace periods and explicit database reference checks. **Check:** force failures after blob write, after insert, and during commit; eventually reclaim only unreferenced blobs without deleting in-flight uploads.

### LS-31 — Documented quickstart cannot work from an unchanged example environment

**Medium · Reproduced/source-confirmed.** Evidence: README Quickstart; `.env.example:32`; `__main__.py:53`, `:217`; `store/blobs.py:56`.

The README says copy `.env.example` and run Compose. Its `LEDGER_SENSE_KEK=changeme` is not a Fernet key, and startup intentionally constructs/probes the blob store before serving. A direct constructor probe raises `ValueError: Fernet key must be 32 url-safe base64-encoded bytes.` The failure is appropriate; the startup instructions are incomplete. A fresh CI/browser environment needs key preparation too.

**Fix:** provide a local bootstrap command that creates generated secrets once and refuses to overwrite existing keys, or document the necessary generation steps and wire CI secrets explicitly. Validate placeholder HMAC values rather than accepting nonempty `changeme`. Document client TLS/provisioning setup and the fact that the API requires Secure cookies.

**Check:** a disposable fresh checkout follows the published instructions without undocumented manual setup and without weakening the client session/data boundary.

### LS-32 — Accounting integration has no established contract-test proof or complete connection flow

**Medium · Source-confirmed.** Evidence: `tests/conftest.py:16-35`; `tests/writeback/test_qbo_sandbox.py:12-26`, `:73`; `.github/workflows/ci.yml:173-209`; `writeback/qbo.py:301-324`, `:431-455`; `.env.example:46-48`.

Four live contract tests skip without credentials, and no recorded cassettes exist. Once cassettes exist, `record_mode='once'` replays them, so the job described as a nightly live drift check need not contact the live API. The runtime token store must already contain tokens, but there is no connected OAuth onboarding flow; example accounting variable names also differ from the runtime's `QBO_CLIENT_ID`/`QBO_CLIENT_SECRET` names.

**Fix:** finish sandbox onboarding/token seeding with documented matching configuration. Separate deterministic cassette replay from an explicitly live sandbox smoke; make missing credentials a visible skip/incomplete status, not evidence of integration success. Record sanitized cassettes only with authorized sandbox credentials.

**Check:** a fresh configured sandbox can connect, write, reconcile retry, refuse closed periods, and reverse; a deliberate fake upstream contract change fails the live check instead of replaying old success.

### LS-33 — Recovery and readiness rely too heavily on manual intervention

**Medium · Source-confirmed.** Evidence: `api/app.py:192`; `api/inbox.py:203`; `run/document.py:379-405`; `__main__.py:206-221`; `docker-compose.yml:141-151`, `:190`.

Health always returns `ok` after startup; it does not detect a later database/storage failure. Extraction is a response background task rather than a durable job. A crash after receipt can strand work; `run_pending` exists as an operator command but is not scheduled/recovered at boot. API/web services lack the restart policy used for Postgres, and retention errors are logged then delayed until the next daily cycle. No tested restore procedure or automated backup verification is established in the repo.

**Fix:** distinguish liveness/readiness; add durable extraction job/retry state, startup recovery, bounded retry/backoff and operator-visible failures. Alert on purge failures and backlog age. Document and test restoration across Postgres, encrypted blobs, calibration, writeback stores, and separately backed-up keys.

**Check:** kill the API after upload acknowledgment, restart it, and observe completion exactly once; fail storage/DB and confirm readiness changes; restore a backup and demonstrate document readability and writeback retry integrity.

### LS-34 — Remote reversal has an unreconciled failure window

**High · Source-confirmed; no live QBO deletion attempted.** Evidence: `writeback/qbo.py:253-261`, `:413-425`; `writeback/audit.py:362-370`.

Reversal deletes the remote Bill, then updates the local ledger, then appends the audit record. A lost response or crash after remote deletion leaves local state claiming the bill still exists. A retry fetches/deletes the now-absent object without a durable reversal operation that can reconcile this outcome. The reversal path also does not itself check the original transaction date against book close or establish whether the remote Bill has since been paid/changed; upstream refusal cannot substitute for the promised local explanatory guard.

**Suggested fix:** persist a reversal intent referencing the original immutable write; inspect current remote state and closed period; reconcile already-absent objects and resume ledger/audit completion idempotently. Do not assume every absent object proves this caller deleted it.

**Acceptance check:** inject failures before/after remote deletion, ledger mutation, and audit append; repeat completed reversals; test closed-period and changed/paid objects with an explicit supported outcome.

## Completion matrix against the original brief

Some narrow design slices explicitly defer these items. That is valid scope management, but it must be reflected in public product claims and release criteria.

| Capability | Present state | Required completion or explicit scope decision |
|---|---|---|
| Digital pay-application intake | API and deterministic pipeline exist | Connect browser journey; resolve coverage/validation defects above |
| Standalone continuation sheets, invoices, lien waivers | Runner refuses everything except pay applications (`run/document.py:358`) | Implement document-specific pipelines or narrow stated v1 scope |
| Scans/photos/OCR | OCR module exists; runner refuses documents without a text layer | Keep honest refusal, measure proportion, then integrate bounded OCR if supported |
| Contract retainage/stepping, stored-material history, approved changes, tax, waiver checks | Mostly absent/gated convention rules and placeholder contract | Persist authoritative contract/history and implement domain-reviewed rules |
| Bank-detail history/confirmation | Typed domain/store scaffolding; ingest path absent | Deliver end-to-end extraction, history/diff, hard stop, and confirmation |
| Full-history semantic duplicates | Library present; runner primarily detects identical bytes | Integrate canonical-vendor historical fingerprints with missing-value safeguards |
| Confidence calibration/routing | Library and review labels; no production routing/publish/withdraw lifecycle | Complete LS-09/23/24; publish actual reliability evidence |
| Extraction learns reviewed geometry | `ProfileCache.learn` has no production caller | Integrate with provenance/versioning or avoid improvement claims |
| Reconciliation | Separate in-memory demo store | Persist and use effective uploaded-document Claims |
| Baseline study | Instrument/report exists; volatile and submission-unsafe | Durable, resumable, validated study lifecycle |
| Metrics, straight-through rate and misses | `/api/metrics/` remains a stub (`api/_stub_routes.py:52`); no metrics screen | Publish denominators, unsupported/failed docs, misses, baseline-linked savings, and calculation provenance |
| Provenance rail | No complete Claim parent/source/method/actor/timestamp rail | Implement evidence navigation and accessible keyboard/reduced-motion behavior; review boxes alone are insufficient |
| CSV/IIF fallback | CSV environment factory; IIF class support, no product selection/download flow | Atomic exports, supported import semantics, UI retrieval, reconciliation and reversal limitations |
| Accounting mapping/approval | One deployment-level account default; caller-supplied vendor; unpaid sandbox Bills | Explicit mapping/preview/approval decision and connection scoping |
| Data handling | Encrypted originals, tenant checks, retention and erase are substantial | Side-store lineage, orphan recovery, restore drills, agreed operational/data terms |
| Corpus/methodology | Strong arithmetic fixtures; incomplete broader validation/calibration evidence | Independent layouts and domain-consistent scenarios, measured misses, published reliability/methodology |

The synthetic corpus deserves particular care: a “clean” application uses zero retainage against a 10% contract rate (`corpus/generate.py:141-145`); this is clean only for the checks currently run. Some injected retainage/stored-material errors trip incidental arithmetic rather than the advertised domain rule. `generate_history()` returns the same PDF bytes for both records despite different in-memory application numbers (`corpus/errors.py:84-85`), proving byte duplication rather than semantic-history detection. Fix corpus semantics and require the intended rule to catch each advertised scenario.

## Verification performed and what the suite does not prove

Observed checks in this review:

| Check | Result |
|---|---|
| `npm test -- --reporter=dot` | 10 files, 56 tests passed |
| `npm run build` | Production compilation, type checking, and page generation passed |
| `.venv/Scripts/python.exe -m ruff check .` | Passed |
| `.venv/Scripts/python.exe -m mypy --strict ledger_sense` | Passed: 82 source files |
| `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` | 965 passed, 10 skipped; 350.43 seconds. Deprecation warnings concerned TestClient/httpx and per-request cookies |
| Local writeback probes | Reproduced LS-01/02/03/04/05 using migrated temporary DB and TestClient |
| Local parser/control probes | Reproduced LS-12 with the same child parser and generated original-layout PDF |
| Local baseline probes | Reproduced negative-cost output, empty-report exception, creation race, and shortcut interference |
| Pipeline probes | Reproduced payment omission, row loss, calibration edge cases, context-sensitive money, and empty-hash duplicates |
| Live QBO, production Postgres behavior, fresh Compose deployment, Playwright/axe | Not executed in this review; no live-integration or browser-accessibility success is claimed |

Important testing blind spots:

1. Demo browser seeding issues authentication and directly creates findings; reconciliation is independently seeded. These tests cannot prove normal login/upload/extraction/reconciliation/posting works.
2. Hand-built rule inputs exercise values that extraction never captures. Add uploaded-PDF acceptance tests for each marketed rule.
3. The baseline test uses `.fill()`, fast responses, and no restart/failure path; this misses keyboard interference, races, and lost measurements.
4. Accessibility coverage excludes inbox/vendor ledger. Generic review/reconciliation entries lack a populated-state readiness assertion (`web/e2e/test_accessibility.spec.ts:55-104`), allowing a loading state to satisfy checks. Test final populated/error/partial states and keyboard behavior.
5. CI's 75% coverage floors cover selected domain/rule/baseline/extract/ingest modules, not the complete API/run/review/route/writeback/store integration surface (`.github/workflows/ci.yml:63-74`). Coverage percentages alone cannot establish workflow safety.
6. Add fault-injection tests at filesystem/database/remote/audit boundaries, two-tenant identity tests, concurrent export tests, and restart/resume tests. Passing mocks and static guards should not be presented as live accounting proof.

## Suggested repair sequence

1. **Contain accounting risk:** repair LS-01/02/03, reconcile LS-04/08, and replace the writeback gate in LS-05. Require an explicit reviewed posting preview; keep client accounting writes disabled until the acceptance checks pass.
2. **Make validation complete and honest:** fix lost summary/row data, bound actual extraction, correct parser queue handling, and persist uncalibrated field-review requirements. Publish unsupported rules/document classes clearly.
3. **Connect the actual product:** ordinary login/upload/navigation → durable processing → review → persisted reconciliation → explicit export/writeback/reversal. Prove it without seeding shortcuts.
4. **Protect baseline evidence:** persist/resume studies; fix races, retryable samples, validation, keyboard behavior, and metadata synchronization.
5. **Complete evidence and operational readiness:** repair calibration math/identity, add promised domain controls with expert evidence, metrics/provenance, retention side-store/orphan handling, backup restoration, and sandbox contract verification.

Do not treat completion of the largest feature list as the first milestone. The first reviewable milestone should be one real, persisted, tenant-isolated upload whose extracted and corrected values reconcile to an explicitly approved export, survive a restart, and can be reversed without affecting another document.
