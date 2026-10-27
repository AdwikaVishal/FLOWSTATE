import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { setActorRole } from '../api/client'
import { installFetchMock } from '../test/fixtures'

function renderInbox() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/decisions']}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('decision inbox', () => {
  beforeEach(() => {
    setActorRole('ADMIN')
    vi.stubGlobal('fetch', vi.fn(installFetchMock()))
  })

  afterEach(() => {
    setActorRole('ADMIN')
  })

  it('classifies the blocker and shows persisted evidence and decision history', async () => {
    renderInbox()

    expect(await screen.findByRole('heading', { name: 'Decision Inbox' })).toBeInTheDocument()

    // Both open decisions are listed with their cause (list item + detail pane).
    expect((await screen.findAllByText(/despite a 45.00 USD variance/)).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Which cost centre/).length).toBeGreaterThan(0)
    expect(screen.getAllByText('Conflicting evidence').length).toBeGreaterThan(0)

    // Evidence packet rows come from persisted facts, not prose.
    expect(screen.getByText('Invoice total (cents)')).toBeInTheDocument()
    expect(screen.getByText('Purchase order total (cents)')).toBeInTheDocument()
    expect(screen.getByText('invoice.amount_cents')).toBeInTheDocument()
    expect(screen.getByText('purchase_order.amount_cents')).toBeInTheDocument()
    expect(screen.getByText('184500')).toBeInTheDocument()
    expect(screen.getByText('180000')).toBeInTheDocument()

    // The decision history records the earlier deferral.
    expect(screen.getByText('Decision history')).toBeInTheDocument()
    expect(screen.getByText('operator-1')).toBeInTheDocument()

    // Resumption conditions are spelled out.
    expect(screen.getByText(/invoice still open/)).toBeInTheDocument()

    // Workflow navigation is available.
    const workflowLink = screen.getByRole('link', { name: /Invoice Discrepancy Review/ })
    expect(workflowLink).toHaveAttribute('href', '/workflows/wf-2')
  })

  it('filters the inbox by blocker cause', async () => {
    const user = userEvent.setup()
    renderInbox()

    await screen.findAllByText(/despite a 45.00 USD variance/)

    await user.selectOptions(screen.getByLabelText(/Blocker cause/i), 'MISSING_INPUT')

    expect((await screen.findAllByText(/Which cost centre/)).length).toBeGreaterThan(0)
    await waitFor(() => {
      expect(screen.queryAllByText(/despite a 45.00 USD variance/)).toHaveLength(0)
    })
    expect(screen.getByText(/Cost centre on the invoice/)).toBeInTheDocument()
  })

  it('refuses to let an operator decide a finance blocker', async () => {
    setActorRole('OPERATOR')
    renderInbox()

    expect(await screen.findByText(/Your acting role cannot decide this/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Reject' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Defer' })).toBeEnabled()
  })

  it('records an approval and reports the re-check outcome', async () => {
    setActorRole('FINANCE_APPROVER')
    const user = userEvent.setup()
    renderInbox()

    await screen.findAllByText(/despite a 45.00 USD variance/)
    expect(screen.queryByText(/Your acting role cannot decide this/)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Approve' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/Finance Approver/i)).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: /Re-check & resume/ }))

    expect(await screen.findByText(/Authorized by FINANCE_APPROVER/)).toBeInTheDocument()
  })

  it('supports deferring without unblocking the run', async () => {
    const user = userEvent.setup()
    renderInbox()

    await screen.findAllByText(/despite a 45.00 USD variance/)
    await user.click(screen.getByRole('button', { name: 'Defer' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Defer' }))

    expect(await screen.findByText(/Deferred\. The run stays blocked/)).toBeInTheDocument()
  })
})