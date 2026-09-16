'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import * as pdfjs from 'pdfjs-dist'
import { toOverlayStyle, type BBox } from '@/lib/bbox'

pdfjs.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs'

interface AcceptedBox {
  id: string
  page: number
  bbox: BBox
}

interface Props {
  pdfUrl: string
  page: number
  focusBox: BBox | null
  /** Boxes already accepted this session. Their rule STAYS — see Task 31. */
  accepted: ReadonlyArray<AcceptedBox>
  scale?: number
}

export function DocumentPane({ pdfUrl, page, focusBox, accepted, scale = 1.5 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })

  useEffect(() => {
    let cancelled = false
    void (async () => {
      const doc = await pdfjs.getDocument({ url: pdfUrl }).promise
      const pdfPage = await doc.getPage(page + 1)
      const viewport = pdfPage.getViewport({ scale })
      const canvas = canvasRef.current
      if (!canvas || cancelled) return
      canvas.width = viewport.width
      canvas.height = viewport.height
      setSize({ width: viewport.width, height: viewport.height })
      await pdfPage.render({ canvas, viewport }).promise
    })()
    return () => { cancelled = true }
  }, [pdfUrl, page, scale])

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
      <canvas
        ref={canvasRef}
        className="block"
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
       * Four rectangles. Fewer lines than the polygon, works everywhere, and
       * each one animates independently under prefers-reduced-motion. */}
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
            <div key={i} className="absolute bg-ink/40"
                 style={{ position: 'absolute', ...rect }} />
          ))}
        </div>
      )}

      {/* Accepted fields keep a thin rule that STAYS. */}
      {accepted.filter((a) => a.page === page).map((a) => (
        <div
          key={a.id}
          data-testid={`accepted-rule-${a.id}`}
          className="absolute border-b-2 border-accent pointer-events-none"
          style={{ position: 'absolute', ...toOverlayStyle(a.bbox, scale) }}
        />
      ))}

      {focus && (
        <div
          data-testid="focus-box"
          className="absolute border-2 border-accent pointer-events-none"
          style={{ position: 'absolute', ...focus }}
        />
      )}
    </div>
  )
}
