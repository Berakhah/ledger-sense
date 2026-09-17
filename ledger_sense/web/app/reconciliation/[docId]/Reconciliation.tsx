'use client'

import { useEffect, useState, type ReactNode } from 'react'

// The web app and the API are separate origins (next.config.ts,
// docker-compose.yml) — same default as app/review/[docId]/ReviewQueue.tsx
// and e2e/seed.ts.
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

/** Mirrors ledger_sense.domain.models.ContinuationLine. Money/percent fields
 *  are Decimal-safe STRINGS (Never-rule #2) and stay strings here — never
 *  parsed to Number. balance_to_finish and percent_complete are optional: a
 *  column not extracted, never a guessed zero. */
interface ContinuationLine {
  item_no: string
  description: string
  scheduled_value: string
  previous: string
  this_period: string
  stored: string
  total_completed_and_stored: string
  retainage: string
  balance_to_finish: string | null
  percent_complete: string | number | null
}

/** Mirrors ledger_sense.api.reconciliation.Discrepancy. `field` is either a
 *  bare summary key ("retainage_total") or a per-line key formatted exactly
 *  like the arithmetic rules' Finding.field ("line[010].total_completed_and_stored") —
 *  the two lookup keys below (lineFieldKey / a bare summary field name) are
 *  built to match those formats exactly rather than parsed out of it. */
interface Discrepancy {
  field: string
  rule_id: string
  expected: string
  actual: string
  delta: string
  arithmetic: string
}

interface ReconciliationData {
  lines: ContinuationLine[]
  summary: Record<string, string>
  discrepancies: Discrepancy[]
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

const LINE_COLUMNS: ReadonlyArray<{ field: keyof ContinuationLine; label: string }> = [
  { field: 'scheduled_value', label: 'Scheduled value' },
  { field: 'previous', label: 'Previous' },
  { field: 'this_period', label: 'This period' },
  { field: 'stored', label: 'Stored' },
  { field: 'total_completed_and_stored', label: 'Total completed and stored' },
  { field: 'retainage', label: 'Retainage' },
  { field: 'balance_to_finish', label: 'Balance to finish' },
  { field: 'percent_complete', label: '% complete' },
]

const SUMMARY_ROWS: ReadonlyArray<{ field: string; label: string }> = [
  { field: 'total_completed_and_stored', label: 'Total completed and stored' },
  { field: 'retainage_total', label: 'Retainage' },
  { field: 'previous_payments', label: 'Previous payments' },
  { field: 'change_orders_net', label: 'Net change orders' },
]

function lineFieldKey(itemNo: string, field: string): string {
  return `line[${itemNo}].${field}`
}

function formatCell(value: string | number | null): string {
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

/** A discrepant cell carries a severity glyph (role="img", named) AND a
 *  visible text label — never colour alone — and its `arithmetic` derivation
 *  inline underneath, not in a tooltip: this screen gets printed and
 *  forwarded to auditors. */
function DiscrepantCell({ value, discrepancy }: { value: string; discrepancy: Discrepancy }) {
  return (
    <td
      data-testid="discrepant-cell"
      className="numeric border-2 border-flag bg-flag/10 px-2 py-1 align-top text-right"
    >
      <div className="flex items-center justify-end gap-1 text-xs font-semibold uppercase tracking-wide text-flag">
        <span role="img" aria-label="Discrepancy">
          &#9650;
        </span>
        <span>Discrepancy</span>
      </div>
      <div className="mt-0.5">{value}</div>
      <div
        data-testid="inline-arithmetic"
        className="mt-1 whitespace-normal text-left text-xs font-normal normal-case tracking-normal text-ink-muted"
      >
        {discrepancy.arithmetic}
      </div>
    </td>
  )
}

function PlainCell({ value }: { value: string }) {
  return <td className="numeric px-2 py-1 text-right">{value}</td>
}

export function Reconciliation({ docId }: { docId: string }) {
  const [data, setData] = useState<ReconciliationData | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const body = await api<ReconciliationData>(`/api/reconciliation/${docId}`)
        if (!cancelled) setData(body)
      } catch (err) {
        if (!cancelled) {
          setLoadError(err instanceof Error ? err.message : 'Could not load the reconciliation')
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [docId])

  if (loadError) {
    return (
      <main className="mx-auto max-w-full px-4 py-6 md:px-8">
        <h1 className="text-xl font-semibold">Reconciliation</h1>
        <p role="alert" data-testid="load-error" className="mt-4 text-flag">
          Could not load the reconciliation: {loadError}
        </p>
      </main>
    )
  }

  if (!data) {
    return (
      <main className="mx-auto max-w-full px-4 py-6 md:px-8">
        <h1 className="text-xl font-semibold">Reconciliation</h1>
        <p data-testid="reconciliation-loading" className="mt-4 text-ink-muted">
          Loading reconciliation…
        </p>
      </main>
    )
  }

  // Bound to a local const, not read as `data` inside the closures below:
  // TypeScript does not carry the `data !== null` narrowing from the early
  // returns above into a nested function declaration, since it cannot prove
  // the outer `data` state binding is unchanged by the time that function
  // runs. `reconciliation` is that same value, narrowed once and captured.
  const reconciliation = data
  const byField = new Map(reconciliation.discrepancies.map((d) => [d.field, d]))

  // Every key a rendered line or summary cell will look up, computed BEFORE
  // any JSX runs — not as a side effect of rendering. An earlier version
  // built this set by having lineCell/summaryCell mutate it while React
  // walked the table JSX and read it immediately afterwards on the very same
  // line; JS evaluates top-to-bottom, so that read always ran before any
  // cell had actually been rendered, and every discrepancy came back
  // "unconsumed" — doubled into the fallback section below on top of its
  // correct cell. Fixed live against the browser (not just the API's own
  // tests, which never render a cell): renderableKeys is now a pure
  // computation the two lookups below only read from.
  const renderableKeys = new Set<string>()
  for (const line of reconciliation.lines) {
    for (const col of LINE_COLUMNS) renderableKeys.add(lineFieldKey(line.item_no, col.field))
  }
  for (const row of SUMMARY_ROWS) renderableKeys.add(row.field)

  function lineCell(line: ContinuationLine, field: keyof ContinuationLine): ReactNode {
    const key = lineFieldKey(line.item_no, field)
    const value = formatCell(line[field])
    const discrepancy = byField.get(key)
    if (discrepancy) {
      return <DiscrepantCell key={field} value={value} discrepancy={discrepancy} />
    }
    return <PlainCell key={field} value={value} />
  }

  function summaryCell(field: string): ReactNode {
    const value = formatCell(reconciliation.summary[field] ?? null)
    const discrepancy = byField.get(field)
    if (discrepancy) {
      return <DiscrepantCell value={value} discrepancy={discrepancy} />
    }
    return <PlainCell value={value} />
  }

  const otherDiscrepancies = reconciliation.discrepancies.filter(
    (d) => !renderableKeys.has(d.field),
  )

  return (
    <main className="mx-auto max-w-full px-4 py-6 md:px-8">
      <header className="border-b border-rule pb-3">
        <h1 className="text-xl font-semibold">Reconciliation</h1>
        <p className="text-sm text-ink-muted">
          Continuation sheet against the summary — document{' '}
          <span className="numeric">{docId}</span>.
        </p>
      </header>

      {/* Two panes side by side, joined by a rule at the footing row: the
          continuation sheet's own bottom border meets the summary pane's top
          border below 768px, and its left border above it. */}
      <div className="reconciliation-grid mt-4 grid grid-cols-[minmax(0,1fr)] gap-6 md:grid-cols-[minmax(0,2fr)_minmax(16rem,1fr)]">
        <section aria-label="Continuation sheet" className="max-w-full overflow-x-auto">
          <h2 className="text-sm font-semibold">Continuation sheet</h2>
          <table className="mt-2 w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-rule text-left text-xs uppercase tracking-wide text-ink-muted">
                <th scope="col" className="px-2 py-1">Item</th>
                <th scope="col" className="px-2 py-1">Description</th>
                {LINE_COLUMNS.map((col) => (
                  <th key={col.field} scope="col" className="px-2 py-1 text-right">
                    {col.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.lines.map((line) => (
                <tr key={line.item_no} className="border-b border-rule">
                  <td className="numeric px-2 py-1">{line.item_no}</td>
                  <td className="px-2 py-1">{line.description}</td>
                  {LINE_COLUMNS.map((col) => lineCell(line, col.field))}
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section
          aria-label="Summary"
          className="border-t-2 border-ink pt-3 md:border-l-2 md:border-t-0 md:pl-6 md:pt-0"
        >
          <h2 className="text-sm font-semibold">Summary</h2>
          <table className="mt-2 w-full border-collapse text-sm">
            <tbody>
              {SUMMARY_ROWS.map((row) => (
                <tr key={row.field} className="border-b border-rule">
                  <td className="px-2 py-1 text-ink-muted">{row.label}</td>
                  {summaryCell(row.field)}
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>

      {otherDiscrepancies.length > 0 && (
        <section className="mt-6 border-t border-rule pt-4" aria-label="Other discrepancies">
          <h2 className="text-sm font-semibold">Other discrepancies</h2>
          <ul className="mt-2 space-y-2 text-sm">
            {otherDiscrepancies.map((d) => (
              <li
                key={`${d.rule_id}-${d.field}`}
                data-testid="discrepant-cell"
                className="border-2 border-flag bg-flag/10 px-2 py-1"
              >
                <div className="flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-flag">
                  <span role="img" aria-label="Discrepancy">
                    &#9650;
                  </span>
                  <span>Discrepancy</span>
                </div>
                <p className="numeric mt-0.5">
                  {d.field}: {d.actual}
                </p>
                <p data-testid="inline-arithmetic" className="mt-1 text-xs text-ink-muted">
                  {d.arithmetic}
                </p>
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  )
}
