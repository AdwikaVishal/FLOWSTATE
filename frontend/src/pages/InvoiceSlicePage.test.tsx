import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { InvoiceSlicePage } from './InvoiceSlicePage'

const state = vi.hoisted(() => ({ data: {} as Record<string, unknown> }))

vi.mock('../api/queries', () => {
  const q = (data: unknown) => ({
    data,
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  })
  return {
    useWorkflows: () => q(state.data.workflows),
    useRuns: () => q(state.data.runs),
    useScenarios: () => q(state.data.scenarios),
    useDecisions: () => q(state.data.decisions),
    useRecoveries: () => q(state.data.recoveries),
    useRegressionTests: () => q(state.data.regressions),
    useCaptures: () => q(state.data.captures),
    useAudit: () => q(state.data.audit),
    useVersion: () => q(state.data.version),
  }
})

const workflow = {
  id: 'wf-proc',
  slug: 'invoice-processing',
  name: 'Invoice Processing',
  description: '',
  owner: 'demo-workspace',
  origin: 'DEMO',
  seed_batch: 'demo-invoice-processing-v1',
  current_version_id: 'v-proc-1',
  created_at: '2026-10-26T08:00:00Z',
  archived_at: null,
  version_count: 1,
  latest_version: 1,
  latest_status: 'PUBLISHED',
  run_count: 3,
}

const runs = [
  {
    id: 'run-ok',
    workflow_id: 'wf-proc',
    workflow_name: 'Invoice Processing',
    status: 'SUCCEEDED',
    verdict: 'VERIFIED',
    trigger: 'DEMO',
    created_at: '2026-10-26T09:00:00Z',
    event_count: 38,
    fault_scenario_id: null,
    failure_code: null,
  },
  {
    id: 'run-po',
    workflow_id: 'wf-proc',
    workflow_name: 'Invoice Processing',
    status: 'FAILED',
    verdict: 'FAILED',
    trigger: 'CHAOS',
    created_at: '2026-10-26T09:10:00Z',
    event_count: 14,
    fault_scenario_id: 'scn-po',
    failure_code: 'TIMEOUT_BEFORE_EXECUTION',
  },
  {
    id: 'run-mm',
    workflow_id: 'wf-proc',
    workflow_name: 'Invoice Processing',
    status: 'AWAITING_DECISION',
    verdict: 'PENDING',
    trigger: 'CHAOS',
    created_at: '2026-10-26T09:20:00Z',
    event_count: 21,
    fault_scenario_id: 'scn-mm',
    failure_code: null,
  },
]

const scenarios = [
  { id: 'scn-po', workflow_id: 'wf-proc', name: 'PO API timeout', fault_type: 'TIMEOUT_BEFORE_EXECUTION' },
  { id: 'scn-mm', workflow_id: 'wf-proc', name: 'Invoice total mismatch', fault_type: 'CONTRADICTORY_TOTALS' },
  { id: 'scn-lr', workflow_id: 'wf-proc', name: 'Lost response after payment executes', fault_type: 'RESPONSE_LOST' },
  { id: 'scn-ma', workflow_id: 'wf-proc', name: 'Approval never arrives', fault_type: 'DELAYED_APPROVAL' },
]

const decision = {
  id: 'dec-proc',
  workflow_id: 'wf-proc',
  run_id: 'run-mm',
  kind: 'CONFLICTING_EVIDENCE',
  required_role: 'FINANCE_APPROVER',
  status: 'NOTIFIED',
  question: 'Approve the variance?',
  options: [],
}

const recovery = {
  id: 'rec-proc',
  run_id: 'run-po',
  strategy: 'RESUME',
  status: 'VERIFIED',
  started_at: '2026-10-26T09:15:00Z',
  verification: { checks: [{ check: 'contract', ok: true, detail: 'ok' }] },
}

const regression = {
  id: 'reg-proc',
  name: 'Invoice slice: partial ledger commit',
  status: 'FAILING',
  last_result: 'FAILED',
  spec: { workflow_id: 'wf-proc', expected: { invariant_name: 'Invoice reaches PAID' } },
}

const version = {
  id: 'v-proc-1',
  actions: [
    { id: 'a1', ordinal: 0, label: 'Retrieve the invoice', node_type: 'ACTION', kind: 'API_CALL' },
    { id: 'a2', ordinal: 1, label: 'Retrieve the purchase order', node_type: 'ACTION', kind: 'API_CALL' },
    { id: 'a3', ordinal: 2, label: 'Validate required fields', node_type: 'VALIDATION', kind: 'ASSERT' },
  ],
}

function seedFull() {
  state.data = {
    workflows: [workflow],
    runs,
    scenarios,
    decisions: [decision],
    recoveries: [recovery],
    regressions: [regression],
    captures: [],
    audit: { checked: 3, valid: 3, broken: 0, checked_at: '2026-10-26T10:00:00Z', runs: [] },
    version,
  }
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/slice']}>
      <InvoiceSlicePage />
    </MemoryRouter>,
  )
}

describe('invoice processing slice', () => {
  beforeEach(seedFull)

  it('derives every stage from live backend records', () => {
    renderPage()

    expect(screen.getByRole('heading', { name: 'Invoice processing slice' })).toBeInTheDocument()
    expect(screen.getAllByText('Invoice Processing').length).toBeGreaterThan(0)
    expect(screen.getByText('v1')).toBeInTheDocument()

    const normal = screen.getByText('Normal execution').closest('.slice-stage') as HTMLElement
    expect(within(normal).getByText('Verified')).toBeInTheDocument()
    expect(within(normal).getByText(/38 recorded events/)).toBeInTheDocument()

    expect(screen.getByText('2/4 scenarios run')).toBeInTheDocument()
    const checklist = screen
      .getByText('Required chaos scenarios')
      .closest('.card') as HTMLElement
    expect(within(checklist).getByText('PO API timeout')).toBeInTheDocument()
    expect(within(checklist).getByText('Failed')).toBeInTheDocument()
    expect(within(checklist).getAllByText('Not run yet')).toHaveLength(2)

    const bottleneck = screen.getByText('Human bottleneck').closest('.slice-stage') as HTMLElement
    expect(within(bottleneck).getByText('1 open')).toBeInTheDocument()
    expect(within(bottleneck).getByRole('link', { name: 'Review the decision' })).toHaveAttribute(
      'href',
      '/decisions?decision=dec-proc',
    )

    const recoveryStage = screen.getByText('Verified recovery').closest('.slice-stage') as HTMLElement
    expect(within(recoveryStage).getByText('Verified')).toBeInTheDocument()
    expect(within(recoveryStage).getByText(/1 contract checks/)).toBeInTheDocument()

    expect(screen.getByText(/last result FAILED/)).toBeInTheDocument()
    expect(screen.getByText('3/3 chains valid')).toBeInTheDocument()
  })

  it('shows absence honestly when nothing has been executed', () => {
    state.data = {
      workflows: [workflow],
      runs: [],
      scenarios,
      decisions: [],
      recoveries: [],
      regressions: [],
      captures: [],
      audit: { checked: 0, valid: 0, broken: 0, checked_at: '2026-10-26T10:00:00Z', runs: [] },
      version,
    }
    renderPage()

    expect(screen.getByText('No verified run')).toBeInTheDocument()
    expect(screen.getByText('0/4 scenarios run')).toBeInTheDocument()
    expect(screen.getAllByText('Not run yet')).toHaveLength(4)
    expect(screen.getByText('No open decision')).toBeInTheDocument()
    expect(screen.getByText('No recovery attempt')).toBeInTheDocument()
    expect(screen.getByText('No regression test')).toBeInTheDocument()
    expect(screen.getByText('Not recorded yet')).toBeInTheDocument()
  })
})