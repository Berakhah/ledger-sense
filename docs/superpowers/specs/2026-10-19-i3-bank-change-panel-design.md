# I3 bank-change panel — design

Date: 2026-10-19 · Follows LS-10 (bank-change hard stop, backend) · Browser slice of LS-13

## Goal

A reviewer can clear, and undo, an I3 bank-detail hard stop from the review queue in the
browser, keyboard-only, without the UI ever holding a full account number.

## Scope

In: one read-only endpoint; a `BankChangePanel` component; an I3 mode in `useReviewKeys`;
wiring in `ReviewQueue.tsx`; backend, component, key and Playwright/axe tests; docs note.

Out (recorded, not built):
- A "couldn't verify" / follow-up action. The exception stays open and the document stays
  blocked from writeback. A follow-up action is a separate policy decision.
- Any change to the rejected-X0 path, to undo's evidence trail, or to differently labelled
  bank blocks (open decisions from LS-10).
- The rest of LS-13: login, upload, document navigation, posting/export/reversal UI.

## Existing contract this builds on

- `POST /api/review/exceptions/{id}/confirm-bank-change` body `{channel: phone|in_person|
  video_call|other, contact: str ≤200 non-blank, note?: str ≤2000}`. The only way to clear I3
  (Never-rule 6). 409 if the baseline moved since the finding was raised.
- `POST .../undo-confirm-bank-change` reopens a confirmed I3. No time limit.
- accept, edit and reject on I3 return 422 (`_refuse_bank_clearing`). An X0 on the bank
  field may still be rejected.
- `ReviewQueue.tsx` hosts `VendorConfirmPanel` for I2; this panel follows that pattern.

## 1. Data: `GET /api/review/exceptions/{exc_id}/bank-change`

Read-only, session-scoped like its siblings.

Response: `{ vendor_name: str, kind: "first_seen" | "changed" | "changed_back",
previous_last4: str | null, incoming_last4: str }`.

- Computed by re-extracting the document's payment detail and calling `assess_payment_detail`
  against the vendor ledger, as `confirm-bank-change` does, but loading the ledger without
  the write lock and persisting nothing.
- 422 if the exception is not I3; 409 if it is not open.
- Never returns a full account or routing number or the HMAC. Last four only.
- If the baseline moved since the finding was raised, it reports the current kind, so the
  panel matches what confirm will accept. If the baseline has caught up and now equals the
  document's detail, it returns 409 ("re-run the document"), like confirm's own stale-baseline
  409.
- Wording of the finding message is not parsed anywhere in the UI.

## 2. Panel and keyboard

`BankChangePanel.tsx` is standalone (like `VendorConfirmPanel`) so it is unit-testable in
jsdom; `ReviewQueue.tsx` pulls in pdf.js and is not.

- Banner: "Hard stop: bank details", vendor name, kind.
- Diff: previous last four struck through (red), incoming underlined (green); first sighting
  shows the incoming account only. Strike/underline carry the meaning, so it survives
  greyscale. Tabular numerals.
- Attestation form: labelled `fieldset`; contact method radio group; required contact name;
  optional note; limits match the server.
- Guidance: verify with a known-good number, not one printed on the document.
- Confirmed state shows "Bank details confirmed" and an Undo action. No green rule: a
  confirmation is not a field-value claim, and `acceptedBoxesOf` excludes `confirmed` by
  design (as for I2).
- "Leave open, next" moves to the next exception and records nothing. Copy states that the
  stop stays open and the document cannot be posted until verified.
- Server errors (409 stale baseline, 422, network) render inline as text.

Keys: `Enter`, `E`, `R` are inert on an I3 exception (a notice, nothing closes); `J`/`K`
move; `Ctrl+Enter` inside the form confirms only when it is valid; bare `Enter` in a field
does nothing; `Escape` returns focus to the queue. Other rules unchanged. A held bare
`Enter` can never clear a hard stop.

Accessibility: errors in an `aria-live` region; after confirm or undo focus returns to the
queue. The panel does not take focus on appearing (see Amendments).

## Amendments (after reading the code, 2026-10-19)

1. **No `useReviewKeys` I3 mode.** The hook already ignores Ctrl-modified keys and anything
   typed into an input, so `Ctrl+Enter` lives on the form. I3 inertness is `ReviewQueue`'s
   existing `refusal()` / blocked-notice checks (decided client-side so an I3 item is never
   optimistically closed for a write the server will 422).
2. **No auto-focus.** The hook treats a focused `<input>` as typing, so focusing the form
   would make `J`/`K` inert until the reviewer left it. `Tab` from the queue enters the
   form; `Escape` returns to the queue. (Replaces "focus moves to the first form control".)
3. **No green rule on confirm.** See the confirmed-state bullet above.
4. **Confirm waits for the server.** Unlike accept, it does not close optimistically: a
   hard stop must not show "Confirmed" and reopen on a 409. The panel awaits the request
   and shows a refusal inline.
5. **Playwright seed.** `api/fixtures.py` gains a demo-only `bank` scenario that stores a
   real corpus PDF with a remit block and runs the real pipeline, so the queue holds genuine
   I2/I3 findings. `changed` first gives the tenant a confirmed baseline.

## 3. Testing

Backend (pytest, TDD, synthetic corpus): correct kind and last-four for first-seen, changed,
changed-back; 422 non-I3; 409 closed; response scanned for full numbers/HMAC; ledger
unchanged after a call.

Component (vitest/jsdom): diff rendering per kind; form invalid until a contact name is
entered; confirm sends the right body; inline server errors; Undo only when confirmed.

Keys (vitest): I3 inertness of `Enter`/`E`/`R`; `Ctrl+Enter` gated on validity; non-I3
behaviour unchanged.

End to end (Playwright): keyboard-only open → tab through form → `Ctrl+Enter` confirm →
undo; bare `Enter` does nothing; axe zero serious/critical; greyscale check on the diff.

## Risks

- The endpoint re-extracts the PDF, so it costs as much as the confirm path. Acceptable at
  review volumes; revisit if the panel feels slow.
- The I3 seed fixture must exist for Playwright; if the demo seed lacks one, adding it is
  part of the plan.
