import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Unit and component tests. The browser-level suite is Playwright's
// (playwright.config.ts) — these two never overlap: anything needing a real
// document render, a keyboard path, or axe belongs there.
export default defineConfig({
  plugins: [react()],
  test: {
    // jsdom, not node: @testing-library/react needs a DOM, and the tests that
    // matter here are component tests.
    environment: 'jsdom',
    include: ['ledger_sense/web/**/*.test.{ts,tsx}'],
    exclude: ['**/node_modules/**', 'ledger_sense/web/e2e/**'],
    globals: false,
  },
})
