# Ledger Sense — Weeks 5–12 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a construction pay-application document-intelligence product that extracts deterministically, validates against a provenanced domain rulebook, routes uncertain fields to a keyboard-driven review queue, and writes drafts into QuickBooks Online — with no client document ever reaching a model provider.

**Architecture:** Deterministic-first extraction (text layer + geometry + table reconstruction, with a learned per-producer profile cache fed by reviewer corrections), a three-class validation rulebook where domain-convention rules cannot be registered without provenance, and confidence routing that refuses to auto-post until a reliability curve fitted on real labels exists. A model lane exists but is fenced to `SYNTHETIC`/`REDACTED` payloads and is optional.

**Tech Stack:** Python 3.12+, `uv`, `ruff`, `mypy --strict`, FastAPI, Pydantic v2, `pytest` + `hypothesis`, Postgres + `alembic`, Next.js 15 + `bok-ui`, `pypdfium2` / `pdfplumber` / `docling[rapidocr]`, Playwright, `axe-core`.

**Spec:** `docs/superpowers/specs/2025-03-11-ledger-sense-design.md` — read it before Task 1. The plan argues from the spec; executors read both.

> **REVISED 2026-08-29.** This plan was audited; sixty-five defects were found and fixed in place, and
> Phase 1 — which had been written inline in this file, so no `phase-1` document existed — was extracted to
> **`2025-03-11-ledger-sense-phase-1-foundation.md`**, where every other phase already lived. Read
> **`2026-08-29-REVISIONS.md`** before Task 1 — it carries the evidence for every change, and §11 lists seven
> decisions only the operator can make. **`2026-08-29-ledger-sense-phase-0-platform.md`** adds the platform work
> that had no task at all: authentication, tenancy, hardened intake, persistence, encryption and retention, the
> frontend scaffold, and the three CI gates the hard constraints mandate. Phase 0 runs in week 5 alongside Task 1.

---

## Global Constraints

Every task's requirements implicitly include this section. Values copied verbatim from the spec.

- **Python 3.12+**, `uv` for dependencies, `ruff` for lint+format, `mypy --strict` on every module.
- **Licence: Apache-2.0.** Permissive dependencies only — MIT / Apache-2.0 / BSD / OFL.
- **No AGPL.** `PyMuPDF` (AGPL, confirmed in its own PyPI metadata) and `ocrmypdf` (MPL-2.0 package, but requires an AGPL-3.0 Ghostscript binary) are **rejected by name**. `camelot-py` is permitted **only without its `ghostscript` extra**. `docling` is pinned as **`docling[rapidocr]`**, never `docling[easyocr]`.
- **No floats in the money path.** `Decimal` with an explicit context. Enforced by **four** independent layers (Task 3). The fourth exists because the other three are all blind to the one entry point that matters — **verified**: `Money = Annotated[Decimal, "money"]` on a Pydantic v2 model in lax mode *accepts a float and coerces it* (`LineItem(amount=1000.0) -> Decimal('1000.0')`, `0.1+0.2 -> Decimal('0.30000000000000004')`). `mypy` sees `Decimal`, the AST scan sees no literal, the model walk sees an annotation that is not `float`. Money enters through JSON request bodies, and JSON carries floats. Layer 4 is `Strict()` on `Money` plus `strict=True` on every domain model. See `2026-08-29-REVISIONS.md` F-01.
- **No unauthenticated route, no untenanted row.** Every `APIRouter` is mounted through `ledger_sense.api.deps.register`, which applies `require_session`. Every table carries `tenant_id`. Cross-tenant access returns **404, never 403**. Phase 0 Task P2; F-25.
- **No document byte is parsed outside `ingest/intake.py`**, which applies the byte, page, memory and timeout caps from Phase 0 Task P3. `00` §8 requires resource limits on anything executing untrusted input; a PDF is untrusted input. F-26.
- **No AIA form artifact anywhere in the repo.** The synthetic corpus uses our own layout with equivalent column semantics. The non-affiliation notice ships in README, docs and UI:
  > Not developed, endorsed, approved, sponsored by, or affiliated with the American Institute of Architects. AIA®, G702® and G703® are registered trademarks of the American Institute of Architects. References describe format compatibility only.
- **No model name hardcoded.** Model selection resolves through `bok-core` lanes.
- **No secrets in the repo, ever — including test fixtures.** `gitleaks` pre-commit hook.
- **$0 recurring cost.** One-off spend (the week-5 controller session, the legal review) is permitted; recurring subscriptions are not.
- **Everything runs from `docker compose up`** on a fresh machine, with only `.env.example` to copy.
- **Coverage floor: 75%.**
- **Never claim full automation.** Straight-through rate is published including the misses.
- **No CLI.** Dev entrypoint is `python -m ledger_sense`. No Typer/Click, no `[project.scripts]`.

### Boundary gate (blocks specific tasks, named where they occur)

`bok_core.boundary` must be published, and **ledger-sense's own test must assert `CLIENT_CONFIDENTIAL` raises `SensitivityViolation` against the real published package — not a mock**, before either of these ships:

1. Any code that accepts a document from outside the system (upload, email watcher, API intake).
2. Any code that calls the gateway.

Deterministic extraction developed against the synthetic corpus is **not** gated — it calls no model and touches no client data.

### The `bok-core` dependency is far wider than "Task 13" — REVISED (F-61)

The original text said the gate was *"deliberately narrow"* and that Phase 2 was not blocked. That is wrong.
**Task 18 imports `bok_core.provenance.{Claim, FileSource, Method}`. Task 28 imports `HumanSource`. Task 42
depends on both.** Everything from Task 18 onward — 31 of 48 tasks — is blocked on a package that spec §4.1
confirms does not exist and that must additionally ship five bespoke additions requested in prose.

**Resolution: apply the pattern this plan already chose once.** `rules/provenance.py` defines `RuleSource` as a
structural `Protocol` so week 5 is not blocked on `bok-core`. Do the same for the provenance primitives:

- Create **`ledger_sense/provenance.py`** in Task 4, carrying `Claim`, `Method`, `FileSource`, `HumanSource`,
  `RuleSource` and `Labelled` as `Protocol`s **plus** a local frozen-dataclass implementation of each.
- Every module imports from `ledger_sense.provenance`, never from `bok_core` directly.
- When `bok-core` publishes, `ledger_sense/provenance.py` re-exports its concrete classes instead. The
  Protocols are satisfied structurally, nothing above changes, and there is nothing to delete.
- Only `ingest/intake.py` (Task 13) imports `bok_core.boundary` directly, because the boundary is the one thing
  that must not be re-implemented locally — a local `Sensitivity` enum would enforce nothing.

The gate is then genuinely narrow: **the upload path and the gateway call**, exactly as spec §4.1 intends.

### Request these from the `backoffice-kit` session before Task 13

`bok-core` must ship five additions beyond `00` §4, specified in spec §4.1. Raise them in the `backoffice-kit`
session now, not in week 6 — but with `ledger_sense/provenance.py` in place, Task 13 is the only task that
waits on them:

1. `FileSource(path, page, bbox)` — `00` specifies "file+line", which is scanner-shaped. Without a bbox the review queue cannot box a field and the provenance rail cannot point at a cell.
2. `Claim[Decimal]` must serialise as a **string, never a float**.
3. `RuleSource(authority, citation, confirmed_by, confirmed_at)` as a `Source` variant. Task 5 consumes it structurally and does not block on it, but `bok-core` should own the concrete class.
4. `Labelled[T]` must wrap **bytes and file handles**, not only prompts.
5. `ConfidenceMeter` (bok-ui) takes a calibration **basis object** `{state, basis, curve_id, n}`, not a float — Task 41 needs three distinguishable states.

### One external check that gates nothing but changes scope

Spec §2.2: **does any free-tier, vision-capable provider state in writing that it does not train on inputs?** If no, the model lane is `SYNTHETIC`-only permanently and `REDACTED` never unlocks — a simpler product. Answer before Phase 6. No task depends on it.

---

## File Structure

```
ledger_sense/
  __main__.py                    # dev entrypoint: python -m ledger_sense
  domain/
    money.py                     # Money type, Decimal context, rounding policy
    models.py                    # Contract, PayApplication, ContinuationLine, Invoice, Waiver
    vendor.py                    # CanonicalVendor, VendorAlias, PaymentDetail history
  rules/
    provenance.py                # RuleSource protocol (structural; bok-core's class satisfies it)
    registry.py                  # register(); refuses a rule with no RuleSource
    engine.py                    # runs registered rules over a document, emits Findings
    arithmetic/foot.py           # A1  (A9 is convention: see change_orders.py)
    arithmetic/lines.py          # A2, A3, A4
    arithmetic/payment.py        # A5, A6, A7, A8
    integrity/duplicates.py      # I1
    integrity/vendors.py         # I2
    integrity/bank_details.py    # I3
    convention/retainage.py      # C1-C3
    convention/stored.py         # C4-C6
    convention/change_orders.py  # C7, C8, A9   (A9 reclassified, F-04)
    convention/tax.py            # C9, C10
    convention/waivers.py        # C11-C14
  baseline/
    study.py                     # sampling frame, timing instrument, loaded-rate model
    report.py                    # print-ready measured-cost report with confidence interval
  corpus/
    layouts.py                   # OUR layouts. No AIA form geometry, ever.
    generate.py                  # emits pay applications at known ground truth
    errors.py                    # injected error classes, one per validation rule
  ingest/
    intake.py                    # the single labelled door for external documents
    classify.py                  # rules -> document type
    fingerprint.py               # PDF Producer + layout hash -> profile key
  extract/
    textlayer.py · tables.py · anchors.py · profiles.py · ocr.py · assemble.py
  route/
    calibration.py · router.py
  writeback/
    interface.py · qbo.py · export.py · idempotency.py
  api/                           # FastAPI
  web/                           # Next.js 15 + bok-ui

scripts/
  licence_audit.py               # SPDX deny-list + native-layer check
  check_no_float.py              # AST scan of domain/, rules/, writeback/
  aia_guard.py                   # no form artifacts; notice present
tests/
  mirrors the package tree; plus tests/boundary/ and tests/property/
docs/
  methodology.md                 # written incrementally: weeks 5, 11, 12
```

### One design refinement, decided here

The spec says `extract/` emits `Claim`s and no bare value crosses into `domain/`. Implemented as: **domain models carry typed `Money` fields *plus* a `provenance: dict[str, Claim]` sidecar.** Rules compute on the values; findings cite `provenance[field_name]`; the UI renders every figure through its claim. Week 5 builds models with an empty provenance dict, week 7 populates it — **no rewrite, and no generic-over-Claim explosion in the rule code.**

`RuleSource` is consumed as a **structural Protocol** (`rules/provenance.py`), so week 5 is not blocked on `bok-core`. When `bok_core.provenance.RuleSource` lands it satisfies the Protocol with no change. This is a structural type, not a shim — there is nothing to delete later.

---

# Phase documents

**Every phase has its own step-level plan document.** This file carries the Global Constraints and File
Structure that all of them inherit, plus the index below. Read this file first, then the phase file.

| Phase | Weeks | Tasks | Document |
|---|---|---|---|
| **0 — Platform** | **5, concurrent with Task 1** | **P1–P7** | **`2026-08-29-ledger-sense-phase-0-platform.md`** |
| 1 — Foundation | 5–6 | 1–13 | `2025-03-11-ledger-sense-phase-1-foundation.md` |
| 2 — Extraction | 7 | 13b, 14–21 | `2025-03-11-ledger-sense-phase-2-extraction.md` |
| 3 — Rules | 8 | 22–27 | `2025-03-11-ledger-sense-phase-3-rules.md` |
| 4 — Review UI | 9 | 28–33 | `2025-03-11-ledger-sense-phase-4-review-ui.md` |
| 5 — Writeback | 10 | 34–39 | `2025-03-11-ledger-sense-phase-5-writeback.md` |
| 6 — Routing & close-out | 11–12 | 40–48 | `2025-03-11-ledger-sense-phase-6-routing-closeout.md` |

The audit register `2026-08-29-REVISIONS.md` is normative over all of them.

---

# PHASE 1 — Weeks 5–6: Foundation


**Phase 1 has its own step-level plan document:** `2025-03-11-ledger-sense-phase-1-foundation.md`.

It was written inline in this file until 2026-08-29, which made it the one phase nobody could find — every
other phase is a sibling document, and a reader looking for `phase-1` found nothing. The step-level TDD cycles,
test code, implementation and commits now live there. The table below is the index.

**Nothing in Phase 1 is blocked by `bok-core` except Task 13.** `ledger_sense/provenance.py` (Task 4) removes
the rest of the dependency — see Global Constraints, F-61.

| Task | Files | Verification |
|---|---|---|
| **1. Repo scaffold + licence-audit gate** | `pyproject.toml`, `scripts/licence_audit.py`, `.github/workflows/ci.yml`, `tests/test_licence_audit.py` | Ships **before any dependency is installed** — its value is preventing adoption, not documenting it. Reads the real environment (`uv.lock` + `importlib.metadata`), **parses** the SPDX expression rather than substring-matching it, and runs `ldd`/`otool` over installed extension modules for the native layer. **F-33, F-34** |
| **2. Money** | `ledger_sense/domain/money.py`, `tests/domain/test_money.py` | `Decimal` with an explicit context; `RoundingPolicy` supplied by the `Contract`, never a module constant; HALF_UP, not bankers'. Rejects `float`, and rejects `bool` before the `float` check. **F-02** |
| **3. No-float enforcement — four layers** | `scripts/check_no_float.py`, `tests/test_no_floats.py` | AST literal scan; `mypy --strict`; a model-field walk; **and `Strict()` on `Money` plus `strict=True` on every domain model.** Layer 4 exists because the other three are all blind to the one entry point that matters — a JSON request body. **F-01, verified by execution** |
| **4. Domain models** | `ledger_sense/domain/models.py`, `tests/domain/test_models.py` | `Contract`, `PayApplication`, `ContinuationLine`, `Invoice`, `LienWaiver`; the per-instance `provenance` sidecar; `balance_to_finish` and `percent_complete` as real fields; `adjusted_contract_sum`. Money serialises as a **string**, never a float. **F-03, F-15** |
| **5. Rule registry** | `ledger_sense/rules/{provenance,registry}.py`, `tests/rules/test_registry.py` | `register()` raises **at import time** on a `convention` rule with no `RuleSource`, or one whose `confirmed_by` names nobody. This is the mechanism that hard-blocks Phase 3 on the controller session; it is not a formality you can route around |
| **6. A1 — footing** | `ledger_sense/rules/engine.py`, `ledger_sense/rules/arithmetic/foot.py`, tests | Exact equality, no tolerance: a penny is a finding. **A9 is not here.** It reads as first-principles checkable and is not — it fires on the normal mid-period state — so it is a convention rule and ships in Phase 3 Task 24. **F-04** |
| **7. A2, A3, A4 — per-line** | `ledger_sense/rules/arithmetic/lines.py`, tests | Components sum to the line total; the total stays within the scheduled value; work billed against a zero scheduled value trips. These are the rules the spec calls A3 and A4 — the original code implemented something else. **F-03** |
| **8. A5–A8 — the payment chain** | `ledger_sense/rules/arithmetic/payment.py`, tests | Previous payments come off **once**, and **after** retainage. Survived audit intact, including the ordering the spec's own diagnostic got wrong |
| **9. `hypothesis` property tests** | `tests/property/strategies.py`, `tests/property/test_arithmetic_invariants.py` | A strategy yielding internally consistent `(Contract, PayApplication)` pairs; any rule that fires on one is a false positive. 300 examples per invariant. Phase 3 Task 24 adds A9 to the list |
| **10. Baseline study** | `ledger_sense/baseline/study.py`, `tests/baseline/test_study.py` | Sampling frame, timing instrument, loaded-rate model **carrying its own uncertainty**, and a **complete** Student's *t* table — the original truncated at df 9 and fell back to 1.96, understating the margin by 15.4% at df 9, always in the direction that flatters the vendor. Refuses an interval below the stated n. **F-52, F-53, F-54** |
| **11. Baseline report + methodology** | `ledger_sense/baseline/report.py`, `docs/methodology.md`, tests | Never a point estimate without its interval; the method statement embedded verbatim; print stylesheet; no automation claim. Opens the **single** `docs/methodology.md` schema that Phase 6 Task 46 completes. **F-57** |
| **12. Synthetic corpus + AIA guard** | `ledger_sense/corpus/{layouts,errors,generate}.py`, `scripts/aia_guard.py`, tests | Five layouts in **our own** geometry; the summary block actually rendered and asserted; **no summary label colliding with a column header**; every error injector Phase 6 Task 47's demo names. The non-affiliation notice has **one** definition (Phase 0 Task P1). **F-09, F-10, F-55, F-62** |
| **13. `bok-core` boundary — GATED** | `ledger_sense/ingest/intake.py`, `tests/boundary/` | `CLIENT_CONFIDENTIAL` raises `SensitivityViolation` **against the real published package, not a mock**; the AST guard resolves imports through an alias map before matching, so it cannot be evaded by renaming. **DoD 8. F-32** |

**Phase 1 exit criteria are in the phase document.** This table is the index, not the specification.

---

# PHASES 2–6 — Weeks 7–12

The tables below are the index. See **Phase documents** above for the file each one links to.
---

## Phase 2 — Week 7: Extraction, plus the deferred baseline screen

| Task | Files | Verification |
|---|---|---|
| **13b. Baseline study screen** (spec §8 item 1; Tasks 10–11 built the engine and the printable report, this is the interactive instrument) | `ledger_sense/api/baseline.py`, `ledger_sense/web/app/baseline/` | Volume counter; a start/stop timing instrument writing `TimedSample` rows; loaded-rate input; exception log. Playwright: time three documents, enter a loaded rate, produce the Task-11 report with a real interval. **Runs before the pipeline exists — it is what you sell in week one, and it is the DoD 11 outreach asset** |
| **14. Text layer + coordinates** | `ledger_sense/extract/textlayer.py`, `tests/extract/test_textlayer.py` | Extract every word with its bbox from a Task-12 generated PDF; assert positions match the layout constants in `corpus/layouts.py` within 1pt |
| **15. Document fingerprint** | `ledger_sense/ingest/fingerprint.py`, `tests/ingest/test_fingerprint.py` | Two documents from the same generator layout produce the same key; a different layout produces a different key. Uses PDF `Producer` metadata plus a hash of column x-offsets |
| **16. Continuation-sheet table reconstruction** | `ledger_sense/extract/tables.py`, `tests/extract/test_tables.py` | Reconstruct a grid from ruling lines and column alignment; map columns by header semantics; assert every cell matches `corpus.ground_truth()` across 20 seeds |
| **17. Summary anchor + geometry extraction** | `ledger_sense/extract/anchors.py`, `tests/extract/test_anchors.py` | Locate each labelled field by anchor text and take the geometrically adjacent value; assert against `ground_truth()` |
| **18. Claim assembly** | `ledger_sense/extract/assemble.py`, `tests/extract/test_assemble.py` | Every extracted field emits a `Claim` with `method=DETERMINISTIC`, `confidence=None`, `FileSource(path, page, bbox)`. AST test (pattern as in Phase 1 Task 13 step 1) asserting no bare value is constructed into `domain/` outside `assemble.py` |
| **19. Learned profile cache** | `ledger_sense/extract/profiles.py`, `tests/extract/test_profiles.py` | A simulated reviewer correction on an unseen layout writes geometry to the cache; the next document with the same fingerprint extracts that field without correction. Assert the cache persists **geometry only, never a value** |
| **20. Classification + OCR tail** | `ledger_sense/ingest/classify.py`, `ledger_sense/extract/ocr.py`, tests | Classifier assigns the right type for each corpus doc type; the OCR path returns an explicit `CannotExtract` for a rasterised input rather than a low-confidence guess |
| **21. Degraded-mode proof** | `tests/test_degraded_mode.py` | With every provider disabled, the pipeline extracts and validates digital-PDF pay applications end to end. **DoD 2** |

## Phase 3 — Week 8: Convention and integrity rules

**Blocked on the week-5 controller session closing.** `registry.py` enforces this — an unsourced convention rule raises at import time.

| Task | Files | Verification |
|---|---|---|
| **22. Retainage C1–C3** | `ledger_sense/rules/convention/retainage.py`, tests | Rate read from `Contract`, never a constant. Stepping fires at the contract's own milestone. Tripping fixture + clean control per rule. `RuleSource.confirmed_by` names the controller |
| **23. Stored materials C4–C6** | `ledger_sense/rules/convention/stored.py`, tests | Double-count fixture trips C4; a legitimate stored→installed transition across two periods trips nothing; stored-without-backup trips C6 |
| **24. Change orders C7–C8, and A9** | `ledger_sense/rules/convention/change_orders.py`, tests | C8 **must not** fire when an additive change order raised the scheduled value in the same period — the false-positive regression test is mandatory. **A9 lands here, not in Phase 1** (F-04): it fires on the normal mid-period state, so it is a convention rule and needs a `RuleSource` and its own suppression |
| **25. Tax C9–C10, one jurisdiction** | `ledger_sense/rules/convention/tax.py`, tests | The configured jurisdiction validates; every other jurisdiction returns an explicit "tax rules not configured for this jurisdiction" finding rather than passing silently |
| **26. Waivers C11–C14** | `ledger_sense/rules/convention/waivers.py`, tests | **C11 regression test:** conditional-for-payment passes; unconditional-before-funds-clear trips. Assert the module generates no document — extract, validate, flag, stop |
| **27. Integrity I1–I3** | `ledger_sense/rules/integrity/{duplicates,vendors,bank_details}.py`, `ledger_sense/domain/vendor.py`, tests | Duplicate across full history by three fingerprints; vendor first-match requires confirmation and merges reverse cleanly with alias provenance; **bank-detail change is a hard stop — assert no code path updates a payment record without an explicit human confirmation token.** **DoD 3** |

## Phase 4 — Week 9: Review queue and reconciliation

| Task | Files | Verification |
|---|---|---|
| **28. Review-queue API** | `ledger_sense/api/review.py`, tests | Exception list, accept/edit/reject transitions, each writing a `HUMAN`-method Claim |
| **29. Split-view document renderer** | `ledger_sense/web/app/review/`, `pdf.js` | Document at fidelity, field under review boxed from its Claim's bbox, everything else dimmed |
| **30. Keyboard model** | same, Playwright test | `J`/`K`/`Enter`/`E`/`R`. **Playwright clears fifty exceptions with zero mouse events. DoD 5** |
| **31. Accumulating verification rule** | same | An accepted field's box fills with a thin green rule that persists across navigation |
| **32. Reconciliation view** | `ledger_sense/web/app/reconciliation/` | Continuation lines against the summary, discrepancy highlighted at the exact cell, arithmetic inline. Print stylesheet |
| **33. Accessibility** | CI job | `axe-core`, zero serious/critical, at 375px and in all three densities |

## Phase 5 — Week 10: Writeback

Highest-consequence code in the product. Sandbox only; the QBO production gate is not on the critical path.

| Task | Files | Verification |
|---|---|---|
| **34. Writeback port** | `ledger_sense/writeback/interface.py`, tests | `Protocol` with `create_draft_bill`, `is_period_closed`, `reverse`. Two adapters satisfy it |
| **35. Idempotency keys** | `ledger_sense/writeback/idempotency.py`, tests | **Force-fail mid-write and retry; assert exactly one bill exists.** Key derived from document hash + contract + application number |
| **36. QBO adapter** | `ledger_sense/writeback/qbo.py`, tests | Against sandbox. Creates **unapproved** bills only — assert no code path posts an approved transaction. OAuth tokens encrypted at rest with rotation |
| **37. Closed-period refusal** | same | Reads the book-close date first; refuses with an explanation. Test asserts a refusal, not a caught exception |
| **38. Reversal** | same | One-click reversal restores prior state; audit trail records document, approver, timestamp. **DoD 6** |
| **39. CSV/IIF export adapter** | `ledger_sense/writeback/export.py`, tests | Peer adapter, not a fallback. Golden-file diff. Makes the product useful with zero API approval |

## Phase 6 — Weeks 11–12: Routing, calibration, close-out

| Task | Files | Verification |
|---|---|---|
| **40. Calibration machinery** | `ledger_sense/route/calibration.py`, tests | Isotonic fit, curve storage, reliability diagram render, threshold derivation from a stated target |
| **41. Three calibration states** | `ledger_sense/route/router.py`, tests | `uncalibrated` **and** `calibrated:synthetic` both route everything to review. Only `calibrated:field` derives a threshold. Test asserts no constant threshold exists anywhere in the module. **DoD 4** |
| **42. Reviewer corrections become labels** | `ledger_sense/route/calibration.py` | Each `HUMAN` Claim from Task 28 appends a label; assert `calibrated:field` becomes reachable at a stated n |
| **43. Inbox** | `ledger_sense/web/app/inbox/` | Drag-and-drop routed through `intake.accept_document` only. Email watcher is the first cut if week 9 or 10 slipped |
| **44. Vendor ledger** | `ledger_sense/web/app/vendors/` | Payment-detail history as a red-lined diff with date and source document. The screen is the first cut; the Task-27 data model is not |
| **45. Metrics** | `ledger_sense/web/app/metrics/` | Every figure a `Claim` opening the rail. **Straight-through rate published including misses**, caveat stating sample and period. Print stylesheet |
| **46. `docs/methodology.md` complete** | `docs/methodology.md` | Baseline method (Task 11), calibration basis (Task 40), straight-through rate with misses (Task 45). **DoD 9** |
| **47. The sixty-second demo** | `docs/demo.md`, `scripts/demo.py` | Twenty synthetic pay applications in sixty seconds; the queue catches a duplicate, a retainage miscalculation and a stored-materials double-count; straight-through rate shown honestly. **DoD 7** |
| **48. Close-out** | README, `docker compose up` on a clean machine | Non-affiliation notice in README, docs and UI. `aia-guard` and `licence-audit` green. Coverage ≥75%. **DoD 10** |

---

## Not a build task, but on the plan: DoD 11

**Half a day every week from week 5** — roughly forty hours across the phase — on deliberate
partner-finding, with the week-5 baseline study as the outreach asset. This is the only DoD item
no amount of coding satisfies and the one most likely to be unmet at week 12.

If a partner signs, spec §9 states exactly what changes. Two items move immediately: the week-5
controller session becomes their onboarding call, and **the data agreement plus the three-clause
legal review must complete before their corpus arrives** — you become a data processor on document
one, and document one can precede any invoice.

**After roughly week 10, do not re-target the pipeline at real documents.** A late partner
contributes DoD 11 and the baseline case study; re-targeting extraction in week 11 costs weeks
11–12 and finishes nothing.

---

## Cut order if weeks 9–10 slip

In sequence: **vendor ledger screen** (keep the Task-27 data model) → **inbox email watcher**
(drag-and-drop only) → **OCR tail** (route all scanned documents to review, which is the honest
behaviour anyway).

**Never cut:** the no-float tests (Task 3, **all four layers**), the writeback safety tests (Tasks 35, 37, 38),
the authentication and tenancy tests (Phase 0 Task P2), the intake caps (Phase 0 Task P3), the non-affiliation
notice, `docs/methodology.md`.

**On Task 13 — REVISED (F-60).** The original list said *"never cut the boundary test"* while Task 13 itself is
headed **GATED** with *"if it has not landed, skip to Phase 2."* Both cannot be true. The real rule:

> The boundary test is not cuttable **for schedule**. It is blocked only by `bok-core` not existing. If
> `bok-core` slips, **the upload path slips with it** — no code that accepts an externally-supplied document
> ships without the boundary test green against the real published package. Everything else proceeds, because
> `ledger_sense/provenance.py` (see Global Constraints) removes the rest of the dependency.

A gate you can route around is not a gate. What you may defer is the *feature*, never the *test*.
