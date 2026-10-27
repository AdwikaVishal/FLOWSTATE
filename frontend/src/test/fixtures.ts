import type {
  Decision,
  DemoInfo,
  FailureMode,
  Recovery,
  RegressionTest,
  Health,
  Overview,
  RunSummary,
  VersionDetail,
  WorkflowDetail,
  WorkflowSummary,
} from '../api/types'

export const healthFixture: Health = {
  status: 'ok',
  service: 'FLOWSTATE AI',
  version: '0.1.0',
  environment: 'test',
  time: '2026-10-26T10:00:00Z',
}

export const demoFixture: DemoInfo = {
  batch: 'demo-invoice-approval-v1',
  seeded: true,
  workflow_id: 'wf-1',
  enabled: true,
  origin: 'DEMO',
}

export const workflowSummary: WorkflowSummary = {
  id: 'wf-1',
  slug: 'invoice-approval',
  name: 'Invoice Approval',
  description: 'Review a vendor invoice, approve it and confirm the ledger entry.',
  owner: 'demo-workspace',
  origin: 'DEMO',
  seed_batch: 'demo-invoice-approval-v1',
  current_version_id: 'v-1',
  created_at: '2026-10-26T08:00:00Z',
  archived_at: null,
  version_count: 1,
  latest_version: 1,
  latest_status: 'PUBLISHED',
  run_count: 5,
}

export const versionSummary = {
  id: 'v-1',
  input_schema: {},
  output_schema: {},
  redact_paths: [],
  workflow_id: 'wf-1',
  version: 1,
  status: 'PUBLISHED' as const,
  label: 'Invoice Approval v1',
  content_hash: 'a'.repeat(64),
  origin: 'DEMO' as const,
  created_at: '2026-10-26T08:00:00Z',
  published_at: '2026-10-26T08:00:01Z',
  action_count: 1,
  invariant_count: 1,
}

export const workflowDetail: WorkflowDetail = {
  ...workflowSummary,
  versions: [versionSummary],
}

export const versionDetail: VersionDetail = {
  ...versionSummary,
  actions: [
    {
      id: 'action-1',
      ordinal: 0,
      node_key: 'n0',
      node_type: 'ACTION',
      kind: 'ASSERT',
      label: 'Confirm the invoice is submitted',
      target: { surface: 'invoice' },
      input: { effects: [] },
      preconditions: [],
      postconditions: [{ path: 'invoice.status', op: 'eq', value: 'SUBMITTED' }],
      depends_on: [],
      channel: 'SIMULATED',
      side_effect_class: 'READ_ONLY',
      requires_authorization: false,
      timeout_ms: 5000,
      retry_policy: {},
      failure_handler: {},
      input_schema: {},
      output_schema: {},
      redact_paths: [],
    },
  ],
  invariants: [
    {
      id: 'inv-1',
      name: 'Invoice is approved',
      description: 'The run verifies the terminal state.',
      kind: 'TERMINAL_STATE',
      severity: 'CRITICAL',
      expression: { kind: 'state', path: 'invoice.status', op: 'eq', value: 'APPROVED' },
    },
  ],
}

export const runSummary: RunSummary = {
  id: 'run-1',
  workflow_id: 'wf-1',
  workflow_name: 'Invoice Approval',
  workflow_version_id: 'v-1',
  version: 1,
  status: 'SUCCEEDED',
  verdict: 'VERIFIED',
  trigger: 'DEMO',
  origin: 'DEMO',
  driver: 'simulated-v1',
  seed: 7,
  current_ordinal: 8,
  fault_scenario_id: null,
  failure_code: null,
  failure_message: null,
  failure_ordinal: null,
  actor: 'demo-workspace',
  inputs: {},
  outputs: {},
  started_at: '2026-10-26T08:00:00Z',
  ended_at: '2026-10-26T08:00:01Z',
  created_at: '2026-10-26T08:00:00Z',
  event_count: 19,
  demo: true,
}

export const overviewFixture: Overview = {
  generated_at: '2026-10-26T10:00:00Z',
  uptime_seconds: 120,
  database_healthy: true,
  counts: {
    workflows: 1,
    demo_workflows: 1,
    published_versions: 1,
    runs: 5,
    demo_runs: 5,
    live_runs: 0,
    open_decisions: 1,
    scenarios: 4,
    invariant_violations: 2,
  },
  runs_by_status: { SUCCEEDED: 2, FAILED: 2, AWAITING_DECISION: 1 },
  terminal: { total: 4, verified: 2, success_rate: 0.5 },
  audit: { checked: 5, valid: 5, broken: 0 },
  recent_runs: [runSummary],
  driver: {
    name: 'simulated-v1',
    kind: 'engine + learning drivers',
    browser: true,
    note: 'The sandbox runs engine workflows; Playwright replays learned workflows against the controlled demo.',
  },
}

const problem = (status: number, code: string, detail: string) =>
  new Response(JSON.stringify({ status, code, detail, title: code }), {
    status,
    headers: { 'Content-Type': 'application/problem+json' },
  })

export const captureSessionFixture = {
  id: 'capture-1',
  label: 'Invoice lookup',
  status: 'RECORDING' as const,
  demo_url: 'http://127.0.0.1:8000/demo-app/',
  variant: 'default',
  origin: 'LIVE' as const,
  created_by: 'console-operator',
  workflow_id: null,
  workflow_version_id: null,
  version_status: null,
  event_count: 0,
  validation: {},
  notes: '',
  created_at: '2026-10-26T10:00:00Z',
  started_at: '2026-10-26T10:00:00Z',
  ended_at: null,
  validated_at: null,
  actions: [],
  suggested_inputs: {},
  unresolved_ambiguities: [],
  recent_runs: [],
}

export const auditFixture = {
  checked: 3,
  valid: 3,
  broken: 0,
  checked_at: '2026-10-26T10:00:00Z',
  runs: [
    { run_id: 'run-1', workflow_name: 'Invoice Approval', events: 19, valid: true, head_hash: 'c'.repeat(64), broken_at_seq: null },
  ],
}

export const recoveryFixture: Recovery = {
  id: 'rec-1',
  run_id: 'run-2',
  checkpoint_id: 'cp-3',
  strategy: 'COMPENSATE',
  status: 'VERIFIED',
  steps: [
    { step: 'select_checkpoint', outcome: 'SELECTED' },
    { step: 'compensate', outcome: 'APPLIED' },
  ],
  detail: 'Compensating actions restored the target state and the recovery contract holds.',
  verification_event_id: 'evt-1',
  started_at: '2026-10-26T09:45:00Z',
  ended_at: '2026-10-26T09:45:01Z',
  action_ordinal: 3,
  node_key: 'disburse',
  recovery_policy: { strategy: 'COMPENSATE', reversible: true },
  contract: [{ path: 'invoice.ledger_count', op: 'eq', value: 0 }],
  before_state_hash: 'd'.repeat(64),
  after_state_hash: 'e'.repeat(64),
  verification: {
    checks: [
      { check: 'contract:invoice.status', ok: true, detail: "invoice.status == 'SCHEDULED'" },
      { check: 'contract:invoice.ledger_count', ok: true, detail: 'invoice.ledger_count == 0' },
    ],
    probe: null,
    status: 'VERIFIED',
  },
  evidence_id: 'ev-9',
  failure_code: null,
  manual_note: '',
}

export const regressionFixture: RegressionTest = {
  id: 'reg-1',
  source_run_id: 'run-2',
  fault_scenario_id: 'scn-1',
  invariant_id: 'inv-1',
  name: 'Vendor payout must mark the invoice PAID',
  status: 'PASSING',
  last_result: 'PASSED',
  last_run_at: '2026-10-26T09:50:00Z',
  created_at: '2026-10-26T09:40:00Z',
  notes: 'Captured from the broken v1 payout.',
  reviewed_by: 'demo-workspace',
  reviewed_at: '2026-10-26T09:41:00Z',
  spec: {
    template_version: 1,
    workflow_id: 'wf-3',
    workflow_name: 'Vendor Payout',
    workflow_version_id: 'v-3-1',
    version: 1,
    source_run_id: 'run-2',
    seed: 70,
    driver: 'simulated-v1',
    scenario: {
      id: 'scn-1',
      name: 'Payout run',
      fault_type: 'PARTIAL_COMMIT',
      target_ordinal: 3,
      parameters: {},
      seed: 70,
    },
    expected: {
      kind: 'INVARIANTS_SATISFIED',
      invariant_id: 'inv-1',
      invariant_name: 'Invoice reaches PAID',
      description: 'No VIOLATED verdicts on replay.',
      failure_code: 'INVARIANT_VIOLATED',
    },
    observed: {
      run_status: 'FAILED',
      verdict: 'FAILED',
      failure_code: 'INVARIANT_VIOLATED',
      failure_message: '1 invariant(s) violated',
      failed_ordinal: null,
      violated_invariants: [
        { id: 'inv-1', name: 'Invoice reaches PAID', severity: 'CRITICAL', detail: 'status is SCHEDULED' },
      ],
    },
    reproduction: { replay: 'POST /api/v1/regression-tests/{test_id}/replay' },
  },
}

export const failureModeFixture: FailureMode = {
  test_id: 'reg-1',
  test_name: 'Vendor payout must mark the invoice PAID',
  test_status: 'PASSING',
  workflow_id: 'wf-3',
  workflow_name: 'Vendor Payout',
  failure_mode: 'Invoice reaches PAID',
  scenario_fault_type: 'PARTIAL_COMMIT',
  detected_in_version: 1,
  current_version: 2,
  status: 'RESOLVED',
  resolved_in: [2],
  reproduces_in: [1],
  last_checked_at: '2026-10-26T09:50:00Z',
  source_run_id: 'run-2',
  notes: '',
}

export const compareFixture = {
  test_id: 'reg-1',
  test: regressionFixture,
  resolved_in: [2],
  reproduces_in: [1],
  results: [
    {
      id: 'res-1',
      test_id: 'reg-1',
      workflow_id: 'wf-3',
      workflow_version_id: 'v-3-1',
      version: 1,
      run_id: 'run-2',
      result: 'FAILED',
      failure_code: 'INVARIANT_VIOLATED',
      violated_invariant: 'Invoice reaches PAID',
      detail: 'status is SCHEDULED',
      created_at: '2026-10-26T09:50:00Z',
    },
    {
      id: 'res-2',
      test_id: 'reg-1',
      workflow_id: 'wf-3',
      workflow_version_id: 'v-3-2',
      version: 2,
      run_id: 'run-4',
      result: 'PASSED',
      failure_code: null,
      violated_invariant: null,
      detail: 'All invariants satisfied on replay.',
      created_at: '2026-10-26T09:50:01Z',
    },
  ],
}

const evidence = (id: string, label: string, validation: string, source: string, value: unknown) => ({
  id,
  blocker_id: 'blocker-1',
  run_id: 'run-2',
  source_kind: 'STATE_FIELD',
  source_path: source,
  source_ref: null,
  record_ref: null,
  label,
  relation: 'observed amount',
  value_json: { value },
  validation: validation as 'PASS' | 'FAIL' | 'MISSING' | 'EXPIRED' | 'VALID' | 'INFO',
  detail: `${source} = ${String(value)}`,
  observed_at: '2026-10-26T09:40:00Z',
  fresh_until: null,
})

export const conflictingDecision: Decision = {
  id: 'dec-1',
  run_id: 'run-2',
  action_ordinal: 2,
  node_key: 'approve_invoice',
  question: 'Approve invoice INV-2041 despite a 45.00 USD variance against PO-8841?',
  options: [
    { value: 'approve', label: 'Approve the variance', consequence: 'Pays 1,845.00 USD and records an exception' },
    { value: 'reject', label: 'Reject the invoice', consequence: 'Stops the run; nothing is paid' },
    { value: 'defer', label: 'Defer', consequence: 'Keeps the run blocked' },
  ],
  recommended_option: 'reject',
  reason: 'Two system-of-record documents disagree; only a finance approver can accept the variance.',
  policy_citation: 'ActionPolicy 4.2: a variance between the invoice and its purchase order requires finance approval.',
  status: 'NOTIFIED',
  origin: 'DEMO',
  requested_at: '2026-10-26T09:40:00Z',
  decided_at: null,
  decided_by: null,
  decided_role: null,
  expires_at: '2026-10-27T09:40:00Z',
  deferred_until: null,
  capability_token_id: null,
  workflow_name: 'Invoice Discrepancy Review',
  workflow_id: 'wf-2',
  version: 1,
  run_status: 'AWAITING_DECISION',
  kind: 'CONFLICTING_EVIDENCE',
  required_role: 'FINANCE_APPROVER',
  resume_conditions: [
    { code: 'invoice_still_open', path: 'invoice.status', op: 'eq', value: 'SUBMITTED' },
  ],
  input_spec: [],
  provided_input: {},
  permitted_actions: ['approve', 'reject', 'defer'],
  can_decide: true,
  actor_role: 'ADMIN',
  blocker: {
    id: 'blocker-1',
    run_id: 'run-2',
    node_key: 'approve_invoice',
    action_ordinal: 2,
    kind: 'CONFLICTING_EVIDENCE',
    code: 'INVOICE_PO_VARIANCE',
    title: 'Invoice total does not match the purchase order',
    detail: 'The invoice and the purchase order disagree.',
    status: 'OPEN',
    required_role: 'FINANCE_APPROVER',
    resume_conditions: [
      { code: 'invoice_still_open', path: 'invoice.status', op: 'eq', value: 'SUBMITTED' },
    ],
    input_spec: [],
    evidence_hash: 'b'.repeat(64),
    decision_id: 'dec-1',
    origin: 'DEMO',
    created_at: '2026-10-26T09:40:00Z',
    resolved_at: null,
    evidence: [],
  },
  evidence: [],
  evidence_items: [
    evidence('ev-1', 'Invoice total (cents)', 'FAIL', 'invoice.amount_cents', 184500),
    evidence('ev-2', 'Purchase order total (cents)', 'PASS', 'purchase_order.amount_cents', 180000),
    evidence('ev-3', 'Variance (cents)', 'INFO', 'review.delta_cents', 4500),
  ],
  history: [
    {
      id: 'hist-1',
      decision_id: 'dec-1',
      run_id: 'run-2',
      action: 'DEFER',
      actor: 'operator-1',
      actor_role: 'OPERATOR',
      note: 'Waiting for the controller.',
      outcome: 'DEFERRED',
      payload: {},
      created_at: '2026-10-26T09:42:00Z',
    },
  ],
}

export const missingInputDecision: Decision = {
  ...conflictingDecision,
  id: 'dec-2',
  run_id: 'run-3',
  node_key: 'approve_invoice',
  question: 'Which cost centre should invoice INV-2041 be booked to?',
  options: [{ value: 'approve', label: 'Approve', consequence: 'Re-evaluates preconditions' }],
  recommended_option: null,
  reason: 'A required accounting field is absent.',
  policy_citation: 'FinancePolicy 2.1: every booked invoice needs a cost centre.',
  kind: 'MISSING_INPUT',
  required_role: 'OPERATOR',
  resume_conditions: [{ code: 'cost_center_present', path: 'invoice.cost_center', op: 'exists' }],
  input_spec: [
    { key: 'cost_center', label: 'Cost centre', path: 'invoice.cost_center', required: true, type: 'string', placeholder: 'CC-200' },
  ],
  permitted_actions: ['approve', 'provide_info', 'reject', 'defer'],
  can_decide: true,
  blocker: {
    ...conflictingDecision.blocker!,
    id: 'blocker-2',
    kind: 'MISSING_INPUT',
    code: 'COST_CENTRE_REQUIRED',
    title: 'A cost centre is required before approval',
    required_role: 'OPERATOR',
    input_spec: [
      { key: 'cost_center', label: 'Cost centre', path: 'invoice.cost_center', required: true },
    ],
  },
  evidence_items: [
    evidence('ev-4', 'Cost centre on the invoice', 'MISSING', 'invoice.cost_center', null),
  ],
  history: [],
}

const decisionTemplates = [conflictingDecision, missingInputDecision]

function decisionForRole(template: Decision, role: string): Decision {
  return {
    ...template,
    actor_role: role,
    can_decide: role === 'ADMIN' || role === template.required_role,
  }
}

function closeDecision(template: Decision, status: Decision['status'], role: string): Decision {
  return {
    ...decisionForRole(template, role),
    status,
    decided_by: 'console-operator',
    decided_role: role,
    decided_at: '2026-10-26T09:50:00Z',
    can_decide: false,
    history: [
      ...template.history,
      {
        id: `hist-${template.id}-${status}`,
        decision_id: template.id,
        run_id: template.run_id,
        action: status === 'DENIED' ? 'REJECT' : status === 'DEFERRED' ? 'DEFER' : 'APPROVE',
        actor: 'console-operator',
        actor_role: role,
        note: '',
        outcome: status,
        payload: {},
        created_at: '2026-10-26T09:50:00Z',
      },
    ],
  }
}

export function installFetchMock() {
  return async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = new URL(String(input), 'http://console.test')
    const path = url.pathname
    const method = init?.method ?? 'GET'

    if (path === '/api/v1/health') return Response.json(healthFixture)
    if (path === '/api/v1/demo') return Response.json(demoFixture)
    if (path === '/api/v1/system/overview') return Response.json(overviewFixture)
    if (path === '/api/v1/workflows') return Response.json([workflowSummary])
    if (path === '/api/v1/workflows/wf-1') return Response.json(workflowDetail)
    if (path === '/api/v1/workflows/wf-1/versions/v-1') return Response.json(versionDetail)
    if (path === '/api/v1/runs') return Response.json([runSummary])
    if (path === '/api/v1/audit') return Response.json(auditFixture)
    if (path === '/api/v1/recoveries') return Response.json([recoveryFixture])
    if (/^\/api\/v1\/runs\/[^/]+\/recoveries$/.test(path)) return Response.json([recoveryFixture])
    if (/^\/api\/v1\/runs\/[^/]+\/recovery-plan$/.test(path)) {
      return Response.json({
        run_id: 'run-2',
        supported: true,
        action_ordinal: 3,
        node_key: 'disburse',
        action_label: 'Disburse funds (sandbox)',
        strategy: 'COMPENSATE',
        reversible: true,
        allowed_strategies: ['COMPENSATE', 'RESUME'],
        contract: [{ path: 'invoice.ledger_count', op: 'eq', value: 0 }],
        compensate: [{ op: 'remove_last', path: 'invoice.ledger' }],
        probe: null,
        idempotency_path: 'invoice.payment_reference',
        notes: 'The sandbox gateway supports a compensating reversal.',
        reason: '',
        code: null,
        ambiguous: false,
        run_status: 'FAILED',
        recoverable: true,
      })
    }
    if (path === '/api/v1/regression-tests') return Response.json([regressionFixture])
    if (path === '/api/v1/feedback/failure-modes') return Response.json([failureModeFixture])
    if (/^\/api\/v1\/regression-tests\/[^/]+\/compare$/.test(path)) {
      return Response.json(compareFixture)
    }
    if (/^\/api\/v1\/regression-tests\/[^/]+\/replay$/.test(path)) {
      return Response.json({ ...regressionFixture, last_result: 'FAILED', status: 'FAILING' })
    }
    if (/^\/api\/v1\/regression-tests\/[^/]+\/results$/.test(path)) {
      return Response.json(compareFixture.results)
    }

    if (path === '/api/v1/decisions' && method === 'GET') {
      const role = new Headers(init?.headers).get('X-Flowstate-Role') ?? 'ADMIN'
      const kind = url.searchParams.get('kind')
      const rows = decisionTemplates
        .filter((item) => !kind || item.kind === kind)
        .map((item) => decisionForRole(item, role))
      return Response.json(rows)
    }
    const decisionMatch = path.match(/^\/api\/v1\/decisions\/([^/]+)(?:\/([^/]+))?$/)
    if (decisionMatch) {
      const role = new Headers(init?.headers).get('X-Flowstate-Role') ?? 'ADMIN'
      const template = decisionTemplates.find((item) => item.id === decisionMatch[1])
      if (!template) return problem(404, 'DECISION_NOT_FOUND', 'No decision fixture')
      const action = decisionMatch[2]
      if (method === 'GET') return Response.json(decisionForRole(template, role))
      if (action === 'approve') return Response.json(closeDecision(template, 'APPLIED', role))
      if (action === 'reject' || action === 'deny') {
        return Response.json(closeDecision(template, 'DENIED', role))
      }
      if (action === 'provide-info') return Response.json(closeDecision(template, 'APPLIED', role))
      if (action === 'defer') return Response.json(closeDecision(template, 'DEFERRED', role))
      if (action === 'history') return Response.json(template.history)
    }
    if (path === '/api/v1/captures') {
      return method === 'POST'
        ? Response.json(captureSessionFixture, { status: 201 })
        : Response.json([])
    }

    return problem(404, 'HTTP_404', `No fixture for ${method} ${path}`)
  }
}