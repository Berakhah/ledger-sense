'use client'

import { useEffect, useState } from 'react'

// The web app and the API are separate origins (next.config.ts,
// docker-compose.yml) — same default as app/baseline/page.tsx and
// app/reconciliation/[docId]/Reconciliation.tsx.
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

/** Mirrors the row shape ledger_sense/api/inbox.py's `_row`/`_row_of` emit
 *  (GET /api/inbox, `items`). There is deliberately no `confidence_band`
 *  field: no field type has an earned reliability curve yet (CLAUDE.md,
 *  "Confidence routing"), so no document-level confidence number exists to
 *  show — `finding_count` (open findings) is the flag count this screen
 *  surfaces instead, and `status` carries the routing outcome as text. */
interface InboxRow {
  id: string
  filename: string
  sensitivity: string
  origin: string
  received_at: string
  status: string
  doc_type: string | null
  run_note: string | null
  finding_count: number
  claim_count: number
}

async function api<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { credentials: 'include' })
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
  return (await response.json()) as T
}

const RECEIVED_AT_FORMAT = new Intl.DateTimeFormat('en-US', {
  dateStyle: 'medium',
  timeStyle: 'short',
})

function formatReceivedAt(iso: string): string {
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return iso
  return RECEIVED_AT_FORMAT.format(parsed)
}

function flagLabel(count: number): string {
  return `${count} ${count === 1 ? 'flag' : 'flags'}`
}

export default function InboxPage() {
  const [rows, setRows] = useState<InboxRow[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const body = await api<{ items: InboxRow[] }>('/api/inbox')
        if (!cancelled) setRows(body.items)
      } catch (err) {
        if (!cancelled) {
          setLoadError(err instanceof Error ? err.message : 'Could not load the inbox')
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <main className="mx-auto max-w-full px-4 py-6 md:px-8">
      <header className="border-b border-rule pb-3">
        <h1 className="text-xl font-semibold">Inbox</h1>
        <p className="text-sm text-ink-muted">
          Every document that has reached the intake door — including the ones the deterministic
          path could not extract. Hiding a failure here would be lying about the straight-through
          rate.
        </p>
      </header>

      {loadError && (
        <p role="alert" data-testid="load-error" className="mt-4 text-flag">
          Could not load the inbox: {loadError}
        </p>
      )}

      {!loadError && rows === null && (
        <p data-testid="inbox-loading" className="mt-4 text-ink-muted">
          Loading inbox…
        </p>
      )}

      {!loadError && rows !== null && rows.length === 0 && (
        <p data-testid="inbox-empty" className="mt-4 text-ink-muted">
          No documents yet.
        </p>
      )}

      {!loadError && rows !== null && rows.length > 0 && (
        <table className="mt-4 w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-rule text-left text-xs uppercase tracking-wide text-ink-muted">
              <th scope="col" className="px-2 py-1">Filename</th>
              <th scope="col" className="px-2 py-1">Status</th>
              <th scope="col" className="px-2 py-1">Sensitivity</th>
              <th scope="col" className="px-2 py-1 text-right">Flags</th>
              <th scope="col" className="px-2 py-1">Received</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id} data-testid="inbox-row" data-status={row.status} className="border-b border-rule">
                <td className="px-2 py-1">{row.filename}</td>
                {/* Status is a plain text word, never colour alone (00 §5.4):
                    a `cannot_extract` row stays in this list, visibly
                    labelled, rather than being filtered out — hiding a
                    failure is how a demo lies. */}
                <td className="px-2 py-1" data-testid="inbox-status">
                  {row.status}
                </td>
                <td className="px-2 py-1" data-testid="inbox-sensitivity">
                  {row.sensitivity}
                </td>
                <td className="numeric px-2 py-1 text-right" data-testid="inbox-flag-count">
                  {flagLabel(row.finding_count)}
                </td>
                <td className="numeric px-2 py-1">{formatReceivedAt(row.received_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  )
}
