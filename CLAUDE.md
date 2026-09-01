# Ledger Sense

Vertical document intelligence with confidence routing, for construction progress billing.
Weeks 5–12 · Accent: ledger green `oklch(0.58 0.11 158)` · Licence: Apache-2.0

**Pitch:** *"I will measure what your current process actually costs you in week one, then automate the part that is safe to automate and show you the exceptions I caught that a human would have missed."*

## Source of truth

- `../Starting_Documents/00-SHARED-FOUNDATION.md` — the substrate. Read in full before anything.
- `../Starting_Documents/02-LEDGER-SENSE.md` — this project's brief. Read in full.

The briefs win over anything summarised here. This file exists so a fresh session cannot violate a hard rule by accident.

## Workflow

superpowers, in order: `brainstorming` → `using-git-worktrees` → `writing-plans` → `subagent-driven-development` → `test-driven-development` → `requesting-code-review` → `verification-before-completion`. TDD is RED-GREEN-REFACTOR, no exceptions.

Off the happy path: a bug or failing test gets `systematic-debugging` before a fix is proposed, not straight to TDD. Feedback on your own work gets `receiving-code-review` — verify before implementing, don't just comply. A phase or task branch that's done gets `finishing-a-development-branch`.

## Skill map

Which skill to reach for, by what you're touching. Check before improvising; no row matches → default to the nearest stack skill (`ecc:python-review` backend, `ecc:react-review` frontend) rather than skipping review.

| Touching | Reach for | Why |
|---|---|---|
| Any `.py` change | `ecc:python-review`, `ecc:python-patterns`, `ecc:python-testing` | How `mypy --strict`/`ruff` (Never #2) gets hit without a second pass |
| FastAPI surface — gateway, review-queue API, writeback | `ecc:fastapi-review`, `ecc:fastapi-patterns` | Highest-consequence backend code in the product |
| Postgres / alembic | `ecc:postgres-patterns`, `ecc:database-migrations` | Least-privilege role, migration safety (`00` §8) |
| `docker-compose.yml` / packaging | `ecc:docker-patterns`, `ecc:deployment-patterns` | Must still `docker compose up` clean, zero manual steps |
| Any `.tsx` change | `ecc:react-review`, `ecc:react-patterns`, `ecc:nextjs-turbopack` | — |
| New screen or component | `frontend-design` | `00` §5 (editorial instrument, OKLCH tokens, provenance rail, tabular numerals) is the spec, not inspiration |
| Any screen, before calling it done | `ecc:accessibility`, `ecc:frontend-a11y`, `webapp-testing` | WCAG 2.2 AA + axe zero serious/critical is a testing-bar line item; `webapp-testing` proves the review queue's keyboard-only path (`J`/`K`/`Enter`/`E`/`R`) |
| Metrics screen, `docs/methodology.md` diagrams | `dataviz` | Every figure is a `Claim`; charts must survive greyscale print (`00` §5.4) |
| `Sensitivity` gateway, redaction pass, OAuth token storage, `PaymentDetail` | `security-review` | Run before anything else touches it — `00` B2: "the code has to stop you" |
| Synthetic-corpus PDF fixtures | `pdf` | The extraction pipeline itself (`pdfplumber`/Docling/pypdfium2/RapidOCR/img2table) is hand-rolled, not this skill |
| Money arithmetic | none — Never-rule #2 directly | `Decimal` + `hypothesis` property tests + no-float lint, every time |
| Lien waivers / AIA-format output | none — Never-rules #5, #7 directly | Extract, validate, flag deviation, stop |

## Two corrections that override the source report

1. **This is not a vision-model-first product.** $0 budget → free-tier inference → the provider may train on inputs → a subcontractor invoice with bank details is `CLIENT_CONFIDENTIAL` and cannot go there. Extraction is deterministic-first.
2. **AIA G702/G703 are copyrighted and actively enforced.** Never ship, recreate, or redistribute the forms or a corpus of them.

## Never (each has a test proving it)

1. **No client document reaches a free-tier model.** `CLIENT_CONFIDENTIAL` hard-fails at the gateway. The model lane accepts `SYNTHETIC` or `REDACTED` only, and `REDACTED` requires the client's written opt-in plus the `bok-core` redaction verification pass. Build the boundary before anything that touches a document.
2. **No floats in the money path.** `Decimal` with an explicit context, or integer minor units. Rounding policy documented and tested. Enforced by a lint rule *and* a test. `hypothesis` property tests on every arithmetic rule.
3. **Writeback is draft-only in v1.** Unapproved bills / draft transactions. A human approves inside the accounting system. Never post an approved transaction, no matter what the client asks.
4. **Idempotency keys on every write.** Test by force-failing mid-write. Never write to a closed period — check the book-close date, refuse and explain. Full audit trail with one-click clean reversal.
5. **Never generate a legal document.** Lien waivers are legal instruments; several states mandate statutory language. Extract, validate, flag deviation, stop. Say so in the UI and the docs.
6. **Never auto-approve a bank-detail change and never auto-merge a vendor.** Surface, require human confirmation, keep merges reversible with provenance for every alias.
7. **No AIA form artifact anywhere in the repo.** The synthetic corpus uses **our own layout** with equivalent column semantics — mimic the column math, never the form design. Carry the non-affiliation notice: not developed, endorsed, approved, sponsored by or affiliated with the AIA; AIA®, G702®, G703® are registered trademarks of the American Institute of Architects; references describe compatibility only.
8. **Never claim full automation.** Publish the straight-through rate including the misses.

Inherited from `00`: no hardcoded model names (R1), no secrets in the repo including fixtures, $0 recurring cost, permissive licences only. `PyMuPDF` is **AGPL — flag before adopting**; `pypdfium2` is the Apache/BSD alternative.

## Pipeline

```
Document ─▶ Classify ─▶ Layout parse ────▶ Field extraction ─────▶ Validate ─▶ Route
            (rules)     (Docling / PDF     (anchor + geometry +    (domain     (confidence
                         text layer/OCR)    regex + table recon)    rules)      thresholds)
                            └── escalation, SYNTHETIC/REDACTED only ──▶ model lane
```

Layer 1 deterministic carries the majority — pay applications are software-generated, so a text layer with coordinates usually exists. Layer 2 validation **is the product**. Layer 3 model escalation is tightly fenced and optional; a client who declines it still gets a working product with more routed to review, and we say that out loud in the sales call.

Candidate libraries, all permissive: `pdfplumber`, `Docling` (MIT), `pypdfium2`, `RapidOCR`/`Tesseract` for the scanned minority, `img2table` for ruled tables.

## Validation layer — the moat

Arithmetic integrity (G703 must foot **exactly** to G702; per-line sums; percentage complete; balance to finish; current payment due) · retainage (rate matches contract, per-line reconciles to summary, **stepping at 50% / substantial completion**) · stored materials (**never double-counted with completed work**; must leave the stored column when installed; backup required) · change orders (approved COs reconcile to the adjusted contract sum; work against an absent CO is a flag) · duplicates across full history · vendor fuzzy resolution with first-match human confirmation · **bank-detail change = hard stop** · tax by jurisdiction and material/labour split · lien waiver type-vs-stage, through-date, amount, statutory deviation.

Every rule ships with a fixture that must trip it and a clean control that must not.

## Confidence routing

Raw model/OCR confidence is **not calibrated**. Routing on an uncalibrated score is guessing while displaying a number — the exact sin this programme exists to correct.

- Every field carries a `Claim` with `method` and `confidence`.
- `ConfidenceMeter` renders greyed and labelled **uncalibrated** until a reliability curve exists for that field type.
- Curves fitted per field type (isotonic / Platt); the reliability diagram ships in `docs/methodology.md`.
- Thresholds derive from the curve against a stated target, never chosen by feel.
- **Until calibrated, everything routes to review.** Ground Truth (brief 04) supplies the curves.

## Stack & layout

Python 3.12+, `uv`, `ruff`, `mypy --strict`. FastAPI. Next.js 15 + `bok-ui`. Postgres + alembic. `docker compose up` on a clean machine.

Screens: **Baseline study** (the billable first week and the sales artifact) · Inbox · **Review queue** (split view, field boxed, `J`/`K`/`Enter`/`E`/`R`, fifty exceptions cleared without a mouse; accepted fields accumulate a thin green rule that stays) · Reconciliation (G703 vs G702, discrepancy at the exact cell, arithmetic inline) · Vendor ledger (payment-detail history as a red-lined diff) · Metrics (every figure a `Claim`, misses published).

## Watch for

- **No documents, and they are hard to get.** The synthetic generator with injected error classes is a first-class deliverable, not a fixture — the demo runs on it. One design partner, baseline study at cost, written data agreement.
- **Scanned and photographed documents are the hard tail.** Route to review with an honest "cannot extract" rather than guessing. Measure the proportion during the baseline study, before promising a straight-through rate.
- **Accounting API access is a gate we do not control.** Sandbox-first; verify current review timelines and connection limits yourself. Ship a CSV/IIF export fallback so the product works on day one without API approval.
- **Fraud detection creates liability in both directions.** The tool surfaces and requires human confirmation; it never approves or auto-updates. Terms must match.
- **We become a data processor on the first document.** Encryption at rest, retention with automatic deletion, a documented deletion procedure, a signable data agreement, full-disk encryption and a dedicated work environment. Prepare before the first document arrives.
- **The baseline number must be defensible.** Sample size, sampling frame, timing protocol, what counts as touch time, loaded-rate definition, reported confidence interval — in `docs/methodology.md`, handed to the client with the number.
- If a discovery call becomes a feature comparison against a horizontal AP platform, we have already lost — steer back to the measured baseline and the domain rules.

## Testing bar

Synthetic corpus at known ground truth · `hypothesis` property tests on every arithmetic rule · no-float lint + test · boundary tests · writeback tests against sandbox (idempotency under forced retry, closed-period refusal, reversal correctness) · calibration tests · degraded mode (deterministic path extracts and validates digital PDFs end to end with all providers disabled) · Playwright keyboard-only through fifty exceptions · axe zero serious/critical. Coverage floor 75%.

## Definition of done

Brief §12, eleven items — including a signed design-partner agreement or a first paying client.

## Before planning

Brief §13 has 7 open decisions; `00` §12 has 7 more. Q1 (is a design partner in reach?) and Q7 (actual construction domain knowledge) change the entire sequence. Do not assume them — ask.
