# Ledger Sense — Phase 4: Review Queue and Reconciliation (Week 9)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reviewer clears fifty exceptions without touching the mouse, watching the document visibly accumulate verification as they go.

**Architecture:** A FastAPI review queue where every human action writes a `HUMAN`-method `Claim`; a split view rendering the document at fidelity via `pdf.js` with the field under review boxed from its Claim's bbox; a keyboard model as the primary interface, not an accelerator; and a reconciliation view that shows the arithmetic at the exact discrepant cell.

**Tech Stack:** FastAPI, Next.js 15, `bok-ui`, `pdf.js` (Apache-2.0), Vitest, Playwright, `axe-core`.

**Spec:** `docs/superpowers/specs/2025-03-11-ledger-sense-design.md` §8 items 3–4
**Parent plan:** `2025-03-11-ledger-sense-weeks-5-12.md` — its Global Constraints and File Structure apply here in full.

---

**Audit:** `2026-08-29-REVISIONS.md` — five defects in this phase were fixed in place, including one (F-65)
where the signature screen's dim layer does the opposite of what it was written to do.

---

## Phase preconditions

- **Phase 0 complete** — in particular P2 (`register`, `require_session`, `owned`) and P6 (the Next.js scaffold,
  the density tokens Task 33 asserts, and the demo-only seed route every Playwright test here navigates to).
  The original plan assumed all of this existed and nothing created it (G-1, G-2, G-9).
- **Phase 2 complete.** Claims carrying page and bbox are what this phase renders.
- **Phase 3 is NOT required.** This phase has no domain dependency and can run ahead if the
  week-5 controller session slipped. Arithmetic and integrity findings alone populate a queue.

## Two things decide whether this screen works

**1. The keyboard model is the interface, not an accelerator.** A reviewer clearing fifty
exceptions performs the same four keystrokes two hundred times. If any path requires the mouse,
the screen fails at the one thing it exists to do. The Playwright test asserts **zero mouse
events**, not "keyboard also works".

**2. The coordinate conversion.** Phase 2 normalised every bbox to a **top-left origin in PDF
points**. `pdf.js` renders at a scale where CSS pixels = PDF points × scale. So:

```
left   = bbox.x0                  * scale
top    = bbox.top                 * scale
width  = (bbox.x1 - bbox.x0)      * scale
height = (bbox.bottom - bbox.top) * scale
```

No flipping, no page-height subtraction. If boxes appear mirrored vertically, the bug is upstream
in Task 14, not here.

---

### Task 28: Review-queue API

**Files:**
- Create: `ledger_sense/api/review.py`
- Test: `tests/api/test_review.py`

**Interfaces:**
- Consumes: `Finding` (Task 6); `bok_core.provenance.{Claim, Method, HumanSource}`.
- Produces: FastAPI router at `/api/review`:
  - `GET /documents/{doc_id}/exceptions` → `list[ReviewException]`, ordered page → top → left
  - `POST /exceptions/{exc_id}/accept` → `{status, claim_id}`
  - `POST /exceptions/{exc_id}/edit` body `{value: str}` → `{status, claim_id}`
  - `POST /exceptions/{exc_id}/reject` body `{reason: str}` → `{status, claim_id}`
  - `GET /documents/{doc_id}/progress` → `{total, cleared, remaining}`
  - `ReviewException(id, doc_id, field, rule_id, message, expected, actual, page, bbox, state)`
  - `ExceptionState = Literal["open", "accepted", "edited", "rejected"]`
  - `seed_document(doc_id, exception_count)`, `get_claim(claim_id)` — test and demo seams
  - **`ledger_sense/review/actions.py::ReviewAction(kind, field, claim_id, predicted_confidence, actor, at)`
    — NEW (F-59).** Phase 6 Task 42 imports `ReviewAction` from `ledger_sense.review.actions`; no task created
    that module or that class, so Phase 6 does not import. Every handler below constructs one and returns it
    alongside the claim.

**Three cross-cutting requirements this router must satisfy:**

1. **Mount through `register` (F-25).** `app.include_router(router)` leaves the queue unauthenticated. Every
   handler resolves the exception, then calls `owned(session, exception.tenant_id)` — which raises **404, not
   403**, because a 403 tells a prober the exception exists.
2. **Every accept and edit calls `ProfileCache.learn` (F-17).** This is the loop spec §4.3 calls *"the
   commercial one"* and it had no implementing task: `ProfileCache` was built in Task 19 and never called.
   `edit` writes the corrected field's bbox against the document's fingerprint; the next document from the same
   producer extracts that field without a correction.
3. **Every action appends a calibration label (F-46), with the basis derived from the document's sensitivity,
   never from a parameter.** Phase 6 Task 42 asserts `"basis" not in signature(record_action).parameters`, so no
   caller can promote a synthetic review session to field evidence.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_review.py
from bok_core.provenance import Method
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ledger_sense.api.review import get_claim, router, seed_document

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def _exceptions(doc_id="doc-1"):
    return client.get(f"/api/review/documents/{doc_id}/exceptions").json()


def test_exceptions_list_is_ordered_by_page_then_position():
    seed_document("doc-1", exception_count=3)
    keys = [(e["page"], round(e["bbox"][1], 1)) for e in _exceptions()]
    assert keys == sorted(keys)


def test_every_exception_carries_the_box_that_locates_it():
    seed_document("doc-1", exception_count=1)
    exception = _exceptions()[0]
    x0, top, x1, bottom = exception["bbox"]
    assert x1 > x0 and bottom > top
    assert exception["page"] >= 0


def test_accepting_writes_a_human_claim():
    seed_document("doc-1", exception_count=1)
    body = client.post(f"/api/review/exceptions/{_exceptions()[0]['id']}/accept").json()
    assert body["status"] == "accepted"
    assert get_claim(body["claim_id"]).method is Method.HUMAN


def test_editing_records_the_new_value_and_a_human_claim():
    seed_document("doc-1", exception_count=1)
    body = client.post(f"/api/review/exceptions/{_exceptions()[0]['id']}/edit",
                       json={"value": "1234.56"}).json()
    assert body["status"] == "edited"
    claim = get_claim(body["claim_id"])
    assert claim.method is Method.HUMAN
    assert claim.value == "1234.56"


def test_edit_rejects_a_json_float_rather_than_coercing_it():
    seed_document("doc-1", exception_count=1)
    resp = client.post(f"/api/review/exceptions/{_exceptions()[0]['id']}/edit",
                       json={"value": 1234.56})
    assert resp.status_code == 422


def test_rejecting_requires_a_reason():
    seed_document("doc-1", exception_count=1)
    resp = client.post(f"/api/review/exceptions/{_exceptions()[0]['id']}/reject",
                       json={"reason": "   "})
    assert resp.status_code == 422


def test_progress_counts_cleared_against_total():
    seed_document("doc-1", exception_count=3)
    client.post(f"/api/review/exceptions/{_exceptions()[0]['id']}/accept")
    assert client.get("/api/review/documents/doc-1/progress").json() == {
        "total": 3, "cleared": 1, "remaining": 2}


def test_a_cleared_exception_stays_cleared():
    seed_document("doc-1", exception_count=2)
    client.post(f"/api/review/exceptions/{_exceptions()[0]['id']}/accept")
    assert len([e for e in _exceptions() if e["state"] == "open"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_review.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.api.review'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/api/review.py
"""Review-queue API.

Every human action writes a HUMAN-method Claim. Those claims are the labels
Phase 6 fits a calibration curve on, and they are why the product can honestly
say it improves with use.

Values arrive as STRINGS. A JSON float in an edit body is rejected, not coerced.
"""

import uuid
from datetime import datetime, timezone
from typing import Literal

from bok_core.provenance import Claim, HumanSource, Method
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StrictStr, field_validator

router = APIRouter(prefix="/api/review")

ExceptionState = Literal["open", "accepted", "edited", "rejected"]


class ReviewException(BaseModel):
    id: str
    doc_id: str
    field: str
    rule_id: str
    message: str
    expected: str
    actual: str
    page: int
    bbox: tuple[float, float, float, float]
    state: ExceptionState = "open"


class EditBody(BaseModel):
    value: StrictStr


class RejectBody(BaseModel):
    reason: StrictStr

    @field_validator("reason")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a rejection must state a reason")
        return value


# ponytail: in-process stores. Swap for the Postgres session in Task 43.
_EXCEPTIONS: dict[str, ReviewException] = {}
_CLAIMS: dict[str, Claim] = {}


def get_claim(claim_id: str) -> Claim:
    return _CLAIMS[claim_id]


def seed_document(doc_id: str, exception_count: int) -> list[ReviewException]:
    """Test and demo seam. Real exceptions arrive from the rules engine."""
    for key in [k for k, v in _EXCEPTIONS.items() if v.doc_id == doc_id]:
        del _EXCEPTIONS[key]
    seeded = []
    for index in range(exception_count):
        exc = ReviewException(
            id=f"{doc_id}-exc-{index}", doc_id=doc_id,
            field=f"line[{index:03d}].total_completed_and_stored",
            rule_id="A2",
            message="previous + this period + stored does not equal the total",
            expected="1000.00", actual="1005.00", page=0,
            bbox=(288.0, 100.0 + index * 18.0, 368.0, 112.0 + index * 18.0))
        _EXCEPTIONS[exc.id] = exc
        seeded.append(exc)
    return seeded


def _get(exc_id: str) -> ReviewException:
    if exc_id not in _EXCEPTIONS:
        raise HTTPException(status_code=404, detail="exception not found")
    return _EXCEPTIONS[exc_id]


def _record(exc: ReviewException, state: ExceptionState, value: str | None,
            note: str) -> str:
    claim_id = str(uuid.uuid4())
    _CLAIMS[claim_id] = Claim(
        value=value, method=Method.HUMAN, confidence=None,
        observed_at=datetime.now(timezone.utc),
        source=HumanSource(actor="reviewer", note=note))
    exc.state = state
    return claim_id


@router.get("/documents/{doc_id}/exceptions")
def list_exceptions(doc_id: str) -> list[ReviewException]:
    found = [e for e in _EXCEPTIONS.values() if e.doc_id == doc_id]
    return sorted(found, key=lambda e: (e.page, e.bbox[1], e.bbox[0]))


@router.post("/exceptions/{exc_id}/accept")
def accept(exc_id: str) -> dict[str, str]:
    exc = _get(exc_id)
    return {"status": "accepted",
            "claim_id": _record(exc, "accepted", exc.actual, "accepted as extracted")}


@router.post("/exceptions/{exc_id}/edit")
def edit(exc_id: str, body: EditBody) -> dict[str, str]:
    exc = _get(exc_id)
    return {"status": "edited",
            "claim_id": _record(exc, "edited", body.value, "corrected by reviewer")}


@router.post("/exceptions/{exc_id}/reject")
def reject(exc_id: str, body: RejectBody) -> dict[str, str]:
    exc = _get(exc_id)
    return {"status": "rejected",
            "claim_id": _record(exc, "rejected", None, body.reason)}


@router.get("/documents/{doc_id}/progress")
def progress(doc_id: str) -> dict[str, int]:
    found = [e for e in _EXCEPTIONS.values() if e.doc_id == doc_id]
    cleared = sum(1 for e in found if e.state != "open")
    return {"total": len(found), "cleared": cleared, "remaining": len(found) - cleared}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_review.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add ledger_sense/api/review.py tests/api/test_review.py
git commit -m "feat(api): review queue writing a HUMAN claim for every reviewer action"
```

---

### Task 29: Split-view document renderer

**Files:**
- Create: `ledger_sense/web/lib/bbox.ts`
- Create: `ledger_sense/web/components/DocumentPane.tsx`
- Test: `ledger_sense/web/lib/bbox.test.ts`

**Interfaces:**
- Consumes: `pdf.js`; `ReviewException.bbox` from Task 28.
- Produces:
  - `type BBox = readonly [x0: number, top: number, x1: number, bottom: number]`
  - `toOverlayStyle(bbox: BBox, scale: number): OverlayStyle` — `{left, top, width, height}` in CSS px
  - `<DocumentPane pdfUrl page focusBox accepted scale />`

- [ ] **Step 1: Write the failing test**

```typescript
// ledger_sense/web/lib/bbox.test.ts
import { describe, expect, it } from 'vitest'
import { toOverlayStyle, type BBox } from './bbox'

const BOX: BBox = [288, 100, 368, 112]

describe('toOverlayStyle', () => {
  it('maps PDF points to CSS pixels at scale 1', () => {
    expect(toOverlayStyle(BOX, 1)).toEqual({ left: 288, top: 100, width: 80, height: 12 })
  })

  it('scales every dimension together', () => {
    expect(toOverlayStyle(BOX, 2)).toEqual({ left: 576, top: 200, width: 160, height: 24 })
  })

  it('does NOT flip the vertical axis', () => {
    // Phase 2 already normalised to a top-left origin. Flipping here would
    // mirror every box and read as a CSS bug.
    expect(toOverlayStyle(BOX, 1).top).toBe(100)
  })

  it('never produces a negative dimension', () => {
    const inverted: BBox = [368, 112, 288, 100]
    const style = toOverlayStyle(inverted, 1)
    expect(style.width).toBeGreaterThanOrEqual(0)
    expect(style.height).toBeGreaterThanOrEqual(0)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run ledger_sense/web/lib/bbox.test.ts`
Expected: FAIL — `Cannot find module './bbox'`

- [ ] **Step 3: Write the coordinate helper**

```typescript
// ledger_sense/web/lib/bbox.ts
/**
 * Phase 2 normalised every bbox to a TOP-LEFT origin in PDF points.
 * pdf.js renders at a scale where CSS pixels = PDF points x scale.
 *
 * So: no flipping, no page-height subtraction. If boxes appear mirrored
 * vertically, the bug is in extract/textlayer.py, not here.
 */

export type BBox = readonly [x0: number, top: number, x1: number, bottom: number]

export interface OverlayStyle {
  left: number
  top: number
  width: number
  height: number
}

export function toOverlayStyle(bbox: BBox, scale: number): OverlayStyle {
  const [x0, top, x1, bottom] = bbox
  return {
    left: x0 * scale,
    top: top * scale,
    width: Math.max(0, x1 - x0) * scale,
    height: Math.max(0, bottom - top) * scale,
  }
}
```

- [ ] **Step 4: Write the document pane**

```tsx
// ledger_sense/web/components/DocumentPane.tsx
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
      const doc = await pdfjs.getDocument(pdfUrl).promise
      const pdfPage = await doc.getPage(page + 1)
      const viewport = pdfPage.getViewport({ scale })
      const canvas = canvasRef.current
      if (!canvas || cancelled) return
      canvas.width = viewport.width
      canvas.height = viewport.height
      setSize({ width: viewport.width, height: viewport.height })
      await pdfPage.render({ canvasContext: canvas.getContext('2d')!, viewport }).promise
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
            <div key={i} className="absolute bg-[var(--ink)]/40"
                 style={{ position: 'absolute', ...rect }} />
          ))}
        </div>
      )}

      {/* Accepted fields keep a thin rule that STAYS. */}
      {accepted.filter((a) => a.page === page).map((a) => (
        <div
          key={a.id}
          data-testid={`accepted-rule-${a.id}`}
          className="absolute border-b-2 border-[var(--accent)] pointer-events-none"
          style={{ position: 'absolute', ...toOverlayStyle(a.bbox, scale) }}
        />
      ))}

      {focus && (
        <div
          data-testid="focus-box"
          className="absolute border-2 border-[var(--accent)] pointer-events-none"
          style={{ position: 'absolute', ...focus }}
        />
      )}
    </div>
  )
}
```

- [ ] **Step 5: Run the unit test and commit**

Run: `npx vitest run ledger_sense/web/lib/bbox.test.ts`
Expected: PASS, 4 tests.

```bash
git add ledger_sense/web/lib/bbox.ts ledger_sense/web/lib/bbox.test.ts ledger_sense/web/components/DocumentPane.tsx
git commit -m "feat(web): split-view document pane with bbox overlay and dim layer"
```

---

### Task 30: The keyboard model — **DoD 5**

**Files:**
- Create: `ledger_sense/web/lib/useReviewKeys.ts`
- Create: `ledger_sense/web/app/review/[docId]/page.tsx`
- Test: `ledger_sense/web/lib/useReviewKeys.test.ts`
- Test: `tests/e2e/test_review_queue.spec.ts`

**Interfaces:**
- Consumes: Task 28's endpoints; Task 29's `DocumentPane`.
- Produces:
  - `useReviewKeys({ onNext, onPrev, onAccept, onEdit, onReject, enabled }): void` — binds `J`/`K`/`Enter`/`E`/`R`
  - Review page at `/review/[docId]`

- [ ] **Step 1: Write the failing unit test**

```typescript
// ledger_sense/web/lib/useReviewKeys.test.ts
import { renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useReviewKeys } from './useReviewKeys'

function press(key: string, target?: EventTarget) {
  const event = new KeyboardEvent('keydown', { key, bubbles: true })
  ;(target ?? document).dispatchEvent(event)
  return event
}

function setup(overrides: Record<string, unknown> = {}) {
  const handlers = {
    onNext: vi.fn(), onPrev: vi.fn(), onAccept: vi.fn(),
    onEdit: vi.fn(), onReject: vi.fn(), enabled: true, ...overrides,
  }
  renderHook(() => useReviewKeys(handlers as never))
  return handlers
}

describe('useReviewKeys', () => {
  it('binds J to next and K to previous', () => {
    const h = setup()
    press('j'); press('k')
    expect(h.onNext).toHaveBeenCalledOnce()
    expect(h.onPrev).toHaveBeenCalledOnce()
  })

  it('binds Enter to accept, E to edit, R to reject', () => {
    const h = setup()
    press('Enter'); press('e'); press('r')
    expect(h.onAccept).toHaveBeenCalledOnce()
    expect(h.onEdit).toHaveBeenCalledOnce()
    expect(h.onReject).toHaveBeenCalledOnce()
  })

  it('is case-insensitive', () => {
    const h = setup()
    press('J'); press('E')
    expect(h.onNext).toHaveBeenCalledOnce()
    expect(h.onEdit).toHaveBeenCalledOnce()
  })

  it('does nothing when disabled', () => {
    const h = setup({ enabled: false })
    press('j')
    expect(h.onNext).not.toHaveBeenCalled()
  })

  it('ignores keys typed into an input', () => {
    // Otherwise typing "reject" into the edit field fires four shortcuts.
    const h = setup()
    const input = document.createElement('input')
    document.body.appendChild(input)
    press('j', input)
    expect(h.onNext).not.toHaveBeenCalled()
    input.remove()
  })

  it('ignores modified keystrokes so browser shortcuts still work', () => {
    const h = setup()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'j', metaKey: true }))
    expect(h.onNext).not.toHaveBeenCalled()
  })

  it('prevents default so J does not scroll the document pane', () => {
    setup()
    expect(press('j').defaultPrevented).toBe(true)
  })

  it('does not swallow Enter when a button has focus', () => {
    // F-64: a <button> is neither INPUT nor TEXTAREA nor SELECT, so the original
    // isTyping() returned false, preventDefault() ran, and keyboard activation
    // of every button on the screen was suppressed. axe cannot see this.
    const h = setup()
    const button = document.createElement('button')
    document.body.appendChild(button)
    button.focus()
    const event = press('Enter', button)
    expect(event.defaultPrevented).toBe(false)
    expect(h.onAccept).not.toHaveBeenCalled()
    button.remove()
  })

  it('still handles Enter when focus is on the queue itself', () => {
    const h = setup()
    const queue = document.createElement('div')
    queue.tabIndex = 0
    queue.dataset.reviewShortcuts = 'true'
    document.body.appendChild(queue)
    press('Enter', queue)
    expect(h.onAccept).toHaveBeenCalledOnce()
    queue.remove()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run ledger_sense/web/lib/useReviewKeys.test.ts`
Expected: FAIL — `Cannot find module './useReviewKeys'`

- [ ] **Step 3: Write the hook**

```typescript
// ledger_sense/web/lib/useReviewKeys.ts
'use client'

import { useEffect } from 'react'

/**
 * The keyboard model IS the interface, not an accelerator.
 *
 * A reviewer clearing fifty exceptions performs the same four keystrokes two
 * hundred times. Any path that needs the mouse fails the screen at the one
 * thing it exists to do.
 */

interface Handlers {
  onNext: () => void
  onPrev: () => void
  onAccept: () => void
  onEdit: () => void
  onReject: () => void
  enabled?: boolean
}

const EDITABLE = new Set(['INPUT', 'TEXTAREA', 'SELECT'])

/**
 * REVISED (F-64). This originally covered only INPUT/TEXTAREA/SELECT, so focus
 * on a <button> was "not typing" — and the hook then called preventDefault() on
 * Enter, suppressing keyboard activation of every button on the screen.
 *
 * An accessibility regression axe cannot detect, because it is behavioural: the
 * button has its accessible name, its role and its focus ring, and does nothing
 * when a keyboard user activates it.
 */
function handlesItsOwnEnter(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el || !el.tagName) return false
  if (EDITABLE.has(el.tagName) || el.isContentEditable) return true
  if (el.tagName === 'BUTTON' || el.tagName === 'A') return true
  if (el.getAttribute('role') === 'button' || el.getAttribute('role') === 'link') {
    return true
  }
  return el.tabIndex >= 0 && el.dataset.reviewShortcuts !== 'true'
}

export function useReviewKeys({
  onNext, onPrev, onAccept, onEdit, onReject, enabled = true,
}: Handlers): void {
  useEffect(() => {
    if (!enabled) return

    function handle(event: KeyboardEvent) {
      if (event.metaKey || event.ctrlKey || event.altKey) return
      if (handlesItsOwnEnter(event.target)) return

      const actions: Record<string, (() => void) | undefined> = {
        j: onNext, k: onPrev, e: onEdit, r: onReject, enter: onAccept,
      }
      const action = actions[event.key.toLowerCase()]
      if (!action) return

      event.preventDefault()
      action()
    }

    document.addEventListener('keydown', handle)
    return () => document.removeEventListener('keydown', handle)
  }, [onNext, onPrev, onAccept, onEdit, onReject, enabled])
}
```

- [ ] **Step 4: Build the review page**

`ledger_sense/web/app/review/[docId]/page.tsx` — split view, `bok-ui` tokens, `compact` density.

**Responsive, and it is not optional — REVISED (F-63).** Task 33 asserts no horizontal page scroll at 375px,
while this screen renders a `pdf.js` canvas at `scale = 1.5` — 792pt × 1.5 ≈ **1,188 CSS px**. The two
requirements contradict each other unless the layout says how they coexist, and no task said:

- The document pane is `overflow-x: auto` inside a `max-width: 100%` container. **The pane scrolls; the page
  never does.**
- Below 768px the split becomes a stack: the exception detail first, the document below it, with a
  `data-testid="jump-to-document"` control. The keyboard model is unchanged — that is the whole point of it.
- `scale` is derived from the container width, not fixed: `min(1.5, containerWidth / pageWidthPt)`.

- **Left:** `<DocumentPane>` from Task 29, showing the current exception's page with its bbox
  focused and everything else dimmed.
- **Right:** the extracted value in an editable field (`data-testid="edit-input"`), the validation
  rules that fired (rule id, message, expected vs actual in tabular numerals), and the provenance
  rail for the field's Claim.
- **Header:** `data-testid="progress"` reading `"{cleared} / {total}"` from
  `GET /documents/{docId}/progress`. `data-testid="current-field"` shows the field under review.
- Wire `useReviewKeys` to the four endpoints. `E` focuses the edit input; `R` focuses
  `data-testid="reject-reason"`; `Escape` blurs and returns key handling to the queue.
- **Advance automatically** after accept, edit-submit or reject. A reviewer must never need a
  separate "next" keystroke on the happy path — only `Enter`, fifty times.
- Announce each transition through an `aria-live="polite"` region (`role="status"`) reading
  `"{n} of {total}"`, so a screen-reader user hears the queue advance.

- [ ] **Step 5: Write the Playwright test**

```typescript
// tests/e2e/test_review_queue.spec.ts
import { expect, test, type Page } from '@playwright/test'

/** Records any mouse event that reaches the page. DoD 5 is "keyboard-only",
 *  not "keyboard also works". */
async function forbidMouse(page: Page) {
  await page.addInitScript(() => {
    for (const type of ['click', 'mousedown', 'mouseup', 'dblclick']) {
      document.addEventListener(type, () => {
        ;(window as unknown as { __mouseUsed?: boolean }).__mouseUsed = true
      }, true)
    }
  })
}

test('clears fifty exceptions without a single mouse event', async ({ page }) => {
  await forbidMouse(page)
  await page.goto('/review/doc-50')
  await expect(page.getByTestId('progress')).toHaveText('0 / 50')

  for (let i = 0; i < 50; i++) {
    await page.keyboard.press('Enter')
  }

  await expect(page.getByTestId('progress')).toHaveText('50 / 50')
  expect(await page.evaluate(
    () => (window as unknown as { __mouseUsed?: boolean }).__mouseUsed)).toBeUndefined()
})

test('J and K move through the queue in both directions', async ({ page }) => {
  await page.goto('/review/doc-3')
  const field = page.getByTestId('current-field')
  const first = await field.textContent()

  await page.keyboard.press('j')
  expect(await field.textContent()).not.toBe(first)

  await page.keyboard.press('k')
  expect(await field.textContent()).toBe(first)
})

test('E opens the editor and typing does not fire shortcuts', async ({ page }) => {
  await page.goto('/review/doc-3')
  await page.keyboard.press('e')
  await expect(page.getByTestId('edit-input')).toBeFocused()

  // "jerk" contains j, e, r and k — every shortcut. None may fire.
  await page.keyboard.type('jerk')
  await expect(page.getByTestId('edit-input')).toHaveValue('jerk')
  await expect(page.getByTestId('progress')).toHaveText('0 / 3')
})

test('R requires a reason before it clears the exception', async ({ page }) => {
  await page.goto('/review/doc-3')
  await page.keyboard.press('r')
  await expect(page.getByTestId('reject-reason')).toBeFocused()

  await page.keyboard.press('Enter')
  await expect(page.getByTestId('progress')).toHaveText('0 / 3')

  await page.keyboard.type('wrong column picked up')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('progress')).toHaveText('1 / 3')
})

test('the queue advance is announced to assistive technology', async ({ page }) => {
  await page.goto('/review/doc-3')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('status')).toContainText('2 of 3')
})
```

- [ ] **Step 6: Run tests and commit**

Run: `npx vitest run ledger_sense/web/lib/useReviewKeys.test.ts` → PASS, 7 tests.
Run: `npx playwright test tests/e2e/test_review_queue.spec.ts` → PASS, 5 tests. **DoD 5.**

```bash
git add ledger_sense/web/lib/useReviewKeys.ts ledger_sense/web/lib/useReviewKeys.test.ts ledger_sense/web/app/review/ tests/e2e/test_review_queue.spec.ts
git commit -m "feat(web): keyboard-first review queue, fifty exceptions with no mouse"
```

---

### Task 31: The accumulating verification rule

**The feeling the product sells.** As each field is accepted, its box fills with a thin rule that **stays** — the document visibly becomes trustworthy as you work through it.

**Files:**
- Create: `ledger_sense/web/lib/useAcceptedBoxes.ts`
- Modify: `ledger_sense/web/app/review/[docId]/page.tsx`
- Test: `ledger_sense/web/lib/useAcceptedBoxes.test.ts`
- Test: `tests/e2e/test_accumulating_rule.spec.ts`

**Interfaces:**
- Consumes: `DocumentPane`'s `accepted` prop (Task 29); Task 28's accept/edit responses.
- Produces: `useAcceptedBoxes(docId) -> { accepted: AcceptedBox[], markAccepted(box): void }`, persisted per document so rules survive navigation.

- [ ] **Step 1: Write the failing unit test**

```typescript
// ledger_sense/web/lib/useAcceptedBoxes.test.ts
import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { useAcceptedBoxes } from './useAcceptedBoxes'

const EXC = { id: 'e1', page: 0, bbox: [288, 100, 368, 112] as const }

beforeEach(() => window.localStorage.clear())

describe('useAcceptedBoxes', () => {
  it('starts empty', () => {
    const { result } = renderHook(() => useAcceptedBoxes('doc-1'))
    expect(result.current.accepted).toEqual([])
  })

  it('records an accepted box', () => {
    const { result } = renderHook(() => useAcceptedBoxes('doc-1'))
    act(() => result.current.markAccepted(EXC))
    expect(result.current.accepted).toHaveLength(1)
  })

  it('does not duplicate a box accepted twice', () => {
    const { result } = renderHook(() => useAcceptedBoxes('doc-1'))
    act(() => { result.current.markAccepted(EXC); result.current.markAccepted(EXC) })
    expect(result.current.accepted).toHaveLength(1)
  })

  it('keeps rules per document, not globally', () => {
    const first = renderHook(() => useAcceptedBoxes('doc-1'))
    act(() => first.result.current.markAccepted(EXC))
    const second = renderHook(() => useAcceptedBoxes('doc-2'))
    expect(second.result.current.accepted).toEqual([])
  })

  it('survives a remount — the rule STAYS', () => {
    const first = renderHook(() => useAcceptedBoxes('doc-1'))
    act(() => first.result.current.markAccepted(EXC))
    first.unmount()
    const again = renderHook(() => useAcceptedBoxes('doc-1'))
    expect(again.result.current.accepted).toHaveLength(1)
  })

  it('degrades to in-memory when storage throws', () => {
    const original = window.localStorage.getItem
    window.localStorage.getItem = () => { throw new Error('blocked') }
    const { result } = renderHook(() => useAcceptedBoxes('doc-1'))
    act(() => result.current.markAccepted(EXC))
    expect(result.current.accepted).toHaveLength(1)
    window.localStorage.getItem = original
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run ledger_sense/web/lib/useAcceptedBoxes.test.ts`
Expected: FAIL — `Cannot find module './useAcceptedBoxes'`

- [ ] **Step 3: Write the hook**

```typescript
// ledger_sense/web/lib/useAcceptedBoxes.ts
'use client'

import { useCallback, useEffect, useState } from 'react'
import type { BBox } from './bbox'

/**
 * The signature detail: an accepted field's rule STAYS, so the document
 * visually accumulates verification as the reviewer works through it.
 *
 * That is the feeling the product sells — watching a document become
 * trustworthy — so the rule must survive navigation, not just re-render.
 */

export interface AcceptedBox {
  id: string
  page: number
  bbox: BBox
}

function storageKey(docId: string): string {
  return `ledger-sense:accepted:${docId}`
}

function read(docId: string): AcceptedBox[] {
  try {
    return JSON.parse(window.localStorage.getItem(storageKey(docId)) ?? '[]')
  } catch {
    return []
  }
}

function write(docId: string, boxes: AcceptedBox[]): void {
  try {
    window.localStorage.setItem(storageKey(docId), JSON.stringify(boxes))
  } catch {
    // Private window, blocked site data, thumbnail capture. In-memory is fine.
  }
}

export function useAcceptedBoxes(docId: string) {
  const [accepted, setAccepted] = useState<AcceptedBox[]>([])

  useEffect(() => { setAccepted(read(docId)) }, [docId])

  const markAccepted = useCallback((box: AcceptedBox) => {
    setAccepted((current) => {
      if (current.some((b) => b.id === box.id)) return current
      const next = [...current, box]
      write(docId, next)
      return next
    })
  }, [docId])

  return { accepted, markAccepted }
}
```

- [ ] **Step 4: Wire it into the review page**

In `app/review/[docId]/page.tsx`, call `markAccepted({ id, page, bbox })` on every successful
**accept** and **edit**, and pass `accepted` into `<DocumentPane>`. A **rejected** exception gets
no rule — it was not verified.

- [ ] **Step 5: Write the Playwright test**

```typescript
// tests/e2e/test_accumulating_rule.spec.ts
import { expect, test } from '@playwright/test'

test('each accepted field leaves a rule that stays', async ({ page }) => {
  await page.goto('/review/doc-3')
  await expect(page.locator('[data-testid^="accepted-rule-"]')).toHaveCount(0)

  await page.keyboard.press('Enter')
  await expect(page.locator('[data-testid^="accepted-rule-"]')).toHaveCount(1)

  await page.keyboard.press('Enter')
  await expect(page.locator('[data-testid^="accepted-rule-"]')).toHaveCount(2)
})

test('rules survive navigating away and back', async ({ page }) => {
  await page.goto('/review/doc-3')
  await page.keyboard.press('Enter')
  await page.goto('/inbox')
  await page.goto('/review/doc-3')
  await expect(page.locator('[data-testid^="accepted-rule-"]')).toHaveCount(1)
})

test('a rejected field leaves no rule', async ({ page }) => {
  await page.goto('/review/doc-3')
  await page.keyboard.press('r')
  await page.keyboard.type('wrong column')
  await page.keyboard.press('Enter')
  await expect(page.locator('[data-testid^="accepted-rule-"]')).toHaveCount(0)
})
```

- [ ] **Step 6: Run tests and commit**

Run: `npx vitest run ledger_sense/web/lib/useAcceptedBoxes.test.ts` → PASS, 6 tests.
Run: `npx playwright test tests/e2e/test_accumulating_rule.spec.ts` → PASS, 3 tests.

```bash
git add ledger_sense/web/lib/useAcceptedBoxes.ts ledger_sense/web/lib/useAcceptedBoxes.test.ts ledger_sense/web/app/review/ tests/e2e/test_accumulating_rule.spec.ts
git commit -m "feat(web): accepted fields accumulate a verification rule that stays"
```

---

### Task 32: Reconciliation view

**The screen that makes a controller believe you understand their world.** A controller does not want to be told there is a discrepancy — they want to see the addition that produced it, at the cell where it happened.

**Files:**
- Create: `ledger_sense/api/reconciliation.py`
- Create: `ledger_sense/web/app/reconciliation/[docId]/page.tsx`
- Create: `ledger_sense/web/styles/print.css`
- Test: `tests/api/test_reconciliation.py`
- Test: `tests/e2e/test_reconciliation.spec.ts`

**Interfaces:**
- Consumes: `PayApplication`, `Contract`, all arithmetic rules; `corpus.generate`.
- Produces:
  - `GET /api/reconciliation/{doc_id}` → `{lines, summary, discrepancies}`
  - `Discrepancy(field, rule_id, expected, actual, delta, arithmetic)` where `arithmetic` is a
    human-readable derivation, e.g. `"300.00 + 200.00 = 500.00, sheet shows 500.01"`
  - `seed_from_corpus(doc_id, seed, error)` — test and demo seam

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_reconciliation.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ledger_sense.api.reconciliation import router, seed_from_corpus

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def test_a_clean_document_reports_no_discrepancy():
    seed_from_corpus("doc-clean", seed=1, error="clean")
    assert client.get("/api/reconciliation/doc-clean").json()["discrepancies"] == []


def test_a_footing_error_is_located_at_the_exact_field():
    seed_from_corpus("doc-a1", seed=1, error="A1_footing")
    discrepancies = client.get("/api/reconciliation/doc-a1").json()["discrepancies"]
    assert [d["rule_id"] for d in discrepancies] == ["A1"]
    assert discrepancies[0]["field"] == "total_completed_and_stored"


def test_every_discrepancy_shows_its_arithmetic():
    seed_from_corpus("doc-a1", seed=1, error="A1_footing")
    discrepancy = client.get("/api/reconciliation/doc-a1").json()["discrepancies"][0]
    assert "=" in discrepancy["arithmetic"]
    assert discrepancy["expected"] in discrepancy["arithmetic"]


def test_the_delta_is_exact():
    seed_from_corpus("doc-a1", seed=1, error="A1_footing")
    discrepancy = client.get("/api/reconciliation/doc-a1").json()["discrepancies"][0]
    assert discrepancy["delta"] == "0.01"


def test_lines_and_summary_are_both_returned_for_side_by_side_rendering():
    seed_from_corpus("doc-clean", seed=1, error="clean")
    body = client.get("/api/reconciliation/doc-clean").json()
    assert body["lines"] and body["summary"]
    assert "total_completed_and_stored" in body["summary"]


def test_an_unknown_document_is_a_404_not_an_empty_reconciliation():
    assert client.get("/api/reconciliation/doc-missing").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_reconciliation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ledger_sense.api.reconciliation'`

- [ ] **Step 3: Write minimal implementation**

```python
# ledger_sense/api/reconciliation.py
"""Continuation sheet against the summary, with the arithmetic shown.

A controller does not want to be told there is a discrepancy. They want to see
the addition that produced it, at the cell where it happened.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ledger_sense.corpus.generate import generate
from ledger_sense.domain.models import Contract, PayApplication
from ledger_sense.domain.money import money
# Imported for their registration side effect only (F-67).
from ledger_sense.rules.arithmetic import foot, lines, payment  # noqa: F401
from ledger_sense.rules.registry import registered_rules

router = APIRouter(prefix="/api/reconciliation")

# A9 is a `convention` rule (F-04) but it IS a reconciliation check - scheduled
# values against the adjusted contract sum - so it is named here rather than
# selected by class. Named, not imported: Phase 4 may run ahead of Phase 3 if the
# controller session slips, in which case A9 is simply not registered yet and this
# screen shows one fewer discrepancy. An import would turn that into a 500.
_ALSO_RECONCILES = frozenset({"A9"})


def _reconciliation_rules():
    """Read the registry per request, never a hand-written list.

    Module-level would be wrong even so: this module can be imported before the
    convention rules are, and the list would freeze A9 out.
    """
    return [r.fn for r in registered_rules().values()
            if r.rule_class == "arithmetic" or r.rule_id in _ALSO_RECONCILES]

# ponytail: in-process store. Swap for the Postgres session in Task 43.
_DOCS: dict[str, tuple[Contract, PayApplication]] = {}


class Discrepancy(BaseModel):
    field: str
    rule_id: str
    expected: str
    actual: str
    delta: str
    arithmetic: str


def seed_from_corpus(doc_id: str, seed: int, error: str) -> None:
    contract, app, _pdf = generate(seed=seed, error=error)
    _DOCS[doc_id] = (contract, app)


def _arithmetic_for(rule_id: str, app: PayApplication, expected, actual) -> str:
    if rule_id == "A1":
        terms = " + ".join(str(ln.total_completed_and_stored) for ln in app.lines)
        return f"{terms} = {expected}, sheet shows {actual}"
    if rule_id == "A8":
        terms = " + ".join(str(ln.retainage) for ln in app.lines)
        return f"{terms} = {expected}, summary shows {actual}"
    if rule_id == "A9":
        terms = " + ".join(str(ln.scheduled_value) for ln in app.lines)
        return f"{terms} = {actual}, adjusted contract sum is {expected}"
    return f"expected {expected}, sheet shows {actual}"


@router.get("/{doc_id}")
def reconcile(doc_id: str) -> dict[str, object]:
    if doc_id not in _DOCS:
        raise HTTPException(status_code=404, detail="document not found")
    contract, app = _DOCS[doc_id]

    discrepancies = [
        Discrepancy(
            field=finding.field, rule_id=finding.rule_id,
            expected=str(finding.expected), actual=str(finding.actual),
            delta=str(money(abs(finding.actual - finding.expected))),
            arithmetic=_arithmetic_for(finding.rule_id, app,
                                       finding.expected, finding.actual))
        for rule in _reconciliation_rules()
        for finding in rule(app, contract)
    ]

    return {
        "lines": [ln.model_dump(mode="json") for ln in app.lines],
        "summary": {
            "total_completed_and_stored": str(app.total_completed_and_stored),
            "retainage_total": str(app.retainage_total),
            "previous_payments": str(app.previous_payments),
            "change_orders_net": str(app.change_orders_net),
        },
        "discrepancies": [d.model_dump() for d in discrepancies],
    }
```

- [ ] **Step 4: Build the screen and the print stylesheet**

`app/reconciliation/[docId]/page.tsx` — two panes side by side: continuation lines left, summary
right, joined by a rule at the footing row. Every figure in IBM Plex Mono with tabular numerals.

A discrepant cell carries `data-testid="discrepant-cell"`, a **severity glyph** (`role="img"` with
an accessible name) **and** a **text label** — never colour alone — and renders its `arithmetic`
string inline directly beneath the cell in `data-testid="inline-arithmetic"`. Inline, not a
tooltip: this screen gets printed and forwarded to auditors.

```css
/* ledger_sense/web/styles/print.css */
@media print {
  nav, .no-print, [data-testid="dim-layer"] { display: none; }
  body { background: white; color: var(--ink); }
  .figure, td, th { font-variant-numeric: tabular-nums; }
  [data-testid="discrepant-cell"] {
    outline: 2px solid black;
    font-weight: 600;
  }
  .reconciliation-grid { break-inside: avoid; }
  a[href]::after { content: " (" attr(href) ")"; font-size: 0.85em; }
}
```

- [ ] **Step 5: Write the Playwright test**

```typescript
// tests/e2e/test_reconciliation.spec.ts
import { expect, test } from '@playwright/test'

test('a discrepancy is highlighted at the exact cell with its arithmetic', async ({ page }) => {
  await page.goto('/reconciliation/doc-a1')
  const cell = page.getByTestId('discrepant-cell')
  await expect(cell).toHaveCount(1)
  await expect(page.getByTestId('inline-arithmetic')).toContainText('=')
})

test('severity is never encoded in colour alone', async ({ page }) => {
  await page.goto('/reconciliation/doc-a1')
  const cell = page.getByTestId('discrepant-cell')
  await expect(cell.getByRole('img')).toBeVisible()      // the glyph
  await expect(cell).toContainText(/discrepancy/i)        // the text label
})

test('a clean document shows no discrepant cells', async ({ page }) => {
  await page.goto('/reconciliation/doc-clean')
  await expect(page.getByTestId('discrepant-cell')).toHaveCount(0)
})

test('the print stylesheet keeps the discrepancy legible in greyscale', async ({ page }) => {
  await page.goto('/reconciliation/doc-a1')
  await page.emulateMedia({ media: 'print' })
  const outline = await page.getByTestId('discrepant-cell')
    .evaluate((el) => getComputedStyle(el).outlineWidth)
  expect(outline).not.toBe('0px')
})
```

- [ ] **Step 6: Run tests and commit**

Run: `uv run pytest tests/api/test_reconciliation.py -v` → PASS, 6 tests.
Run: `npx playwright test tests/e2e/test_reconciliation.spec.ts` → PASS, 4 tests.

```bash
git add ledger_sense/api/reconciliation.py ledger_sense/web/app/reconciliation/ ledger_sense/web/styles/print.css tests/api/test_reconciliation.py tests/e2e/test_reconciliation.spec.ts
git commit -m "feat(web): reconciliation view with inline arithmetic at the discrepant cell"
```

---

### Task 33: Accessibility gate

Two of the four target markets are literally regulated for accessibility. Shipping a colour-only encoding would be an own goal in a sales conversation.

**Files:**
- Create: `tests/e2e/test_accessibility.spec.ts`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: every screen built so far.
- Produces: an `axe` CI job failing on any serious or critical violation.

- [ ] **Step 1: Write the failing test**

```typescript
// tests/e2e/test_accessibility.spec.ts
import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

const SCREENS = ['/baseline', '/review/doc-3', '/reconciliation/doc-a1']
const DENSITIES = ['comfortable', 'compact', 'dense'] as const

const blocking = (violations: { impact?: string | null }[]) =>
  violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')

for (const screen of SCREENS) {
  test(`${screen} has no serious or critical axe violations`, async ({ page }) => {
    await page.goto(screen)
    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag22aa'])
      .analyze()
    const found = blocking(results.violations)
    expect(found, JSON.stringify(found, null, 2)).toEqual([])
  })

  test(`${screen} does not scroll horizontally at 375px`, async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await page.goto(screen)
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth)
    expect(overflow).toBe(false)
  })

  for (const density of DENSITIES) {
    test(`${screen} passes axe at ${density} density`, async ({ page }) => {
      await page.goto(screen)
      await page.evaluate(
        (d) => document.documentElement.setAttribute('data-density', d), density)
      const results = await new AxeBuilder({ page }).analyze()
      expect(blocking(results.violations)).toEqual([])
    })
  }
}

test('EVERY focusable control has a visible focus ring', async ({ page }) => {
  // REVISED: the original pressed Tab once and checked one element, then called
  // itself "every focusable control".
  await page.goto('/review/doc-3')
  const naked = await page.evaluate(() => {
    const selector = 'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])'
    const offenders: string[] = []
    for (const el of Array.from(document.querySelectorAll<HTMLElement>(selector))) {
      el.focus()
      const style = getComputedStyle(el)
      const ringed = style.outlineWidth !== '0px' || style.boxShadow !== 'none'
      if (!ringed) offenders.push(el.outerHTML.slice(0, 80))
    }
    return offenders
  })
  expect(naked, naked.join('\n')).toEqual([])
})

test('the document pane scrolls internally so the page does not, at 375px', async ({ page }) => {
  // F-63: Task 33 asserts no horizontal page scroll at 375px while Task 30
  // renders a ~1,188px canvas. The pane scrolls; the page never does.
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto('/review/doc-3')
  expect(await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth)).toBe(false)
  expect(await page.getByTestId('document-pane').evaluate(
    (el) => getComputedStyle(el.parentElement!).overflowX)).toBe('auto')
})

test('reduced motion disables the provenance rail animation', async ({ page }) => {
  // The binding is Cmd+. on macOS and Ctrl+. elsewhere. CI Chromium runs on
  // Linux, where Playwright's "Meta" is the Super key, not Cmd — the original
  // pressed a chord the app never receives and would have passed vacuously if
  // the rail simply never opened.
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/review/doc-3')
  const chord = process.platform === 'darwin' ? 'Meta+.' : 'Control+.'
  await page.keyboard.press(chord)
  await expect(page.getByTestId('provenance-rail')).toBeVisible()
  const duration = await page.getByTestId('provenance-rail')
    .evaluate((el) => getComputedStyle(el).animationDuration)
  expect(['0s', '0.01s']).toContain(duration)
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx playwright test tests/e2e/test_accessibility.spec.ts`
Expected: FAIL. Expect real violations on the first pass. Likely ones here: the document canvas
missing an accessible name, the dim layer intercepting focus, discrepant cells encoded in colour
alone, the edit input missing a label.

- [ ] **Step 3: Fix every serious and critical violation**

No new module — fix the screens. **Do not narrow the axe rule set to make the test pass.** The
point of the gate is that it constrains the UI, not that it is green.

- [ ] **Step 4: Run to verify it passes**

Run: `npx playwright test tests/e2e/test_accessibility.spec.ts`
Expected: PASS, 17 tests (3 screens × 5, plus 2 global).

- [ ] **Step 5: Add the CI job and commit**

```yaml
  accessibility:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '22' }
      - run: npm ci
      - run: npx playwright install --with-deps chromium
      - run: npx playwright test tests/e2e/test_accessibility.spec.ts
```

```bash
git add tests/e2e/test_accessibility.spec.ts .github/workflows/ci.yml ledger_sense/web/
git commit -m "test(a11y): axe gate across every screen, density and 375px"
```

---

## Phase 4 exit criteria

- [ ] `uv run pytest tests/api -v` green
- [ ] `npx vitest run` green — `bbox`, `useReviewKeys`, `useAcceptedBoxes`
- [ ] `npx playwright test` green — review queue, accumulating rule, reconciliation, accessibility
- [ ] **DoD 5 satisfied** — fifty exceptions cleared with **zero mouse events**, asserted not assumed
- [ ] `test_does_not_swallow_Enter_when_a_button_has_focus` green — **F-64**, an a11y regression axe cannot see
- [ ] `test_the_document_pane_scrolls_internally_so_the_page_does_not` green — F-63, the contradiction between
      Task 30's 1,188px canvas and Task 33's 375px assertion
- [ ] The dim layer actually cuts a hole — **F-65.** Confirm by eye at least once: under the default `nonzero`
      fill rule the original single-polygon `clip-path` dimmed the field under review, on the signature screen
- [ ] `test_EVERY_focusable_control_has_a_visible_focus_ring` green — the original pressed Tab once
- [ ] Every router mounted through `register` (F-25); `test_every_api_route_requires_a_session` from Phase 0
      Task P2 still green with this phase's routers added
- [ ] An edit teaches the profile cache — F-17, the loop spec §4.3 calls *"the commercial one"*
- [ ] Print stylesheet verified on the reconciliation view under `emulateMedia({ media: 'print' })`
- [ ] `axe-core`: zero serious or critical, on every screen, at all three densities, at 375px
- [ ] Coverage ≥75% on `ledger_sense/api`

**Then Phase 5 — writeback.** The highest-consequence code in the product: you are writing to someone's books. It gets its own week and its own paranoia.
