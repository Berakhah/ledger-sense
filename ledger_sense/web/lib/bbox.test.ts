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
