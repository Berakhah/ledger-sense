# LS-10 slice 1 — bank-change hard stop (I3) end to end

Status: draft for review · 2026-10-18 · Audit ref: `docs/system-audit-2026-10-15.md` LS-10

## Problem

`check_payment_detail` (I3), `PaymentDetail` (HMAC, last4), `VendorLedger.record_payment_detail`
and the `payment_details` table exist, but nothing ingests a payment detail. The runner never
extracts bank details and never calls I3, and `GET /api/vendors/{id}/ledger` always returns
`payment_history: []`. The product's highest-value fraud control is therefore inactive
(Never-rule 6: never auto-approve a bank-detail change).

## Decisions already made (2026-10-18)

- **Confirmation = single-reviewer attestation with recorded evidence** (channel, contact
  spoken to, time, actor). Two-person rule is out of scope.
- **First-seen details are a hard stop too.** The first detail for a vendor becomes the baseline
  only after attestation (no trust-on-first-use).

## Scope

In: synthetic corpus bank block + injected error class; deterministic extraction; runner I3
step; `confirm-bank-change` review action with undo; attestation columns (migration); API
surfaces; tests. Out: semantic duplicates, contract storage, construction convention rules
(retainage, stored materials, CO, tax, lien waivers — need domain authority), the LS-10
scope-statement docs, two-person rule.

## Design

### 1. Corpus and extraction
- Our own layout (never an AIA form, Never-rule 7) gains a "Remit to" block: account and routing.
- `corpus/errors.py` gains an injected class `bank_change` (account differs from the vendor's
  history) alongside a clean control.
- Extraction is anchor-based and deterministic. The full account/routing number exists only in
  memory during a run: it is reduced to `payment_hmac` plus last4 and dropped.
- The `claims` table stores values as text, so the bank claims are stored **masked**
  (`....1234`), never plaintext. Documents stay `CLIENT_CONFIDENTIAL`; nothing here reaches the
  model lane.

### 2. Runner
- After vendor resolution, build a `PaymentDetail` and call I3.
- `check_payment_detail` gains first-seen behaviour: empty history now returns a hard-stop
  finding with a distinct message. A detail equal to an earlier non-current entry
  (`seen_before`) is reported as change-then-change-back, higher risk.
- Findings are replaced on re-run (existing delete-insert), so a rerun does not duplicate.
- If the vendor is unresolved or unconfirmed, I3 still raises (first-seen); the vendor
  confirmation and the bank confirmation remain separate actions.

### 3. Confirmation
- New review action `confirm-bank-change` on the I3 finding. Input: channel, contact, note.
  Records actor and timestamp, then calls `record_payment_detail(confirmed_by=…)`.
- Migration adds attestation columns to `payment_details` (channel, contact, note).
- Undo removes the appended detail by id (same pattern as `undo_confirmation`) and reopens I3.
- Generic `accept` and `edit` on an I3 finding are refused. The only way to clear it is the
  specific confirmation. The existing open-findings writeback gate keeps writeback blocked
  until then.
- API exposes last4 and attestation only; `account_hmac` never leaves the server.

### 4. Tests (each rule: a fixture that trips it, a clean control that doesn't)
- Changed account trips I3; same account is clean.
- First-seen trips I3; clears only after attestation.
- Change-then-change-back is flagged as higher risk.
- Generic accept/edit on I3 returns an error.
- Writeback blocked while I3 open, allowed after confirmation.
- Undo restores the previous baseline and reopens I3.
- Rerun does not duplicate findings.
- Plaintext account/routing never appears in the DB, API responses or logs (scan).
- Bank fields never reach the model lane.
- Synthetic ground truth: injected `bank_change` is caught; clean control is not.

## Order of work
1. Separate small TDD commit first: pass `match_quality` (not NULL→0.0) to the Router for
   deterministic claims, so the predictor matches what labels score.
2. This slice, TDD, with `security-review` on the extraction/storage path before commit.

## Risks
- Plaintext leakage via claims/findings text, logs or exceptions (mitigated by masking + scan test).
- A new I3 hard stop on every first invoice per vendor adds one confirmation per new vendor;
  accepted trade-off.
- Skill map applies: `ecc:python-review`, `ecc:fastapi-review`, `ecc:database-migrations`,
  `security-review`.
