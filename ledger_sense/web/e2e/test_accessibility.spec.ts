import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { tabTo } from './keyboard'
import { seedDocument, seedReconciliationDocument, seedVendorDocument } from './seed'

// Task 33: an axe gate across every screen, density and the 375px viewport.
//
// CORRECTIONS ADDENDUM (see task-33-brief.md):
// 1. Lives under ledger_sense/web/e2e/, this repo's one Playwright testDir.
// 2. There is no ambient fixture data. Each screen seeds its own document
//    through seed.ts's helpers and builds its URL from the id the seeder
//    minted, rather than a hard-coded path.
// 3. /baseline needs no seeding — it is a bare stateful form with no
//    "with data" shortcut; testing the bare route is correct for this task.
// 5. PLAN DEFECT, ruling already made: the "reduced motion disables the
//    provenance rail animation" test is dropped. No provenance rail
//    (data-testid, Cmd+./Ctrl+. chord, reduced-motion CSS) exists anywhere in
//    this codebase, and no task in any phase plan builds one — see the
//    addendum's item 5. Every other test below stays in scope.
//
// Same per-worker key discipline as review.spec.ts/reconciliation.spec.ts:
// the in-memory fixture store is process-wide, so two workers seeding the
// same doc_id under fullyParallel would race each other.
function docKey(stem: string): string {
  return `${stem}-w${test.info().workerIndex}`
}

const blocking = (violations: { impact?: string | null }[]) =>
  violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')

async function expectAxeClean(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze()
  const found = blocking(results.violations)
  // A negative assertion passes just as happily when axe evaluated nothing
  // (see tests/api/test_route_registration.py::test_the_walk_is_not_empty
  // for the house precedent on this shape of bug) — prove axe actually ran
  // real checks against this page before trusting the zero-violations read.
  expect(results.passes.length).toBeGreaterThan(0)
  expect(found, JSON.stringify(found, null, 2)).toEqual([])
}

interface Screen {
  name: string
  seed: (page: Page) => Promise<string>
  /** What must be on screen before axe looks — without it axe can run
   *  against a loading placeholder and pass on markup nobody reviews with. */
  ready?: (page: Page) => Promise<void>
}

const SCREENS: Screen[] = [
  { name: 'baseline', seed: async () => '/baseline' },
  {
    name: 'review',
    seed: async (page) => {
      const docId = await seedDocument(page, docKey('doc-a11y'), 3)
      return `/review/${docId}`
    },
  },
  {
    // The I2 alone, so the cursor lands on it: the confirm-vendor panel
    // (candidate button, new-vendor field) is what axe sees.
    name: 'review with an open vendor panel',
    seed: async (page) => {
      const { docId } = await seedVendorDocument(page, docKey('doc-a11y-vendor'), 0, {
        observed_name: 'Acme Steel Company', known_vendor: 'Acme Steel Co.',
      })
      return `/review/${docId}`
    },
    ready: async (page) => {
      await expect(page.getByTestId('vendor-confirm-panel')).toBeVisible()
    },
  },
  {
    name: 'reconciliation',
    seed: async (page) => {
      // A real error class, not 'clean': axe must exercise the discrepant-cell
      // markup (severity glyph + text label), not an empty grid.
      const docId = await seedReconciliationDocument(page, docKey('doc-a11y'), 'A1_footing')
      return `/reconciliation/${docId}`
    },
  },
]

const DENSITIES = ['comfortable', 'compact', 'dense'] as const

for (const screen of SCREENS) {
  test(`${screen.name} has no serious or critical axe violations`, async ({ page }) => {
    const url = await screen.seed(page)
    await page.goto(url)
    await screen.ready?.(page)
    await expectAxeClean(page)
  })

  test(`${screen.name} does not scroll horizontally at 375px`, async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    const url = await screen.seed(page)
    await page.goto(url)
    await screen.ready?.(page)
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    )
    expect(overflow).toBe(false)
  })

  for (const density of DENSITIES) {
    test(`${screen.name} passes axe at ${density} density`, async ({ page }) => {
      const url = await screen.seed(page)
      await page.goto(url)
      await screen.ready?.(page)
      await page.evaluate(
        (d) => document.documentElement.setAttribute('data-density', d),
        density,
      )
      const results = await new AxeBuilder({ page }).analyze()
      // Same non-vacuity guard as the axe test above.
      expect(results.passes.length).toBeGreaterThan(0)
      expect(blocking(results.violations)).toEqual([])
    })
  }
}

test('review with the vendor-confirmed banner showing passes axe', async ({ page }) => {
  // One A2 then the I2: confirming moves the cursor back to the A2, so what
  // axe sees is the banner on its own, the state a reviewer is left in.
  const { docId } = await seedVendorDocument(page, docKey('doc-a11y-banner'), 1, {
    observed_name: 'Keystone Framing LLC',
  })
  await page.goto(`/review/${docId}`)
  await expect(page.getByTestId('current-field')).toBeVisible()
  await page.keyboard.press('j')
  await tabTo(page, page.getByRole('button', { name: 'Confirm new vendor' }))
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('vendor-confirmed-banner')).toBeVisible()
  await expectAxeClean(page)
})

test('EVERY focusable control has a visible focus ring', async ({ page }) => {
  // The original pressed Tab once and checked one element, then called
  // itself "every focusable control". This walks every element the review
  // queue can put focus on and programmatically focuses each of them.
  const docId = await seedDocument(page, docKey('doc-a11y-focus'), 3)
  await page.goto(`/review/${docId}`)
  await expect(page.getByTestId('current-field')).toBeVisible()
  const { total, offenders: naked } = await page.evaluate(() => {
    const selector = 'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])'
    const offenders: string[] = []
    const elements = Array.from(document.querySelectorAll<HTMLElement>(selector))
    for (const el of elements) {
      el.focus()
      const style = getComputedStyle(el)
      const ringed = style.outlineWidth !== '0px' || style.boxShadow !== 'none'
      if (!ringed) offenders.push(el.outerHTML.slice(0, 80))
    }
    return { total: elements.length, offenders }
  })
  // Belt and braces (see test_the_walk_is_not_empty): the empty-array
  // assertion below passes just as happily if the selector matched nothing.
  expect(total).toBeGreaterThan(0)
  expect(naked, naked.join('\n')).toEqual([])
})

test('the document pane scrolls internally so the page does not, at 375px', async ({ page }) => {
  // F-63: Task 33 asserts no horizontal page scroll at 375px while Task 30
  // renders a ~1,188px canvas. The pane scrolls; the page never does.
  const docId = await seedDocument(page, docKey('doc-a11y-pane'), 3)
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto(`/review/${docId}`)
  await expect(page.getByTestId('document-pane')).toBeVisible()
  // On failure, name the elements poking past the viewport instead of a bare
  // true/false, so a Linux-only overflow (font metrics, scrollbar width) can
  // be diagnosed from the CI log alone.
  const overflow = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
    offenders: Array.from(document.querySelectorAll('body *'))
      .filter((el) => el.getBoundingClientRect().right > window.innerWidth + 0.5)
      .slice(0, 12)
      .map((el) => {
        const box = el.getBoundingClientRect()
        return `${el.tagName.toLowerCase()}[${el.getAttribute('data-testid') ?? ''}].${String(el.className).slice(0, 60)} right=${Math.round(box.right)} width=${Math.round(box.width)}`
      }),
  }))
  expect(overflow.scrollWidth, JSON.stringify(overflow, null, 1)).toBeLessThanOrEqual(
    overflow.innerWidth,
  )
  expect(
    await page
      .getByTestId('document-pane')
      .evaluate((el) => getComputedStyle(el.parentElement!).overflowX),
  ).toBe('auto')
})
