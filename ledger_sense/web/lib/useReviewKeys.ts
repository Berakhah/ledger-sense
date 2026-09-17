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
// ARIA widgets that are typed into: the letters are theirs, like an <input>'s.
const TEXT_ENTRY_ROLES = new Set(['textbox', 'combobox', 'searchbox', 'spinbutton'])

/**
 * REVISED (F-64). This originally covered only INPUT/TEXTAREA/SELECT, so focus
 * on a <button> was "not typing" — and the hook then called preventDefault() on
 * Enter, suppressing keyboard activation of every button on the screen.
 *
 * An accessibility regression axe cannot detect, because it is behavioural: the
 * button has its accessible name, its role and its focus ring, and does nothing
 * when a keyboard user activates it.
 */
function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el || !el.tagName) return false
  if (EDITABLE.has(el.tagName) || el.isContentEditable) return true
  return TEXT_ENTRY_ROLES.has(el.getAttribute('role') ?? '')
}

/** REVISED again: this used to gate every key, so a reviewer who tabbed onto
 *  "Accept" lost J/K/E/R until they tabbed away. Only Enter is the focused
 *  control's to activate; the letters are the queue's wherever focus sits,
 *  short of a text field. */
function handlesItsOwnEnter(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el || !el.tagName) return false
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
      // One keystroke, one action. A held key auto-repeats at the OS rate,
      // and a held Enter would accept a stream of exceptions nobody looked at.
      if (event.repeat) return
      if (event.metaKey || event.ctrlKey || event.altKey) return
      if (isTyping(event.target)) return

      const actions: Record<string, (() => void) | undefined> = {
        j: onNext, k: onPrev, e: onEdit, r: onReject, enter: onAccept,
      }
      const key = event.key.toLowerCase()
      const action = actions[key]
      if (!action) return
      if (key === 'enter' && handlesItsOwnEnter(event.target)) return

      event.preventDefault()
      action()
    }

    document.addEventListener('keydown', handle)
    return () => document.removeEventListener('keydown', handle)
  }, [onNext, onPrev, onAccept, onEdit, onReject, enabled])
}
