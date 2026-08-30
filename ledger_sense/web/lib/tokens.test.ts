import { describe, expect, it } from 'vitest'
import { DENSITIES, tokens } from './tokens'

describe('design tokens', () => {
  it('defines the three densities Task 33 asserts', () => {
    expect(DENSITIES).toEqual(['comfortable', 'compact', 'dense'])
  })
  it('defaults to compact', () => {
    expect(tokens.defaultDensity).toBe('compact')
  })
  it('uses the ledger green accent from 00 §5.2', () => {
    expect(tokens.accent).toBe('oklch(0.58 0.11 158)')
  })
  it('sets tabular numerals on the numeric class', () => {
    expect(tokens.numeric.fontVariantNumeric).toContain('tabular-nums')
  })
  it('defines light as the primary mode', () => {
    expect(tokens.defaultScheme).toBe('light')
  })
})
