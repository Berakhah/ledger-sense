'use client'

import { useEffect, useState } from 'react'

/** Mirrors ledger_sense.api.review.VendorCandidate / VendorMatch. */
interface VendorCandidate {
  vendor_id: string
  name: string
  score: number
}

interface VendorMatchData {
  observed_name: string
  candidate: VendorCandidate | null
}

async function fetchVendorMatch(apiUrl: string, excId: string): Promise<VendorMatchData> {
  const response = await fetch(`${apiUrl}/api/review/exceptions/${excId}/vendor-match`, {
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
  return (await response.json()) as VendorMatchData
}

/** The I2 exception's confirm-vendor step: an I2 finding's own
 *  expected/actual are placeholder money strings (rules/integrity/vendors.py
 *  always returns "0.00"/"0.00"), so this fetches the observed name and the
 *  ledger's own fuzzy-match candidate from the dedicated vendor-match
 *  endpoint rather than parsing it out of the finding's free-text message.
 *
 *  A standalone component, not inline in ReviewQueue.tsx: ReviewQueue pulls
 *  in DocumentPane (pdf.js, ssr:false), which has nothing to say to jsdom, so
 *  keeping the confirm-vendor UI self-contained is what makes it unit
 *  testable at all. */
export function VendorConfirmPanel({
  excId,
  apiUrl,
  disabled,
  onConfirmExisting,
  onConfirmNew,
}: {
  excId: string
  apiUrl: string
  disabled: boolean
  onConfirmExisting: (vendorId: string) => void
  onConfirmNew: (name: string) => void
}) {
  const [match, setMatch] = useState<VendorMatchData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [newName, setNewName] = useState('')

  useEffect(() => {
    let cancelled = false
    setMatch(null)
    setError(null)
    fetchVendorMatch(apiUrl, excId)
      .then((result) => {
        if (cancelled) return
        setMatch(result)
        setNewName(result.observed_name)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [apiUrl, excId])

  if (error !== null) {
    return (
      <p role="alert" data-testid="vendor-match-error" className="mt-3 text-sm text-flag">
        Could not check this vendor: {error}
      </p>
    )
  }
  if (match === null) {
    return (
      <p data-testid="vendor-match-loading" className="mt-3 text-sm text-ink-muted">
        Checking vendor…
      </p>
    )
  }

  return (
    <div data-testid="vendor-confirm-panel" className="mt-3 border-l-2 border-rule pl-3 text-sm">
      <p className="text-ink-muted">
        Extracted vendor: <span className="text-ink">{match.observed_name}</span>
      </p>
      {match.candidate && (
        <button
          type="button"
          disabled={disabled}
          className="mt-2 border border-rule px-3 py-1"
          onClick={() => onConfirmExisting(match.candidate!.vendor_id)}
        >
          Yes, it&rsquo;s {match.candidate.name}
        </button>
      )}
      <div className="mt-2">
        <label htmlFor="new-vendor-name" className="block text-ink-muted">
          {match.candidate ? 'Or, the name of the new vendor' : 'New vendor'}
        </label>
        <input
          id="new-vendor-name"
          data-testid="new-vendor-name"
          className="mt-1 w-full border border-rule bg-paper px-2 py-1"
          value={newName}
          disabled={disabled}
          onChange={(e) => setNewName(e.target.value)}
        />
        <button
          type="button"
          disabled={disabled || newName.trim() === ''}
          className="mt-2 border border-rule px-3 py-1"
          onClick={() => onConfirmNew(newName.trim())}
        >
          {match.candidate ? 'No, new vendor' : 'Confirm new vendor'}
        </button>
      </div>
    </div>
  )
}
