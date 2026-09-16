# Ledger Sense — Plan Audit and Revision Register

**Audited:** 2026-08-29 · **Against:** spec `2025-03-11-ledger-sense-design.md` and the six plan documents
**Method:** every claim traced to the brief that authorises it, every code block read as if it were about to run,
every constant traced to the evidence that set it. Where a claim was checkable by execution, it was executed —
those findings carry a `verified` marker and the observed output.

This register is **normative**. Where it contradicts a plan document, the plan document has been edited to match,
noted in the `Fixed in` line. Findings marked **OPEN** need a decision from the operator and are listed in §11.

---

## 0. Verdict

The plan's *architecture* survives audit. Deterministic-first extraction, the provenance-gated rulebook, the
three-state calibration refusal, the idempotency-ledger-writes-first ordering and the export-as-peer-adapter
decision are all correct and well-motivated. The document is unusually honest about its own risk.

The plan's *execution detail* does not survive intact. **Sixty-seven defects** — sixty-five in the first pass,
and two more (F-66, F-67) found while applying the fixes, one of them a fix that was written down but never
applied. Six would ship a wrong number to a
client, four are security holes in a product handling client financial documents, and eleven are gaps where a
definition-of-done item has no implementing task at all.

Three deserve to be read before anything else:

- **F-01** — the no-float guarantee has a hole at the API boundary that all three declared enforcement layers
  miss. Verified by execution: a JSON float reaches `Decimal` through binary float today.
- **F-14** — `extract/` imports its column definitions from `corpus/`, so extractor and generator share one
  source of truth. "Twenty seeds pass" proves the generator agrees with itself.
- **F-47** — `MIN_LABELS_FOR_FIELD = 200` cannot support the 99.5% accuracy claim it gates; the rule of three
  needs ≥600. The phase built to prevent guessing-while-displaying-a-number contains the same error one level up.

Nothing here invalidates the eight-week shape. Most fixes are small. The schedule risk is not the fixes — it is
§8, the work that was never scheduled.

---

## 1. Severity ladder

| | Meaning |
|---|---|
| **S1** | Ships a wrong number to a client, or exposes client data. Fix before the code is written. |
| **S2** | A stated guarantee is not enforced; a test proves something other than what it claims. |
| **S3** | A definition-of-done item has no implementing task. |
| **S4** | Correctness or efficiency defect with bounded blast radius. |

---

## 2. Money and arithmetic

### F-01 · S1 · The no-float guarantee has a fourth hole and none of the three layers see it

**Verified** (`uv run --with pydantic python`, pydantic 2.13.5):

```
LineItem(amount=1000.0)              -> Decimal('1000.0')   test_line_item_rejects_a_float_amount FAILS
JSON float 12345678901234567.89      -> Decimal('12345678901234568')
0.1 + 0.2                            -> Decimal('0.30000000000000004')
```

`Money = Annotated[Decimal, "money"]` on a Pydantic v2 model in lax mode **accepts a float and coerces it**.
Every declared layer is blind: `mypy` sees `Decimal`, the AST scan sees no float literal in `domain/`, the
model-walk sees an annotation that is not `float`. The money path is entered through FastAPI request bodies,
which are JSON, which carry floats.

Two consequences, both real: `tests/writeback/test_interface.py::test_line_item_rejects_a_float_amount` **fails
as written**, and `0.1 + 0.2` computed upstream arrives in the ledger as `0.30000000000000004`.

**Fix — Layer 4.** `Money = Annotated[Decimal, Strict()]`, `model_config = ConfigDict(strict=True)` on `_Base`,
and a test that a float body is a 422 rather than a coercion.
**Fixed in:** Phase 1 Tasks 2, 4; Phase 5 Task 34.

### F-02 · S4 · `money()` accepts `bool`

**Verified:** `money(True) -> Decimal('1')`. `bool` is not `float`, so the guard passes it. A truthy flag
threaded into an amount silently becomes one dollar. **Fix:** reject `bool` before the `float` check.
**Fixed in:** Phase 1 Task 2.

### F-03 · S1 · A3 and A4 do not implement the rules the spec calls A3 and A4

Spec §6.2 A3: *"Balance to finish = adjusted scheduled value − total completed and stored."* The plan implements
"flag when total exceeds scheduled value" — it never reads a reported balance-to-finish, because
`ContinuationLine` has no such field. A4 has the identical defect: the spec compares a *reported* percentage
against the computed one; the implementation only flags a zero scheduled value and a negative percentage, and
there is no `percent_complete` field either.

Both columns exist on every real continuation sheet. The two rules most likely to catch a transcription error are
unimplemented while DoD 3 reports met.

**Fix:** add `balance_to_finish: Money | None` and `percent_complete: Decimal | None` to `ContinuationLine`;
extract both; A3/A4 reconcile reported against computed, keeping the sanity checks as A3b/A4b.
**Fixed in:** Phase 1 Tasks 4, 7; Phase 2 Task 16.

### F-04 · S2 · A9 is classified arithmetic but is a convention rule

A9 (sum of scheduled values = adjusted contract sum) fires whenever an approved change order exists but the
schedule of values has not been revised to carry it — the normal mid-period state. Classed `arithmetic`, it
bypasses the `RuleSource` gate built to stop unsourced domain claims from shipping, and it will be the noisiest
false positive in the product — the exact failure the plan correctly identifies for C8 and then walks into.

**Fix:** reclassify as `convention` with a `RuleSource`; suppress when `ChangeOrder.affects_item_no` names a line
whose scheduled value has not moved.
**Fixed in:** Phase 1 Task 6 (`rule_a9` removed from `rules/arithmetic/foot.py`, and from the Phase 1 property
suite); Phase 3 Task 24 (implemented in `rules/convention/change_orders.py` with `_scheduled_value_moved`, the
`CHANGE_ORDERS` source, four tests, and re-added to `ALL_RULES`). **The first application of this fix was
incomplete — see F-67.**

### F-05 · S1 · C13 compares the waiver amount to the wrong field

```python
if waiver.amount != app.total_completed_and_stored
```

A lien waiver's amount matches **the payment it accompanies** — current payment due, A6's output. Comparing to
total completed and stored guarantees a finding on every application where any retainage is held or any previous
payment exists: every application after the first.

The test was written against the implementation, so it passes. This is precisely the §6.1 failure mode — a fluent
sentence, silently wrong, with a test that confirms the misunderstanding.
**Fix:** compare against `current_payment_due(app)`. **Fixed in:** Phase 3 Task 26.

### F-06 · S2 · C1 and C3 both fire on the same document, and both demand impossible exactness

`rule_c1` computes `base_rate × total_completed_and_stored`; `rule_c3` computes
`base×installed + stored_rate×stored`. When a contract sets `stored_materials_rate` both are registered, both
run, and both report one underlying condition. C1 has no guard on `stored_materials_rate is None`.

Separately, both demand **exact** equality on a computed rate product. Real applications compute retainage per
line and round per line; the sum differs from `rate × total` by up to `n × 0.005`. C1 and C2 will false-positive
on essentially every real document.

**Fix:** C1 returns `[]` when `stored_materials_rate is not None`; all three compute per line and sum, with
tolerance `n × Decimal("0.01")` documented in the `RuleSource`. **Fixed in:** Phase 3 Task 22.

### F-07 · S2 · C4 detects an exact triple coincidence, not the phenomenon

```python
if carried > 0 and line.this_period == carried and line.stored == carried
```

The described error — material billed as stored and simultaneously as installed — does not generally produce
three equal figures. The fixture was constructed to satisfy the condition; the condition was not derived from the
phenomenon. The rule the spec calls *"the classic silent pay-application error"* will almost never fire on a real
document while its test is green. C4's condition is also a subset of C5's tripping fixture, so both fire on one
input — duplicate findings again.

**Fix:** detect the invariant instead — stored did not decrease while work was installed
(`carried_stored > 0 and this_period > 0 and line.stored >= carried_stored`). Add a second fixture where the
figures are **not** equal and assert it still trips. **Fixed in:** Phase 3 Task 23.

### F-08 · S4 · C12 demands exact date equality

`waiver.through_date != app.period_to` flags a waiver dated the payment date rather than the period end — the
common case. **Fix:** flag when the through-date is *earlier* than the period end (the exposure case) or more
than one period later. **Fixed in:** Phase 3 Task 26.

---

## 3. Extraction

### F-09 · S1 · The corpus layout makes summary extraction return a line-item cell

**Verified.** `CONTINUATION_COLUMNS` and `SUMMARY_FIELDS` share two identical label strings:

```
identical label strings in both blocks: ['Retainage', 'Total Completed and Stored']
```

`extract_summary._find_label` scans words in reading order and returns the **first** match. The grid header row
is above the summary block, so it wins. `_value_near` then takes the first money-shaped token to its right on the
same band, or within 20pt below — the first data row's cell.

`summary["total_completed_and_stored"]` therefore holds **line item 010's total**, not the summary figure, and
carries a `FileSource` bbox pointing at the wrong cell. Every rule reading the summary then compares a line to
itself; A1 would pass on a document that does not foot.

Not a synthetic artifact — real G702-format documents carry the same words in both places for the same reason.

**Fix, three parts:** (1) `extract_summary` searches only below the grid's last data row; (2) `_find_label`
returns **all** matches and `extract_summary` refuses an ambiguous label, recording it unextractable rather than
picking one; (3) corpus summary labels get an unambiguous prefix in our own layout.
**Fixed in:** Phase 1 Task 12; Phase 2 Tasks 17, 18.

### F-10 · S3 · The corpus renderer never renders the summary block

`_render` iterates `CONTINUATION_COLUMNS` only; `SUMMARY_FIELDS` is defined and never drawn. Phase 2 Task 17's
`test_summary_total_matches_the_generated_application` cannot pass, and Task 21's
`assert built.provenance["_unextractable"] == []` cannot hold.

The plan **notices this in Task 21 Step 2** and tells the executor to fix `_render` there — a defect discovered
three tasks after it was introduced, in the task that proves DoD 2. Fix it where it is created.
**Fixed in:** Phase 1 Task 12.

### F-11 · S2 · The fingerprint test cannot fail

`monkeypatch.setattr(layouts, "CONTINUATION_COLUMNS", shifted)` — but `generate.py` binds the name at import, so
`_render` holds its own reference. The PDF is identical, the fingerprint is identical, and
`test_a_shifted_column_layout_yields_a_different_key` asserts inequality on two equal values. It fails, and the
"fix" an executor reaches for is to weaken the assertion.
**Fix:** `_render(app, columns=CONTINUATION_COLUMNS)`; the test passes shifted columns explicitly.
**Fixed in:** Phase 1 Task 12; Phase 2 Task 15.

### F-12 · S1 · Column assignment ignores the right edge, so right-aligned money lands in the wrong column

`_column_for` picks the column with the greatest header `x0` not exceeding the word's `x0`, and never consults
the column's right edge. Our renderer left-aligns every cell, so the tests pass. Real pay applications
right-align money. Any layout whose columns are narrower than ours puts a right-aligned value under the *next*
column's header — `Previous` read as `This Period` is a wrong bill that foots.

**Fix:** `Column.width_pt` is already defined and unused. Assign to the column whose
`[x0 − slack, x0 + width + slack]` span contains the word's **centre**; raise when a word falls in no column or
in two. **Fixed in:** Phase 2 Task 16.

### F-13 · S4 · `reconstruct_rows` recomputes the banding once per row

```python
for band_top in sorted(_band(body)):
    for word in _band(body)[band_top]:
```

`_band(body)` is O(len(body)), called twice per row. A 200-line sheet (~1,800 words) does ~720,000 dictionary
operations instead of 1,800. **Fix:** hoist to `bands = _band(body)`. **Fixed in:** Phase 2 Task 16.

### F-14 · S2 · `extract/` imports its ground truth from `corpus/`, so the extraction tests are self-confirming

`extract/tables.py` does `from ledger_sense.corpus.layouts import CONTINUATION_COLUMNS`. Header phrases, required
fields and column geometry all come from the module that generated the document being read.

The twenty-seed test therefore proves the generator agrees with the generator. It is not evidence about layout
robustness, and spec §4.3's whole argument is that *"real documents vary enormously in geometry while being
semantically identical."* This is the largest verifiability defect in the plan, because it makes a green suite
feel like evidence for the claim the product is sold on.

**Fix, three parts:**
1. `extract/schema.py` owns a **header synonym table** (`scheduled_value` ← "Scheduled Value", "Sched. Value",
   "Contract Value", "SOV Amount", …). `extract/` imports nothing from `corpus/`.
2. `corpus/layouts.py` gains **four adversarial variants** — right-aligned money, alternate header wording,
   wrapped two-line descriptions, a two-page sheet with a repeated header — selected by a `layout` argument.
3. The twenty-seed test runs across all five layouts. A layout the extractor cannot read must produce
   `CannotExtract`, never a wrong value.

**Fixed in:** Phase 1 Task 12; Phase 2 Tasks 14, 16, 17.

### F-15 · S2 · `assemble` invents `contract_id`, `application_no` and `period_to`

```python
contract_id="", application_no=0, period_to=date.today()
```

Task 17's own docstring says *"a guessed contract sum is a wrong bill with a confident number on it"*, and Task
18 then defaults three fields rather than recording them unextractable. `period_to = date.today()` is the worst:
C12 compares a waiver's through-date against it, so a document processed a month late flags every waiver.

**Fix:** extract all three; when absent, append to `_unextractable` and leave the field `None`.
**Fixed in:** Phase 1 Task 4; Phase 2 Task 18.

### F-16 · S3 · Nothing produces `document_hash`

`build_idempotency_key(document_hash, …)` has no producer in any of the six plan documents.
**Fix:** `ingest/fingerprint.py::document_hash(pdf: bytes) -> str`. **Fixed in:** Phase 2 Task 15.

### F-17 · S3 · The profile cache is never wired to anything

Spec §4.3 calls the reviewer-teaches-the-extractor loop *"the commercial one"* — *"the product gets better as you
use it, without an asterisk."* `ProfileCache` is built in Task 19 and tested in isolation. **No task calls
`lookup()` from `assemble`, and no task calls `learn()` from the review API.** The loop carrying the product's
central commercial claim is never closed; Task 19's verification line describes an integration that does not
exist.

**Fix:** `assemble` consults the cache before falling back to anchors; the review API's edit handler calls
`learn()` with the corrected bbox; an integration test asserts the second document needs no correction.
**Fixed in:** Phase 2 Tasks 18, 19; Phase 4 Task 28.

**Follow-up (2026-09-16):** deferred out of Phase 4 Task 28 / review-queue API Slice 1, with sign-off. Wiring
`learn()` into the API alone would write a signal nothing reads — the runner still never passes a `ProfileCache`
into `assemble`, and `ProfileKey` is computed from raw PDF bytes, which the API would have to decrypt
`CLIENT_CONFIDENTIAL` documents to compute. Ships whole as its own "profile learning" slice after the review
screen exists, once the reviewer can confirm/move the box — the only edit that carries the geometry `learn()`
needs. Recorded in the Slice 1 spec (`2025-03-11-ledger-sense-phase-4-review-ui.md`, Task 28).

### F-18 · S1 · The profile cache is shared across clients with no tenant scope

The key is `producer:column_hash`. Two clients whose subcontractors use the same accounting package share a
profile, so a wrong correction by client A's bookkeeper silently shifts extraction geometry for client B. The
docstring argues geometry-only makes this safe — geometry-only prevents **data** leakage, not **influence**, and
in a product whose differentiator is that a buyer can trust the number, one tenant steering another's extraction
is the harder problem.
**Fix:** primary key becomes `(tenant_id, key, field)`; the cache cannot be queried without a tenant.
**Fixed in:** Phase 2 Task 19.

### F-19 · S4 · Every SQLite store opens one connection and is used from FastAPI handlers

`ProfileCache`, `IdempotencyLedger`, `TokenStore`, `AuditLog` and `CalibrationStore` each `sqlite3.connect()`
once in `__init__` and hold it. FastAPI runs sync handlers in a threadpool; `sqlite3` defaults to
`check_same_thread=True`, so the first cross-thread use raises `ProgrammingError`. None sets WAL or a busy
timeout either, so concurrent writers get `database is locked` rather than waiting.
**Fix:** a shared `ledger_sense/store/sqlite.py` — `check_same_thread=False`, `journal_mode=WAL`,
`busy_timeout=5000`, `foreign_keys=ON`, per-thread connection via `threading.local`.
**Fixed in:** Phase 0 Task P4; referenced from Phase 2 Task 19 and Phase 5 Tasks 35, 36, 38.

### F-20 · S4 · `ProfileCache.lookup` writes and commits on every read

A hit-counter `UPDATE` plus `commit()` per field lookup: 50 write transactions to read a 50-field document.
**Fix:** drop the counter. **Fixed in:** Phase 2 Task 19.

### F-21 · S4 · The PDF is parsed up to four times per document

`extract_words` opens it; `fingerprint._producer` opens it; `_column_hash` calls `extract_words`, opening it
again; `has_text_layer` opens it once more. **Fix:** one `ParsedDocument`, opened once, threaded through.
**Fixed in:** Phase 2 Tasks 14, 15.

### F-22 · S4 · `RapidOCR()` is instantiated at module import

`_ENGINE = RapidOCR()` at module scope loads ONNX models on any import — including bare test collection.
Hundreds of megabytes and seconds of latency on a path that may never OCR anything, and an import error on a
machine without the weights. **Fix:** lazy `functools.lru_cache(maxsize=1)` accessor.
**Fixed in:** Phase 2 Task 20.

### F-23 · S2 · `ocr_words` discards recovered pages when one page is blank

`return CannotExtract(...)` inside the page loop throws away every word already recovered. A three-page document
with a blank page 2 yields nothing. **Fix:** per-page results; the failure is recorded against the page and the
document is partially extracted. **Fixed in:** Phase 2 Task 20.

### F-24 · S2 · `MIN_CONFIDENCE = 0.5` is exactly the constant Phase 6 forbids

Phase 6 asserts *"no derived threshold is hard-coded anywhere in the router"* and enforces it with an AST test.
`extract/ocr.py` carries an uncalibrated cut-off written by feel, on the noisiest path in the product, with no
equivalent guard. The principle is right; its scope is wrong.
**Fix:** OCR emits every token with its confidence and routes through `route/router.py`; extend the AST guard to
`extract/`. **Fixed in:** Phase 2 Task 20; Phase 6 Task 41.

---

## 4. Boundary, security and data protection

### F-25 · S1 · No API endpoint has authentication, authorisation, or tenant isolation

Phases 2, 4 and 6 define routes at `/api/baseline`, `/api/review`, `/api/reconciliation`, `/api/inbox`,
`/api/vendors`, `/api/metrics`. **Not one requires a credential.** `docker compose up` stands up an
unauthenticated service holding construction pay applications, vendor bank details, and OAuth tokens for a
client's QuickBooks company.

`GET /api/reconciliation/{doc_id}` and `POST /api/review/exceptions/{exc_id}/accept` take an identifier straight
from the caller with no ownership check — a textbook IDOR, on financial documents.

Brief `02` B9: *"you become a data processor the moment you accept a document."* `00` §8 specifies a
least-privilege Postgres role and an audit log; neither document mentions application authentication because both
assume it. The plan inherits the assumption and implements nothing.

**Fix:** Phase 0 Task P2 — session auth, `tenant_id` on every row and every query, one ownership dependency
applied to every router, and a per-router test asserting 401 without a session and 404 across tenants.
**Fixed in:** Phase 0.

### F-26 · S1 · Uploaded PDFs are parsed in-process with no resource limits

`pdfplumber`/`pdfminer.six` parse attacker-supplied structure. Decompression bombs, deeply nested object graphs,
million-page documents and font-table pathologies are known DoS classes. `00` §8 requires that *"any subprocess
execution runs containerised: non-root, read-only rootfs, no network, memory and CPU limits, hard timeout"* —
extraction is not a subprocess, so the rule was never applied to the one place the product touches hostile bytes.

**Fix:** an extraction worker with a 25 MB byte cap, a 200-page cap, a 60 s wall-clock timeout, an `RLIMIT_AS`
memory cap, and magic-byte sniffing rejecting anything not starting `%PDF`. Fuzz corpus of 20 malformed PDFs
asserting refusal rather than hang. **Fixed in:** Phase 0 Task P3.

### F-27 · S1 · CSV and IIF export are formula-injection vectors, fed by untrusted document text

`ExportAdapter` writes `request.vendor_ref` and `item.description` — both extracted from a client-supplied PDF —
into a `.csv` that a bookkeeper opens in Excel. A description beginning `=`, `+`, `-`, `@`, tab or CR executes as
a formula on open. This is the most likely path from "we processed your subcontractor's invoice" to "we executed
code on your controller's workstation."

The IIF writer is worse: `"\t".join(...)` with a tab or newline inside a description corrupts the record and can
inject an additional transaction line into a file destined for QuickBooks Desktop.

**Fix:** prefix any cell whose first character is in `=+-@\t\r` with a single quote; strip control characters;
for IIF, reject or escape tab/CR/LF in every field. Tests with a `=HYPERLINK(...)` description and a tab-bearing
vendor name. **Fixed in:** Phase 5 Task 39.

### F-28 · S2 · The cassettes will contain access tokens

`filter_headers=["authorization"]` scrubs the request header; `filter_post_data_parameters` scrubs the request
body. **Neither touches the response body**, and the OAuth token endpoint returns `access_token` and
`refresh_token` there. `test_no_cassette_contains_a_token` would catch it — but only after the secret has been
written to disk and quite possibly committed. Hard constraint `00` §3.4: *"No secrets in a repo, ever — including
test fixtures."*
**Fix:** `before_record_response` scrubbing `access_token`/`refresh_token`/`id_token` from JSON bodies, plus
`gitleaks` over `tests/writeback/cassettes/` in CI. **Fixed in:** Phase 5 Task 37.

### F-29 · S2 · Cassette matching ignores the query string, so the wrong response replays

`match_on=["method", "scheme", "host", "path"]`. Every `find_bill_by_docnumber` call is
`GET /v3/company/{realm}/query?query=…` — same path, different query. Two different queries replay each other's
responses, and the contract test that exists to prove the fake matches reality proves nothing.
**Fix:** add `"query"` to `match_on`. **Fixed in:** Phase 5 Task 37.

### F-30 · S3 · No encryption at rest, no retention policy, no deletion procedure

`02` B9 and `00` B7 both require encryption at rest, a retention policy with automatic deletion, and a documented
deletion procedure — *"prepare this before the first document arrives, not after the first client asks."* Spec
§12.1 does not schedule it; no task implements it; `docs/security.md`, required by `00` §6 in every repo, is
never created. This is contractual as much as engineering: a design partner's data agreement will name a
retention period, and no code honours one.
**Fix:** Phase 0 Task P5 — envelope encryption for stored bytes, a `retention_days` column, a deletion job with a
test proving bytes *and* derived claims both go, and `docs/security.md`. **Fixed in:** Phase 0.

### F-31 · S2 · Three mandated CI gates are never created

`gitleaks` (`00` §3.4, quoted verbatim in the plan's own Global Constraints), `pip-audit`/`npm audit` (`00` §8)
and the CycloneDX SBOM (`00` §8) appear in no task and no `ci.yml` fragment.
**Fix:** Phase 0 Task P1. **Fixed in:** Phase 0.

### F-32 · S2 · The AST guards are trivially evaded, and one guards the wrong thing

Four guards share the pattern `getattr(node.func, "id"/"attr", "")`:

| Guard | Evaded by |
|---|---|
| `Labelled` constructed outside `intake.py` | `bok_core.boundary.Labelled(...)` (an `ast.Attribute`); `import … as L` |
| domain model built outside `assemble.py` | any aliased import |
| waivers module generates nothing | `getattr(obj, "render")()` |
| `post_bill_payment` never called | anything in `HttpQBOClient` — the real risk is `httpx.post(f"{base}/billpayment")`, a **URL**, not a method name |

The last matters most: the guard protects a method on our own fake while the code that could actually post a
payment is an HTTP call whose path is a string.
**Fix:** resolve imports before matching (walk `ast.ImportFrom` into an alias map); for QBO, additionally scan
every string literal under `writeback/` for `billpayment`.
**Fixed in:** Phase 1 Task 13; Phase 2 Task 18; Phase 3 Task 26; Phase 5 Task 36.

### F-33 · S2 · The licence gate has no implementation that reads a real package

`scripts/licence_audit.py` defines `audit(packages: list[PackageInfo])` and nothing that produces
`PackageInfo`. CI runs `uv run python -m scripts.licence_audit --check-lockfile uv.lock`; the module has no
argument parsing and no lockfile reader, so **it exits 0 unconditionally**. The task is titled *"the
licence-audit gate"*, scheduled first *"before any dependency is installed"*, and described as the job whose
*"value is preventing adoption."* As written it is a unit test of a deny-list against six hand-written tuples.

**Verified bypasses in the deny-list itself:**

```
AGPL-3.0-only OR LGPL-3.0        denied=False   <- the allow-check short-circuits
GPL-1.0-only                     denied=False
BUSL-1.1                         denied=False
Elastic-2.0                      denied=False
PolyForm-Noncommercial-1.0.0     denied=False   <- "NON-COMMERCIAL" != "Noncommercial"
```

`_spdx_denied` returns `False` if *any* allowed fragment appears anywhere, so a dual licence containing "LGPL"
whitelists the package outright.

**Fix:** (1) a real reader walking `uv.lock` plus `importlib.metadata`; (2) parse the SPDX expression — a
disjunction is denied only if **every** branch is denied, a conjunction if **any** is; (3) extend the deny-list
with `GPL-1.0`, `SSPL`, `BUSL`, `Elastic`, `PolyForm`, `RSAL`, `Commons-Clause`, and case-fold/strip punctuation
before matching `noncommercial`. **Fixed in:** Phase 1 Task 1.

### F-34 · S2 · The native-layer check is three hardcoded names presented as a layer

`NATIVE_DENY` has one entry, `EXTRA_DENY` two. The claim is that the job *"checks the native and system layer,
not just Python metadata."* It checks three names somebody typed; a fourth AGPL native dependency is invisible,
and spec §3.2's design consequence rests on this being a mechanism.
**Fix:** keep the deny-list and add a real check — after `uv sync`, run `ldd`/`otool` over installed extension
modules and fail on any shared object resolving outside the venv that is not allowlisted. Where impractical, say
so in `docs/methodology.md` rather than implying coverage. **Fixed in:** Phase 1 Task 1.

### F-35 · S1 · `PaymentDetail` stores only `last4`, which is both a miss risk and a lost-provenance risk

Two problems in the highest-consequence rule in the product.

1. **Detection.** Four digits give a 1-in-10,000 chance a fraudulent redirection is invisible, and the comparison
   is only against `payment_history[-1]` — an attacker who changes details, obtains one confirmation, then
   changes back is never flagged.
2. **Provenance.** `record_payment_detail` takes `confirmed_by` and **discards it**. `PaymentDetail` has no
   `confirmed_by` and no `confirmed_at`. Spec §8 item 5 requires the vendor ledger to render bank-detail changes
   *"as a red-lined diff with the date and source document"*, and B5 requires human confirmation **on the
   record**. The confirmation is not on the record.

**Fix:** store `account_hmac` — HMAC-SHA256 of the full normalised account+routing under a key held only in the
environment — alongside `last4`. Compare the HMAC (exact, no collisions, still no recoverable account number);
display the last4. Add `confirmed_by` and `confirmed_at` to `PaymentDetail`, asserted set for every entry after
the first. **Fixed in:** Phase 3 Task 27.

### F-36 · S1 · `merge` loses the absorbed vendor's payment history; `unmerge` can delete a legitimate alias

`VendorLedger.merge` pops the absorbed vendor and appends one alias. `payment_history` goes with the popped
record — so after merging two records for the same vendor, the keeper has no knowledge of the absorbed record's
bank details, and the next invoice compares against the wrong baseline. **The fraud control silently loses its
evidence at exactly the moment two records are reconciled.** `unmerge` then removes keeper aliases **by name**,
destroying a pre-existing alias that happened to share the absorbed vendor's name.

B6 requires *"merges are reversible with provenance for every alias."* Neither half holds.
**Fix:** merge migrates `payment_history` into the keeper, ordered by `observed_at` and tagged with its origin
vendor id, and the `MergeToken` records exactly which entries moved; `unmerge` removes aliases by identity.
Test: merge-then-unmerge is the identity on both records. **Fixed in:** Phase 3 Task 27.

### F-37 · S2 · `find_duplicates` matches an invoice number across different vendors

```python
if candidate.invoice_number == prior.invoice_number:
```

No vendor comparison. Two vendors both numbering from `INV-1001` produce a permanent false duplicate. `line_hash`
matching has the same defect and, worse, `line_hash` is never produced by any task. Detection is specified
*"across full history"* while `find_duplicates(candidate, history: list)` takes an in-memory list nothing
persists or indexes — a linear Python scan over years of records.
**Fix:** scope invoice-number and line-hash matching to the resolved vendor; define `line_hash` in Task 18 as a
sha256 over sorted `(item_no, description, total)` triples; back history with an indexed table and query, not
scan. **Fixed in:** Phase 2 Task 18; Phase 3 Task 27.

### F-38 · S3 · `VendorLedger` has no persistence

`self._vendors: dict[str, CanonicalVendor]` in memory. I1's *"across full history"*, I2's alias accumulation and
I3's payment-detail baseline all evaporate on restart. DoD 3 reports met against a store that does not survive
`docker compose restart`. **Fix:** SQLite-backed via the shared store from F-19, same API.
**Fixed in:** Phase 3 Task 27.

---

## 5. Writeback

### F-39 · S1 · Reversal leaves the idempotency record `committed`, so a reversed bill can never be rewritten

`QBOAdapter.reverse` deletes the remote bill and returns. The ledger row still says `committed` with the
now-dead `remote_id`. A subsequent `create_unapproved_bill` with the same key hits the `state == "committed"`
branch and returns `WriteResult(remote_id=<deleted id>, was_replay=True)` — a success response pointing at an
object that no longer exists in QuickBooks. The audit trail then records a write that did not happen, against a
bill that is gone. The highest-consequence code in the product returning a confident wrong answer.

**Fix:** add `reversed` to `WriteState`; `reverse` takes the `idempotency_key` and transitions the record;
`begin` on a `reversed` record opens a fresh `pending`. Test: write → reverse → write produces a **new** bill and
two audit entries. **Fixed in:** Phase 5 Tasks 35, 36, 38.

### F-40 · S1 · Hijacking `DocNumber` for the idempotency key destroys the client's own duplicate detection

`DocNumber` on a QBO Bill is the **vendor's invoice number** — what the bookkeeper reads, what they reconcile
against the paper, and what QuickBooks' own duplicate-document-number warning keys on. Overwriting it with
`LS3f9a…` removes the vendor's reference from the client's books and disables a control they already had, in a
product whose pitch is catching duplicates.

The plan chose `DocNumber` because it is queryable, which is a real constraint. But the trade-off is stated
nowhere and the client is never told.
**Fix (recommended):** `DocNumber` carries the vendor invoice number when one was extracted; the idempotency key
goes in `PrivateNote`. Crash reconciliation queries `Bill WHERE TxnDate = … AND VendorRef = …` and matches
`PrivateNote` client-side — a bounded scan, and correct. Where no vendor invoice number was extracted, fall back
to the key in `DocNumber` and record that in the audit detail.
**Status: OPEN — Q3.** Documented in Phase 5 Task 34.

### F-41 · S2 · Nothing tests OAuth refresh, and QBO rotates the refresh token on every use

The plan says *"refreshed on 401"* in prose; there is no test. Intuit rotates the refresh token on each refresh
and expires it after ~100 days. Failing to persist the new one locks the integration out permanently, surfacing
as a nightly smoke failure with no obvious cause.
**Fix:** test that a 401 triggers refresh, that the **new** refresh token is persisted **before** the retry, and
that a crash between refresh and persist is recoverable. **Fixed in:** Phase 5 Task 36.

### F-42 · S2 · `AuditedAdapter._doc_by_remote` is in-memory, so reversal after a restart audits `"unknown"`

The audit trail's own document linkage does not survive a restart — and a reversal is precisely the operation
most likely to happen a day later. **Fix:** resolve `doc_id` from `AuditLog` by `remote_id`.
**Fixed in:** Phase 5 Task 38.

### F-43 · S2 · "Append-only" is a property of the Python class, not of the data

`test_the_audit_trail_has_no_delete_or_update_method` asserts four attribute names are absent; the SQLite file is
writable by the same process. What a client hears — *"full audit trail"* — implies tamper evidence.
**Fix (cheap):** a `prev_hash` column, each entry hashing `(prev_hash, payload)`, plus `verify_chain()`. Two
tests: an unmodified log verifies; a log with one row's `detail` edited fails at that row.
**Fixed in:** Phase 5 Task 38.

### F-44 · S4 · `IdempotencyLedger.fail` is never called

The adapter propagates exceptions without recording a reason, so `fail()` is dead code with a passing test, and a
QBO 400 is indistinguishable from a crash. **Fix:** wrap the post; call `fail()` for a non-retryable status;
re-raise. **Fixed in:** Phase 5 Task 36.

### F-45 · S4 · `ExportAdapter.reverse` drops rows by substring match

`[ln for ln in lines[1:] if remote_id not in ln]` removes any line whose description happens to contain the key.
**Fix:** parse the CSV and compare the `idempotency_key` column; for IIF, split on tab and compare field 6.
**Fixed in:** Phase 5 Task 39.

---

## 6. Calibration and routing

### F-46 · S1 · Nothing in the pipeline produces a confidence, so the router has no input

`extract/assemble.py` sets `confidence=None` on every claim, and Task 18's test **asserts** it:
`assert claim.confidence is None, "deterministic claims carry no confidence"`.

`Router.decide(field, confidence: float)` requires one. `labels_from_action` requires
`action.predicted_confidence`. `CalibrationCurve.fit` requires `predicted_confidence` per label.

**DoD 4 rests on a value the product never computes.** The whole of Tasks 40–42 is machinery with no feed.

**Fix:** deterministic extraction emits a **match quality score** — a documented, bounded function of the
evidence it actually has: label found exactly vs fuzzily; value token uniquely in the expected geometry vs one of
several candidates; column assignment unambiguous vs within slack; profile-cache hit vs cold anchor. It is
explicitly *not* a probability, which is why it must be calibrated before it routes anything — the argument the
phase already makes. `Claim.confidence` stays `None` for `DETERMINISTIC`; `Claim.match_quality: float` is the new
field, and the router consumes that. **Fixed in:** Phase 2 Task 18; Phase 6 Tasks 40, 41.

**Follow-up (2026-09-16):** the "every action appends a calibration label" half of this finding, as it bears on
Phase 4 Task 28 / review-queue API Slice 1, is already satisfied — no new work deferred or otherwise. The claims
table records a `HUMAN` claim with `observed_at` next to the `DETERMINISTIC` claim it judges. Phase 6 derives
`(predicted, actual)` pairs from that with one query, and basis comes from the document's `sensitivity` column,
so no caller can spoof it. No separate labels table, no `ReviewAction` class until Phase 6 says what it needs.
Recorded in the Slice 1 spec (`2025-03-11-ledger-sense-phase-4-review-ui.md`, Task 28).

### F-47 · S1 · `MIN_LABELS_FOR_FIELD = 200` cannot support a 99.5% claim

**Verified:** a 95% lower confidence bound at 99.5% accuracy needs, by the rule of three, `n ≥ 3/0.005 = 600`
consecutive correct observations. With 200 labels the finest accuracy resolvable is 1/200 = 0.5% — the entire
error budget. The constant is asserted by a test and traced to nothing. The phase whose stated purpose is *"the
code refuses to emit a threshold it has not earned"* emits one it has not earned.

**Fix:** delete the constant. `threshold_for(target_accuracy)` derives its own requirement — compute a
**Clopper–Pearson lower bound** on observed accuracy at and above each candidate threshold and return the lowest
candidate whose *lower bound* meets the target. A curve with too few labels then yields `None` automatically, for
a reason the code can state. `MIN_LABELS_FOR_FIELD` becomes a derived, reported property.
**Fixed in:** Phase 6 Task 40.

### F-48 · S2 · The curve is fitted and evaluated on the same labels

Isotonic regression with one knot per observation interpolates its training data. `threshold_for` reads a fitted
value that is optimistically biased, and `docs/methodology.md` ships the resulting reliability diagram as
evidence. **Fix:** fit on a seeded random 70% and evaluate the threshold on the held-out 30%, recording both n's
on the curve. Test: a curve fitted on pure noise yields no threshold. **Fixed in:** Phase 6 Task 40.

### F-49 · S1 · Once auto-posting starts, the labelled sample is censored and drift is invisible

Labels come only from fields routed to review. The moment a field type crosses its threshold, its high-confidence
population stops producing labels, so the curve freezes on the evidence that authorised it and can never detect
that extraction has degraded — a producer changing its PDF template, say, the single most likely real-world
failure. A feedback loop that fails silently in the optimistic direction, in the product whose thesis is that
optimistic silence is the industry's defining sin.

**Fix:** a **hold-back audit sample** — route a fixed fraction (5% proposed, recorded in methodology) of
above-threshold fields to review anyway, labelled as audit; refit on audit labels; withdraw the threshold
automatically when audit accuracy falls below the target's lower bound. Test: seeded degradation withdraws the
threshold. **Fixed in:** Phase 6 Task 41.

### F-50 · S4 · `predicted_confidence` is never range-checked

`test_a_field_curve_that_never_reaches_the_target` generates confidences up to **3.0**; nothing validates
`0.0 ≤ c ≤ 1.0`. **Fix:** validate on `Label`. **Fixed in:** Phase 6 Task 40.

### F-51 · S4 · The curve stores one knot per label

300 labels produce 300 xs and 300 knots, JSON-serialised into SQLite, linear-scanned by `calibrated()`. PAVA
output has few distinct values by construction. **Fix:** collapse to unique `(x, knot)` blocks; bisect.
**Fixed in:** Phase 6 Task 40.

---

## 7. The baseline study — the artifact sold in week one

### F-52 · S1 · The Student's *t* table truncates at df = 9 and falls back to 1.96

**Verified.** `_T_95` covers df 1–9; every larger sample uses `_T_95_LARGE = 1.96`.

```
df=  9  true t=2.262   plan uses 1.96   margin understated by 15.4%
df= 19  true t=2.093   plan uses 1.96   margin understated by  6.8%
df= 49  true t=2.010   plan uses 1.96   margin understated by  2.6%
```

Every error is in the direction that makes the vendor's interval look tighter than it is, in the artifact whose
entire purpose is defensibility (B13), for exactly the sample sizes a real baseline study uses (n = 10–50).
**Fix:** compute *t* rather than table it, or ship the table to df = 200 and use 1.96 only beyond.
**Fixed in:** Phase 1 Task 10.

### F-53 · S2 · The interval propagates uncertainty in touch time only

`confidence_interval()` treats `effective_hourly()` as exact and multiplies. The overhead multiplier is an
estimate the client supplies off the top of their head, so the published interval is narrower than the real
uncertainty in cost-per-document — the opposite of B13's posture.
**Fix:** the method statement states verbatim that the interval covers sampling variability in touch time and
holds the loaded rate fixed at the value supplied; `LoadedRate` gains optional `hourly_low`/`hourly_high` that,
when supplied, widen the reported interval. **Fixed in:** Phase 1 Tasks 10, 11.

### F-54 · S2 · A *t*-interval on n = 3 right-skewed observations is not defensible

`test_cost_per_document_uses_mean_touch_time_and_loaded_rate` uses n = 3. Touch time is right-skewed.
**Fix:** refuse below n = 8 with a stated reason — the same posture `confidence_interval` already takes at n < 2 —
report the median alongside the mean, and state the normality assumption.
**Fixed in:** Phase 1 Task 10.

---

## 8. Work that has no task — the real schedule risk

Each is required by the spec or the definition of done and is implemented by nothing.

| # | Missing | Required by | Consequence if left |
|---|---|---|---|
| G-1 | **Frontend scaffold** — Next.js 15 app, Tailwind v4 `@theme`, `bok-ui` wiring, `pdf.js` worker asset, Playwright and Vitest config, the three density tokens Task 33 asserts | Spec §8; Phase 4 assumes it | Week 9 begins with zero UI infrastructure and six screens to build |
| G-2 | **FastAPI application object** — `api/app.py`, router registration, error handlers | Phase 6 Task 43 imports it | Phase 6 does not import |
| G-3 | **Postgres + alembic** — the stack line and `00` §8 specify it; every store is SQLite or a dict, and three `ponytail:` comments defer to "the Postgres session in Task 43", which does no database work | Stack; `00` §8 | `docker compose up` has no schema; nothing persists |
| G-4 | **`docker-compose.yml`, `Dockerfile`, `.env.example`, `LICENSE`, `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, `pyproject.toml` body** | `00` §6; DoD 10; **Task 48 tests four of these and no task creates any** | Close-out fails on files never written |
| G-5 | **AuthN / AuthZ / tenancy** | F-25 | Unauthenticated financial data |
| G-6 | **Untrusted-input hardening for PDF parsing** | `00` §8; F-26 | DoS, and worse |
| G-7 | **Encryption at rest, retention, deletion, `docs/security.md`** | `02` B9; `00` §6, §8; F-30 | Cannot sign a data agreement |
| G-8 | **`gitleaks`, `pip-audit`, `npm audit`, SBOM** | `00` §3.4, §8; F-31 | Three hard constraints unenforced |
| G-9 | **E2E backend seeding** — every Playwright test navigates to `/review/doc-50` with no task creating the fixture route | Tasks 30–33 | Every E2E test 404s |
| G-10 | **Duplicate-history and vendor persistence** | `02` §5; F-37, F-38 | I1–I3 do not survive a restart |
| G-11 | **The profile-cache learning loop** | Spec §4.3; F-17 | The central commercial claim is unimplemented — deferred by sign-off (2026-09-16) to a dedicated "profile learning" slice; see F-17 follow-up |

G-1 to G-4 and G-9 together are roughly a week the eight-week plan does not contain.
**Fix:** a new **Phase 0 — Platform** document scheduled in week 5 alongside Task 1, carrying P1–P7. See
`2026-08-29-ledger-sense-phase-0-platform.md`.

---

## 9. Contradictions inside the plan

### F-55 · S2 · Two different non-affiliation notices, each asserted by a different test

| Where | String |
|---|---|
| Global Constraints, spec §11, `scripts/aia_guard.py` | `not developed, endorsed, approved, sponsored by, or affiliated with` |
| Phase 6 Task 48 `test_the_readme_carries_the_non_affiliation_notice` | `not affiliated with, endorsed by, or licensed by` |

Both run in the same suite. They cannot both pass unless both strings sit in the README.
**Fix:** one constant, `ledger_sense/notice.py::NON_AFFILIATION_NOTICE`, imported by the guard, the close-out
test and the UI footer. **Fixed in:** Phase 1 Task 12; Phase 6 Task 48.

### F-56 · S2 · Task 48's AIA regex fails against Task 12's own code

`test_the_repo_contains_no_form_artifact` forbids `\bG70[23]\b` and `american institute of architects` in every
`.py` outside `docs/superpowers/` and `CLAUDE.md`. `scripts/aia_guard.py` contains both. The close-out test fails
against the code the plan tells you to write.
**Fix:** allowlist `scripts/aia_guard.py`, `ledger_sense/notice.py` and `tests/test_aia_guard.py`; scan for
rendered form artifacts, not for the letters. **Fixed in:** Phase 6 Task 48.

### F-57 · S2 · Two incompatible section schemas for `docs/methodology.md`

Master Task 11 Step 5 specifies `## Baseline study`, `## Calibration basis`, `## Straight-through rate`. Phase 6
Task 46 asserts `## What we measure`, `## How we measure it`, `## Corpus`, `## Calibration`, `## What these
numbers do not mean`. Task 46's test fails on the document Task 11 creates. Task 46 additionally asserts
`"g702" not in DOC` while spec §11 requires the notice — which names G702® — in *"the README, the docs and the
product UI."*
**Fix:** one schema, Task 46's, written incrementally from Task 11; the notice lives in README and UI, and
`methodology.md` links to it. **Fixed in:** Phase 1 Task 11; Phase 6 Task 46.

### F-58 · S2 · Two frontend layouts

Phase 4 builds `ledger_sense/web/app/…` and runs `npx vitest` from the repo root; Phase 6 builds
`ui/src/screens/…` and runs `cd ui && npm test`. Same application, two trees.
**Fix:** `ledger_sense/web/` everywhere. **Fixed in:** Phase 6 Tasks 43, 44, 45.

### F-59 · S2 · `ledger_sense.review.actions` does not exist

Phase 6 Task 42 imports `ReviewAction` from it; Task 28 built `ledger_sense/api/review.py` and defined no such
class. **Fix:** `ledger_sense/review/actions.py`, created in Task 28, consumed in Task 42.
**Fixed in:** Phase 4 Task 28; Phase 6 Task 42.

### F-60 · S2 · The cut order forbids cutting the one task the plan says may be skipped

*"Never cut: the boundary test (Task 13)"* — while Task 13 is headed **GATED**, *"If it has not landed, skip to
Phase 2."*
**Fix:** state the real rule — the boundary test is not cuttable *for schedule*, and no code accepting an
external document ships without it. If `bok-core` slips, the upload path slips with it.
**Fixed in:** master, cut order.

### F-61 · S2 · The `bok-core` gate is far wider than the plan says

Phase 2's preconditions say *"Task 13 is not required."* But Task 18 imports
`bok_core.provenance.{Claim, FileSource, Method}`, Task 28 imports `HumanSource`, and Task 42 depends on both.
Everything from Task 18 onward is blocked on an unpublished package that spec §4.1 confirms does not exist, and
which must additionally ship five bespoke additions requested in prose. The plan calls the gate *"deliberately
narrow."* It is 31 of 48 tasks.

**Fix:** define the five additions as a **local protocol module**, `ledger_sense/provenance.py`, exactly as
`rules/provenance.py` already does for `RuleSource` — structural types that `bok-core`'s concrete classes satisfy
on arrival, with a local dataclass implementation used until then. The plan already chose this pattern once and
should apply it consistently. Nothing is deleted later.
**Fixed in:** master (Global Constraints, Task 13); Phase 2 Task 18; Phase 4 Task 28.

### F-62 · S2 · Task 47's demo requires error injectors `errors.py` explicitly does not implement

`test_the_demo_catches_a_duplicate_invoice` expects `I1`; `test_the_demo_catches_a_retainage_miscalculation`
expects `C1`/`C2`. `corpus/errors.py` returns the application **unchanged** for both. DoD 7 cannot be met.
**Fix:** implement both — `I1_duplicate` emits a second application with the same invoice number and a shifted
date; `C1_retainage_rate` sets a `retainage_total` inconsistent with `Contract.retainage.base_rate`.
**Fixed in:** Phase 1 Task 12.

### F-63 · S4 · Task 33's 375px assertion contradicts Task 30's design

Task 33 asserts no horizontal scroll at 375px on `/review/doc-3`. Task 30 specifies a split view with a `pdf.js`
canvas at `scale = 1.5` — 792pt × 1.5 ≈ 1,188 CSS px. The pane must scroll internally, which no task specifies.
**Fix:** `overflow-x: auto` inside a `max-width: 100%` container; the split stacks below 768px.
**Fixed in:** Phase 4 Tasks 29, 30.

### F-64 · S4 · `preventDefault` on `Enter` breaks native button activation

`useReviewKeys` calls `preventDefault()` for `Enter` whenever focus is not in an `INPUT`/`TEXTAREA`/`SELECT`.
Focus on a `<button>` is none of those, so keyboard activation of every button is suppressed — an accessibility
regression `axe` cannot detect, because it is behavioural.
**Fix:** extend `isTyping` to cover `button`, `a[href]`, `[role="button"]` and any element with `tabindex >= 0`.
**Fixed in:** Phase 4 Task 30.

### F-65 · S4 · The dim layer's `clip-path` will not cut a hole

The polygon is an outer rectangle followed by the focus rectangle, comma-joined. Under the default `nonzero` fill
rule with both subpaths wound the same way, no hole appears — the layer dims the field under review, the opposite
of the intent. **Fix:** four absolutely-positioned rectangles around the focus box. Fewer lines, works everywhere,
animates. **Fixed in:** Phase 4 Task 29.

**Also fixed, no separate finding number:** `Meta+.` is the Super key on the Linux Chromium that runs CI — bind
both `Meta+.` and `Control+.` (Phase 4 Task 33). `test_every_focusable_control_has_a_visible_focus_ring` presses
Tab once and checks one element (Phase 4 Task 33). `test_a_clean_clone_passes_its_own_test_suite` runs `pytest`
in the working tree from inside the suite, doubling CI time and testing nothing about a clone — clone to a temp
directory, mark `slow` (Phase 6 Task 48).

---

## 9a. Found while applying the fixes

Two defects were introduced or left behind by the first revision pass, and are recorded here rather than
silently corrected. A register that only lists the original author's mistakes is not a register.

### F-66 · S3 · Phase 1 had no plan document

Phases 0 and 2–6 each have a step-level document. **Phase 1 — Tasks 1–13, the entire foundation — was written
inline in `2025-03-11-ledger-sense-weeks-5-12.md`**, occupying 2,730 of its 3,003 lines. Nothing named `phase-1`
existed, so an executor following the phase-document convention could not find the first phase, and the master
plan was too large to serve as the index it also claims to be.

Every phase document says *"Parent plan: `weeks-5-12.md` — its Global Constraints and File Structure apply here
in full"*, which only reads correctly if the parent is constraints plus index. It was constraints, index, **and**
one phase's full step-level body.

**Fix:** extract Tasks 1–13 to `2025-03-11-ledger-sense-phase-1-foundation.md` with a sibling-shaped header
(goal, architecture, stack, spec, parent, audit, preconditions, exit criteria). The master keeps Global
Constraints, File Structure, a **Phase documents** index that now lists all seven phases, and a Phase 1 summary
table in the same shape as the Phase 2–6 tables. Master: 3,003 lines → 302.
**Fixed in:** master plan; new Phase 1 document.

### F-67 · S2 · F-04 was described in prose but never applied to the code

The first pass rewrote the **commentary** around A9 and left the **code** untouched. As shipped, Phase 1 Task 6
still contained:

```python
@register("A9", "convention", source=CHANGE_ORDERS)
def rule_a9(app: PayApplication, contract: Contract) -> list[Finding]:
    ...
         and not _scheduled_value_moved(app, co.affects_item_no)), money("0")))
```

in `ledger_sense/rules/arithmetic/foot.py`, with a note underneath saying it should be moved to Phase 3. Three
consequences, in order of how fast you hit them:

1. **`rules/arithmetic/foot.py` does not import.** `CHANGE_ORDERS` comes from `rules/convention/sources.py`
   (Phase 3 Task 22) and `_scheduled_value_moved` is defined nowhere at all. `NameError` at module import,
   in week 6, taking every rule test in Phase 1 with it — Tasks 6, 7, 8 and 9 all import through it.
2. **Task 6's own steps contradicted each other.** Step 1's test file imported and exercised `rule_a9`; Step 4
   said *"Expected: PASS, 2 tests (the two A9 cases moved to Phase 3 Task 24)"*. Step 1 defines four.
3. **A9 existed nowhere.** Phase 3 Task 24 was never given the rule — searching the Phase 3 document for `A9`
   returned nothing. The reclassification deleted a validation rule instead of moving it, which is the one
   outcome worse than leaving it misclassified.

Item 3 is the reason this is S2 and not S4. F-04's whole argument is that A9 must carry a `RuleSource`; the
applied fix produced a plan in which A9 carried nothing, because it did not exist.

**Fix:** actually move it. Phase 1 Task 6 ships A1 only — heading, interfaces, test file, implementation, commit
message and property-test list all corrected, with a comment at the removal site saying where the rule went and
why it cannot live there. Phase 3 Task 24 becomes *"Change orders C7–C8, and A9 (reclassified)"* and carries
`rule_a9`, `_scheduled_value_moved`, the `CHANGE_ORDERS` source, four tests including the SOV-lag regression and
its narrowness control, and the one-line edit that puts A9 back into `ALL_RULES`.

**Note on `_scheduled_value_moved` with no prior application:** it returns `False`, which *suppresses* A9. That
direction is deliberate and documented at the function: F-04 exists because a rule that fires on the normal
mid-period state trains a reviewer to dismiss everything it says.
**Fixed in:** Phase 1 Task 6; Phase 3 Task 24; master File Structure.

---

## 10. Fact-check of the plan's external claims

| Claim | Status |
|---|---|
| `PyMuPDF` is AGPL / Artifex dual | **Sound.** Correctly rejected; `pypdfium2` is the right substitute. |
| `ocrmypdf` is MPL-2.0 but requires AGPL Ghostscript | **Sound — the best catch in the document.** A metadata-only scan does wave it through. Note the gate meant to catch it does not run: F-33. |
| `camelot-py` 2.0 defaults to `pypdfium2`, Ghostscript optional | **Plausible; re-verify.** Version-sensitive, and the audit date is wrong — below. |
| EasyOCR's CRAFT weights carry research-use ambiguity | **Sound as a caution.** Pin `docling[rapidocr]`. |
| QBO production keys need an app-assessment questionnaire; no App Store listing required; sandbox ungated | **Directionally sound; re-verify.** The load-bearing part — "sandbox needs no approval" — is stable, and it is what keeps the gate off the critical path. |
| Xero: 25 uncertified connections, 2 uncertified apps per org, Custom Connections is a paid subscription | **Flagged by the plan itself** as unverified (`developer.xero.com` timed out). Still unverified. QBO-first does not depend on the exact numbers, which is acceptable **provided nobody later cites them to a client.** |
| **Spec §3 headed "Verified external facts (2025-03-11)"** | **Wrong, and it matters.** The briefs are dated 27 August 2026, the fixtures use 2026 dates, today is 2026-08-29. Either the verification is 17 months stale or the header is wrong. `00` §11: *"Re-verify provider limits and terms on the day you build."* **OPEN — Q1.** |
| `MIN_LABELS_FOR_FIELD = 200` | **False.** F-47. |
| Student's *t* table | **Truncated, biased optimistic.** F-52. |
| A5/A6 payment-chain ordering | **Correct**, including the ordering the spec's own diagnostic got wrong. |
| C11 waiver direction | **Correct** — conditional for payment, unconditional after funds clear. |
| PAVA implementation | **Correct.** Verified: `fit_isotonic([0.1,0.2,0.3],[0.1,0.8,0.2]) == [0.1,0.5,0.5]`, sum preserved, output non-decreasing on adversarial input. |
| `MONEY_TOKEN` | **Passes every assertion the plan makes of it.** Verified. Also accepts `"(1234.56"` with an unbalanced parenthesis as negative — worth tightening, not worth a finding. |

---

## 11. Open decisions — operator, not executor

| Q | Decision | Why it cannot be defaulted | Status |
|---|---|---|---|
| **Q1** | **Re-verify spec §3.1 and §3.2 and re-date them.** | The header says 2025-03-11; the programme is dated August 2026. Either the QBO/Xero gates and every pinned dependency version are 17 months stale, or the spec is mis-dated. QBO-first and the licence deny-list both rest on §3. Two hours; blocks nothing else. | **Resolved (2026-08-29).** Re-verified live and re-dated in `2025-03-11-ledger-sense-design.md` §3. Findings: QBO gate unchanged (turnaround variance now wider — treat "1–3 weeks" as optimistic, not median); **Xero's flat 25-connection ceiling was replaced 2026-03-02 by a 5-tier priced model**, whose free Starter tier (5 connections, no certification) weakens but does not override the settled QBO-first call (`02` §13 Q2); dependency licences all held except `docling`/`docling-ibm-models` version bumps (latter major) and `ocrmypdf`'s Ghostscript dependency, now optional-but-auto-used rather than hard-required — REJECT still stands. |
| **Q2** | **Does `bok-core` exist by week 7?** If not, adopt the local-protocol fallback (F-61) in week 5. | 31 of 48 tasks depend on it. The plan currently discovers this in week 7. | OPEN — unknowable before week 7. Fallback (F-61) stands as written; no action needed until then. |
| **Q3** | **`DocNumber`: vendor invoice number, or idempotency key?** (F-40) | Overwriting the vendor's reference disables a control the client already has, in a product sold on catching duplicates. A client-visible product decision, not an implementation detail. | **Resolved (2026-08-29): `DocNumber` stays the vendor's invoice number.** Recorded in `2025-03-11-ledger-sense-phase-5-writeback.md`. |
| **Q4** | **Hold-back audit fraction** (F-49) — 5% proposed. | A direct trade between straight-through rate and drift detection, and the number goes in `docs/methodology.md`. Pick it deliberately. | OPEN — no objection raised to the 5% proposal; treat as the working default until Phase 6 methodology work fixes it in writing. |
| **Q5** | **Single-tenant or multi-tenant?** (F-18, F-25) | The `CLIENT` compose profile implies one client per deployment, making tenancy free. The bookkeeping-firm buyer in `02` §9 — *"they feel the pain across 20+ clients at once"* — implies the opposite. Sizing G-5 needs the answer. | **Resolved (2026-08-29): multi-tenant.** Already what `2026-08-29-ledger-sense-phase-0-platform.md` Task P2 builds — `tenant_id` on every table, 404-not-403 ownership checks, per-tenant retention. No plan change required. |
| **Q6** | **Is the week-5 controller session booked?** | Phase 3 is hard-blocked by `registry.py`. If no, Phase 4 runs first — the plan permits this, but the swap must be decided in week 5, not discovered in week 8. | **Resolved (2026-08-29): booked.** Phase 3 proceeds in its planned position; no sequencing swap needed. |
| **Q7** | **`sources.py` ships `confirmed_at=date(2026, 9, 2)` as a placeholder.** | A hardcoded plausible date inside the mechanism built to stop hardcoded plausible values. Fixed in Phase 3 Task 22 to require `None` until a real session date exists — but the operator must supply the date. | OPEN by design — code correctly requires `None` until the Q6 session actually happens; the real `confirmed_by`/`confirmed_at` values get filled in then, not before. |

---

## 12. What the audit did not find

Recorded because a review that only lists faults is not a review.

- The **payment chain A5/A6** is correct, including the ordering the spec's diagnostic got wrong. Previous
  payments come off once, after retainage, and the regression test guards it properly.
- The **C11 waiver direction** is correct, and the regression test names the danger explicitly.
- The **C8 suppression** is correctly narrow — scheduled value moved **and** work did not go backwards, both
  conditions, with a test for the blanket-exemption failure. The best rule in the document.
- The **idempotency ordering** — record `pending`, post, record `committed`, and on retry *query the remote
  system* — is right, and the force-fail test genuinely exercises it. F-39 is a gap in the reversal path, not in
  this.
- **PAVA** is correct, and the decision to write fifteen readable lines rather than take a scikit-learn
  dependency is right for this product and this buyer.
- **Export as a peer adapter** rather than a fallback is the correct structural answer to B12, and the decision
  most likely to make the product useful on day one.
- The **three-state calibration model**, and the refusal to let synthetic evidence license automation, is the
  single best idea in the plan. F-47 and F-48 are failures to live up to it, not failures of it.
