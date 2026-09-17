import { expect, test, type Page } from '@playwright/test'
import { seedDocument } from './seed'

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
  await page.keyboard.press('Enter')
  await expect(page.getByRole('status')).toContainText('2 of 3')
})
