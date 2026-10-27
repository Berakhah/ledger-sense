/**
 * Design tokens — 00 §5.2.
 *
 * This module is the source of truth for the values that appear in more than
 * one place. app/globals.css restates them as CSS custom properties inside
 * Tailwind v4's `@theme`, because CSS cannot import TypeScript; tokens.css.test.ts
 * fails if the two ever drift. That test exists because F-55 was exactly this
 * shape — one constant written down twice, both copies asserted, neither
 * derived from the other.
 *
 * Anything used only by CSS (spacing ramp, radii, font stacks) lives only in
 * globals.css. Only the literals TypeScript also needs are duplicated here.
 */

/** 00 §5.2. Ordered loosest to tightest; the review queue's density control
 *  steps through them in this order, so the order is part of the contract. */
export const DENSITIES = ['comfortable', 'compact', 'dense'] as const

export type Density = (typeof DENSITIES)[number]

export type ColourScheme = 'light' | 'dark'

/** Row height per density, in px. The review queue is a list of exceptions
 *  read at speed; the density control is what makes fifty of them fit. */
export const DENSITY_ROW_HEIGHT: Record<Density, number> = {
  comfortable: 44,
  compact: 36,
  dense: 28,
}

export const tokens = {
  /**
   * Ledger green. The one accent: it marks the accepted-field rule in the
   * review queue and nothing else, so its presence on screen means one thing.
   * OKLCH, not hex — 00 §5.2 specifies perceptual lightness so the accent
   * keeps its weight against both schemes and survives a greyscale print.
   */
  accent: 'oklch(0.58 0.11 158)',

  /** The working screens boot dark: a luminous document page on a dark stage,
   *  read for hours. Light stays supported, and print is always light
   *  (globals.css) because the reconciliation screen is handed to auditors. */
  defaultScheme: 'dark' as ColourScheme,

  /** 00 §5.2. Comfortable is a preference; compact is the working default. */
  defaultDensity: 'compact' as Density,

  /**
   * Every money and quantity column. `tabular-nums` is what makes a G703
   * column scan as a column — proportional digits put the decimal points out
   * of line and the eye stops being able to add. `slashed-zero` keeps 0 and O
   * apart in an extracted field a human is checking against a scan.
   */
  numeric: {
    fontVariantNumeric: 'tabular-nums slashed-zero lining-nums',
  },
} as const
