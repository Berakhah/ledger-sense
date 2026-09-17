'use client'

import dynamic from 'next/dynamic'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import type { BBox } from '@/lib/bbox'
import { useReviewKeys } from '@/lib/useReviewKeys'

// Browser only. pdf.js renders to a canvas and has nothing to say on the
// server; its module scope also touches globals (`Iterator`) that a Node 20
// pre-render lacks, which turned the route into an intermittent 500.
const DocumentPane = dynamic(
  () => import('@/components/DocumentPane').then((m) => m.DocumentPane),
  { ssr: false },
)

// The web app and the API are separate origins (next.config.ts,
// docker-compose.yml) — same default as app/baseline/page.tsx and e2e/seed.ts.
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

/** US Letter landscape, in PDF points — ledger_sense/corpus/layouts.py's
 *  PAGE_WIDTH_PT. DocumentPane does not report the rendered page's width, and
 *  every pay application the corpus generates is this wide, so the
 *  fit-to-width scale is derived from it until the pane exposes the real
 *  page size. */
const PAGE_WIDTH_PT = 792

/** The pane never renders larger than this (F-63): 792pt x 1.5 is already
 *  ~1,188 CSS px tall, and a wider canvas buys nothing a reviewer can read. */
const MAX_SCALE = 1.5

type ExceptionState = 'open' | 'accepted' | 'edited' | 'rejected'

/** Mirrors ledger_sense.api.review.ReviewException. `expected`/`actual` are
 *  Decimal-safe STRINGS and stay strings here — never parsed to Number
 *  (Never-rule #2); an edit goes back to the API as the reviewer typed it. */
interface ReviewException {
  id: string
  doc_id: string
  field: string
  rule_id: string
  message: string
  expected: string | null
  actual: string | null
  page: number | null
  bbox: [number, number, number, number] | null
  state: ExceptionState
}

interface Progress {
  total: number
  cleared: number
  remaining: number
}

interface AcceptedBox {
  id: string
  page: number
  bbox: BBox
}

const STATE_LABEL: Record<ExceptionState, string> = {
  open: 'Open',
  accepted: 'Accepted',
  edited: 'Edited',
  rejected: 'Rejected',
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { credentials: 'include', ...init })
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

function postJson(path: string, body: unknown): Promise<unknown> {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** The next OPEN exception after `from`, searching forward and wrapping to
 *  the earlier ones; null when nothing is open any more. */
function nextOpen(items: ReadonlyArray<ReviewException>, from: number): number | null {
  for (let step = 1; step <= items.length; step++) {
    const i = (from + step) % items.length
    if (items[i]?.state === 'open') return i
  }
  return null
}

export function ReviewQueue({ docId }: { docId: string }) {
  const [items, setItems] = useState<ReviewException[]>([])
  const [cursor, setCursor] = useState(0)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [accepted, setAccepted] = useState<AcceptedBox[]>([])
  const [editValue, setEditValue] = useState('')
  const [reason, setReason] = useState('')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  // The queue's source of truth. A keystroke must see the previous one's
  // result immediately, before React has rendered it — a reviewer pressing
  // Enter fifty times must clear fifty exceptions, not race fifty requests at
  // the first one — so the model lives in a ref and `commit` mirrors it into
  // state synchronously.
  const model = useRef<{ items: ReviewException[]; cursor: number }>({ items: [], cursor: 0 })
  const loaded = useRef(false)
  const typedAhead = useRef<Array<() => void>>([])

  const queueRef = useRef<HTMLDivElement | null>(null)
  const editRef = useRef<HTMLInputElement | null>(null)
  const reasonRef = useRef<HTMLInputElement | null>(null)
  const paneRef = useRef<HTMLDivElement | null>(null)
  const [paneWidth, setPaneWidth] = useState<number | null>(null)

  // Mirrors the model into state in one synchronous flush. The edit field is
  // (re)filled here too, not in a trailing effect: E may run the instant the
  // queue lands, and an effect that fills the input afterwards would undo
  // the selection E just made.
  const shownId = useRef<string | null>(null)
  const commit = useCallback(() => {
    const { items, cursor } = model.current
    const current = items[cursor] ?? null
    flushSync(() => {
      setItems([...items])
      setCursor(cursor)
      if (current?.id !== shownId.current) {
        shownId.current = current?.id ?? null
        setEditValue(current?.actual ?? '')
        setReason('')
      }
    })
  }, [])

  const focusQueue = useCallback(() => {
    queueRef.current?.focus()
  }, [])

  // Never awaited by a keystroke. Responses can land out of order, so only
  // the latest request issued is allowed to set the count.
  const progressSeq = useRef(0)
  const refetchProgress = useCallback(() => {
    const seq = ++progressSeq.current
    void api<Progress>(`/api/review/documents/${docId}/progress`)
      .then((prog) => {
        if (seq === progressSeq.current) setProgress(prog)
      })
      .catch((err: unknown) => {
        setActionError(err instanceof Error ? err.message : 'Could not refresh progress')
      })
  }, [docId])

  // Initial load.
  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const [list, prog] = await Promise.all([
          api<ReviewException[]>(`/api/review/documents/${docId}/exceptions`),
          api<Progress>(`/api/review/documents/${docId}/progress`),
        ])
        if (cancelled) return
        const first = list.findIndex((e) => e.state === 'open')
        model.current = { items: list, cursor: first === -1 ? Math.max(0, list.length - 1) : first }
        commit()
        setProgress(prog)
        focusQueue()
        loaded.current = true
        for (const action of typedAhead.current.splice(0)) action()
      } catch (err) {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : 'Could not load the queue')
      }
    })()
    return () => {
      cancelled = true
    }
  }, [docId, commit, focusQueue])

  const current = items[cursor] ?? null

  // F-63: fit the page to the pane, never the pane to the page.
  useLayoutEffect(() => {
    const el = paneRef.current
    if (!el) return
    const measure = () => setPaneWidth(el.clientWidth)
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  const scale = paneWidth === null || paneWidth === 0 ? null : Math.min(MAX_SCALE, paneWidth / PAGE_WIDTH_PT)

  const move = useCallback((delta: number) => {
    const { items, cursor } = model.current
    if (items.length === 0) return
    model.current = { items, cursor: Math.min(items.length - 1, Math.max(0, cursor + delta)) }
    commit()
  }, [commit])

  const onNext = useCallback(() => move(1), [move])
  const onPrev = useCallback(() => move(-1), [move])

  const setItemState = useCallback((id: string, state: ExceptionState) => {
    const { items, cursor } = model.current
    model.current = { items: items.map((e) => (e.id === id ? { ...e, state } : e)), cursor }
  }, [])

  /** Act on the open exception under the cursor, or say why not: Enter on an
   *  item K brought back to is a no-op with a reason, not a 409 from the API.
   *
   *  Optimistic on purpose. The item closes and the cursor advances on the
   *  keystroke, not on the response: the keyboard model is the interface, and
   *  a cursor that lags the network lags the reviewer. A failed write reopens
   *  the item where it was — the next wrap of the queue finds it again — and
   *  says so. */
  const actOnCurrent = useCallback((
    state: ExceptionState,
    request: (item: ReviewException) => Promise<unknown>,
  ) => {
    const { items, cursor } = model.current
    const item = items[cursor]
    if (!item) return
    if (item.state !== 'open') {
      setActionError(`This exception is already ${STATE_LABEL[item.state].toLowerCase()}.`)
      return
    }
    setActionError(null)
    setItemState(item.id, state)
    model.current = { ...model.current, cursor: nextOpen(model.current.items, cursor) ?? cursor }
    commit()

    void request(item)
      .then(() => {
        if (state !== 'rejected' && item.page !== null && item.bbox !== null) {
          const box: AcceptedBox = { id: item.id, page: item.page, bbox: item.bbox }
          setAccepted((prev) => [...prev, box])
        }
        refetchProgress()
      })
      .catch((err: unknown) => {
        setItemState(item.id, 'open')
        commit()
        const detail = err instanceof Error ? err.message : 'the write failed'
        setActionError(`${item.field} is still open: ${detail}`)
      })
  }, [commit, refetchProgress, setItemState])

  const onAccept = useCallback(() => {
    actOnCurrent('accepted', (item) => postJson(`/api/review/exceptions/${item.id}/accept`, {}))
  }, [actOnCurrent])

  // The value goes to the API exactly as typed — a string, never a Number.
  const submitEdit = useCallback((value: string) => {
    actOnCurrent('edited', (item) => postJson(`/api/review/exceptions/${item.id}/edit`, { value }))
    focusQueue()
  }, [actOnCurrent, focusQueue])

  const submitReject = useCallback((why: string) => {
    if (why.trim() === '') return
    actOnCurrent('rejected', (item) =>
      postJson(`/api/review/exceptions/${item.id}/reject`, { reason: why }))
    focusQueue()
  }, [actOnCurrent, focusQueue])

  const onEdit = useCallback(() => {
    const input = editRef.current
    if (!input) return
    input.focus()
    input.select()
  }, [])

  const onReject = useCallback(() => {
    reasonRef.current?.focus()
  }, [])

  // Type-ahead. A reviewer who presses a key while the queue is still loading
  // has said what they want; the keystroke waits for the data rather than
  // being dropped on an empty page, and replays in the order it was typed.
  const typeAhead = useCallback(
    (action: () => void) => () => {
      if (loaded.current) action()
      else typedAhead.current.push(action)
    },
    [],
  )
  const keys = useMemo(
    () => ({
      onNext: typeAhead(onNext),
      onPrev: typeAhead(onPrev),
      onAccept: typeAhead(onAccept),
      onEdit: typeAhead(onEdit),
      onReject: typeAhead(onReject),
    }),
    [typeAhead, onNext, onPrev, onAccept, onEdit, onReject],
  )
  useReviewKeys({ ...keys, enabled: loadError === null })

  const jumpToDocument = useCallback(() => {
    paneRef.current?.scrollIntoView({ block: 'start' })
    paneRef.current?.focus()
  }, [])

  const total = progress?.total ?? items.length
  const cleared = progress?.cleared ?? 0
  const allCleared = items.length > 0 && items.every((e) => e.state !== 'open')
  const focusBox: BBox | null = current?.bbox ?? null

  return (
    <main className="mx-auto max-w-full px-4 py-6 md:px-8">
      <header className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 border-b border-rule pb-3">
        <h1 className="text-xl font-semibold">Review queue</h1>
        <p className="flex gap-x-4 text-sm text-ink-muted">
          {/* Always rendered, even before the first item, so the announcement
              region exists before the first transition and assistive
              technology has something to watch. */}
          <span role="status" aria-live="polite" className="numeric">
            {current ? `${cursor + 1} of ${items.length}` : ''}
          </span>
          <span>
            Cleared{' '}
            <span className="numeric text-ink" data-testid="progress">
              {cleared} / {total}
            </span>
          </span>
        </p>
      </header>

      {loadError && (
        <p role="alert" data-testid="load-error" className="mt-4 text-flag">
          Could not load the queue: {loadError}
        </p>
      )}

      {/* minmax(0, …) on every track, in both layouts: an `auto` track grows
          to fit the canvas inside it, and the pane would then measure a
          container it had itself widened. */}
      <div className="mt-4 grid gap-6 grid-cols-[minmax(0,1fr)] md:grid-cols-[minmax(0,1fr)_minmax(18rem,26rem)]">
        {/* Exception detail. First in the stack below 768px; right-hand
            column above it. The queue's own focus target: Enter here is
            the hook's, not a control's (data-review-shortcuts). */}
        <section
          ref={queueRef}
          tabIndex={0}
          data-review-shortcuts="true"
          aria-label="Exception under review"
          className="order-1 md:order-2"
        >
          {current ? (
            <>
              <p className="text-xs uppercase tracking-wide text-ink-muted">
                Field · {STATE_LABEL[current.state]}
              </p>
              <h2 className="numeric mt-1 text-base font-semibold" data-testid="current-field">
                {current.field}
              </h2>

              {allCleared && (
                <p data-testid="all-cleared" className="mt-3 border-l-2 border-accent pl-3 text-sm">
                  All {items.length} exceptions cleared. Nothing is left open on this document.
                </p>
              )}

              <div className="mt-4">
                <label htmlFor="edit-input" className="block text-sm text-ink-muted">
                  Extracted value
                </label>
                <input
                  id="edit-input"
                  ref={editRef}
                  data-testid="edit-input"
                  className="numeric mt-1 w-full border border-rule bg-paper px-2 py-1"
                  value={editValue}
                  disabled={current.state !== 'open'}
                  onChange={(e) => setEditValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault()
                      submitEdit(editValue)
                    } else if (e.key === 'Escape') {
                      e.preventDefault()
                      focusQueue()
                    }
                  }}
                />
              </div>

              <h3 className="mt-5 text-sm font-semibold">Rules that fired</h3>
              <ul className="mt-1 divide-y divide-rule border-y border-rule text-sm">
                <li className="py-2">
                  <p>
                    <span className="numeric text-ink-muted">{current.rule_id}</span>{' '}
                    {current.message}
                  </p>
                  <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5">
                    <dt className="text-ink-muted">Expected</dt>
                    <dd className="numeric">{current.expected ?? '—'}</dd>
                    <dt className="text-ink-muted">Actual</dt>
                    <dd className="numeric">{current.actual ?? '—'}</dd>
                  </dl>
                </li>
              </ul>

              <h3 className="mt-5 text-sm font-semibold">Provenance</h3>
              <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 border-l-2 border-rule pl-3 text-sm">
                <dt className="text-ink-muted">Page</dt>
                <dd className="numeric">{current.page === null ? 'not located' : current.page + 1}</dd>
                <dt className="text-ink-muted">Box</dt>
                <dd className="numeric">
                  {current.bbox ? current.bbox.map((v) => v.toFixed(1)).join(', ') + ' pt' : 'not located'}
                </dd>
                <dt className="text-ink-muted">Confidence</dt>
                <dd className="text-ink-muted">uncalibrated</dd>
              </dl>

              <div className="mt-5">
                <label htmlFor="reject-reason" className="block text-sm text-ink-muted">
                  Reason for rejection
                </label>
                <input
                  id="reject-reason"
                  ref={reasonRef}
                  data-testid="reject-reason"
                  className="mt-1 w-full border border-rule bg-paper px-2 py-1"
                  value={reason}
                  disabled={current.state !== 'open'}
                  onChange={(e) => setReason(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault()
                      submitReject(reason)
                    } else if (e.key === 'Escape') {
                      e.preventDefault()
                      focusQueue()
                    }
                  }}
                />
              </div>

              {actionError && (
                <p role="alert" data-testid="action-error" className="mt-3 text-sm text-flag">
                  {actionError}
                </p>
              )}

              <div className="mt-5 flex flex-wrap gap-2 text-sm">
                <button type="button" className="border border-rule px-3 py-1" onClick={onAccept}>
                  Accept <kbd className="text-ink-muted">Enter</kbd>
                </button>
                <button type="button" className="border border-rule px-3 py-1" onClick={onEdit}>
                  Edit <kbd className="text-ink-muted">E</kbd>
                </button>
                <button type="button" className="border border-rule px-3 py-1" onClick={onReject}>
                  Reject <kbd className="text-ink-muted">R</kbd>
                </button>
                <button type="button" className="border border-rule px-3 py-1" onClick={onPrev}>
                  Previous <kbd className="text-ink-muted">K</kbd>
                </button>
                <button type="button" className="border border-rule px-3 py-1" onClick={onNext}>
                  Next <kbd className="text-ink-muted">J</kbd>
                </button>
                <button
                  type="button"
                  data-testid="jump-to-document"
                  className="border border-rule px-3 py-1 md:hidden"
                  onClick={jumpToDocument}
                >
                  Jump to document
                </button>
              </div>
            </>
          ) : (
            !loadError && <p className="text-ink-muted">No exceptions on this document.</p>
          )}
        </section>

        {/* The document. overflow-x: auto inside max-width: 100% — the pane
            scrolls, the page never does (F-63). */}
        <div
          ref={paneRef}
          role="region"
          tabIndex={0}
          data-review-shortcuts="true"
          aria-label="Document"
          className="order-2 max-w-full overflow-x-auto md:order-1"
        >
          {current && scale !== null && (
            <DocumentPane
              pdfUrl={`${API_URL}/api/inbox/${docId}/pdf`}
              page={current.page ?? 0}
              focusBox={focusBox}
              accepted={accepted}
              scale={scale}
            />
          )}
        </div>
      </div>
    </main>
  )
}
