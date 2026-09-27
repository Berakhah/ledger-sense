import { expect, test, type Page } from '@playwright/test'
import { expectNoMouse, forbidMouse, tabTo } from './keyboard'
import { seedVendorDocument, type VendorScenario } from './seed'

// The I2 confirm-vendor step and its undo, keyboard-only. Each test seeds one
// A2 finding plus one I2 after it (api/fixtures.py's vendor scenario), so the
// I2 is reached with J and confirming it moves the cursor back to the A2 —
// the case the cursor-independent "Vendor confirmed" banner exists for.

/** Per-worker keys, same reason as review.spec.ts: the seeder replaces
 *  exception rows by `doc_id` prefix. */
function docKey(stem: string): string {
  return `${stem}-w${test.info().workerIndex}`
}

const NEW_VENDOR: VendorScenario = { observed_name: 'Keystone Framing LLC' }
const KNOWN_VENDOR: VendorScenario = {
  observed_name: 'Acme Steel Company',
  known_vendor: 'Acme Steel Co.',
}

async function openOnTheVendorFinding(page: Page, stem: string, vendor: VendorScenario) {
  const seeded = await seedVendorDocument(page, docKey(stem), 1, vendor)
  await page.goto(`/review/${seeded.docId}`)
  await expect(page.getByTestId('current-field')).toHaveText('line[000].total_completed_and_stored')
  await expect(page.getByTestId('progress')).toHaveText('0 / 2')
  await page.keyboard.press('j')
  await expect(page.getByTestId('current-field')).toHaveText('vendor_name')
  return seeded
}

const queue = (page: Page) => page.getByRole('region', { name: 'Exception under review' })

test('a new vendor is confirmed and undone without a mouse', async ({ page }) => {
  await forbidMouse(page)
  await openOnTheVendorFinding(page, 'doc-vendor-new', NEW_VENDOR)

  // The announcer exists before anything is put in it — a live region
  // inserted together with its content is not reliably announced.
  const announcer = page.getByTestId('vendor-announcer')
  await expect(announcer).toHaveAttribute('aria-live', 'polite')

  await tabTo(page, page.getByRole('button', { name: 'Confirm new vendor' }))
  await page.keyboard.press('Enter')

  const banner = announcer.getByTestId('vendor-confirmed-banner')
  await expect(banner).toContainText('Vendor confirmed.')
  await expect(banner.getByRole('link', { name: /View vendor ledger/ })).toBeVisible()
  await expect(page.getByTestId('progress')).toHaveText('1 / 2')
  // The panel that held focus has unmounted; focus goes back to the queue,
  // not to <body>, so the next Tab starts where the reviewer is.
  await expect(queue(page)).toBeFocused()

  await tabTo(page, banner.getByTestId('undo-vendor-confirmation-banner'))
  await page.keyboard.press('Enter')
  await expect(banner).toHaveCount(0)
  await expect(page.getByTestId('progress')).toHaveText('0 / 2')
  await expect(queue(page)).toBeFocused()

  // Open again: the confirm step is back on the I2.
  await page.keyboard.press('j')
  await expect(page.getByTestId('current-field')).toHaveText('vendor_name')
  await expect(page.getByTestId('vendor-confirm-panel')).toBeVisible()
  await expectNoMouse(page)
})

test('undoing a link to a known vendor leaves that vendor in the ledger', async ({ page }) => {
  await forbidMouse(page)
  const { knownVendorId } = await openOnTheVendorFinding(page, 'doc-vendor-known', KNOWN_VENDOR)
  expect(knownVendorId).not.toBeNull()

  await tabTo(page, page.getByRole('button', { name: /^Yes, it.s Acme Steel Co\.$/ }))
  await page.keyboard.press('Enter')

  const banner = page.getByTestId('vendor-confirmed-banner')
  await expect(banner.getByRole('link', { name: /View vendor ledger/ }))
    .toHaveAttribute('href', `/vendors/${knownVendorId}`)
  await expect(page.getByTestId('progress')).toHaveText('1 / 2')

  await tabTo(page, banner.getByTestId('undo-vendor-confirmation-banner'))
  await page.keyboard.press('Enter')
  await expect(banner).toHaveCount(0)
  await expect(page.getByTestId('progress')).toHaveText('0 / 2')

  // The vendor predates this document (its evidence is a prior bill), so
  // undo must not have deleted it.
  await page.goto(`/vendors/${knownVendorId}`)
  await expect(page.getByRole('heading', { level: 1, name: 'Acme Steel Co.' })).toBeVisible()
  await expectNoMouse(page)
})

test('a confirmation can be undone on a later visit', async ({ page }) => {
  await forbidMouse(page)
  await openOnTheVendorFinding(page, 'doc-vendor-audit', NEW_VENDOR)
  await tabTo(page, page.getByRole('button', { name: 'Confirm new vendor' }))
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('progress')).toHaveText('1 / 2')

  // A later audit: no banner, no client-side memory of the confirmation —
  // the link and Undo come from the server's own record of the vendor.
  await page.reload()
  await expect(page.getByTestId('current-field')).toHaveText('line[000].total_completed_and_stored')
  await expect(page.getByTestId('vendor-confirmed-banner')).toHaveCount(0)
  await page.keyboard.press('j')
  await expect(page.getByTestId('current-field')).toHaveText('vendor_name')
  await expect(page.getByRole('link', { name: /View vendor ledger/ })).toBeVisible()

  await tabTo(page, page.getByTestId('undo-vendor-confirmation'))
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('progress')).toHaveText('0 / 2')
  await expect(page.getByTestId('vendor-confirm-panel')).toBeVisible()
  await expect(queue(page)).toBeFocused()
  await expectNoMouse(page)
})
