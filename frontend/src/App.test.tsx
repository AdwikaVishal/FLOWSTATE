import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'
import { installFetchMock } from './test/fixtures'

function renderAt(path: string) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
    },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('console navigation', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(installFetchMock()))
  })

  it('renders the overview from the backend and labels demo fixtures', async () => {
    renderAt('/')

    expect(await screen.findByRole('heading', { name: 'Operations overview' })).toBeInTheDocument()
    expect(await screen.findByText('Demonstration workspace')).toBeInTheDocument()
    expect(await screen.findByText('demo-invoice-approval-v1')).toBeInTheDocument()
    expect(screen.getByText('50%')).toBeInTheDocument()
    expect(screen.getAllByText(/Invoice Approval/).length).toBeGreaterThan(0)
    expect(screen.getAllByText('simulated-v1').length).toBeGreaterThan(0)
  })

  it('navigates to Workflow Studio and opens a workflow version history', async () => {
    const user = userEvent.setup()
    renderAt('/')

    await screen.findByRole('heading', { name: 'Operations overview' })
    await user.click(screen.getByRole('link', { name: /Workflow Studio/ }))

    expect(await screen.findByRole('heading', { name: 'Workflow Studio' })).toBeInTheDocument()
    const workflowLink = await screen.findByRole('link', { name: 'Open Invoice Approval' })
    expect(screen.getByText('invoice-approval')).toBeInTheDocument()

    await user.click(workflowLink)

    expect(await screen.findByRole('heading', { name: 'Invoice Approval', level: 1 })).toBeInTheDocument()
    expect(await screen.findByText('Version history')).toBeInTheDocument()
    expect(await screen.findByText('Confirm the invoice is submitted')).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: /Invariants/ }))
    expect(await screen.findByText('Invoice is approved')).toBeInTheDocument()
  })

  it('starts a controlled recording from the Learning Studio', async () => {
    const user = userEvent.setup()
    renderAt('/learning')

    expect(await screen.findByRole('heading', { name: 'Learning Studio' })).toBeInTheDocument()
    expect(
      await screen.findByRole('heading', { name: 'Start a controlled recording' }),
    ).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Start recording/ }))

    expect(await screen.findByRole('heading', { name: 'Controlled demo app' })).toBeInTheDocument()
    expect(screen.getByTitle('Northwind invoice demo')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Stop and review/ })).toBeInTheDocument()
  })

  it('shows a 404 route without crashing', async () => {
    renderAt('/does-not-exist')

    expect(await screen.findByText('Console route not found')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Back to overview/ })).toBeInTheDocument()
  })

  it('renders an actionable error when the backend cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    renderAt('/')

    await waitFor(() => {
      expect(screen.getByText('The overview could not load')).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
    expect(screen.getByText(/could not reach the FLOWSTATE API/)).toBeInTheDocument()
  })
})