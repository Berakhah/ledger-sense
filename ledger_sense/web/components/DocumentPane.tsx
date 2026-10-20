'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import * as pdfjs from 'pdfjs-dist'
import type { AcceptedBox } from '@/lib/acceptedBoxes'
import { toOverlayStyle, type BBox } from '@/lib/bbox'

pdfjs.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs'

interface Props {
  pdfUrl: string
  page: number
  focusBox: BBox | null
  /** Fields the server records as verified (accepted or edited). Their rule
   *  STAYS — across keystrokes and across navigation (Task 31). */
  accepted: ReadonlyArray<AcceptedBox>
  scale?: number
}

export function DocumentPane({ pdfUrl, page, focusBox, accepted, scale = 1.5 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  const [loadError, setLoadError] = useState<string | null>(null)
  const [doc, setDoc] = useState<pdfjs.PDFDocumentProxy | null>(null)

  // Fetch once per document. A scale change (the review queue derives scale
  // from its container width, so every resize tick is one) must re-render,
  // never re-fetch the bytes.
  useEffect(() => {
    let cancelled = false
    // withCredentials: the API is a separate origin and the bytes are behind
    // the session cookie (GET /api/inbox/{id}/pdf); without it every fetch
    // is anonymous and 401s.
    const loadingTask = pdfjs.getDocument({ url: pdfUrl, withCredentials: true })
    setLoadError(null)
    setDoc(null)
    void (async () => {
      try {
        const loaded = await loadingTask.promise
        if (!cancelled) setDoc(loaded)
      } catch (err) {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : 'Could not load document')
      }
    })()
    return () => {
      cancelled = true
      void loadingTask.destroy()
    }
  }, [pdfUrl])

  // Render the page at the current scale from the already-loaded document.
  useEffect(() => {
    if (!doc) return
    let cancelled = false
    // pdf.js refuses two overlapping render() calls on one canvas, so a
    // superseded render is cancelled, not left to finish.
    let inFlight: pdfjs.RenderTask | null = null
    void (async () => {
      try {
        const pdfPage = await doc.getPage(page + 1)
        const viewport = pdfPage.getViewport({ scale })
        const canvas = canvasRef.current
        if (!canvas || cancelled) return
        canvas.width = viewport.width
        canvas.height = viewport.height
        setSize({ width: viewport.width, height: viewport.height })
        inFlight = pdfPage.render({ canvas, viewport })
        await inFlight.promise
      } catch (err) {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : 'Could not load document')
      }
    })()
    return () => {
      cancelled = true
      inFlight?.cancel()
    }
  }, [doc, page, scale])

  const focus = useMemo(
    () => (focusBox ? toOverlayStyle(focusBox, scale) : null),
    [focusBox, scale],
  )

  return (
    <div
      className="relative"
      style={{ width: size.width, height: size.height }}
      data-testid="document-pane"
    >
      {loadError && (
        <div role="alert" data-testid="document-pane-error" className="text-flag">
          Could not load document: {loadError}
        </div>
      )}

      <canvas
        ref={canvasRef}
        className="document-page block"
        role="img"
        aria-label={`Document page ${page + 1}`}
      />

      {/* Dim everything except the field under review.
       *
       * REVISED (F-65). This was one clip-path polygon: an outer rectangle
       * followed by the focus rectangle, comma-joined. Under the DEFAULT
       * `nonzero` fill rule, with both subpaths wound the same direction, no
       * hole appears — so the layer dimmed the field under review, which is the
       * opposite of the intent, on the signature screen.
       *
       * Four rectangles instead: fewer lines than the polygon, and correct
       * under every fill rule. */}
      {focus && (
        <div aria-hidden data-testid="dim-layer" className="pointer-events-none">
          {[
            { top: 0, left: 0, width: size.width, height: focus.top },
            { top: focus.top + focus.height, left: 0, width: size.width,
              height: Math.max(0, size.height - focus.top - focus.height) },
            { top: focus.top, left: 0, width: focus.left, height: focus.height },
            { top: focus.top, left: focus.left + focus.width,
              width: Math.max(0, size.width - focus.left - focus.width),
              height: focus.height },
          ].map((rect, i) => (
            <div key={i} className="absolute bg-dim" style={rect} />
          ))}
        </div>
      )}

      {/* Accepted fields keep a thin rule that STAYS. */}
      {accepted.filter((a) => a.page === page).map((a) => (
        <div
          key={a.id}
          aria-hidden
          data-testid={`accepted-rule-${a.id}`}
          className="absolute border-b-2 border-accent pointer-events-none"
          style={toOverlayStyle(a.bbox, scale)}
        />
      ))}

      {focus && (
        <div
          data-testid="focus-box"
          className="absolute border-2 border-accent pointer-events-none"
          style={focus}
        />
      )}
    </div>
  )
}
