# Methodology

## What we measure

This document reports the measured cost-per-document baseline — touch time
multiplied by the client's loaded hourly rate — together with its confidence
interval. The figure and its interval are computed by
`ledger_sense/baseline/study.py` (`BaselineStudy`) and rendered for print by
`ledger_sense/baseline/report.py` (`render_report`); nothing here is
recomputed independently of that code.

## How we measure it

**Sampling frame.** The population sampled is stated per study — typically
all accounts-payable documents received by the client over the collection
period — and is recorded on every report alongside the sample size drawn
from it, so the two numbers travel together rather than the ratio being left
implicit.

**Sample size.** A minimum of 8 timed samples is required before an interval
is reported at all. Below that floor, a Student's *t* interval on a
right-skewed quantity is not defensible to a controller who asks how the
number was produced, so the study refuses to produce one rather than
reporting an interval narrower than the sample supports.

**Collection period.** Each study records the calendar start and end date
over which documents were timed. The figure describes that window only.

**Timing protocol.** Each sampled document is timed individually by
observation as it is processed, not recalled or estimated after the fact.

**Touch-time definition.** Touch time is defined, verbatim, as:

> Touch time is wall-clock time from opening the document to the moment it is filed or posted, including interruptions shorter than 60 seconds and excluding longer ones. It does not include time waiting on a third party.

**Loaded-rate definition.** The loaded hourly rate is the client-supplied
base hourly rate multiplied by an overhead multiplier to cover employer
overhead (payroll taxes, benefits, and similar burden). Where the client
supplies a low and high hourly rate instead of a single figure, that
uncertainty is propagated into the reported interval rather than assumed
away.

**Interval method.** The confidence interval is a Student's *t* interval,
two-tailed, at the 95% level, computed on the mean touch time. The critical
value is looked up from a table tabulated to 200 degrees of freedom rather
than collapsing to the asymptotic 1.96 value above single-digit degrees of
freedom — at the sample sizes a real baseline study uses, that collapse
understates the margin and makes the interval look tighter than it is.

**Interval scope.** The interval's scope is stated, verbatim, as:

> The interval covers sampling variability in observed touch time only. It holds the loaded rate fixed at the figure you supplied; if that figure is itself uncertain, the true interval on cost per document is wider. Supply a low and high hourly rate to have that uncertainty included.

## Corpus

To be completed in a later phase.

## Writeback

### DocNumber / PrivateNote (F-40)

QuickBooks Online's `DocNumber` field on a Bill is the **vendor's own
invoice number** — the value a bookkeeper reconciles against the paper
invoice, and the field QBO's own duplicate-document-number warning keys
on. Ledger Sense's idempotency key never overwrites it:

- When a vendor invoice number was extracted from the document,
  `DocNumber` keeps that invoice number unchanged, and the Ledger Sense
  idempotency key is embedded in `PrivateNote` instead (recoverable after
  a crash by `ledger_sense.writeback.qbo.extract_key_from_private_note`).
- Only when no vendor invoice number was extracted does `DocNumber` fall
  back to holding the idempotency key, as the original design did — and
  when that fallback happens, `PrivateNote` records in plain language that
  it happened, so a human reading the books later understands why
  `DocNumber` looks like a key rather than an invoice number, rather than
  being left to guess.

The reasoning: overwriting `DocNumber` with our own key would silently
disable a duplicate-invoice control the client already has, in a product
whose pitch is catching duplicates. Never depriving the client of a
control they already had, in order to add a control of our own, governs
this decision.

### Platform reality (client-facing)

QuickBooks Online Bills have no draft state. Ledger Sense therefore
creates every Bill **unpaid**, stamped `AWAITING REVIEW`, and never
creates a `BillPayment`. Said plainly, for a client or reviewer reading
this document rather than the source: **we create the bill; you approve
it by paying it; we never move money.** A human approves by paying the
bill inside QuickBooks — that is the actual mechanism, and a truer
sentence than a claim about a draft state the platform does not have.

## Calibration

To be completed in a later phase.

### `match_quality` (extraction, Phase 2)

Every field `assemble_pay_application` extracts carries a `match_quality`
score in `[0.0, 1.0]`, computed by `ledger_sense.extract.assemble.match_quality`
as the unweighted average of four booleans:

- `label_exact` — the field's label was found by literal anchor match, not
  inferred from remembered geometry.
- `value_unique` — the value did not require merging or choosing among
  several candidate words. Not yet computed everywhere it's passed; several
  call sites currently pass a documented constant `True` pending that work.
- `column_unambiguous` — the field's label did not also collide with a
  second occurrence elsewhere on the page (see `extract_summary`'s
  `ambiguous` list).
- `profile_hit` — the value came from a box remembered from a past
  reviewer correction (`ProfileCache`) rather than a fresh label anchor.

This is **explicitly not a probability.** It is four bits of evidence, one
data point per field per document, with no reliability curve fitted to it
yet. Phase 6 either calibrates it against reviewer-corrected ground truth
(isotonic or Platt, per field type — see "How we measure it" above for the
same discipline applied to the baseline number) or refuses to route on it —
`ConfidenceMeter` renders **uncalibrated** until that curve exists. Until
then, every field routes to review regardless of this score.

## What these numbers do not mean

- The figure describes the sampled documents over the stated period. It is
  not an industry average, and it is not comparable to published
  per-invoice cost figures whose provenance we could not verify.

For the product's non-affiliation notice, see the README.

## Single-process constraint

`ledger_sense/__main__.py` runs uvicorn with a single worker (`WORKERS = 1`),
asserted at that call site. The writeback path's in-process
`ConcurrentWriteInProgressError` guard (`ledger_sense/writeback/qbo.py`) is
an in-memory, per-process set — it closes the concurrent-write race
described in `writeback/idempotency.py`'s module docstring only within one
process. Running more than one worker (or more than one container) would
silently reopen the duplicate-bill race `IdempotencyLedger`'s
`BEGIN IMMEDIATE` transaction alone cannot close. Raising the worker count
requires first replacing that in-process guard with a cross-process lock in
the idempotency ledger itself — deferred until the deployment topology
actually needs more than one process.

## Single-tenant idempotency key (I-3, final whole-branch review)

The idempotency key (`build_idempotency_key(content_sha256, contract_id,
application_no)`) carries no tenant identifier, and both stores it backs —
`IdempotencyLedger` and `ExportAdapter`'s own CSV key-scan — are keyed on
that value alone. Two tenants posting byte-identical documents (a shared
synthetic corpus makes this a real, not hypothetical, collision) would
compute the same key: the second tenant's post would find the first
tenant's `pending`/`committed` row already there and silently return the
first tenant's `remote_id` instead of creating its own bill. This is
inherited from how the writeback stores were built — `IdempotencyLedger`
and `AuditLog` both predate any multi-tenant plumbing reaching into the
writeback module (see `writeback/audit.py`'s module docstring) — not
something this pass fixes. Closing it requires qualifying
`build_idempotency_key`'s payload with `tenant_id`, at the cost of
invalidating every idempotency key already on disk (a client's already-
posted documents would need one-time reconciliation against the old,
tenant-less keys before the new scheme could safely replace them).
