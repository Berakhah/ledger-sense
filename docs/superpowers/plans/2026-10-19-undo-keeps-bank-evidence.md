# Undo keeps the bank-detail evidence (LS-13 follow-up)

Status: PLAN, not started. Needs a go before any schema change.

## Decision (safest option, taken on the user's "go the safest way")

Undoing a bank-detail confirmation must NOT erase the record. Today
`undo_payment_detail` (domain/vendor.py) deletes the `PaymentDetail` row, and with
it the attestation (channel, contact, note), who confirmed, and when. A wrong
confirmation is exactly the case an auditor wants evidence for. Undo becomes:
mark the entry reversed (who, when), keep everything else, reopen the exception.

## Invariants to hold

1. A reversed entry is never a baseline. `record_payment_detail` picks the last
   CONFIRMED entry; it must skip reversed ones, or undo would not restore the
   previous baseline (a security regression, not a cosmetic one).
2. A reversed entry still counts as "seen before". `seen_before` is the
   change-then-change-back detector; forgetting an account that was confirmed
   and reversed would blind it.
3. `find_payment_detail` (undo's lookup) skips already-reversed entries, so a
   second undo of the same confirmation is still a 409, and a re-confirm after
   undo creates a NEW entry rather than reviving the old one.
4. Append-only: nothing is deleted by undo. Retention purge (store/retention.py)
   is unchanged and remains the only deleter.
5. Reversal needs a named human (`reversed_by`), like every other write here.

## Changes, in build order (each RED first)

1. Domain tests (hypothesis where arithmetic-like, otherwise plain): undo keeps
   the entry with reversed_by/reversed_at; baseline falls back to the previous
   confirmed entry; seen_before still true for the reversed account; second undo
   refused; re-confirm after undo makes a second entry. Then `PaymentDetail`
   gains `reversed_by`, `reversed_at`; `undo_payment_detail(vendor_id, detail_id,
   reversed_by)` sets them; baseline filters and `find_payment_detail` updated.
2. Store: columns on `payment_details`, alembic migration (nullable, no
   backfill needed), and `store/vendors.py` round-trip (it deletes and
   re-inserts the tenant's rows on every save, so the new fields must be in
   both directions or a save silently drops the reversal).
   Skills: ecc:database-migrations, ecc:postgres-patterns.
3. API: `undo_confirm_bank_change` passes `session.user_id`; `PaymentDetailOut`
   in api/vendors.py exposes reversed_by/reversed_at; API tests in
   tests/api/test_bank_change.py (undo then GET vendor history shows the entry
   marked reversed, with its attestation intact).
4. UI: vendor ledger red-line diff labels a reversed entry (text, not colour
   alone, per the greyscale rule); Playwright covers it.
5. security-review on the diff before merge (CLAUDE.md: PaymentDetail).

## Not in this plan (still open, safest default noted)

- Rejected-X0 dead end: default = leave the exception open and say why; never
  auto-resolve.
- Differently labelled bank blocks: default = one shared wording for "this is a
  bank-detail hard stop", per-kind detail underneath.
- "Couldn't verify" action: default = records nothing, keeps the stop open,
  optionally notes the attempt; never clears or downgrades it.
