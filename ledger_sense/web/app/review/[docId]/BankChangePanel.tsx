'use client'

import { useEffect, useState } from 'react'

export type BankChannel = 'phone' | 'in_person' | 'video_call' | 'other'

/** Mirrors ledger_sense.api.review.ConfirmBankChangeBody. */
export interface BankAttestation {
  channel: BankChannel
  contact: string
  note: string | null
}

/** Mirrors ledger_sense.api.review.BankChangeView. Last four digits only —
 *  the server never sends a full number, so the panel cannot show one. */
interface BankChangeView {
  vendor_name: string
  kind: 'first_seen' | 'changed' | 'changed_back'
  previous_last4: string | null
  incoming_last4: string
}

const CONTACT_MAX = 200
const NOTE_MAX = 2000

const CHANNELS: ReadonlyArray<readonly [BankChannel, string]> = [
  ['phone', 'Phone'],
  ['in_person', 'In person'],
  ['video_call', 'Video call'],
  ['other', 'Other'],
]

const KIND_LEAD: Record<BankChangeView['kind'], string> = {
  first_seen: 'First bank details seen for this vendor — nothing on file confirms them.',
  changed: 'These bank details differ from the account on file.',
  changed_back: 'These bank details have changed back to an account seen earlier.',
}

async function fetchBankChange(apiUrl: string, excId: string): Promise<BankChangeView> {
  const response = await fetch(`${apiUrl}/api/review/exceptions/${excId}/bank-change`, {
    credentials: 'include',
  })
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // A non-JSON error body: the status line above is the best we have.
    }
    throw new Error(detail)
  }
  return (await response.json()) as BankChangeView
}

/** The I3 hard stop's confirm step. Standalone, like VendorConfirmPanel, so it
 *  is testable in jsdom without pdf.js.
 *
 *  Keys. A bare Enter must never clear a hard stop, so no field submits on
 *  Enter; Ctrl/Cmd+Enter does, and only when the form is valid. Escape hands
 *  the keyboard back to the queue (the hook ignores everything typed into an
 *  input, so J/K work again only once focus has left the form). The panel does
 *  not take focus when it appears for the same reason: Tab from the queue
 *  enters it.
 *
 *  Confirming waits for the server (onConfirm resolves) instead of closing
 *  optimistically — a hard stop must not show "Confirmed" and then reopen on a
 *  409. A rejection is shown inline and the form stays usable. */
export function BankChangePanel({
  excId,
  apiUrl,
  disabled,
  onConfirm,
  onLeaveOpen,
  onEscape,
}: {
  excId: string
  apiUrl: string
  disabled: boolean
  onConfirm: (attestation: BankAttestation) => Promise<void>
  onLeaveOpen: () => void
  onEscape: () => void
}) {
  const [view, setView] = useState<BankChangeView | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [channel, setChannel] = useState<BankChannel>('phone')
  const [contact, setContact] = useState('')
  const [note, setNote] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setView(null)
    setLoadError(null)
    fetchBankChange(apiUrl, excId)
      .then((result) => {
        if (!cancelled) setView(result)
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [apiUrl, excId])

  const valid =
    contact.trim() !== '' && contact.length <= CONTACT_MAX && note.length <= NOTE_MAX

  async function submit(): Promise<void> {
    if (!valid || submitting || disabled) return
    setSubmitting(true)
    setError(null)
    try {
      await onConfirm({
        channel,
        contact: contact.trim(),
        note: note.trim() === '' ? null : note,
      })
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Could not confirm the bank details')
    } finally {
      setSubmitting(false)
    }
  }

  if (loadError !== null) {
    return (
      <p role="alert" data-testid="bank-change-error" className="mt-3 text-sm text-flag">
        Could not check these bank details: {loadError}
      </p>
    )
  }
  if (view === null) {
    return (
      <p data-testid="bank-change-loading" className="mt-3 text-sm text-ink-muted">
        Checking bank details…
      </p>
    )
  }

  return (
    <div
      data-testid="bank-change-panel"
      className="mt-3 border-l-2 border-flag pl-3 text-sm"
      onKeyDown={(e) => {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
          e.preventDefault()
          void submit()
        } else if (e.key === 'Escape') {
          e.preventDefault()
          onEscape()
        }
      }}
    >
      <h3 className="font-semibold text-flag">Hard stop: bank details</h3>
      <p className="mt-1">
        <span className="text-ink-muted">Vendor</span> {view.vendor_name}
      </p>
      <p className="mt-1">{KIND_LEAD[view.kind]}</p>

      {/* Strike-through and underline carry the meaning, so the change reads in
          greyscale; colour only reinforces it. */}
      <p data-testid="bank-diff" className="numeric mt-2">
        {view.previous_last4 !== null && (
          <>
            <del className="text-flag line-through">
              <span className="sr-only">Previous account ending </span>
              ....{view.previous_last4}
            </del>{' '}
          </>
        )}
        <ins className="text-ink underline decoration-accent decoration-2">
          <span className="sr-only">Incoming account ending </span>
          ....{view.incoming_last4}
        </ins>
      </p>

      <p className="mt-2 text-ink-muted">
        Verify with the vendor using a number you already trust — not one printed on the document.
        The stop stays open until you do; nothing is recorded until you confirm.
      </p>

      <fieldset className="mt-3" disabled={disabled || submitting}>
        <legend className="text-ink-muted">How did you verify?</legend>
        <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
          {CHANNELS.map(([value, label]) => (
            <label key={value} className="flex items-center gap-1">
              <input
                type="radio"
                name={`bank-channel-${excId}`}
                value={value}
                checked={channel === value}
                onChange={() => setChannel(value)}
              />
              {label}
            </label>
          ))}
        </div>

        <label htmlFor={`bank-contact-${excId}`} className="mt-2 block text-ink-muted">
          Who did you speak to?
        </label>
        <input
          id={`bank-contact-${excId}`}
          data-testid="bank-contact"
          className="mt-1 w-full border border-rule bg-paper px-2 py-1"
          maxLength={CONTACT_MAX}
          value={contact}
          onChange={(e) => setContact(e.target.value)}
        />

        <label htmlFor={`bank-note-${excId}`} className="mt-2 block text-ink-muted">
          Note (optional)
        </label>
        <textarea
          id={`bank-note-${excId}`}
          data-testid="bank-note"
          className="mt-1 w-full border border-rule bg-paper px-2 py-1"
          maxLength={NOTE_MAX}
          rows={2}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </fieldset>

      <div aria-live="polite">
        {error !== null && (
          <p role="alert" data-testid="bank-error" className="mt-2 text-flag">
            {error}
          </p>
        )}
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          data-testid="bank-confirm"
          className="border border-rule px-3 py-1"
          disabled={disabled || submitting || !valid}
          aria-keyshortcuts="Control+Enter"
          onClick={() => void submit()}
        >
          Confirm bank details <kbd aria-hidden="true" className="text-ink-muted">Ctrl+Enter</kbd>
        </button>
        <button
          type="button"
          data-testid="bank-leave-open"
          className="border border-rule px-3 py-1"
          onClick={onLeaveOpen}
        >
          Leave open, next
        </button>
      </div>
    </div>
  )
}
