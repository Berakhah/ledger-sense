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

  it('ignores auto-repeat, so a held Enter does not accept a stream of exceptions', () => {
    const h = setup()
    document.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', repeat: true, cancelable: true, bubbles: true }))
    expect(h.onAccept).not.toHaveBeenCalled()
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

  it('still fires J/K/E/R when a button has focus — only Enter is the button\'s', () => {
    // A reviewer who tabs onto "Accept" must not lose the whole keyboard
    // model until they tab away again. Enter belongs to the button (F-64);
    // the letters belong to the queue wherever focus is, short of a text
    // field.
    const h = setup()
    const button = document.createElement('button')
    document.body.appendChild(button)
    button.focus()
    press('j', button); press('k', button); press('e', button); press('r', button)
    expect(h.onNext).toHaveBeenCalledOnce()
    expect(h.onPrev).toHaveBeenCalledOnce()
    expect(h.onEdit).toHaveBeenCalledOnce()
    expect(h.onReject).toHaveBeenCalledOnce()
    button.remove()
  })

  it('leaves every key to an ARIA text-entry widget, native tag or not', () => {
    // A role=textbox/combobox/searchbox is typed into like an <input>; the
    // letters are its, exactly as they are an <input>'s.
    const h = setup()
    const widget = document.createElement('div')
    widget.setAttribute('role', 'combobox')
    widget.tabIndex = 0
    document.body.appendChild(widget)
    widget.focus()
    press('j', widget); press('Enter', widget)
    expect(h.onNext).not.toHaveBeenCalled()
    expect(h.onAccept).not.toHaveBeenCalled()
    widget.remove()
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
