import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { installFetchMock } from '../test/fixtures'

function renderRecovery() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/recovery']}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('recovery and feedback loop', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(installFetchMock()))
  })

  it('reports a failure mode as resolved in a later version', async () => {
    renderRecovery()

    expect(await screen.findByRole('heading', { name: 'Recovery & Audit' })).toBeInTheDocument()
    expect(await screen.findByText('Failure modes')).toBeInTheDocument()
    expect(await screen.findByText('RESOLVED')).toBeInTheDocument()
    expect(screen.getByText('resolved in v2')).toBeInTheDocument()
    expect(screen.getByText(/detected in v1 · current v2/)).toBeInTheDocument()
  })

  it('shows a verified recovery with its contract checks and evidence', async () => {
    renderRecovery()

    await screen.findByText('Failure modes')
    const attempts = (await screen.findByText('Recovery attempts')).closest('.card') as HTMLElement
    expect(await within(attempts).findByText('Verified')).toBeInTheDocument()
    expect(within(attempts).getByText(/restored the target state/)).toBeInTheDocument()
    expect(within(attempts).getByText('contract:invoice.ledger_count')).toBeInTheDocument()
    expect(within(attempts).getByText('Verification evidence')).toBeInTheDocument()
    expect(within(attempts).getByText('COMPENSATE')).toBeInTheDocument()
  })

  it('compares a regression test across workflow versions', async () => {
    const user = userEvent.setup()
    renderRecovery()

    await screen.findByText('Vendor payout must mark the invoice PAID')
    expect(screen.getAllByText('Invoice reaches PAID').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/PARTIAL_COMMIT/).length).toBeGreaterThan(0)

    await user.click(screen.getByRole('button', { name: /Compare versions/ }))

    const matrix = (await screen.findByText('Version comparison')).closest(
      '.regression-matrix',
    ) as HTMLElement
    expect(await within(matrix).findByText('v1')).toBeInTheDocument()
    expect(within(matrix).getByText('v2')).toBeInTheDocument()
    expect(within(matrix).getByText('FAILED')).toBeInTheDocument()
    expect(within(matrix).getByText('PASSED')).toBeInTheDocument()
  })
})