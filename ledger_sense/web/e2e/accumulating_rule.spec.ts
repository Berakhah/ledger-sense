import { expect, test, type Page } from '@playwright/test'
import { seedDocument } from './seed'

const API_URL = process.env.PLAYWRIGHT_API_URL ?? 'http://localhost:8000'

// Task 31: the accumulating verification rule. Each accepted field leaves a
// thin rule on the document that STAYS — across keystrokes, across
// navigation, and across whatever the server already knows. Same seeder and
// per-worker key discipline as review.spec.ts.
function docKey(stem: string): string {
  return `${stem}-w${test.info().workerIndex}`
}

const rules = (page: Page) => page.locator('[data-testid^="accepted-rule-"]')

test('each accepted field leaves a rule, and the rules accumulate', async ({ page }) => {
  const docId = await seedDocument(page, docKey('rule-3'), 3)
  await page.goto(`/review/${docId}`)
  await expect(page.getByTestId('current-field')).toBeVisible()
  await expect(rules(page)).toHaveCount(0)

  await page.keyboard.press('Enter')
  await expect(rules(page)).toHaveCount(1)

  await page.keyboard.press('Enter')
  await expect(rules(page)).toHaveCount(2)
})

test('rules survive navigating away and back', async ({ page }) => {
  const docId = await seedDocument(page, docKey('rule-3'), 3)
  await page.goto(`/review/${docId}`)
  await expect(page.getByTestId('current-field')).toBeVisible()
  await page.keyboard.press('Enter')
  await expect(rules(page)).toHaveCount(1)

  await page.goto('/baseline')
  await page.goto(`/review/${docId}`)
  await expect(page.getByTestId('current-field')).toBeVisible()
  await expect(rules(page)).toHaveCount(1)
})

test('a field the server already closed gets its rule on resync', async ({ page }) => {
  const key = docKey('rule-2')
  const docId = await seedDocument(page, key, 2)
  await page.goto(`/review/${docId}`)
  await expect(page.getByTestId('current-field')).toBeVisible()
  await expect(rules(page)).toHaveCount(0)

  // Accept exception 0 behind the UI's back, so the UI's own Enter gets a 409
  // and resyncs from the server list — which now says the field is verified.
  const closed = await page.request.post(
    `${API_URL}/api/review/exceptions/${encodeURIComponent(`${key}:exc:0`)}/accept`)
  expect(closed.status()).toBe(200)

  await page.keyboard.press('Enter')
  await expect(page.getByTestId('progress')).toHaveText('1 / 2')
  await expect(rules(page)).toHaveCount(1)
})

test('a rejected field leaves no rule', async ({ page }) => {
  const docId = await seedDocument(page, docKey('rule-3'), 3)
  await page.goto(`/review/${docId}`)
  await expect(page.getByTestId('current-field')).toBeVisible()

  await page.keyboard.press('r')
  await page.keyboard.type('wrong column picked up')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('progress')).toHaveText('1 / 3')
  await expect(rules(page)).toHaveCount(0)
})
