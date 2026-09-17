import { describe, expect, it } from 'vitest'
import { acceptedBoxesOf, type ExceptionLike } from './acceptedBoxes'

const BBOX = [288, 100, 368, 112] as const

function exc(over: Partial<ExceptionLike> & { id: string }): ExceptionLike {
  return { page: 0, bbox: BBOX, state: 'accepted', ...over }
}

describe('acceptedBoxesOf', () => {
  it('is empty for an empty queue', () => {
    expect(acceptedBoxesOf([])).toEqual([])
  })

  it('gives an accepted field a rule', () => {
    expect(acceptedBoxesOf([exc({ id: 'e1' })])).toEqual([{ id: 'e1', page: 0, bbox: BBOX }])
  })

  it('gives an edited field a rule — the reviewer verified the corrected value', () => {
    expect(acceptedBoxesOf([exc({ id: 'e1', state: 'edited' })])).toHaveLength(1)
  })

  it('gives a rejected field no rule — it was not verified', () => {
    expect(acceptedBoxesOf([exc({ id: 'e1', state: 'rejected' })])).toEqual([])
  })

  it('gives an open field no rule', () => {
    expect(acceptedBoxesOf([exc({ id: 'e1', state: 'open' })])).toEqual([])
  })

  it('skips a closed field with no located geometry', () => {
    expect(acceptedBoxesOf([exc({ id: 'e1', page: null })])).toEqual([])
    expect(acceptedBoxesOf([exc({ id: 'e2', bbox: null })])).toEqual([])
  })

  it('keeps queue order', () => {
    const boxes = acceptedBoxesOf([
      exc({ id: 'e2', page: 1 }),
      exc({ id: 'e1', state: 'open' }),
      exc({ id: 'e3', state: 'edited' }),
    ])
    expect(boxes.map((b) => b.id)).toEqual(['e2', 'e3'])
  })
})
