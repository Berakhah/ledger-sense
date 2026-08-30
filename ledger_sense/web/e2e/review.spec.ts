import { expect, test } from '@playwright/test'
import { seedDocument } from './seed'

// The fixture Phase 4's suite is written against. G-9: every Phase 4 test
// navigates to /review/doc-50 and no task created it. This is that task's
// proof the path works end to end — seed, then navigate, then read the page.
// Phase 4 replaces the assertion with the real split view and the
// J/K/Enter/E/R run; the seeding half above it stays as it is.
test('a seeded document opens in the review queue', async ({ page }) => {
  const docId = await seedDocument(page, 'doc-50', 50)
  expect(docId).toBe('doc-50')

  await page.goto(`/review/${docId}`)
  await expect(page.getByTestId('doc-id')).toHaveText('doc-50')
})
