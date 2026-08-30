import { defineConfig, devices } from '@playwright/test'

/**
 * Browser-level suite. Phase 4's keyboard-only run through fifty exceptions
 * and its axe pass live here; vitest.config.ts owns everything that does not
 * need a real browser.
 *
 * The stack under test is the compose `demo` profile and only ever that:
 * `demo` is the profile in which POST /api/fixtures/seed exists at all (see
 * ledger_sense/api/fixtures.py), its Postgres is tmpfs, and every row it
 * serves is labelled SYNTHETIC. A `client` deployment has no seed route, so
 * pointing this suite at one fails loudly on the first fixture rather than
 * quietly touching real documents.
 */
const WEB_URL = process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:3000'

export default defineConfig({
  testDir: './ledger_sense/web/e2e',
  fullyParallel: true,
  // A retry masks the flake that a keyboard-path test is supposed to catch.
  retries: 0,
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: WEB_URL,
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: {
    // --wait blocks until every service reports started, so the url poll below
    // is a readiness check rather than the only thing holding the suite back.
    command: 'docker compose --profile demo up --build --wait',
    url: `${WEB_URL}/`,
    // Locally, reuse a stack the developer already has up; in CI always build
    // a fresh one, because "it passed against whatever was running" is not a
    // result.
    reuseExistingServer: !process.env.CI,
    timeout: 300_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
})
