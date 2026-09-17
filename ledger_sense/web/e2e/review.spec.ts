import { expect, test, type Page } from '@playwright/test'
import { API_URL, seedDocument } from './seed'

// DoD 5: fifty exceptions cleared without a mouse. Every test seeds its own
// document through POST /api/fixtures/seed (demo profile only — see seed.ts)
// and navigates to the id the seeder minted; page.request is not a mouse
// event, so seeding after forbidMouse is fine.

/** The seeder keys exception rows by `doc_id` prefix and REPLACES them on
 *  re-seed (api/fixtures.py). Under fullyParallel two tests seeding 'doc-3' at
 *  once would delete each other's fixtures mid-test, so each worker seeds
 *  under its own key. Tests in one worker run sequentially and may reuse it. */
function docKey(stem: string): string {
  return `${stem}-w${test.info().workerIndex}`
}

/** Records any mouse event that reaches the page. DoD 5 is "keyboard-only",
 *  not "keyboard also works". */
async function forbidMouse(page: Page) {
  await page.addInitScript(() => {
    for (const type of ['click', 'mousedown', 'mouseup', 'dblclick']) {
      document.addEventListener(type, () => {
        ;(window as unknown as { __mouseUsed?: boolean }).__mouseUsed = true
      }, true)
    }
  })
}

test('clears fifty exceptions without a single mouse event', async ({ page }) => {
  await forbidMouse(page)
  const docId = await seedDocument(page, docKey('doc-50'), 50)
  await page.goto(`/review/${docId}`)
  await expect(page.getByTestId('progress')).toHaveText('0 / 50')

  for (let i = 0; i < 50; i++) {
    await page.keyboard.press('Enter')
  }

  await expect(page.getByTestId('progress')).toHaveText('50 / 50')
  expect(await page.evaluate(
    () => (window as unknown as { __mouseUsed?: boolean }).__mouseUsed)).toBeUndefined()
})

test('J and K move through the queue in both directions', async ({ page }) => {
  const docId = await seedDocument(page, docKey('doc-3'), 3)
  await page.goto(`/review/${docId}`)
  const field = page.getByTestId('current-field')
  const first = await field.textContent()

  await page.keyboard.press('j')
  expect(await field.textContent()).not.toBe(first)

  await page.keyboard.press('k')
  expect(await field.textContent()).toBe(first)
})

test('E opens the editor and typing does not fire shortcuts', async ({ page }) => {
  const docId = await seedDocument(page, docKey('doc-3'), 3)
  await page.goto(`/review/${docId}`)
  await page.keyboard.press('e')
  await expect(page.getByTestId('edit-input')).toBeFocused()

  // "jerk" contains j, e, r and k — every shortcut. None may fire.
  await page.keyboard.type('jerk')
  await expect(page.getByTestId('edit-input')).toHaveValue('jerk')
  await expect(page.getByTestId('progress')).toHaveText('0 / 3')
})

test('R requires a reason before it clears the exception', async ({ page }) => {
  const docId = await seedDocument(page, docKey('doc-3'), 3)
  await page.goto(`/review/${docId}`)
  await page.keyboard.press('r')
  await expect(page.getByTestId('reject-reason')).toBeFocused()

  await page.keyboard.press('Enter')
  await expect(page.getByTestId('progress')).toHaveText('0 / 3')

  await page.keyboard.type('wrong column picked up')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('progress')).toHaveText('1 / 3')
})

test('the queue advance is announced to assistive technology', async ({ page }) => {
  const docId = await seedDocument(page, docKey('doc-3'), 3)
  await page.goto(`/review/${docId}`)
  // Enter is deliberately NOT type-ahead (R-A): pressed before the queue has
  // loaded it does nothing, so wait for the first exception to be on screen.
  await expect(page.getByTestId('current-field')).toBeVisible()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('status')).toContainText('2 of 3')
})

test('Enter before the queue has loaded accepts nothing', async ({ page }) => {
  const docId = await seedDocument(page, docKey('doc-3'), 3)
  await page.goto(`/review/${docId}`)
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('current-field')).toBeVisible()
  await expect(page.getByRole('status')).toContainText('1 of 3')
  await expect(page.getByTestId('progress')).toHaveText('0 / 3')
})

test('a write that fails leaves a marker on the item until it is retried', async ({ page }) => {
  const docId = await seedDocument(page, docKey('doc-3'), 3)
  await page.goto(`/review/${docId}`)
  await expect(page.getByTestId('progress')).toHaveText('0 / 3')

  // The accept fails; the resync GETs that follow are untouched and succeed.
  await page.route('**/api/review/exceptions/*/accept', (route) =>
    route.fulfill({ status: 500, contentType: 'application/json', body: '{}' }))
  await page.keyboard.press('Enter')

  // Advanced on the keystroke; the failure shows up where the reviewer is
  // not, and on the item when they go back to it.
  await expect(page.getByRole('status')).toContainText('2 of 3')
  await expect(page.getByTestId('failed-count')).toBeVisible()
  await expect(page.getByTestId('progress')).toHaveText('0 / 3')
  await page.keyboard.press('k')
  await expect(page.getByTestId('item-failure')).toBeVisible()
  await expect(page.getByTestId('failed-count')).toHaveCount(0)

  await page.unroute('**/api/review/exceptions/*/accept')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('progress')).toHaveText('1 / 3')
  await expect(page.getByTestId('item-failure')).toHaveCount(0)
  await expect(page.getByTestId('failed-count')).toHaveCount(0)
})

test('a failed write whose resync also fails is never reported as cleared', async ({ page }) => {
  const docId = await seedDocument(page, docKey('doc-2'), 2)
  await page.goto(`/review/${docId}`)
  await expect(page.getByTestId('progress')).toHaveText('0 / 2')

  const fail = (route: import('@playwright/test').Route) =>
    route.fulfill({ status: 500, contentType: 'application/json', body: '{}' })
  await page.route('**/api/review/exceptions/*/accept', fail)
  await page.route('**/api/review/documents/*/exceptions', fail)
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('unverified-count')).toBeVisible()
  await expect(page.getByTestId('all-cleared')).toHaveCount(0)

  // Writes work again: the next successful one resyncs rather than merely
  // recounting, so the unverified item surfaces as a marked, open exception.
  await page.unroute('**/api/review/exceptions/*/accept')
  await page.unroute('**/api/review/documents/*/exceptions')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('progress')).toHaveText('1 / 2')
  await expect(page.getByTestId('unverified-count')).toHaveCount(0)
  await expect(page.getByTestId('all-cleared')).toHaveCount(0)
  await page.keyboard.press('k')
  await expect(page.getByTestId('item-failure')).toBeVisible()
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('progress')).toHaveText('2 / 2')
  await expect(page.getByTestId('all-cleared')).toBeVisible()
})

test('Escape drops an edit in progress and Enter then accepts the original', async ({ page }) => {
  const docId = await seedDocument(page, docKey('doc-3'), 3)
  await page.goto(`/review/${docId}`)
  await page.keyboard.press('e')
  await expect(page.getByTestId('edit-input')).toBeFocused()
  const original = await page.getByTestId('edit-input').inputValue()

  await page.keyboard.type('x')
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('edit-input')).not.toBeFocused()
  await expect(page.getByTestId('edit-input')).toHaveValue(original)

  const accepted = page.waitForRequest((r) => r.url().endsWith('/accept') && r.method() === 'POST')
  await page.keyboard.press('Enter')
  await accepted
  await expect(page.getByTestId('progress')).toHaveText('1 / 3')
})

test('a write the server has already applied resyncs instead of reopening', async ({ page }) => {
  const key = docKey('doc-2')
  const docId = await seedDocument(page, key, 2)
  await page.goto(`/review/${docId}`)
  await expect(page.getByTestId('progress')).toHaveText('0 / 2')

  // Close exception 0 behind the UI's back — same session, straight to the
  // API — so the UI's Enter on it gets a 409 from review.py's compare-and-swap.
  const closed = await page.request.post(
    `${API_URL}/api/review/exceptions/${encodeURIComponent(`${key}:exc:0`)}/accept`)
  expect(closed.status()).toBe(200)

  await page.keyboard.press('Enter')
  await expect(page.getByTestId('progress')).toHaveText('1 / 2')
  await expect(page.getByTestId('item-failure')).toHaveCount(0)
  await expect(page.getByTestId('failed-count')).toHaveCount(0)
  await expect(page.getByRole('status')).toContainText('2 of 2')

  await page.keyboard.press('Enter')
  await expect(page.getByTestId('progress')).toHaveText('2 / 2')
  await expect(page.getByTestId('all-cleared')).toBeVisible()
})
