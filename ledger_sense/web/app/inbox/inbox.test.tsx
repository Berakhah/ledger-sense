import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import InboxPage from './page'

// Adapted from the Task 43 brief's illustrative assertions to the real,
// already-shipped API shape (ledger_sense/api/inbox.py's `_row`/`_row_of`):
// no `confidence_band` exists and none is fabricated (CLAUDE.md, "Confidence
// routing" — nothing has an earned reliability curve yet), so this screen's
// a11y requirement ("never colour alone") is satisfied by the status word
// and the visible finding_count instead.
const ROWS = [
  {
    id: 'doc-1',
    filename: 'payapp-01.pdf',
    sensitivity: 'SYNTHETIC',
    origin: 'upload',
    received_at: '2026-09-20T14:30:00Z',
    status: 'extracted',
    doc_type: 'pay_application',
    run_note: null,
    finding_count: 2,
    claim_count: 12,
  },
  {
    id: 'doc-2',
    filename: 'scanned-invoice.pdf',
    sensitivity: 'CLIENT_CONFIDENTIAL',
    origin: 'upload',
    received_at: '2026-09-21T09:05:00Z',
    status: 'cannot_extract',
    doc_type: null,
    run_note: 'scanned image, no text layer and OCR confidence too low',
    finding_count: 0,
    claim_count: 0,
  },
]

function stubFetchOnce(body: unknown, ok = true): void {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok,
      status: ok ? 200 : 500,
      statusText: ok ? 'OK' : 'Internal Server Error',
      json: async () => body,
    }),
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('InboxPage', () => {
  it('shows a cannot_extract row rather than filtering it out', async () => {
    stubFetchOnce({ items: ROWS })
    render(<InboxPage />)

    await waitFor(() => {
      expect(screen.getAllByTestId('inbox-row')).toHaveLength(2)
    })
    expect(screen.getByText('scanned-invoice.pdf')).toBeTruthy()
    const cannotExtractRow = screen
      .getAllByTestId('inbox-row')
      .find((row) => row.textContent?.includes('scanned-invoice.pdf'))
    expect(cannotExtractRow?.getAttribute('data-status')).toBe('cannot_extract')
  })

  it('renders status as visible text, not colour alone', async () => {
    stubFetchOnce({ items: ROWS })
    render(<InboxPage />)

    await waitFor(() => {
      expect(screen.getAllByTestId('inbox-status')).toHaveLength(2)
    })
    const statusTexts = screen.getAllByTestId('inbox-status').map((el) => el.textContent)
    expect(statusTexts).toContain('extracted')
    expect(statusTexts).toContain('cannot_extract')
  })

  it("shows each row's sensitivity to the reviewer", async () => {
    stubFetchOnce({ items: ROWS })
    render(<InboxPage />)

    await waitFor(() => {
      expect(screen.getAllByTestId('inbox-sensitivity')).toHaveLength(2)
    })
    const sensitivities = screen.getAllByTestId('inbox-sensitivity').map((el) => el.textContent)
    expect(sensitivities).toContain('SYNTHETIC')
    expect(sensitivities).toContain('CLIENT_CONFIDENTIAL')
  })

  it('shows a visible error rather than a blank page on fetch failure', async () => {
    stubFetchOnce({ detail: 'boom' }, false)
    render(<InboxPage />)

    await waitFor(() => {
      expect(screen.getByTestId('load-error')).toBeTruthy()
    })
    expect(screen.getByTestId('load-error').textContent).toContain('boom')
  })
})
