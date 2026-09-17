import { renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useReviewKeys } from './useReviewKeys'

function press(key: string, target?: EventTarget) {
  // cancelable, like a real keydown: preventDefault() on a non-cancelable
  // event is a no-op per the DOM spec, so defaultPrevented could never be
  // observed without it.
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })
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
