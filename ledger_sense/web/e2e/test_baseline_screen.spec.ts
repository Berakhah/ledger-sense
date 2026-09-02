import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'
import { seedDocument } from './seed'

// ledger_sense.baseline.study.MIN_SAMPLES_FOR_AN_INTERVAL (F-54): a
// t-interval on fewer right-skewed observations is not defensible, so both
// the API and app/baseline/page.tsx's MIN_SAMPLES_FOR_REPORT refuse a
// report below this count. The Task 13b brief predates that revision and
// used 3 samples / "at least 2" here; adapted to the current, correct rule.
const MIN_SAMPLES = 8

test('times documents and produces a report with an interval', async ({ page }) => {
  // Called purely for its session-issuing side effect: seedDocument mints a
  // throwaway tenant and leaves the session cookie on this page's browser
  // context, the same way review.spec.ts gets one. The doc id it returns is
  // not used — a baseline study is not tenant-scoped fixture data.
  await seedDocument(page, 'baseline-screen-session', 0)

  await page.goto('/baseline')
  await page.getByLabel('Documents in period').fill('500')
  await page.getByLabel('Sampling frame').fill('all AP documents received Aug 2026')

  for (let i = 0; i < MIN_SAMPLES; i++) {
    await page.keyboard.press('Space') // start
    await page.waitForTimeout(1100)
    await page.keyboard.press('Space') // stop
    await page.keyboard.press('Enter') // file the sample
  }
  await expect(page.getByTestId('sample-count')).toHaveText(String(MIN_SAMPLES))

  await page.getByLabel('Hourly rate').fill('30.00')
  await page.getByLabel('Overhead multiplier').fill('1.4')

  const [report] = await Promise.all([
    page.waitForEvent('popup'),
    page.getByRole('button', { name: 'Produce report' }).click(),
  ])
  await expect(report.locator('body')).toContainText('95% confidence interval')
})

test('report button explains itself below the minimum sample size', async ({ page }) => {
  await seedDocument(page, 'baseline-screen-session-2', 0)
  await page.goto('/baseline')
  await expect(page.getByRole('button', { name: 'Produce report' })).toBeDisabled()
  await expect(page.getByTestId('report-blocked-reason')).toContainText(`at least ${MIN_SAMPLES}`)
})

// Fix-wave finding 7: the window-level Space/Enter handler used to
// `preventDefault()` unconditionally except for the exception-note input,
// which meant a keyboard user tabbed to "Log exception" (Space/Enter are the
// NATIVE activation keys for a focused <button>) got the global timer/sample
// shortcut instead of activating the button they were actually focused on.
// The existing tests above use `page.keyboard.press` with nothing focused
// (so they exercise the global shortcut correctly) and never catch this --
// this test explicitly focuses a button first.
test('space and enter activate a focused button instead of the global shortcut', async ({ page }) => {
  await seedDocument(page, 'baseline-screen-session-3', 0)
  await page.goto('/baseline')

  const noteInput = page.getByLabel('Exception note')
  const logButton = page.getByRole('button', { name: 'Log exception' })
  await noteInput.fill('slow scan, re-timed')
  await logButton.focus()
  await page.keyboard.press('Enter')
  // If Enter had been swallowed by the global handler instead of activating
  // the focused button, fileSample() would have fired (routing to review
  // with elapsedMs still 0 is a no-op there) and the exception would never
  // have been logged -- so a logged exception is proof Enter reached the
  // button, not the global shortcut.
  await expect(page.getByText('slow scan, re-timed')).toBeVisible()
  // The timer must not have been touched by that Enter press.
  await expect(page.getByTestId('sample-count')).toHaveText('0')

  const startButton = page.getByRole('button', { name: 'Start' })
  await startButton.focus()
  await page.keyboard.press('Space')
  // Space activating the focused Start button (native <button> behaviour)
  // and Space activating the global timer toggle land on the same outcome
  // here, so assert the mechanism directly: the key must not have been
  // preventDefault()-ed, which native button activation never does but the
  // old global handler always did.
  const defaultPrevented = await page.evaluate(
    () => new Promise<boolean>((resolve) => {
      window.addEventListener('keydown', (e) => resolve(e.defaultPrevented), { once: true })
      document.activeElement?.dispatchEvent(
        new KeyboardEvent('keydown', { key: ' ', code: 'Space', bubbles: true, cancelable: true }))
    }))
  expect(defaultPrevented).toBe(false)
})

test('the screen has no serious or critical accessibility violations', async ({ page }) => {
  await seedDocument(page, 'baseline-screen-session-4', 0)
  await page.goto('/baseline')
  const results = await new AxeBuilder({ page }).analyze()
  const seriousOrCritical = results.violations.filter(
    (v) => v.impact === 'serious' || v.impact === 'critical')
  expect(seriousOrCritical, JSON.stringify(seriousOrCritical, null, 2)).toEqual([])
})
