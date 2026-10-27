import type {
  AuditOverview,
  CaptureActionPatch,
  CaptureSession,
  CaptureWorkflowResponse,
  ChainVerification,
  ActorRole,
  BlockerKind,
  Decision,
  Timeline,
  DemoInfo,
  DemoSeedResponse,
  ExecutionEvent,
  FaultType,
  Health,
  Overview,
  ProblemDocument,
  Readiness,
  FailureMode,
  Recovery,
  RecoveryPlan,
  RecoveryStrategy,
  RegressionCompare,
  RegressionResult,
  RegressionTest,
  RunDetail,
  RunSummary,
  ChaosReport,
  Scenario,
  ScenarioCreatePayload,
  SystemInfo,
  VersionDetail,
  WorkflowCreatePayload,
  WorkflowDetail,
  WorkflowSummary,
} from './types'

const API_ORIGIN = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
const API_PREFIX = '/api/v1'
const API_TOKEN = import.meta.env.VITE_API_TOKEN as string | undefined
const ROLE_KEY = 'flowstate.actorRole'
const ROLES: ActorRole[] = ['OPERATOR', 'REVIEWER', 'FINANCE_APPROVER', 'ADMIN']

function readStoredRole(): ActorRole {
  if (typeof localStorage === 'undefined') return 'ADMIN'
  const stored = localStorage.getItem(ROLE_KEY)
  return ROLES.includes(stored as ActorRole) ? (stored as ActorRole) : 'ADMIN'
}

let actorRole: ActorRole = readStoredRole()

export function getActorRole(): ActorRole {
  return actorRole
}

export function setActorRole(role: ActorRole): void {
  actorRole = role
  if (typeof localStorage !== 'undefined') localStorage.setItem(ROLE_KEY, role)
}

export const ACTOR_ROLES = ROLES

export function evidenceUrl(runId: string, evidenceId: string): string {
  return `${API_ORIGIN}${API_PREFIX}/runs/${runId}/evidence/${evidenceId}`
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly requestId: string | null
  readonly details: Record<string, string> | null

  constructor(
    message: string,
    options: {
      status: number
      code: string
      requestId?: string | null
      details?: Record<string, string> | null
    },
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = options.status
    this.code = options.code
    this.requestId = options.requestId ?? null
    this.details = options.details ?? null
  }
}

export interface LiveRunEvent {
  event_id: string
  event_type: string
  execution_id: string
  workflow_id: string
  timestamp: string
  sequence: number
  payload: Record<string, unknown>
  schema_version: number
}

export type LiveConnectionState = 'connecting' | 'connected' | 'reconnecting' | 'complete' | 'error'

export async function streamRunEvents(
  runId: string,
  after: number,
  signal: AbortSignal,
  onEvent: (event: LiveRunEvent) => void,
  onState: (state: LiveConnectionState) => void,
): Promise<void> {
  let cursor = after
  let retryMs = 500
  let attempts = 0
  while (!signal.aborted) {
    onState(attempts === 0 ? 'connecting' : 'reconnecting')
    attempts += 1
    const headers = new Headers({
      Accept: 'text/event-stream',
      'X-Flowstate-Actor': 'console-operator',
      'X-Flowstate-Role': actorRole,
    })
    if (API_TOKEN) headers.set('Authorization', `******`)
    if (cursor >= 0) headers.set('Last-Event-ID', String(cursor))

    try {
      const response = await fetch(
        `${API_ORIGIN}${API_PREFIX}/runs/${encodeURIComponent(runId)}/events/stream`,
        { headers, signal },
      )
      if (!response.ok) {
        const error = await parseError(response)
        if (error.status > 0) {
          onState('error')
          return
        }
        throw error
      }
      if (!response.body) throw new ApiError('The event stream has no response body.', {
        status: response.status,
        code: 'EVENT_STREAM_UNAVAILABLE',
      })
      onState('connected')
      retryMs = 500
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let eventType = ''
      let eventData = ''
      let complete = false
      while (!signal.aborted) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        let boundary = buffer.indexOf('\n')
        while (boundary >= 0) {
          const line = buffer.slice(0, boundary).replace(/\r$/, '')
          buffer = buffer.slice(boundary + 1)
          if (line === '') {
            if (eventType === 'run-event' && eventData) {
              const event = JSON.parse(eventData) as LiveRunEvent
              if (Number.isInteger(event.sequence) && event.sequence > cursor) {
                cursor = event.sequence
                onEvent(event)
              }
            } else if (eventType === 'complete') {
              complete = true
            }
            eventType = ''
            eventData = ''
            if (complete) {
              onState('complete')
              return
            }
          } else if (line.startsWith('event:')) {
            eventType = line.slice(6).trim()
          } else if (line.startsWith('data:')) {
            eventData += line.slice(5).trim()
          }
          boundary = buffer.indexOf('\n')
        }
      }
      await reader.cancel()
      if (signal.aborted) return
      onState('reconnecting')
    } catch (error) {
      if (signal.aborted || (error instanceof DOMException && error.name === 'AbortError')) return
      onState('reconnecting')
    }

    await new Promise<void>((resolve) => {
      const timeout = window.setTimeout(resolve, retryMs)
      signal.addEventListener('abort', () => {
        window.clearTimeout(timeout)
        resolve()
      }, { once: true })
    })
    retryMs = Math.min(retryMs * 2, 10_000)
  }
}

async function parseError(response: Response): Promise<ApiError> {
  const requestId = response.headers.get('X-Request-ID')
  let body: ProblemDocument | null = null
  try {
    body = (await response.json()) as ProblemDocument
  } catch {
    body = null
  }
  return new ApiError(
    body?.detail ?? `The API returned HTTP ${response.status}.`,
    {
      status: response.status,
      code: body?.code ?? `HTTP_${response.status}`,
      requestId: body?.instance?.replace('urn:request:', '') ?? requestId,
      details: body?.errors ?? null,
    },
  )
}

export async function request<T>(
  path: string,
  init: RequestInit = {},
  signal?: AbortSignal,
): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  headers.set('X-Flowstate-Actor', 'console-operator')
  headers.set('X-Flowstate-Role', actorRole)
  if (init.body) headers.set('Content-Type', 'application/json')
  if (API_TOKEN) headers.set('Authorization', `Bearer ${API_TOKEN}`)

  let response: Response
  try {
    response = await fetch(`${API_ORIGIN}${API_PREFIX}${path}`, {
      ...init,
      headers,
      signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(
      'The console could not reach the FLOWSTATE API. Check that the backend is running.',
      { status: 0, code: 'NETWORK_ERROR' },
    )
  }

  if (!response.ok) throw await parseError(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

function json(body: unknown): RequestInit {
  return { method: 'POST', body: JSON.stringify(body) }
}

export const api = {
  health: (signal?: AbortSignal) => request<Health>('/health', {}, signal),
  readiness: (signal?: AbortSignal) => request<Readiness>('/health/ready', {}, signal),
  system: (signal?: AbortSignal) => request<SystemInfo>('/system', {}, signal),
  overview: (signal?: AbortSignal) => request<Overview>('/system/overview', {}, signal),

  demo: (signal?: AbortSignal) => request<DemoInfo>('/demo', {}, signal),
  seedDemo: () => request<DemoSeedResponse>('/demo/seed', { method: 'POST' }),
  resetDemo: () => request<{ deleted: number; runs: number }>('/demo/reset', { method: 'DELETE' }),

  workflows: (signal?: AbortSignal) =>
    request<WorkflowSummary[]>('/workflows', {}, signal),
  workflow: (id: string, signal?: AbortSignal) =>
    request<WorkflowDetail>(`/workflows/${id}`, {}, signal),
  createWorkflow: (payload: WorkflowCreatePayload) =>
    request<WorkflowDetail>('/workflows', json(payload)),
  version: (workflowId: string, versionId: string, signal?: AbortSignal) =>
    request<VersionDetail>(`/workflows/${workflowId}/versions/${versionId}`, {}, signal),
  createVersion: (
    workflowId: string,
    payload: { actions: WorkflowCreatePayload['actions']; invariants: WorkflowCreatePayload['invariants']; publish: boolean },
  ) => request<VersionDetail>(`/workflows/${workflowId}/versions`, json(payload)),
  publishVersion: (workflowId: string, versionId: string) =>
    request<VersionDetail>(`/workflows/${workflowId}/versions/${versionId}/publish`, {
      method: 'POST',
    }),

  runs: (params: { status?: string; origin?: string; workflowId?: string } = {}, signal?: AbortSignal) => {
    const query = new URLSearchParams()
    if (params.status) query.set('status', params.status)
    if (params.origin) query.set('origin', params.origin)
    if (params.workflowId) query.set('workflow_id', params.workflowId)
    const suffix = query.size ? `?${query.toString()}` : ''
    return request<RunSummary[]>(`/runs${suffix}`, {}, signal)
  },
  run: (id: string, signal?: AbortSignal) => request<RunDetail>(`/runs/${id}`, {}, signal),
  createRun: (payload: {
    workflow_id: string
    version_id?: string
    seed?: number
    inputs?: Record<string, unknown>
    background?: boolean
  }) => request<RunDetail>('/runs', json(payload)),
  cancelRun: (id: string, reason = '') =>
    request<RunDetail>(`/runs/${id}/cancel${reason ? `?reason=${encodeURIComponent(reason)}` : ''}`, {
      method: 'POST',
    }),
  timeline: (id: string, signal?: AbortSignal) =>
    request<Timeline>(`/runs/${id}/timeline`, {}, signal),
  events: (id: string, signal?: AbortSignal) =>
    request<ExecutionEvent[]>(`/runs/${id}/events`, {}, signal),
  verifyRun: (id: string) =>
    request<ChainVerification>(`/runs/${id}/events/verify`),
  recoveryPlan: (runId: string, signal?: AbortSignal) =>
    request<RecoveryPlan>(`/runs/${runId}/recovery-plan`, {}, signal),
  recoverRun: (id: string, strategy?: RecoveryStrategy) =>
    request<Recovery>(`/runs/${id}/recover`, json(strategy ? { strategy } : {})),
  noteRecovery: (id: string, note: string) =>
    request<Recovery>(`/recoveries/${id}/note`, json({ note })),
  runRecoveries: (id: string, signal?: AbortSignal) =>
    request<Recovery[]>(`/runs/${id}/recoveries`, {}, signal),

  decisions: (
    options: { openOnly?: boolean; kind?: BlockerKind | null } = {},
    signal?: AbortSignal,
  ) => {
    const params = new URLSearchParams({ limit: '200' })
    if (options.openOnly) params.set('open_only', 'true')
    if (options.kind) params.set('kind', options.kind)
    return request<Decision[]>(`/decisions?${params.toString()}`, {}, signal)
  },
  decision: (id: string, signal?: AbortSignal) =>
    request<Decision>(`/decisions/${id}`, {}, signal),
  approveDecision: (id: string, note = '') =>
    request<Decision>(`/decisions/${id}/approve`, json({ note })),
  rejectDecision: (id: string, reason = '') =>
    request<Decision>(`/decisions/${id}/reject`, json({ reason })),
  denyDecision: (id: string, reason = '') =>
    request<Decision>(`/decisions/${id}/deny`, json({ reason })),
  provideDecisionInfo: (id: string, values: Record<string, unknown>, note = '') =>
    request<Decision>(`/decisions/${id}/provide-info`, json({ values, note })),
  deferDecision: (id: string, reason = '') =>
    request<Decision>(`/decisions/${id}/defer`, json({ reason })),

  scenarios: (signal?: AbortSignal) => request<Scenario[]>('/scenarios', {}, signal),
  createScenario: (payload: ScenarioCreatePayload) =>
    request<Scenario>('/scenarios', json(payload)),
  runScenario: (id: string) => request<RunDetail>(`/scenarios/${id}/run`, { method: 'POST' }),
  evaluateScenario: (id: string) =>
    request<ChaosReport>(`/scenarios/${id}/evaluate`, { method: 'POST' }),
  scenarioReport: (id: string, signal?: AbortSignal) =>
    request<ChaosReport>(`/scenarios/${id}/report`, {}, signal),

  audit: (signal?: AbortSignal) => request<AuditOverview>('/audit', {}, signal),
  verifyAudit: (runId: string) => request<ChainVerification>(`/audit/verify/${runId}`),
  recoveries: (signal?: AbortSignal) => request<Recovery[]>('/recoveries', {}, signal),

  regressionTests: (signal?: AbortSignal) =>
    request<RegressionTest[]>('/regression-tests', {}, signal),
  createRegression: (runId: string) =>
    request<RegressionTest>('/regression-tests', json({ run_id: runId })),
  replayRegression: (id: string, versionId?: string) =>
    request<RegressionTest>(
      `/regression-tests/${id}/replay${versionId ? `?version_id=${versionId}` : ''}`,
      { method: 'POST' },
    ),
  compareRegression: (id: string) =>
    request<RegressionCompare>(`/regression-tests/${id}/compare`, { method: 'POST' }),
  regressionResults: (id: string, signal?: AbortSignal) =>
    request<RegressionResult[]>(`/regression-tests/${id}/results`, {}, signal),
  updateRegression: (id: string, payload: Record<string, unknown>) =>
    request<RegressionTest>(`/regression-tests/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
      headers: { 'Content-Type': 'application/json' },
    }),
  activateRegression: (id: string) =>
    request<RegressionTest>(`/regression-tests/${id}/activate`, { method: 'POST' }),
  failureModes: (signal?: AbortSignal) =>
    request<FailureMode[]>('/feedback/failure-modes', {}, signal),

  captures: (signal?: AbortSignal) =>
    request<CaptureSession[]>('/captures?limit=100', {}, signal),
  capture: (id: string, signal?: AbortSignal) =>
    request<CaptureSession>(`/captures/${id}`, {}, signal),
  startCapture: (payload: { label?: string; demo_url?: string }) =>
    request<CaptureSession>('/captures', json(payload)),
  pauseCapture: (id: string) =>
    request<CaptureSession>(`/captures/${id}/pause`, { method: 'POST' }),
  resumeCapture: (id: string) =>
    request<CaptureSession>(`/captures/${id}/resume`, { method: 'POST' }),
  stopCapture: (id: string) =>
    request<CaptureSession>(`/captures/${id}/stop`, { method: 'POST' }),
  recordCaptureEvents: (id: string, events: Record<string, unknown>[]) =>
    request<CaptureSession>(`/captures/${id}/events`, json({ events })),
  updateCapturedAction: (sessionId: string, actionId: string, payload: CaptureActionPatch) =>
    request<CaptureSession>(`/captures/${sessionId}/actions/${actionId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  validateCapture: (id: string) =>
    request<CaptureSession>(`/captures/${id}/validate`, { method: 'POST' }),
  saveCaptureWorkflow: (
    id: string,
    payload: { name?: string; description?: string; publish: boolean },
  ) => request<CaptureWorkflowResponse>(`/captures/${id}/workflow`, json(payload)),
  replayCapture: (
    id: string,
    payload: { inputs: Record<string, unknown>; variant: 'default' | 'changed' },
  ) => request<RunDetail>(`/captures/${id}/replay`, json(payload)),
  captureRuns: (id: string, signal?: AbortSignal) =>
    request<RunSummary[]>(`/captures/${id}/runs`, {}, signal),
}

export const faultTypes: FaultType[] = [
  'TIMEOUT',
  'RESPONSE_LOST',
  'HTTP_500',
  'MALFORMED_RESPONSE',
  'MISSING_FIELD',
  'DUPLICATE_RESPONSE',
  'STALE_READ',
  'LATENCY',
  'PARTIAL_COMMIT',
  'POSTCONDITION_FAILURE',
  'TIMEOUT_BEFORE_EXECUTION',
  'NETWORK_DROP',
  'RATE_LIMITED',
  'TRANSIENT_ERROR',
  'CONTRADICTORY_TOTALS',
  'DELAYED_APPROVAL',
  'SELECTOR_DRIFT',
]

export const recoveryStrategies: RecoveryStrategy[] = [
  'RESUME',
  'COMPENSATE',
  'REPLAY',
  'MANUAL',
  'ESCALATE',
]