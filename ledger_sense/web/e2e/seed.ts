import type { Page } from '@playwright/test'

// The API is a separate origin from the app under test, so it is not
// baseURL-relative and has to be named. Same default as playwright.config.ts's
// webServer brings up.
export const API_URL = process.env.PLAYWRIGHT_API_URL ?? 'http://localhost:8000'

/**
 * Mint a synthetic document and its exceptions, and leave the page logged in
 * as the throwaway tenant that owns them.
 *
 * Call it through `page.request`, not a bare APIRequestContext: page.request
 * shares cookie storage with the page's browser context, so the session cookie
 * the seed response sets is the session the very next `page.goto` sends. That
 * is the whole reason the seed cookie is not marked Secure — over http://
 * localhost a Secure cookie is discarded by the client and the navigation that
 * follows is anonymous. ledger_sense/api/fixtures.py's module docstring states
 * the scope of that relaxation; nothing here is exempt from require_session.
 */
export async function seedDocument(
  page: Page,
  docId: string,
  exceptions: number,
): Promise<string> {
  const response = await page.request.post(`${API_URL}/api/fixtures/seed`, {
    data: { doc_id: docId, exceptions },
  })
  if (response.status() !== 201) {
    throw new Error(
      `seed failed (${response.status()}): ${await response.text()}\n` +
        'A 404 here means the API is not running under LEDGER_SENSE_PROFILE=demo — ' +
        'refuse to seed rather than pointing this suite at a client deployment.',
    )
  }
  const body = await response.json()
  return body.doc_id as string
}

export interface VendorScenario {
  observed_name: string
  known_vendor?: string
}

/**
 * seedDocument() plus one I2 vendor finding after the `exceptions` A2 rows.
 * With `known_vendor`, that vendor is already in the tenant's ledger (its
 * evidence a synthetic prior bill, not this document), so the review queue
 * offers "Yes, it's <known_vendor>". Same route, same demo-only guard.
 */
export async function seedVendorDocument(
  page: Page,
  docId: string,
  exceptions: number,
  vendor: VendorScenario,
): Promise<{ docId: string; knownVendorId: string | null }> {
  const response = await page.request.post(`${API_URL}/api/fixtures/seed`, {
    data: { doc_id: docId, exceptions, vendor },
  })
  if (response.status() !== 201) {
    throw new Error(`vendor seed failed (${response.status()}): ${await response.text()}`)
  }
  const body = await response.json()
  return { docId: body.doc_id as string, knownVendorId: body.known_vendor_id as string | null }
}

/**
 * Mint a synthetic reconciliation document and leave the page logged in as
 * the throwaway tenant that owns it.
 *
 * A separate seed route from seedDocument()'s: /api/fixtures/seed always
 * seeds error="clean" into the real Postgres-backed findings table, which is
 * the review queue's store, not this screen's — see
 * ledger_sense/api/reconciliation.py's module docstring. Same reasoning as
 * seedDocument() for going through page.request rather than a bare fetch:
 * the session cookie this route sets has to be visible to the page.goto()
 * that follows.
 */
export async function seedReconciliationDocument(
  page: Page,
  docId: string,
  error: string,
  seed = 1,
): Promise<string> {
  const response = await page.request.post(`${API_URL}/api/reconciliation/seed`, {
    data: { doc_id: docId, seed, error },
  })
  if (response.status() !== 201) {
    throw new Error(
      `reconciliation seed failed (${response.status()}): ${await response.text()}\n` +
        'A 404/405 here means the API is not running under LEDGER_SENSE_PROFILE=demo — ' +
        'refuse to seed rather than pointing this suite at a client deployment.',
    )
  }
  const body = await response.json()
  return body.doc_id as string
}
