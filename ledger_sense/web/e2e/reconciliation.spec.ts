import { expect, test } from '@playwright/test'
import { seedReconciliationDocument } from './seed'

// Task 32: the reconciliation view. Every test seeds its own document through
// POST /api/reconciliation/seed (demo profile only — see seed.ts) and
// navigates to the id it minted. Same per-worker key discipline as
// review.spec.ts: the in-memory store is process-wide, so two workers seeding
// the same doc_id under fullyParallel would race each other.
function docKey(stem: string): string {
  return `${stem}-w${test.info().workerIndex}`
}

test('a discrepancy is highlighted at the exact cell with its arithmetic', async ({ page }) => {
  const docId = await seedReconciliationDocument(page, docKey('doc-a1'), 'A1_footing')
  await page.goto(`/reconciliation/${docId}`)
  const cell = page.getByTestId('discrepant-cell')
  await expect(cell).toHaveCount(1)
  await expect(page.getByTestId('inline-arithmetic')).toContainText('=')
})

test('a per-line discrepancy is highlighted inside the line table, not the summary or fallback', async ({ page }) => {
  // A2_line_sum mutates lines[0] (item_no "010") — the discrepant cell must
  // land inside that line's row in the continuation-sheet table. (The same
  // mutation also throws the summary footing off (A1), since the lines no
  // longer sum to the unchanged summary total — that one lands in the
  // Summary table, which this test leaves alone.)
  const docId = await seedReconciliationDocument(page, docKey('doc-a2'), 'A2_line_sum')
  await page.goto(`/reconciliation/${docId}`)

  const row = page.getByLabel('Continuation sheet').locator('tbody tr', { hasText: '010' })
  const cell = row.getByTestId('discrepant-cell')
  await expect(cell).toHaveCount(1)
  await expect(cell.getByTestId('inline-arithmetic')).toContainText('=')

  // Not duplicated into the "Other discrepancies" fallback, which only ever
  // holds a field with no dedicated cell on this screen.
  await expect(page.getByLabel('Other discrepancies')).toHaveCount(0)
})

test('severity is never encoded in colour alone', async ({ page }) => {
  const docId = await seedReconciliationDocument(page, docKey('doc-a1'), 'A1_footing')
  await page.goto(`/reconciliation/${docId}`)
  const cell = page.getByTestId('discrepant-cell')
  await expect(cell.getByRole('img')).toBeVisible() // the glyph
  await expect(cell).toContainText(/discrepancy/i) // the text label
})

test('a clean document shows no discrepant cells', async ({ page }) => {
  const docId = await seedReconciliationDocument(page, docKey('doc-clean'), 'clean')
  await page.goto(`/reconciliation/${docId}`)
  await expect(page.getByTestId('discrepant-cell')).toHaveCount(0)
})

test('the print stylesheet keeps the discrepancy legible in greyscale', async ({ page }) => {
  const docId = await seedReconciliationDocument(page, docKey('doc-a1'), 'A1_footing')
  await page.goto(`/reconciliation/${docId}`)
  await page.emulateMedia({ media: 'print' })
  const outline = await page
    .getByTestId('discrepant-cell')
    .evaluate((el) => getComputedStyle(el).outlineWidth)
  expect(outline).not.toBe('0px')
})

test('an unknown document reports a load error rather than an empty grid', async ({ page }) => {
  await seedReconciliationDocument(page, docKey('doc-any'), 'clean')
  await page.goto('/reconciliation/doc-definitely-not-seeded')
  await expect(page.getByTestId('load-error')).toBeVisible()
})
