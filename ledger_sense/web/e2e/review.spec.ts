import { expect, test } from '@playwright/test'
import { expectNoMouse, forbidMouse } from './keyboard'
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

/** How long the server's own count may take to reach 50 / 50. One reviewer's
 *  fifty accepts were measured landing in under a second; under fullyParallel
 *  several workers each fire fifty at the one API process at once, and with
 *  ten workers draining them was measured at 10-28s, every write a 200 and
 *  none lost. The keyboard's speed is asserted separately, on the writes, not
 *  on this count. */
const SERVER_CONFIRM_MS = 60_000

/** Seeding renders a PDF under the process-wide PDFium lock, so parallel
 *  workers queue for it (measured up to ~20s at ten workers), and that wait
 *  comes out of this test's budget. The budget must outlast seed plus the
 *  confirm wait, or a genuine stall hits the test timeout first and the
 *  failure evidence below is never attached. */
const FIFTY_TEST_MS = 30_000 + SERVER_CONFIRM_MS

test('clears fifty exceptions without a single mouse event', async ({ page }) => {
  test.setTimeout(FIFTY_TEST_MS)
  await forbidMouse(page)
  const docId = await seedDocument(page, docKey('doc-50'), 50)
  let accepts = 0
  page.on('request', (r) => {
    if (r.method() === 'POST' && r.url().endsWith('/accept')) accepts++
  })
  // Evidence for the one failure this test has shown that is not a budget:
  // once in ~440 parallel runs the count stopped at 49 / 50 with that item
  // really open in the database, while every request the API logged was a
  // 200. Unreproduced since, so recorded and attached on failure rather than
  // guessed at. pdf.js aborting its own PDF fetch is routine and not recorded.
  const evidence: string[] = []
  const t0 = Date.now()
  const note = (line: string) => evidence.push(`${Date.now() - t0}ms ${line}`)
  page.on('requestfailed', (r) => {
    if (!r.url().endsWith('/pdf')) note(`FAILED ${r.method()} ${r.url()} ${r.failure()?.errorText ?? ''}`)
  })
  page.on('response', (r) => {
    const url = r.url()
    if (r.request().method() === 'POST' && url.endsWith('/accept')) note(`${r.status()} POST ${url}`)
    else if (r.status() >= 400) note(`${r.status()} ${r.request().method()} ${url}`)
    else if (url.endsWith('/progress')) void r.text().then((body) => note(`200 progress ${body}`), () => undefined)
  })
  page.on('pageerror', (err) => note(`pageerror ${err.message}`))
  page.on('console', (m) => { if (m.type() === 'error') note(`console ${m.text()}`) })
  await page.goto(`/review/${docId}`)
  await expect(page.getByTestId('progress')).toHaveText('0 / 50')

  for (let i = 0; i < 50; i++) {
    await page.keyboard.press('Enter')
  }

  // Every keystroke became its own write, on the keystroke (optimistic, see
  // ReviewQueue's actOnCurrent): a dropped Enter fails here, fast.
  await expect.poll(() => accepts).toBe(50)
  try {
    await expect(page.getByTestId('progress'))
      .toHaveText('50 / 50', { timeout: SERVER_CONFIRM_MS })
  } catch (error) {
    // Best effort: a closed page must not replace the assertion's own error.
    const markers: string[] = []
    for (const id of ['progress', 'failed-count', 'unverified-count', 'action-error']) {
      const text = await page.getByTestId(id).textContent({ timeout: 1_000 }).catch(() => null)
      if (text !== null) markers.push(`${id}: ${text}`)
    }
    await test.info().attach('fifty-exception failure evidence', {
      body: ['page markers:', ...markers, 'network, in order:', ...evidence].join('\n'),
      contentType: 'text/plain',
    }).catch(() => undefined)
    throw error
  }
  // Exactly fifty: a duplicate write fired after the poll above saw 50 fails here.
  expect(accepts).toBe(50)
  await expectNoMouse(page)
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
