import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { DENSITIES, tokens } from './tokens'

// tokens.ts and globals.css necessarily hold the same literals — CSS cannot
// import a module. F-55 was this exact shape: one constant written twice, both
// copies asserted, neither derived from the other, and they drifted. This is
// the cheap thing that fails when they do.
const css = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '..', 'app', 'globals.css'),
  'utf8',
)

describe('globals.css agrees with tokens.ts', () => {
  it('declares the same accent', () => {
    expect(css).toContain(`--color-accent: ${tokens.accent};`)
  })

  it('declares the same numeric font-variant', () => {
    expect(css).toContain(`font-variant-numeric: ${tokens.numeric.fontVariantNumeric};`)
  })

  it('has a block for every density', () => {
    for (const density of DENSITIES) {
      expect(css, density).toMatch(new RegExp(`\\[data-density="${density}"\\]`))
    }
  })

  it('keeps the default density as the bare :root metrics', () => {
    // layout.tsx stamps data-density, but a document rendered before hydration
    // (or with the attribute stripped) must still get the default's metrics
    // rather than none.
    expect(css).toMatch(
      new RegExp(`:root,\\s*\\[data-density="${tokens.defaultDensity}"\\]`),
    )
  })

  it('loads no font or stylesheet from a third party', () => {
    // 00 §5.2: self-hosted, for the offline demo and because a font request
    // says who is reading what to someone else's server. Same reason the
    // pdf.js worker is copied into public/ instead of pulled from a CDN.
    expect(css).not.toMatch(/@import\s+url\(\s*['"]?https?:/)
    expect(css).not.toMatch(/src:\s*url\(\s*['"]?https?:/)
  })
})
