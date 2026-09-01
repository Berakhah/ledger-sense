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
