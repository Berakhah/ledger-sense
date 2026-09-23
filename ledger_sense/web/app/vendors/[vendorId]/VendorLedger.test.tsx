import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { VendorLedger } from './VendorLedger'

const LEDGER = {
  id: 'vendor-1',
  name: 'Acme Steel Company',
  aliases: [
    {
      id: 'alias-1',
      name: 'Acme Steel Co.',
      first_seen: '2026-09-15T10:00:00Z',
      source_document: 'doc-1',
      confirmed_by: 'reviewer-1',
    },
  ],
  payment_history: [],
}

function stubFetchOnce(body: unknown, ok = true): void {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok,
      status: ok ? 200 : 404,
      statusText: ok ? 'OK' : 'Not Found',
      json: async () => body,
    }),
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('VendorLedger', () => {
  it('renders the canonical name and confirmed aliases', async () => {
    stubFetchOnce(LEDGER)
    render(<VendorLedger vendorId="vendor-1" />)
    await waitFor(() => expect(screen.getByText('Acme Steel Company')).toBeInTheDocument())
    expect(screen.getByText('Acme Steel Co.')).toBeInTheDocument()
    expect(screen.getByText('reviewer-1')).toBeInTheDocument()
  })

  it('states plainly that no payment details are on file, rather than hiding the section', async () => {
    stubFetchOnce(LEDGER)
    render(<VendorLedger vendorId="vendor-1" />)
    await waitFor(() =>
      expect(screen.getByText(/no payment details on file/i)).toBeInTheDocument(),
    )
  })

  it('surfaces a 404 as an explicit not-found state, not a silent blank screen', async () => {
    stubFetchOnce({ detail: 'not found' }, false)
    render(<VendorLedger vendorId="nope" />)
    await waitFor(() => expect(screen.getByText(/not found/i)).toBeInTheDocument())
  })
})
