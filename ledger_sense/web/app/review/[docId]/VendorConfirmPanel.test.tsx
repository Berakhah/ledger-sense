import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { VendorConfirmPanel } from './VendorConfirmPanel'

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

describe('VendorConfirmPanel', () => {
  it('offers to link the resolved candidate when one exists', async () => {
    stubFetchOnce({
      observed_name: 'Acme Steel Co.',
      candidate: { vendor_id: 'vendor-1', name: 'Acme Steel Company', score: 81 },
    })
    const onConfirmExisting = vi.fn()
    render(
      <VendorConfirmPanel
        excId="exc-1"
        apiUrl="http://api.test"
        disabled={false}
        onConfirmExisting={onConfirmExisting}
        onConfirmNew={vi.fn()}
      />,
    )
    const button = await screen.findByRole('button', { name: /Acme Steel Company/i })
    fireEvent.click(button)
    expect(onConfirmExisting).toHaveBeenCalledWith('vendor-1')
  })

  it('offers only a new-vendor form when nothing resolved', async () => {
    stubFetchOnce({ observed_name: 'Brand New Roofing Co.', candidate: null })
    render(
      <VendorConfirmPanel
        excId="exc-1"
        apiUrl="http://api.test"
        disabled={false}
        onConfirmExisting={vi.fn()}
        onConfirmNew={vi.fn()}
      />,
    )
    await waitFor(() => expect(screen.getByTestId('new-vendor-name')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /yes, it's/i })).not.toBeInTheDocument()
  })

  it('confirms a new vendor with the typed name', async () => {
    stubFetchOnce({ observed_name: 'Brand New Roofing Co.', candidate: null })
    const onConfirmNew = vi.fn()
    render(
      <VendorConfirmPanel
        excId="exc-1"
        apiUrl="http://api.test"
        disabled={false}
        onConfirmExisting={vi.fn()}
        onConfirmNew={onConfirmNew}
      />,
    )
    const input = await screen.findByTestId('new-vendor-name')
    expect(input).toHaveValue('Brand New Roofing Co.')
    fireEvent.change(input, { target: { value: 'Totally Different Co.' } })
    fireEvent.click(screen.getByRole('button', { name: /confirm new vendor/i }))
    expect(onConfirmNew).toHaveBeenCalledWith('Totally Different Co.')
  })

  it('surfaces a fetch failure rather than staying blank', async () => {
    stubFetchOnce({ detail: 'no extracted vendor name to match' }, false)
    render(
      <VendorConfirmPanel
        excId="exc-1"
        apiUrl="http://api.test"
        disabled={false}
        onConfirmExisting={vi.fn()}
        onConfirmNew={vi.fn()}
      />,
    )
    await waitFor(() =>
      expect(screen.getByTestId('vendor-match-error')).toHaveTextContent(
        /no extracted vendor name to match/,
      ),
    )
  })

  it('disables every control when told to', async () => {
    stubFetchOnce({
      observed_name: 'Acme Steel Co.',
      candidate: { vendor_id: 'vendor-1', name: 'Acme Steel Company', score: 81 },
    })
    render(
      <VendorConfirmPanel
        excId="exc-1"
        apiUrl="http://api.test"
        disabled
        onConfirmExisting={vi.fn()}
        onConfirmNew={vi.fn()}
      />,
    )
    expect(await screen.findByRole('button', { name: /Acme Steel Company/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /no, new vendor/i })).toBeDisabled()
    expect(screen.getByTestId('new-vendor-name')).toBeDisabled()
  })
})
