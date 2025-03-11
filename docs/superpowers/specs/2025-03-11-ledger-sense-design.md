# Ledger Sense — Design

**Status:** approved 2025-03-11 · **Scope:** weeks 5–12 · **Repo:** `ledger-sense` · **Licence:** Apache-2.0
**Depends on:** `../Starting_Documents/00-SHARED-FOUNDATION.md`, `../Starting_Documents/02-LEDGER-SENSE.md`

Vertical document intelligence with confidence routing, for construction progress billing.

> *"I will measure what your current process actually costs you in week one, then automate
> the part that is safe to automate and show you the exceptions I caught that a human
> would have missed."*

---

## 1. The two corrections this design is built around

Both override the source report the briefs were derived from. They are load-bearing, not caveats.

1. **This is not a vision-model-first product.** $0 budget → free-tier inference → the provider
   may train on inputs → a subcontractor invoice carrying bank details is `CLIENT_CONFIDENTIAL`
   and cannot go there. **Extraction is deterministic-first.** The model lane is fenced to
   `SYNTHETIC` or `REDACTED` payloads only, and `REDACTED` additionally requires the client's
   written opt-in plus `bok-core`'s redaction verification pass.
2. **AIA G702/G703 are copyrighted and actively enforced.** No form artifact appears anywhere in
   this repo — not in the corpus, not in the demo, not in the docs. The synthetic corpus uses
   **our own layout** with equivalent column semantics: mimic the column math, never the form
   design. The non-affiliation notice ships with the product.

The first correction is a sales asset, not a limitation. *"Your documents never leave your
infrastructure and are never sent to a model provider"* is a sentence no enterprise AP vendor
can say. Lead with it.

---

## 2. Decisions settled (do not re-litigate)

### 2.1 From the briefs' open-decision sections

| Ref | Decision | Rationale |
|---|---|---|
| `02` §13 Q1 | **Synthetic-only is the baseline path through week 12.** A partner is an upgrade that re-sequences, never a prerequisite that blocks. | See §9. |
| `02` §13 Q2 | **QuickBooks Online first.** Xero deferred to v2 behind the same port. | Evidence in §3. One-sided, not a preference. |
| `02` §13 Q3 | **Pay applications first.** | The rulebook split (§6) removes the domain risk that motivated the alternative. Invoices-first arrives at something indistinguishable from horizontal AP — the B10 comparison that loses. |
| `02` §13 Q4 | **Architect for air-gapped, do not productise it.** | The compose-only CLIENT profile already *is* an air-gapped deployment. Marginal engineering cost ≈ 0. Do not price or promise a support SLA on a deployment you cannot reach. |
| `02` §13 Q5 | **Lawyer before the first paid engagement** — review, not bespoke drafting, scoped to three clauses: fraud-detection limitation of liability, data processing/retention/deletion, IP ownership of the rulebook. Fraud stance is settled by the brief: surface, require human confirmation, never auto-approve, client responsible for verification. | B5. Moves earlier the day a partner signs (§9). |
| `02` §13 Q6 / `00` §12 Q4 | **Split by data class.** Two compose profiles: `CLIENT` = compose-only Postgres, no third-party processor ever; `DEMO` = synthetic corpus only, hostable anywhere. | Keeps *"your documents never leave your infrastructure"* literally true while preserving a click-a-link demo. Extends the existing `SYNTHETIC` / `CLIENT_CONFIDENTIAL` boundary to storage. |
| `02` §13 Q7 | **Domain knowledge is low.** Immersion block opens week 5; no convention rule is written before it closes; every rule carries provenance. | §6.4. Established by diagnostic — see §6.1. |
| `00` §12 Q1 | `bok-core` (PyPI) and `bok-ui` (npm) are **published packages**, not vendored. | Given. |
| `00` §12 Q2 | **Four independent CLIs; Ledger Sense ships none.** Dev entrypoint is `python -m ledger_sense` — stdlib, no Typer/Click, no `[project.scripts]`. | Buyer entrypoint is the web UI. Only `agent-perimeter` ships a real CLI, because CI is its distribution channel. |
| `00` §12 Q5 | **Public from commit one.** | No audience exists; the repo is the distribution. The moat is domain correctness and the partner relationship, not source. Requires: no client data or partner-identifying rule detail ever committed — enforced by `gitleaks` plus a written CLIENT-DATA-NEVER policy. |
| `00` §12 Q6 | **Apache-2.0 everywhere.** | This project rejects two dependencies for being AGPL and ships CI that fails the build on AGPL. Shipping an AGPL product behind an AGPL deny-list is a contradiction to explain in every sales call. Apache also preserves cross-repo reuse and carries the patent grant. |
| `00` §12 Q7 | **35–40 h/week, weeks 5–12.** | Given. |

### 2.2 `00` §12 Q3 — provider inventory

Ledger Sense needs **one** verified fact, not the full sweep:

> Does any free-tier, vision-capable provider state **in writing** that it does not train on inputs?

- **Yes** → `REDACTED` can eventually unlock, with client written opt-in.
- **No** → the model lane is **`SYNTHETIC`-only, permanently**; `REDACTED` never unlocks. This is a
  *simpler* product and the design accommodates it with no change.

Either answer is compatible with everything below. The full provider inventory is a `bok-core`
task; a provider outage costs this product nothing because the deterministic path carries it.

Per-provider checks, from the machine that will run this, on the account that will be used —
signing up, not reading marketing: availability at location (B1 geofencing is silent); exact
RPM/TPM/RPD today, screenshotted and dated; **the training-on-inputs sentence, quoted with URL
and date**; the commercial-use sentence (B3 — Cohere's trial forbids it); card required?; vision
on the *free* tier specifically. Google AI Studio is the decisive one: its free tier's "may be
used to improve Google products" language is what disqualifies the vision lane for real
documents. If that sentence has changed, part of §1's argument changes with it.

---

## 3. Verified external facts (2025-03-11)

Re-verify on the day. These are snapshots, which is the operating principle of the programme.

### 3.1 Accounting platform gates

**QuickBooks Online**
- Production keys gated on an **app assessment questionnaire** — legal, tech, security, platform.
  Required for every app touching production data, listed or not.
- **No App Store listing is required for production keys.** A private, unlisted app can be pointed
  at real QBO companies. This is the critical unblock.
- Intuit's material: ~30–40 minutes to complete; "in most cases the assessment process is fast."
  Integrator write-ups report 1–3 weeks end to end. Covers hosting, host domain, launch and
  disconnect URLs, token storage, retention policy, API usage, authorisation, error handling.
- **Sandbox requires no approval at all.**

**Xero**
- Uncertified app: **25 permanent active connections** (demo company excluded). Any one
  organisation may connect at most **2 uncertified apps**.
- Beyond 25 → **App Partner certification**: security assessment reviewed by Xero's security team,
  App Store listing, checkpoints across branding/naming/scopes/error handling/data integrity/tax,
  annual re-certification, live customer connections onboarded first. Reported to take **months**.
  ≥1000 connections triggers an advanced security assessment.
- The clean single-org path is **Custom Connections** (client_credentials, one org, no
  certification) — but it is a **paid subscription** (~$5/mo USD, £5 GBP, $10 AUD/NZD), sold only
  in AU/NZ/UK/US. **Breaches the $0 recurring hard constraint and geofences.**

**Caveat:** `developer.xero.com` is a JS SPA; every direct fetch path timed out during
verification. Xero figures come from Xero Central, Xero Community, and three independent
integrator docs that agree. **Verify from a browser before committing money.**

**Consequence:** QBO first. Its gate is a self-service form; Xero's is either an unschedulable
certification or a constraint breach. **Nothing in weeks 5–12 depends on the QBO gate, because
sandbox is ungated** — so the plan never waits on an approval (B12 satisfied structurally).

### 3.2 Licence status of every proposed PDF/OCR dependency

**Clean — adopt:**

| Package | Version checked | Licence |
|---|---|---|
| `pdfplumber` | 0.11.10 | MIT |
| `pdfminer.six` | 20260107 | MIT |
| `pypdf` | 6.16.2 | BSD-3-Clause |
| **`pypdfium2`** | 5.13.0 | BSD-3-Clause + Apache-2.0 |
| `docling` | 2.123.0 | MIT |
| `docling-core` / `-parse` / `-ibm-models` | 2.92.0 / 7.16.0 / 3.14.0 | MIT |
| weights `ds4sd/docling-models` | — | CDLA-Permissive-2.0 + Apache-2.0 |
| weights `ds4sd/docling-layout-heron` | — | Apache-2.0 |
| `rapidocr-onnxruntime` | 1.4.4 | Apache-2.0 |
| Tesseract + `pytesseract` / `tesserocr` | 0.3.13 / 2.11.0 | Apache-2.0 / MIT |
| `img2table` | 2.0.0 | MIT |
| `camelot-py` | 2.0.0 | MIT (see flag 3) |
| `opencv-python-headless` | 5.0.0.93 | Apache-2.0 |
| `spaCy` | 3.8.16 | MIT |
| GLiNER + `urchade/gliner_*-v2.1` weights | 0.2.28 | Apache-2.0 (weights verified on HF, not assumed) |
| `pdf.js` (review-queue renderer) | — | Apache-2.0 |

**Flagged:**

1. **`PyMuPDF` 1.28.2 — AGPL, confirmed. REJECT.** Its own PyPI metadata reads *"Dual Licensed -
   GNU AFFERO GPL 3.0 or Artifex Commercial."* `pypdfium2` is the drop-in.
2. **`ocrmypdf` 17.10.0 — REJECT, and this is the trap.** The package is MPL-2.0, so a scan of
   Python metadata passes it. But it **requires an external Ghostscript binary, and Ghostscript is
   AGPL-3.0** — same rightsholder as PyMuPDF (Artifex), who litigate this question
   (*Artifex v. Hancom*). The AGPL arrives through a native dependency no Python tool reports.
3. **`camelot-py` 2.0.0 — safe with a condition.** Camelot 2.0 moved Ghostscript to an optional
   extra and defaults to `pypdfium2` with no system dependencies. Safe **only** while the
   `ghostscript` extra is never installed.
4. **EasyOCR — avoid.** Code is Apache-2.0, but the bundled CRAFT detector weights carry
   research-use ambiguity. Docling 2.123 makes OCR engines *optional extras* — a base
   `pip install docling` pulls no OCR engine — so pin **`docling[rapidocr]`** and never
   `docling[easyocr]`.

**Design consequence:** a **`licence-audit` CI job** with an SPDX deny-list (AGPL / GPL /
non-commercial) that **also checks the native and system layer, not just Python metadata**. Flags
2 and 4 are exactly what a metadata-only scan waves through. This job lands in **week 5, before
any dependency is installed** — its value is preventing adoption, not documenting it.

---

## 4. Architecture

### 4.1 The `bok-core` contract

`backoffice-kit` does not exist. This design treats `bok-core` as a **contract specified here and
built there**. Nothing in this repo builds the substrate.

**Hard prerequisite.** The boundary gates exactly two paths:

1. **Any code that accepts a document from outside the system** (upload, email watcher, API intake).
2. **Any code that calls the gateway.**

Neither may ship until `bok_core.boundary` is published **and ledger-sense's own test suite**
asserts `CLIENT_CONFIDENTIAL` raises `SensitivityViolation` against the real published package —
not a mock, not a stub. Ledger Sense re-tests the boundary itself rather than trusting it. This is
B2: the code must stop you at 2am in week 11, and a mocked boundary stops nothing.

The gate is deliberately **narrow**. Deterministic extraction developed against the synthetic
corpus calls no model and touches no client data, so the boundary has nothing to enforce there and
that work is not gated. An over-broad gate is one that gets rationalised around; a true one holds.

**Five additions required beyond `00` §4**, each because this product needs something the
code-shaped `00` spec does not cover:

| # | Addition | Why |
|---|---|---|
| 1 | **`Source` needs a document coordinate**: `FileSource(path, page, bbox)` | `00` specifies "file+line", which is scanner-shaped. Without a bounding box the review queue cannot box the field on the document and the provenance rail cannot point at a cell — killing the signature screen and the signature component at once. |
| 2 | **`Claim[Decimal]` must serialise as a string, never a float** | A generic `Claim[T]` gets this wrong by default; `model_dump_json` is where the no-floats rule dies quietly. Tested in `bok-core`, re-tested here. |
| 3 | **`RuleSource(authority, citation, confirmed_by, confirmed_at)`** as a `Source` variant | A validation rule is a claim about the domain and carries provenance in the same primitive as everything else. See §6.4. |
| 4 | **`Labelled[T]` wraps bytes and file handles**, not only prompts | The boundary applies where a file is *opened*. If the label attaches late, the leak happens early. |
| 5 | **`ConfidenceMeter` takes a calibration basis object** `{state, basis, curve_id, n}`, not a float | "Calibrated on synthetic corpus only" must render differently from "calibrated on 400 real labelled fields". See §7. |

### 4.2 Module layout

```
ledger_sense/
  ingest/      classify.py · fingerprint.py          # doc type; Producer + layout hash
  extract/     textlayer.py · tables.py · anchors.py
               profiles.py · ocr.py · assemble.py     # emits Claims, never bare values
  domain/      money.py · models.py · vendor.py       # Decimal context; typed pay application
  rules/       arithmetic/ · integrity/ · convention/
               registry.py · engine.py
  route/       calibration.py · router.py
  writeback/   interface.py · qbo.py · export.py · idempotency.py
  baseline/    study.py · report.py                   # the billable week-one artifact
  corpus/      generate.py · errors.py · layouts.py   # our layouts, never AIA
  api/                                                # FastAPI
  web/                                                # Next.js 15 + bok-ui
```

Three boundaries stated explicitly:

- **`extract/` emits `Claim`s and nothing else.** No bare value crosses into `domain/`. This makes
  every number on every screen openable in the provenance rail *structurally*, rather than by
  remembering to.
- **`rules/registry.py` refuses to register a rule without a `RuleSource`.** The §13 Q7 mitigation
  enforced in code, on the same pattern and for the same reason as the data boundary. A rule
  sourced from a web search cannot be registered, therefore cannot ship.
- **`writeback/interface.py` is a port; `qbo.py` and `export.py` are adapters.** Xero becomes a
  third adapter in v2 with no change above the port. CSV/IIF export is a **peer adapter, not a
  bolted-on fallback** — which is what makes B12 survivable.

### 4.3 Extraction flow

```
document
  └─ ingest/classify        rules → G702-format | G703-format | invoice | waiver
  └─ ingest/fingerprint     PDF Producer metadata + layout hash → profile key
       ├─ profile hit  → extract/profiles   cached geometry, high yield
       └─ profile miss →
            G703-format → extract/tables    grid from ruling lines + column alignment,
                                            columns mapped by header semantics
            G702-format → extract/anchors   label text + geometric adjacency
            scanned     → extract/ocr       RapidOCR, low yield, honest "cannot extract"
  └─ extract/assemble       → Claim[Decimal] with FileSource(page, bbox)
  └─ rules/engine           → Findings
  └─ route/router           → auto | review
       └─ reviewer corrects → geometry written back to extract/profiles
```

**The last line is the commercial one.** The review queue is the training signal for the
deterministic extractor. A reviewer correcting a field on an unseen layout teaches the profile
cache, so the next document from that producing software extracts cleanly. No model, no inference
cost, entirely inside the boundary — and *"the product gets better as you use it"* becomes a true
statement without an asterisk, in a category where it is usually a lie about a model.

**Why this shape.** The current G702/G703 edition is the 1992 revision and dozens of construction
accounting products print onto it, so real documents vary enormously in geometry while being
semantically identical. Pure template profiles are blocked by B3 (no documents to author them
from). Pure anchor+geometry never improves. The hybrid handles unseen layouts on day one and
converges on the layouts the client actually sends.

---

## 5. Money

**`Decimal` with an explicit context, not integer minor units.** Pay applications are
percentage-heavy — percentage complete, retainage rate, unit price × quantity — and minor units
force scaling gymnastics exactly where rounding matters. `Money = Annotated[Decimal, ...]`, one
context, `ROUND_HALF_UP`.

**The rounding policy is a field on the `Contract` record, not a global constant.** Percentage
complete is computed "to the contract's stated rounding" — rounding is a *contract term*.
Hardcoding it is the same class of error as the invented retainage constant in §6.1: a plausible
default standing in for a negotiated value.

**No floats, enforced in three layers** because one is insufficient:

1. `mypy --strict` on `Money` — catches assignment.
2. A ruff AST check flagging `float(` and float literals in `domain/`, `rules/`, `writeback/`.
3. A test walking every Pydantic model in `ledger_sense.domain`, asserting no field annotation
   resolves to `float` — catches the serialisation case the other two miss.

**`hypothesis` invariants** over randomly generated schedules of values: per-line sums; footing to
summary; balance-to-finish complement; the payment-due chain; per-line retainage summing to
summary retainage; and no operation producing more decimal places than the contract's rounding.

---

## 6. The validation layer — the moat

### 6.1 Why the rulebook is split by provenance

A diagnostic run during design established the failure mode. Eight plausibly-worded validation
rules were drafted; **four were wrong**, and the author did not notice writing one of them
backwards *during an exercise about not getting them wrong*. The wrong ones:

- *"Retainage is 10%, stepping to 5% at 50% completion"* — retainage is a **contract term**. Flat
  5%, flat 10%, 10% with no step, 0% on stored materials, release at substantial completion are all
  common, and several states cap it statutorily with different limits for public and private work.
  Hardcoded, this fires false positives on most real contracts.
- *"Total earned less retainage = completed and stored − retainage − previous payments; payment due
  then adjusted for change orders"* — wrong twice. Previous payments come off **after**, to reach
  current payment due; folded in early they are subtracted twice, and the bill is wrong by the
  entire prior-payments figure. Change orders are already inside the adjusted contract sum.
- *"Percentage complete must never decrease"* — an additive change order raises a line's scheduled
  value, so percentage complete legitimately falls. As an absolute rule it flags every change order.
- *"Unconditional waiver is exchanged for the payment; conditional once funds clear"* — **reversed**.
  The conditional waiver is exchanged for the payment, conditioned on it clearing, which is what
  protects the claimant if the cheque bounces. The unconditional goes out only after funds clear,
  because it extinguishes the lien right outright. As written it advises a subcontractor to
  surrender lien rights before being paid.

These rules are *fluent* to state and *silent* when wrong. Nothing in the sentences looks broken.
They would pass code review and pass tests written from the same misunderstanding, then fail in
front of a controller. Hence: **provenance, enforced by the registry.**

### 6.2 Arithmetic rules — verifiable from first principles, build immediately

| | Rule |
|---|---|
| A1 | Continuation-sheet total completed-and-stored foots **exactly** to the summary. Zero tolerance; a discrepancy is always a finding. |
| A2 | Per line: previous + this period + stored = total completed and stored |
| A3 | Balance to finish = adjusted scheduled value − total completed and stored |
| A4 | Percentage complete = total completed and stored ÷ adjusted scheduled value, at the contract's rounding |
| A5 | Total earned less retainage = total completed and stored − retainage |
| A6 | Current payment due = total earned less retainage − previous payments |
| A7 | Adjusted contract sum = original contract sum + net approved change orders |
| A8 | Per-line retainage sums to summary retainage |
| A9 | Sum of scheduled values = adjusted contract sum |

A6 was one of the four the diagnostic got wrong, and it is in the *arithmetic* class — checkable
from first principles and still botched. That is an argument for the property tests, not only for
the immersion block.

### 6.3 Integrity rules — no construction knowledge required, therefore not blocked by immersion

These need the domain model and persistence (duplicate detection runs across full history), so they
land in week 8 alongside the convention rules — but for scheduling reasons, not provenance ones.
Nothing here waits on domain knowledge.

| | Rule |
|---|---|
| I1 | Duplicates across **full history**: vendor + amount + date window; invoice number; line-item fingerprint |
| I2 | Vendor fuzzy resolution — **human confirmation on first match**, reversible merges, provenance for every alias (B6: auto-merging is unrecoverable) |
| I3 | **Bank-detail change = hard stop.** Surface, require human confirmation, never auto-approve, never auto-update (B5) |

### 6.4 Convention rules — blocked on immersion; `registry.py` rejects them without a `RuleSource`

| | Rule | Provenance required |
|---|---|---|
| C1–C3 | Retainage rate matches contract; stepping at the contract's milestone; separate rate on stored vs installed | Contract clause + expert confirmation that stepping is **contract-specific**, not the universal schedule §6.1 invented |
| C4–C6 | Stored materials never double-counted with completed work; must leave the stored column in the period they are installed; backup (supplier invoice, insurance, bonded storage) required | Expert confirmation of the exact mechanism |
| C7 | Work billed against an absent or unapproved change order | Contract |
| C8 | Percentage-complete decrease — flag, **suppressed when the scheduled value moved the same period** | Expert; this is the false-positive rule |
| C9–C10 | Sales/use tax by jurisdiction, material vs labour split; tax-exempt projects | **Statute cite per jurisdiction** |
| C11–C14 | Waiver type vs payment stage; through-date matches billing period; amount matches payment; statutory-form deviation detected and flagged, never opined on (B4) | Statute + expert; C11 is the rule §6.1 got backwards |

**Tax scope, v1: exactly one named jurisdiction.** Every other jurisdiction routes to review with
an explicit *"tax rules not configured for this jurisdiction."* Generalised tax-by-jurisdiction is
a research project with legal exposure attached and is the rule most likely to be confidently
wrong. Naming the limit is cheaper and more credible than implying coverage.

**Every rule in all three classes ships with a fixture that must trip it and a clean control that
must not.** No exceptions (DoD 3).

---

## 7. Confidence routing and calibration

Raw model and OCR confidence is **not calibrated**. Routing on an uncalibrated score is guessing
while displaying a number — the exact sin this programme exists to correct (B7, B10).

Ground Truth (brief 04) ships weeks 21–26, and B5 forbids depending on an unbuilt project. So
Ledger Sense ships the calibration **machinery** itself — isotonic fitting, curve storage,
reliability diagram, threshold derivation — and by week 12 the only labelled data available is the
synthetic corpus.

**A curve fitted on synthetic documents does not predict accuracy on real ones.** Claiming
otherwise would be the sin, restated. Hence three states, not two:

| State | Renders as | Routing effect |
|---|---|---|
| `uncalibrated` | Greyed, labelled | **Everything to review** |
| `calibrated:synthetic` | *"Calibrated on synthetic corpus (n=N) — not predictive for your documents"* | **Still everything to review** |
| `calibrated:field` | Basis = *n* real labels from this client's own documents | Thresholds derive from the curve against a stated target |

This is why `ConfidenceMeter` takes a basis object (§4.1, addition 5). The synthetic curve proves
the machinery and satisfies DoD 4 honestly; **it does not license auto-posting.**

**The path to `calibrated:field` is already built: every human correction in the review queue is a
label.** The product accumulates its own calibration data per client from day one. That yields a
claim no incumbent can match — *"we auto-post when your own data says it's safe, and we show you
the curve"* — and makes the honest position and the commercial position the same position.

**Never claim full automation.** Straight-through rate is published **including the misses**, as a
`Claim` whose caveat states the sample and period. Through week 12 that number describes the
synthetic corpus, and the caveat says so verbatim.

---

## 8. Screens

Light-first editorial instrument per `00` §5. Ledger green for reconciliation states only.
Tabular numerals wherever a number appears.

1. **Baseline study** — the billable first week and the sales artifact. Volume counter, timing
   instrument for sampled documents, loaded-rate input, exception log. Output: a one-page measured
   cost report with a stated method and a real confidence interval, print-ready.
2. **Inbox** — email watcher + drag-and-drop. Cards with type, vendor, amount, status, exception
   count. Keyboard-navigable.
3. **Review queue — the signature screen.** Split view: document at fidelity on the left, the field
   under review boxed and everything else dimmed; on the right the editable value, the validation
   rules that fired, and the provenance rail. `J`/`K` between exceptions, `Enter` accept, `E` edit,
   `R` reject. Fifty exceptions cleared without touching the mouse. **As a field is accepted, its
   box fills with a thin green rule that stays** — the document visibly accumulates verification.
4. **Reconciliation view** — continuation-sheet lines against the summary, discrepancy highlighted
   at the exact cell, arithmetic shown inline.
5. **Vendor ledger** — canonical vendors, aliases, payment-detail history as a change timeline.
   Bank-detail changes rendered as a red-lined diff with date and source document.
6. **Metrics** — documents processed, straight-through rate, exceptions caught, dollars reconciled
   against *their measured baseline*, time saved with its calculation visible. Every figure is a
   `Claim`; every figure opens the rail. **Publish the misses.**

**Print stylesheet** on the baseline report, the reconciliation view and the metrics screen. These
get printed and forwarded to auditors; that is the market.

**The demo that closes deals:** sixty seconds processing twenty synthetic pay applications, where
the exception queue catches a duplicate submission, a retainage miscalculation and a
stored-materials double-count. Straight-through rate shown honestly, misses included.

---

## 9. The design-partner path

**Baseline is synthetic-only through week 12.** Nothing in the eight weeks waits on a partner.

**What changes the day a partner signs:**

1. **Week 5's paid controller session becomes the partner's onboarding call.** Same money, strictly
   better outcome — immersion, corpus and rule spec from one relationship. If they sign before
   week 5, do not hire a separate expert.
2. **The baseline study runs for real, at cost, first.** It is already built in week 5. It stops
   being something demoed and becomes the thing done — DoD 1, DoD 11 and the case study together.
3. **The legal work moves forward hard. This is the item that must move.** Data agreement signed
   and the three-clause review completed **before the corpus arrives**, not before first payment.
   B9: you are a data processor on document one, and document one can precede any invoice. This
   costs money earlier than planned and is not optional.
4. **Convention rules take their `RuleSource`s from the partner's actual contracts** rather than an
   expert's general knowledge (B3 — their validation rules become the product spec). A provenance
   upgrade: `confirmed_by` names a person holding a contract.
5. **The tax jurisdiction (C9–C10) is named by their location** instead of chosen.
6. **`calibrated:field` becomes reachable.** Real corrections produce real labels; DoD 4 upgrades
   from machinery-proven-on-synthetic to a curve fitted on their documents.
7. **`REDACTED` becomes reachable — conditionally.** Only if §2.2's check found a vision provider
   stating in writing that it does not train, *and* written opt-in is given. If the check came back
   negative, nothing changes: the model lane stays `SYNTHETIC`-only and the product is simpler.

**The trap:** a partner arriving makes the synthetic generator *feel* redundant. It is not. The
partner's documents cannot be demoed, and the generator remains the fixture source. DoD 7 stands.

**What does not change:** the week order, the boundary gate, deterministic-first, the three
calibration states, QBO-first, the rulebook split.

**Cutoff — after roughly week 10, do not re-target the pipeline at real documents.** A late
partner's contribution is DoD 11 and the baseline case study, both valuable and neither requiring
a change to the build. Re-targeting extraction in week 11 costs weeks 11–12 and finishes nothing.

---

## 10. Testing standard

TDD, RED-GREEN-REFACTOR, no exceptions. Coverage floor **75%**.

- **Synthetic corpus at known ground truth**; every validation rule has a tripping fixture and a
  clean control.
- **`hypothesis` property tests** on every arithmetic rule (§5).
- **No-float**: lint + AST check + model-walk test (§5).
- **Boundary**: ledger-sense's own test asserting `CLIENT_CONFIDENTIAL` raises against the real
  published `bok-core`; redaction verification catches a deliberately planted identifier.
- **Writeback**: idempotency under forced mid-write failure; closed-period refusal; reversal
  correctness — against QBO sandbox.
- **Calibration**: `uncalibrated` and `calibrated:synthetic` both route everything to review;
  thresholds derive from a curve, never from a constant.
- **Degraded mode**: every provider disabled, the deterministic path extracts and validates digital
  PDFs end to end.
- **E2E**: Playwright through the review queue, keyboard-only, fifty exceptions.
- **Accessibility**: `axe-core`, zero serious/critical.
- **`licence-audit` CI**: SPDX deny-list covering AGPL/GPL/non-commercial, checking the **native and
  system layer** as well as Python metadata — the job that catches `ocrmypdf` and the `camelot`
  ghostscript extra.
- **AIA guard CI**: asserts no form artifact in the repo and that the non-affiliation notice is
  present.

---

## 11. Non-affiliation notice

Ships in the README, the docs and the product UI:

> Not developed, endorsed, approved, sponsored by, or affiliated with the American Institute of
> Architects. AIA®, G702® and G703® are registered trademarks of the American Institute of
> Architects. References describe format compatibility only.

Outputs are described as "G702-format" or "pay application". Processing a client's own purchased,
completed form on their behalf is permitted; distributing it is not.

---

## 12. Sequence and definition of done

### 12.1 The eight weeks

The implementation plan elaborates this into tasks. One sequencing choice is deliberate and
non-obvious: **the baseline study is built in week 5, not week 12.** It touches no document — you
time a human handling *their* documents, you do not ingest them — so it is not gated by §4.1. It is
what you actually sell. Defining "touch time" precisely enough to build the timing instrument *is*
domain immersion. And it is the one thing needed on day one if a partner appears in week 7.

| Wk | Work | Gate |
|---|---|---|
| **5** | Controller session booked day 1. **`licence-audit` CI first, before any dependency is installed.** `domain/money` · `domain/models` · `rules/registry` · `rules/arithmetic` A1–A9 + `hypothesis` · **`baseline/` study tooling + print-ready report** · `methodology.md` baseline section | None — touches no document |
| **6** | Synthetic corpus generator: our layouts, injected error classes, known ground truth · **AIA guard CI ships with the generator** · `bok-core` boundary integrated; ledger-sense's own boundary test green against the published package | `bok_core.boundary` for the **upload path only** |
| **7** | Extraction: `textlayer` · `tables` · `anchors` · `assemble` → `Claim` with page+bbox · `fingerprint` + profile cache | — |
| **8** | `rules/convention` C1–C14, each with a `RuleSource` from week 5 · `rules/integrity` I1–I3 · OCR tail with honest "cannot extract" | **Immersion closed** |
| **9** | **Review queue + reconciliation view.** Keyboard-only, fifty exceptions, accumulating verification rule. Playwright + axe | |
| **10** | **Writeback.** QBO sandbox adapter, idempotency under forced mid-write failure, closed-period refusal, reversal. CSV/IIF export as peer adapter | |
| **11** | Routing + calibration machinery · inbox · vendor ledger · metrics · `methodology.md` calibration section | |
| **12** | The sixty-second demo · degraded-mode proof · `methodology.md` straight-through rate · hardening | |

**Half a day every week from week 5 goes to DoD 11** — deliberate partner-finding, with the
week-5 baseline study as the outreach asset. Roughly forty hours across the phase, rather than none.

`licence-audit` and the AIA guard land early on purpose: their value is *preventing* adoption and
*preventing* a form artifact, not documenting either after the fact. `methodology.md` is written
incrementally for the same reason — written at the end, it is written from memory.

### 12.2 Definition of done — week 12

Brief `02` §12, mapped:

| # | Item | Where |
|---|---|---|
| 1 | Baseline study produces a print-ready measured-cost report with stated method and CI | §8.1, week 5 |
| 2 | Deterministic extraction handles digital-PDF pay applications and invoices end to end, no model | §4.3, week 7 |
| 3 | All validation rules implemented, each with tripping fixture and clean control | §6, weeks 5 + 8 |
| 4 | Confidence routing live, calibration state visible, thresholds derived from a curve | §7, week 11 |
| 5 | Review queue clears fifty exceptions keyboard-only | §8.3, week 9 |
| 6 | QBO draft writeback against sandbox: idempotency, closed-period refusal, reversal | §4.2, week 10 |
| 7 | Synthetic corpus generator shipped and documented; the sixty-second demo runs on it | §4.3, weeks 6 + 12 |
| 8 | Boundary enforcement proven: no client document can reach a free-tier model | §4.1, week 6 |
| 9 | `docs/methodology.md` states baseline method, calibration basis, straight-through rate with misses | weeks 5, 11, 12 — written incrementally |
| 10 | Non-affiliation notice present; no AIA form artifacts anywhere | §11, weeks 6 + 12 |
| 11 | **First paying client, or a signed design-partner agreement with a document corpus** | §9 — half a day per week from week 5, not a build task |

DoD 11 is the only item no amount of coding satisfies, and the one most likely to be unmet. It
receives explicit recurring time rather than being left to happen.

---

## 13. Risk register (project-specific additions to `02` §10)

| Risk | Mitigation |
|---|---|
| Weeks 9 and 10 are the slip points — two signature screens, then the highest-consequence code | Cut order, in sequence: vendor ledger *screen* (keep the data model), inbox email watcher (drag-and-drop only), OCR tail (route all scanned to review — the honest behaviour anyway). **Never cut:** boundary test, no-float tests, writeback safety tests, non-affiliation notice, `methodology.md` |
| Controller session takes days to schedule | Book day 1 of week 5; read free material while waiting |
| `bok-core` slips | Gate is narrow (§4.1) — only the upload path and the gateway call are blocked. Weeks 5–8 proceed |
| Scanned/photographed documents defeat geometry (B11) | Route to review with an honest "cannot extract"; measure the proportion during the baseline study **before** promising a straight-through rate |
| Discovery call becomes a feature comparison against a horizontal AP platform (B10) | Steer back to the measured baseline and the domain rules |
