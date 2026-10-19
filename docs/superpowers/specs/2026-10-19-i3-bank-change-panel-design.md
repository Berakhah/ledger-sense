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
  panel matches what confirm will accept.
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
- Confirmed state shows "Confirmed" and an Undo action. The accepted-field green rule
  appears only after a confirm.
- "Leave open, next" moves to the next exception and records nothing. Copy states that the
  stop stays open and the document cannot be posted until verified.
- Server errors (409 stale baseline, 422, network) render inline as text.

Keys (`useReviewKeys`, I3 mode): `Enter`, `E`, `R` inert; `J`/`K` move; `Ctrl+Enter` confirms
only when the form is valid. Other rules unchanged. A held bare `Enter` can never clear a
hard stop.

Accessibility: errors in an `aria-live` region; focus moves to the first form control when
an I3 exception is selected; after confirm or undo focus returns to the exception list.

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
