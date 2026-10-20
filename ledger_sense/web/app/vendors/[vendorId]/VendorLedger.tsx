'use client'

import { useEffect, useState } from 'react'

// Same-origin-split convention as Reconciliation.tsx / ReviewQueue.tsx.
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

interface VendorAlias {
  id: string
  name: string
  first_seen: string
  source_document: string
  confirmed_by: string
}

interface PaymentDetail {
  account_last4: string
  routing_last4: string
  observed_at: string
  source_document: string
  confirmed_by: string | null
  confirmed_at: string | null
  reversed_by?: string | null
  reversed_at?: string | null
}

interface VendorLedgerData {
  id: string
  name: string
  aliases: VendorAlias[]
  payment_history: PaymentDetail[]
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

export function VendorLedger({ vendorId }: { vendorId: string }) {
  const [data, setData] = useState<VendorLedgerData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api<VendorLedgerData>(`/api/vendors/${vendorId}/ledger`)
      .then((result) => {
        if (!cancelled) setData(result)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [vendorId])

  if (error !== null) {
    return (
      <main className="p-6">
        <p role="alert">{error}</p>
      </main>
    )
  }
  if (data === null) {
    return (
      <main className="p-6">
        <p>Loading…</p>
      </main>
    )
  }

  return (
    <main className="p-6">
      <h1 className="text-xl font-semibold">{data.name}</h1>

      <section className="mt-6">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-muted">
          Confirmed aliases
        </h2>
        {data.aliases.length === 0 ? (
          <p className="mt-2 text-ink-muted">No confirmed aliases on file for this vendor.</p>
        ) : (
          <table className="mt-2 w-full text-left">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">First seen</th>
                <th scope="col">Source document</th>
                <th scope="col">Confirmed by</th>
              </tr>
            </thead>
            <tbody>
              {data.aliases.map((alias) => (
                <tr key={alias.id}>
                  <td>{alias.name}</td>
                  <td>{alias.first_seen}</td>
                  <td>{alias.source_document}</td>
                  <td>{alias.confirmed_by}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="mt-6">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-muted">
          Payment detail history
        </h2>
        {data.payment_history.length === 0 ? (
          // Never-rule #8's spirit: an unbuilt feature is stated plainly,
          // never a hidden or silently-empty section.
          <p className="mt-2 text-ink-muted">
            No payment details on file for this vendor. Payment-detail tracking is not yet
            connected to any document ingest path.
          </p>
        ) : (
          <table className="mt-2 w-full text-left">
            <thead>
              <tr>
                <th scope="col">Account</th>
                <th scope="col">Routing</th>
                <th scope="col">Observed</th>
                <th scope="col">Source document</th>
                <th scope="col">Confirmed by</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {data.payment_history.map((detail, i) => (
                <tr key={i}>
                  <td>····{detail.account_last4}</td>
                  <td>····{detail.routing_last4}</td>
                  <td>{detail.observed_at}</td>
                  <td>{detail.source_document}</td>
                  <td>{detail.confirmed_by ?? '—'}</td>
                  <td>
                    {detail.reversed_at
                      ? `Reversed by ${detail.reversed_by ?? 'unknown'} on ${detail.reversed_at}`
                      : 'In force'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </main>
  )
}
