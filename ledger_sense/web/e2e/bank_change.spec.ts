import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { expectNoMouse, forbidMouse, tabTo } from './keyboard'
import { seedBankDocument } from './seed'

// The I3 bank-detail hard stop, keyboard-only: a bare Enter changes nothing,
// the attestation form confirms with Ctrl+Enter, and Undo reopens it. Each test
// seeds a real corpus PDF through the real pipeline (api/fixtures.py's bank
// scenario), so the findings are the product's own, not hand-made rows.

function docKey(stem: string): string {
  return `${stem}-w${test.info().workerIndex}`
}

const queue = (page: Page) => page.getByRole('region', { name: 'Exception under review' })

/** Walk to the exception raised by `rule` with J then K, as a reviewer would.
 *  The real pipeline raises R1 (field not cleared: uncalibrated) on every field
 *  too, so the I-rule items are among dozens, in whatever order the queue gives. */
async function goToRule(page: Page, rule: string) {
  const current = () => page.getByTestId('current-rule').textContent()
  for (const key of ['j', 'k']) {
    for (let i = 0; i < 80; i++) {
      if ((await current()) === rule) return
      await page.keyboard.press(key)
    }
  }
  throw new Error(`never reached ${rule}`)
}

/** The queue's "cleared / total" counter, as numbers. */
async function progressOf(page: Page): Promise<{ cleared: number; total: number }> {
  const text = (await page.getByTestId('progress').textContent()) ?? ''
  const m = /(\d+)\s*\/\s*(\d+)/.exec(text)
  if (!m) throw new Error(`unreadable progress: ${text}`)
  return { cleared: Number(m[1]), total: Number(m[2]) }
}

async function expectCleared(page: Page, cleared: number) {
  await expect(page.getByTestId('progress')).toHaveText(new RegExp('^\\s*' + cleared + '\\s*/'))
}

/** Confirm the open I3 panel: name a contact, Ctrl+Enter. Confirming is pessimistic
 *  (waits for the server) and the cursor then moves on to the next open item, so
 *  walk back to the confirmed I3 with K/J. */
async function confirmAndReturn(page: Page) {
  const before = (await progressOf(page)).cleared
  await tabTo(page, page.getByTestId('bank-contact'))
  await page.keyboard.type('J. Vendor')
  await page.keyboard.press('Control+Enter')
  await expectCleared(page, before + 1)
  await expect(queue(page)).toBeFocused()
  await goToRule(page, 'I3')
  await expect(page.getByTestId('bank-confirmed')).toBeVisible()
}

async function seriousViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze()
  return results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
}

test('a changed account is confirmed with Ctrl+Enter and undone, keyboard-only', async ({ page }) => {
  await forbidMouse(page)
  const { docId } = await seedBankDocument(page, docKey('bank-changed'), { seed: 40, variant: 'changed' })
  await page.goto(`/review/${docId}`)
  await goToRule(page, 'I3')
  expect((await progressOf(page)).total).toBeGreaterThan(1)

  const panel = page.getByTestId('bank-change-panel')
  await expect(panel).toBeVisible()
  await expect(panel.locator('del')).toHaveCount(1)
  await expect(panel.locator('ins')).toHaveCount(1)

  // Bare Enter is the queue's Accept key — on a hard stop it must do nothing.
  await queue(page).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('action-error')).toContainText('Bank-detail changes are cleared only')
  await expectCleared(page, 0)
  await expect(panel).toBeVisible()
  // E and R are inert too.
  await page.keyboard.press('e')
  await page.keyboard.press('r')
  await expectCleared(page, 0)

  await tabTo(page, page.getByTestId('bank-contact'))
  await page.keyboard.type('J. Vendor')
  // A bare Enter inside the form does not confirm either.
  await page.keyboard.press('Enter')
  await expectCleared(page, 0)

  await page.keyboard.press('Control+Enter')
  // Pessimistic: the server answers, the item clears, focus returns to the queue,
  // and the cursor moves on to the next open exception (the real queue has ~60).
  await expectCleared(page, 1)
  await expect(queue(page)).toBeFocused()
  await goToRule(page, 'I3')
  await expect(page.getByTestId('bank-confirmed')).toBeVisible()

  await tabTo(page, page.getByTestId('undo-bank-change'))
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('bank-change-panel')).toBeVisible()
  await expectCleared(page, 0)
  await expectNoMouse(page)
})

test('a first sighting is reached after confirming the vendor, then confirmed', async ({ page }) => {
  await forbidMouse(page)
  const { docId } = await seedBankDocument(page, docKey('bank-first'), { seed: 41 })
  await page.goto(`/review/${docId}`)

  // I2 first: confirming bank details against an unconfirmed vendor is refused.
  await goToRule(page, 'I2')
  await tabTo(page, page.getByRole('button', { name: 'Confirm new vendor' }))
  await page.keyboard.press('Enter')
  await goToRule(page, 'I3')
  await expect(page.getByTestId('bank-change-panel').locator('del')).toHaveCount(0)

  await confirmAndReturn(page)
  await expectNoMouse(page)
})

test('"Leave open, next" records nothing and the stop stays open', async ({ page }) => {
  const { docId } = await seedBankDocument(page, docKey('bank-leave'), { seed: 42, variant: 'changed' })
  await page.goto(`/review/${docId}`)
  await goToRule(page, 'I3')
  await tabTo(page, page.getByTestId('bank-leave-open'))
  await page.keyboard.press('Enter')
  // Other exceptions are open, so the cursor moves on; nothing is recorded.
  await expect(page.getByTestId('current-rule')).not.toHaveText('I3')
  await expectCleared(page, 0)
  await goToRule(page, 'I3')
  await expect(page.getByTestId('bank-change-panel')).toBeVisible()
  await expect(page.getByTestId('bank-confirmed')).toHaveCount(0)
})

test('the open panel passes axe, and the diff reads in greyscale', async ({ page }, testInfo) => {
  const { docId } = await seedBankDocument(page, docKey('bank-a11y'), { seed: 43, variant: 'changed' })
  await page.goto(`/review/${docId}`)
  await goToRule(page, 'I3')
  await expect(page.getByTestId('bank-change-panel')).toBeVisible()
  expect(await seriousViolations(page)).toEqual([])

  await page.addStyleTag({ content: 'html { filter: grayscale(1); }' })
  await testInfo.attach('bank-diff-greyscale', {
    body: await page.getByTestId('bank-change-panel').screenshot(),
    contentType: 'image/png',
  })
})

test('the confirmed state passes axe', async ({ page }) => {
  const { docId } = await seedBankDocument(page, docKey('bank-a11y2'), { seed: 44, variant: 'changed' })
  await page.goto(`/review/${docId}`)
  await goToRule(page, 'I3')
  await confirmAndReturn(page)
  expect(await seriousViolations(page)).toEqual([])
})
