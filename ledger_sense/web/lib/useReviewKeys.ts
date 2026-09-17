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
