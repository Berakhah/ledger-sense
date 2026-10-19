import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BankChangePanel } from './BankChangePanel'

type View = {
  vendor_name: string
  kind: 'first_seen' | 'changed' | 'changed_back'
  previous_last4: string | null
  incoming_last4: string
}

function stubView(view: View, ok = true, detail = 'nope'): void {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok,
      status: ok ? 200 : 409,
      statusText: ok ? 'OK' : 'Conflict',
      json: async () => (ok ? view : { detail }),
    }),
  )
}

const CHANGED: View = {
  vendor_name: 'Acme Steel Company', kind: 'changed',
  previous_last4: '1234', incoming_last4: '5678',
}

function renderPanel(overrides: Partial<Parameters<typeof BankChangePanel>[0]> = {}) {
  const props = {
    excId: 'exc-1', apiUrl: 'http://api.test', disabled: false,
    onConfirm: vi.fn().mockResolvedValue(undefined),
    onLeaveOpen: vi.fn(), onEscape: vi.fn(), ...overrides,
  }
  render(<BankChangePanel {...props} />)
  return props
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('BankChangePanel', () => {
  it('shows the previous account struck through and the incoming one underlined', async () => {
    stubView(CHANGED)
    renderPanel()
    const diff = await screen.findByTestId('bank-diff')
    expect(diff.querySelector('del')).toHaveTextContent('1234')
    expect(diff.querySelector('ins')).toHaveTextContent('5678')
    expect(screen.getByText(/Acme Steel Company/)).toBeInTheDocument()
  })

  it('shows only the incoming account for a first sighting', async () => {
    stubView({ vendor_name: 'Acme', kind: 'first_seen', previous_last4: null, incoming_last4: '5678' })
    renderPanel()
    const diff = await screen.findByTestId('bank-diff')
    expect(diff.querySelector('del')).toBeNull()
    expect(diff.querySelector('ins')).toHaveTextContent('5678')
    expect(screen.getByText(/first bank details/i)).toBeInTheDocument()
  })

  it('says so when the account has changed back to an earlier one', async () => {
    stubView({ ...CHANGED, kind: 'changed_back' })
    renderPanel()
    expect(await screen.findByText(/changed back/i)).toBeInTheDocument()
  })

  it('cannot confirm until a contact is named', async () => {
    stubView(CHANGED)
    const props = renderPanel()
    const confirm = await screen.findByTestId('bank-confirm')
    expect(confirm).toBeDisabled()
    fireEvent.change(screen.getByTestId('bank-contact'), { target: { value: '   ' } })
    expect(confirm).toBeDisabled()
    fireEvent.change(screen.getByTestId('bank-contact'), { target: { value: 'J. Vendor' } })
    expect(confirm).toBeEnabled()
    expect(props.onConfirm).not.toHaveBeenCalled()
  })

  it('confirms with the chosen channel, the trimmed contact and a null empty note', async () => {
    stubView(CHANGED)
    const props = renderPanel()
    await screen.findByTestId('bank-confirm')
    fireEvent.click(screen.getByLabelText('In person'))
    fireEvent.change(screen.getByTestId('bank-contact'), { target: { value: '  J. Vendor ' } })
    fireEvent.click(screen.getByTestId('bank-confirm'))
    await waitFor(() => expect(props.onConfirm).toHaveBeenCalledWith(
      { channel: 'in_person', contact: 'J. Vendor', note: null }))
  })

  it('sends the note when one is typed', async () => {
    stubView(CHANGED)
    const props = renderPanel()
    await screen.findByTestId('bank-confirm')
    fireEvent.change(screen.getByTestId('bank-contact'), { target: { value: 'J. Vendor' } })
    fireEvent.change(screen.getByTestId('bank-note'), { target: { value: 'called the number on file' } })
    fireEvent.click(screen.getByTestId('bank-confirm'))
    await waitFor(() => expect(props.onConfirm).toHaveBeenCalledWith(
      { channel: 'phone', contact: 'J. Vendor', note: 'called the number on file' }))
  })

  it('Ctrl+Enter confirms from inside the form when valid', async () => {
    stubView(CHANGED)
    const props = renderPanel()
    const contact = await screen.findByTestId('bank-contact')
    fireEvent.change(contact, { target: { value: 'J. Vendor' } })
    fireEvent.keyDown(contact, { key: 'Enter', ctrlKey: true })
    await waitFor(() => expect(props.onConfirm).toHaveBeenCalledOnce())
  })

  it('Ctrl+Enter does nothing while the form is invalid', async () => {
    stubView(CHANGED)
    const props = renderPanel()
    const contact = await screen.findByTestId('bank-contact')
    fireEvent.keyDown(contact, { key: 'Enter', ctrlKey: true })
    expect(props.onConfirm).not.toHaveBeenCalled()
  })

  it('a bare Enter in the form never confirms', async () => {
    stubView(CHANGED)
    const props = renderPanel()
    const contact = await screen.findByTestId('bank-contact')
    fireEvent.change(contact, { target: { value: 'J. Vendor' } })
    fireEvent.keyDown(contact, { key: 'Enter' })
    expect(props.onConfirm).not.toHaveBeenCalled()
  })

  it('Escape hands the keys back to the queue', async () => {
    stubView(CHANGED)
    const props = renderPanel()
    const contact = await screen.findByTestId('bank-contact')
    fireEvent.keyDown(contact, { key: 'Escape' })
    expect(props.onEscape).toHaveBeenCalledOnce()
  })

  it('shows a server refusal inline and stays usable', async () => {
    stubView(CHANGED)
    const onConfirm = vi.fn().mockRejectedValue(new Error('bank details differ from the vendor'))
    renderPanel({ onConfirm })
    await screen.findByTestId('bank-confirm')
    fireEvent.change(screen.getByTestId('bank-contact'), { target: { value: 'J. Vendor' } })
    fireEvent.click(screen.getByTestId('bank-confirm'))
    expect(await screen.findByTestId('bank-error')).toHaveTextContent('bank details differ from the vendor')
    expect(screen.getByTestId('bank-confirm')).toBeEnabled()
  })

  it('returns focus to the contact input after a refused confirm', async () => {
    stubView(CHANGED)
    // A browser blurs a focused control when its fieldset becomes disabled;
    // jsdom does not, so simulate it.
    const onConfirm = vi.fn().mockImplementation(async () => {
      ;(document.activeElement as HTMLElement | null)?.blur()
      throw new Error('refused')
    })
    renderPanel({ onConfirm })
    await screen.findByTestId('bank-confirm')
    const contact = screen.getByTestId('bank-contact')
    fireEvent.change(contact, { target: { value: 'J. Vendor' } })
    contact.focus()
    fireEvent.keyDown(contact, { key: 'Enter', ctrlKey: true })
    await screen.findByTestId('bank-error')
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId('bank-contact')))
  })

  it('does not submit twice while a confirm is in flight, nor on key auto-repeat', async () => {
    stubView(CHANGED)
    const onConfirm = vi.fn().mockReturnValue(new Promise<void>(() => {}))
    renderPanel({ onConfirm })
    await screen.findByTestId('bank-confirm')
    const contact = screen.getByTestId('bank-contact')
    fireEvent.change(contact, { target: { value: 'J. Vendor' } })
    // One act(): React state has not re-rendered between these events, so only
    // a synchronous in-flight flag can stop the duplicates.
    act(() => {
      fireEvent.keyDown(contact, { key: 'Enter', ctrlKey: true })
      fireEvent.keyDown(contact, { key: 'Enter', ctrlKey: true })
      fireEvent.click(screen.getByTestId('bank-confirm'))
      fireEvent.keyDown(contact, { key: 'Enter', ctrlKey: true, repeat: true })
    })
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('a key auto-repeat alone never submits', async () => {
    stubView(CHANGED)
    const props = renderPanel()
    await screen.findByTestId('bank-confirm')
    const contact = screen.getByTestId('bank-contact')
    fireEvent.change(contact, { target: { value: 'J. Vendor' } })
    fireEvent.keyDown(contact, { key: 'Enter', ctrlKey: true, repeat: true })
    expect(props.onConfirm).not.toHaveBeenCalled()
  })

  it('says the document cannot be posted until the bank details are verified', async () => {
    stubView(CHANGED)
    renderPanel()
    expect(await screen.findByText(/cannot be posted until the bank details are\s+verified/i))
      .toBeInTheDocument()
  })

  it('leaving it open records nothing', async () => {
    stubView(CHANGED)
    const props = renderPanel()
    fireEvent.click(await screen.findByTestId('bank-leave-open'))
    expect(props.onLeaveOpen).toHaveBeenCalledOnce()
    expect(props.onConfirm).not.toHaveBeenCalled()
    expect(screen.getByTestId('bank-change-panel')).toHaveTextContent(/stays open/i)
  })

  it('reports a failed lookup instead of offering to confirm', async () => {
    stubView(CHANGED, false, 'the stored document is no longer available')
    renderPanel()
    expect(await screen.findByRole('alert')).toHaveTextContent('the stored document is no longer available')
    expect(screen.queryByTestId('bank-confirm')).not.toBeInTheDocument()
  })

  it('offers a retry after a failed lookup, and loads the panel when it works', async () => {
    const failure = { ok: false, status: 503, statusText: 'Unavailable', json: async () => ({ detail: 'try later' }) }
    const success = { ok: true, status: 200, statusText: 'OK', json: async () => CHANGED }
    const fetchMock = vi.fn().mockResolvedValueOnce(failure).mockResolvedValue(success)
    vi.stubGlobal('fetch', fetchMock)
    renderPanel()
    expect(await screen.findByRole('alert')).toHaveTextContent('try later')
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByTestId('bank-change-panel')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('keeps the retry reachable when it fails again', async () => {
    stubView(CHANGED, false, 'still down')
    renderPanel()
    await screen.findByRole('alert')
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Try again' })).toHaveFocus())
  })

  it('disables everything while the exception is not open', async () => {
    stubView(CHANGED)
    renderPanel({ disabled: true })
    fireEvent.change(await screen.findByTestId('bank-contact'), { target: { value: 'J. Vendor' } })
    expect(screen.getByTestId('bank-confirm')).toBeDisabled()
  })

  it('bounds the contact and note to the server limits', async () => {
    stubView(CHANGED)
    renderPanel()
    expect(await screen.findByTestId('bank-contact')).toHaveAttribute('maxlength', '200')
    expect(screen.getByTestId('bank-note')).toHaveAttribute('maxlength', '2000')
  })
})
