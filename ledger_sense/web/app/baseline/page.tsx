'use client'

import { useEffect, useRef, useState } from 'react'

// The web app and the API are separate origins (next.config.ts,
// docker-compose.yml) — same default host:port as
// ledger_sense/web/e2e/seed.ts's PLAYWRIGHT_API_URL fallback.
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

// ledger_sense.baseline.study.MIN_SAMPLES_FOR_AN_INTERVAL (F-54): a
// t-interval on fewer right-skewed observations is not defensible, so the
// API itself refuses to render a report below this count. Kept in sync by
// hand; a shared constant would remove the duplication if it ever drifts.
const MIN_SAMPLES_FOR_REPORT = 8

// No document-type selector in this screen's four regions (volume counter,
// timing instrument, loaded-rate input, exception log) — every timed sample
// is a pay application, this study's whole sampling frame.
const DOC_TYPE = 'pay_application'

interface LoggedException {
  id: string
  note: string
}

function formatElapsed(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

export default function BaselinePage() {
  const [populationSize, setPopulationSize] = useState('')
  const [samplingFrame, setSamplingFrame] = useState('')
  const [hourly, setHourly] = useState('')
  const [overheadMultiplier, setOverheadMultiplier] = useState('')

  const [studyId, setStudyId] = useState<string | null>(null)
  const [sampleCount, setSampleCount] = useState(0)
  // Fix-wave finding 8: this is the billable week-one timing instrument --
  // an operator seeing an inflated, silently-wrong count with no indication
  // anything failed is exactly the defensibility problem CLAUDE.md's
  // methodology section exists to rule out.
  const [actionError, setActionError] = useState<string | null>(null)

  const [running, setRunning] = useState(false)
  const [elapsedMs, setElapsedMs] = useState(0)
  const startedAtRef = useRef<number | null>(null)

  const [exceptions, setExceptions] = useState<LoggedException[]>([])
  const [exceptionNote, setExceptionNote] = useState('')
  const pendingExceptionsRef = useRef(0)
  const noteInputRef = useRef<HTMLInputElement | null>(null)

  // The window-level keydown handler below is added once and must still see
  // current values, so the state it reads is mirrored into refs rather than
  // recreating the listener on every keystroke.
  const samplingFrameRef = useRef(samplingFrame)
  samplingFrameRef.current = samplingFrame
  const populationRef = useRef(populationSize)
  populationRef.current = populationSize
  const runningRef = useRef(running)
  runningRef.current = running
  const elapsedRef = useRef(elapsedMs)
  elapsedRef.current = elapsedMs
  const studyIdRef = useRef(studyId)
  studyIdRef.current = studyId
  const sampleCountRef = useRef(sampleCount)
  sampleCountRef.current = sampleCount

  useEffect(() => {
    if (!running) return
    const id = window.setInterval(() => {
      if (startedAtRef.current !== null) setElapsedMs(Date.now() - startedAtRef.current)
    }, 100)
    return () => window.clearInterval(id)
  }, [running])

  async function ensureStudy(): Promise<string> {
    if (studyIdRef.current) return studyIdRef.current
    const today = new Date().toISOString().slice(0, 10)
    // ponytail: no date-range control in this screen's four regions, so the
    // collection period defaults to a single day (today). Add a range input
    // if a study needs to be entered through this screen over several days
    // rather than started fresh each day.
    const resp = await fetch(`${API_URL}/api/baseline/studies`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        population_size: Number(populationRef.current) || 0,
        sampling_frame: samplingFrameRef.current,
        collection_period: [today, today],
      }),
    })
    // Fix-wave finding 8, VERIFIED live before the fix: an expired session or
    // validation error still landed here and was cast straight to
    // `{ study_id: string }` with no check, so `study_id` came back
    // `undefined` and every later sample POST 404'd against
    // `/studies/undefined/samples` -- silently, with the visible count still
    // climbing. Fail loudly instead so the caller can roll back.
    if (!resp.ok) {
      throw new Error(`could not start the study (server said ${resp.status})`)
    }
    const body = (await resp.json()) as { study_id: string }
    setStudyId(body.study_id)
    studyIdRef.current = body.study_id
    return body.study_id
  }

  function toggleTimer(): void {
    if (runningRef.current) {
      setRunning(false)
      return
    }
    startedAtRef.current = Date.now() - elapsedRef.current
    setRunning(true)
  }

  async function fileSample(): Promise<void> {
    if (runningRef.current || elapsedRef.current <= 0) return
    const touchSeconds = Math.max(1, Math.round(elapsedRef.current / 1000))
    const exceptionsForThisSample = pendingExceptionsRef.current
    const documentId = `doc-${sampleCountRef.current + 1}`
    // Reset synchronously, before the network round trip below, not after it
    // resolves: a slow POST (this API server is single-process — a Docker
    // demo, not something sized for concurrent load) staying in flight past
    // the next Space/Space/Enter cycle would otherwise null out *that*
    // cycle's startedAtRef and elapsedMs out from under it once this one
    // finally lands, which the elapsedRef.current <= 0 guard above then
    // reads as "nothing timed yet" and silently drops the next sample.
    pendingExceptionsRef.current = 0
    startedAtRef.current = null
    setElapsedMs(0)
    setSampleCount((count) => count + 1)
    setActionError(null)
    try {
      const id = await ensureStudy()
      const resp = await fetch(`${API_URL}/api/baseline/studies/${id}/samples`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          document_id: documentId,
          doc_type: DOC_TYPE,
          touch_seconds: touchSeconds,
          exceptions: exceptionsForThisSample,
        }),
      })
      if (!resp.ok) {
        throw new Error(`sample was not recorded (server said ${resp.status})`)
      }
    } catch (err) {
      // Fix-wave finding 8, VERIFIED live before the fix: the count above was
      // incremented before the round trip and never rolled back, so a failed
      // POST (expired session, validation error, `ensureStudy` itself
      // failing) still left a higher, WRONG visible count with no
      // indication anything failed. Roll it back and say so -- the count
      // must never overstate what the server actually recorded.
      setSampleCount((count) => count - 1)
      setActionError(err instanceof Error ? err.message : 'sample was not recorded')
    }
  }

  useEffect(() => {
    // Space/Enter are page-level shortcuts for the timer, not scoped to
    // whichever field last had focus (a click into "Sampling frame" leaves
    // focus there, and Space still has to start the clock — proven live by
    // the first e2e test in test_baseline_screen.spec.ts, which fills
    // "Sampling frame" last and then drives the whole timing loop by
    // keyboard with focus still sitting there) — the ONE deliberate
    // exception is the exception-note field, which needs a literal space
    // character. WCAG 2.2 §2.1.1 adds a second, narrower exception: Space
    // and Enter are ALSO the native activation keys for a focused <button>,
    // and "Log exception"/"Produce report" have no other keyboard route —
    // so the shortcuts defer to a focused *button* too, but not to inputs
    // in general (an earlier version of this guard deferred to every
    // HTMLInputElement, which silently broke the "Sampling frame" case
    // above: Space stopped starting the clock whenever that field still had
    // focus, exactly the regression the original comment was warning
    // against).
    function onKeyDown(event: KeyboardEvent): void {
      const active = document.activeElement
      if (active instanceof HTMLButtonElement || active === noteInputRef.current) return
      if (event.code === 'Space') {
        event.preventDefault()
        toggleTimer()
      } else if (event.key === 'Enter') {
        event.preventDefault()
        void fileSample()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  function logException(): void {
    const note = exceptionNote.trim()
    if (!note) return
    setExceptions((list) => [...list, { id: crypto.randomUUID(), note }])
    pendingExceptionsRef.current += 1
    setExceptionNote('')
  }

  async function produceReport(): Promise<void> {
    const id = studyIdRef.current
    if (!id) return
    setActionError(null)
    // Opened synchronously, in the same tick as the click, so the browser
    // treats it as a direct response to user activation rather than an
    // unsolicited popup; its destination is filled in once the rate PUT
    // (below) has landed.
    const popup = window.open('about:blank', '_blank')
    const resp = await fetch(`${API_URL}/api/baseline/studies/${id}/rate`, {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hourly, overhead_multiplier: overheadMultiplier }),
    })
    if (!resp.ok) {
      // Fix-wave finding 8: don't send the popup to a report built on a rate
      // that was never actually set -- close it and say so instead.
      if (popup) popup.close()
      setActionError(`could not set the loaded rate (server said ${resp.status})`)
      return
    }
    if (popup) popup.location.href = `${API_URL}/api/baseline/studies/${id}/report`
  }

  const reportBlocked = sampleCount < MIN_SAMPLES_FOR_REPORT

  return (
    <main style={{ padding: '2rem', maxWidth: '40rem' }}>
      <h1 style={{ fontSize: '1.25rem', fontWeight: 600 }}>Baseline study</h1>
      <p style={{ color: 'var(--color-ink-muted)' }}>
        The billable first week. Time real documents, then produce the print-ready report.
      </p>

      {actionError && (
        <p role="alert" data-testid="action-error" style={{ color: 'var(--color-flag)' }}>
          {actionError}
        </p>
      )}

      <section style={{ marginTop: '1.5rem' }}>
        <div>
          <label htmlFor="population-size">Documents in period</label>
          <br />
          <input
            id="population-size"
            type="number"
            min={0}
            value={populationSize}
            onChange={(e) => setPopulationSize(e.target.value)}
          />
        </div>
        <div style={{ marginTop: '0.75rem' }}>
          <label htmlFor="sampling-frame">Sampling frame</label>
          <br />
          <input
            id="sampling-frame"
            type="text"
            value={samplingFrame}
            onChange={(e) => setSamplingFrame(e.target.value)}
            style={{ width: '100%' }}
          />
        </div>
      </section>

      <section style={{ marginTop: '1.5rem' }}>
        <p className="numeric" style={{ fontSize: '2rem', margin: 0 }}>
          {formatElapsed(elapsedMs)}
        </p>
        <button type="button" onClick={toggleTimer}>
          {running ? 'Stop' : 'Start'}
        </button>
        <p style={{ color: 'var(--color-ink-muted)' }}>
          Space starts and stops the timer; Enter files the sample.
        </p>
        <p>
          Samples filed: <span className="numeric" data-testid="sample-count">{sampleCount}</span>
        </p>
      </section>

      <section style={{ marginTop: '1.5rem' }}>
        <div>
          <label htmlFor="hourly-rate">Hourly rate</label>
          <br />
          {/* Never type="number": that permits float coercion in the browser
              before the value ever reaches the API's StrictStr schema. */}
          <input
            id="hourly-rate"
            type="text"
            inputMode="decimal"
            value={hourly}
            onChange={(e) => setHourly(e.target.value)}
          />
        </div>
        <div style={{ marginTop: '0.75rem' }}>
          <label htmlFor="overhead-multiplier">Overhead multiplier</label>
          <br />
          <input
            id="overhead-multiplier"
            type="text"
            inputMode="decimal"
            value={overheadMultiplier}
            onChange={(e) => setOverheadMultiplier(e.target.value)}
          />
        </div>
      </section>

      <section style={{ marginTop: '1.5rem' }}>
        <h2 style={{ fontSize: '1rem', fontWeight: 600 }}>Exception log</h2>
        <ul>
          {exceptions.map((exc) => (
            <li key={exc.id}>{exc.note}</li>
          ))}
        </ul>
        <label htmlFor="exception-note">Exception note</label>
        <br />
        <input
          id="exception-note"
          ref={noteInputRef}
          type="text"
          value={exceptionNote}
          onChange={(e) => setExceptionNote(e.target.value)}
        />
        <button type="button" onClick={logException}>
          Log exception
        </button>
      </section>

      <section style={{ marginTop: '1.5rem' }}>
        <button type="button" onClick={() => void produceReport()} disabled={reportBlocked}>
          Produce report
        </button>
        {reportBlocked && (
          <p data-testid="report-blocked-reason" style={{ color: 'var(--color-ink-muted)' }}>
            Time at least {MIN_SAMPLES_FOR_REPORT} documents before producing a report — a
            confidence interval on fewer is not defensible.
          </p>
        )}
      </section>
    </main>
  )
}
