import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

// WCAG 2.2 AA for the colour tokens themselves, not for whichever screens the
// axe pass happens to render. --color-flag shipped at 3.75:1 on paper and
// 3.35:1 on its own tint; axe only caught it on the reconciliation screen,
// though every screen sets small text in it.
const css = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '..', 'app', 'globals.css'),
  'utf8',
)

type Rgb = [number, number, number]

function token(name: string): Rgb {
  const match = css.match(new RegExp(`--color-${name}: oklch\\(([\\d.]+) ([\\d.]+) ([\\d.]+)\\);`))
  if (!match) throw new Error(`--color-${name} is not an oklch() literal in globals.css`)
  const [, l, c, h] = match
  return oklchToSrgb(Number(l), Number(c), Number(h))
}

/** OKLCH to gamma-encoded sRGB, clamped to gamut as a browser renders it. */
function oklchToSrgb(l: number, c: number, h: number): Rgb {
  const a = c * Math.cos((h * Math.PI) / 180)
  const b = c * Math.sin((h * Math.PI) / 180)
  const l_ = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3
  const m_ = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3
  const s_ = (l - 0.0894841775 * a - 1.291485548 * b) ** 3
  const linear = [
    4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
    -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
    -0.0041960863 * l_ - 0.7034186147 * m_ + 1.707614701 * s_,
  ]
  const encode = (x: number) => (x <= 0.0031308 ? 12.92 * x : 1.055 * x ** (1 / 2.4) - 0.055)
  return linear.map((x) => Math.min(1, Math.max(0, encode(x)))) as Rgb
}

/** `bg-<token>/10` composited over paper, as the browser paints it. */
function tint(colour: Rgb, over: Rgb, alpha: number): Rgb {
  return colour.map((v, i) => alpha * v + (1 - alpha) * over[i]) as Rgb
}

function contrast(x: Rgb, y: Rgb): number {
  const lum = (c: Rgb) => {
    const [r, g, b] = c.map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b
  }
  const [hi, lo] = [lum(x), lum(y)].sort((p, q) => q - p)
  return (hi + 0.05) / (lo + 0.05)
}

const hex = (c: Rgb) => `#${c.map((v) => Math.round(v * 255).toString(16).padStart(2, '0')).join('')}`

/** AA for normal-size text: every one of these tokens sets 12-14px copy. */
const AA_TEXT = 4.5

describe('colour tokens meet WCAG AA for text', () => {
  it('reproduces what axe measured, so the maths here can be trusted', () => {
    // axe on the reconciliation screen: #cd632d on #f6ede8 at 3.35:1.
    const shipped = oklchToSrgb(0.62, 0.15, 45)
    const paper = token('paper')
    expect(hex(shipped)).toBe('#cd632d')
    expect(hex(tint(shipped, paper, 0.1))).toBe('#f6ede8')
    expect(contrast(shipped, tint(shipped, paper, 0.1))).toBeCloseTo(3.35, 1)
  })

  for (const name of ['ink', 'ink-muted', 'flag']) {
    it(`--color-${name} reads on paper`, () => {
      expect(contrast(token(name), token('paper'))).toBeGreaterThanOrEqual(AA_TEXT)
    })
  }

  it('--color-flag reads on its own tint (a discrepant cell: bg-flag/10)', () => {
    const flag = token('flag')
    expect(contrast(flag, tint(flag, token('paper'), 0.1))).toBeGreaterThanOrEqual(AA_TEXT)
  })
})
