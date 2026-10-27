export type Origin = 'DEMO' | 'LIVE'

export type VersionStatus = 'DRAFT' | 'PUBLISHED' | 'DEPRECATED' | 'ARCHIVED'

export type RunStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'AWAITING_DECISION'
  | 'AWAITING_RECOVERY'
  | 'VERIFYING'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'CANCELLED'
  | 'TIMED_OUT'

export type RunVerdict = 'VERIFIED' | 'FAILED' | 'CANCELLED' | 'TIMED_OUT' | 'PENDING'

export type RunTrigger = 'MANUAL' | 'CHAOS' | 'SCHEDULED' | 'REGRESSION' | 'DEMO'

export type DecisionStatus =
  | 'REQUESTED'
  | 'NOTIFIED'
  | 'AUTHORIZED'
  | 'DENIED'
  | 'DEFERRED'
  | 'INFO_PROVIDED'
  | 'EXPIRED'
  | 'APPLIED'
  | 'CANCELLED'

export type BlockerKind =
  | 'MISSING_INPUT'
  | 'VALIDATION_FAILED'
  | 'CONFLICTING_EVIDENCE'
  | 'PERMISSION_REQUIRED'
  | 'HUMAN_APPROVAL_REQUIRED'
  | 'TOOL_UNAVAILABLE'
  | 'UNKNOWN_OUTCOME'
  | 'RECOVERY_PREREQUISITE_MISSING'

export type ActorRole = 'OPERATOR' | 'REVIEWER' | 'FINANCE_APPROVER' | 'ADMIN'

export interface BlockerEvidence {
  id: string
  blocker_id: string
  run_id: string
  source_kind: string
  source_path: string
  source_ref: string | null
  record_ref: string | null
  label: string
  relation: string
  value_json: Record<string, unknown>
  validation: 'PASS' | 'FAIL' | 'MISSING' | 'EXPIRED' | 'VALID' | 'INFO'
  detail: string
  observed_at: string
  fresh_until: string | null
}

export interface Blocker {
  id: string
  run_id: string
  node_key: string
  action_ordinal: number
  kind: BlockerKind
  code: string
  title: string
  detail: string
  status: 'OPEN' | 'RESOLVED' | 'SUPERSEDED'
  required_role: ActorRole
  resume_conditions: Record<string, unknown>[]
  input_spec: DecisionInputSpec[]
  evidence_hash: string
  decision_id: string | null
  origin: Origin
  created_at: string
  resolved_at: string | null
  evidence: BlockerEvidence[]
}

export interface DecisionInputSpec {
  key: string
  label: string
  path?: string
  required?: boolean
  type?: string
  placeholder?: string
}

export interface DecisionHistory {
  id: string
  decision_id: string
  run_id: string
  action: 'APPROVE' | 'REJECT' | 'PROVIDE_INFO' | 'DEFER' | 'RESUME'
  actor: string
  actor_role: string
  note: string
  outcome: string
  payload: Record<string, unknown>
  created_at: string
}

export type InvariantVerdict = 'SATISFIED' | 'VIOLATED' | 'INCONCLUSIVE'

export type FaultType =
  | 'TIMEOUT'
  | 'RESPONSE_LOST'
  | 'HTTP_500'
  | 'MALFORMED_RESPONSE'
  | 'MISSING_FIELD'
  | 'DUPLICATE_RESPONSE'
  | 'STALE_READ'
  | 'LATENCY'
  | 'PARTIAL_COMMIT'
  | 'POSTCONDITION_FAILURE'
  | 'TIMEOUT_BEFORE_EXECUTION'
  | 'NETWORK_DROP'
  | 'RATE_LIMITED'
  | 'TRANSIENT_ERROR'
  | 'CONTRADICTORY_TOTALS'
  | 'DELAYED_APPROVAL'
  | 'SELECTOR_DRIFT'

export type NodeType =
  | 'START'
  | 'END'
  | 'ACTION'
  | 'VALIDATION'
  | 'BRANCH'
  | 'HUMAN_DECISION'
  | 'CHECKPOINT'
  | 'RECOVERY_HANDLER'

export type NodeStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'PASSED'
  | 'FAILED'
  | 'BLOCKED'
  | 'SKIPPED'
  | 'CANCELLED'
  | 'RECOVERED'

export type ActionChannel = 'SIMULATED' | 'BROWSER' | 'API'

export type RecoveryStrategy = 'RESUME' | 'COMPENSATE' | 'REPLAY' | 'ESCALATE' | 'MANUAL'

export interface Health {
  status: 'ok'
  service: string
  version: string
  environment: string
  time: string
}

export interface Readiness {
  status: 'ready' | 'degraded'
  database: boolean
  schema_version: number
  checks: Record<string, string>
}

export interface DemoInfo {
  batch: string
  seeded: boolean
  workflow_id: string | null
  enabled: boolean
  origin: Origin
}

export interface DemoSeedResponse {
  created: boolean
  reason: string
  workflow_id: string | null
  workflow_slug: string | null
  version_id: string | null
  batch: string
  runs: string[]
  decisions: string[]
  scenarios: string[]
}

export interface OverviewCounts {
  workflows: number
  demo_workflows: number
  published_versions: number
  runs: number
  demo_runs: number
  live_runs: number
  open_decisions: number
  scenarios: number
  invariant_violations: number
}

export interface Overview {
  generated_at: string
  uptime_seconds: number
  database_healthy: boolean
  counts: OverviewCounts
  runs_by_status: Record<string, number>
  terminal: {
    total: number
    verified: number
    success_rate: number | null
  }
  audit: {
    checked: number
    valid: number
    broken: number
  }
  recent_runs: RunSummary[]
  driver: DriverInfo
}

export interface DriverInfo {
  name: string
  kind: string
  browser: boolean
  note: string | null
}

export interface SystemInfo {
  app: string
  version: string
  environment: string
  api_prefix: string
  server_time: string
  uptime_seconds: number
  database: {
    url: string
    schema_version: number
    healthy: boolean
  }
  checks: Record<string, string>
  settings: {
    auth_mode: string
    cors_origins: string[]
    demo_seed_enabled: boolean
    decision_ttl_seconds: number
    run_action_budget: number
    log_level: string
    log_json: boolean
  }
  driver: DriverInfo
}

export interface WorkflowSummary {
  id: string
  slug: string
  name: string
  description: string
  owner: string
  origin: Origin
  seed_batch: string | null
  current_version_id: string | null
  created_at: string
  archived_at: string | null
  version_count: number
  latest_version: number | null
  latest_status: VersionStatus | null
  run_count: number
}

export interface VersionSummary {
  id: string
  workflow_id: string
  version: number
  status: VersionStatus
  label: string
  content_hash: string
  origin: Origin
  created_at: string
  published_at: string | null
  action_count: number
  invariant_count: number
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown>
  redact_paths: string[]
}

export interface RetryPolicy {
  max_attempts?: number
  retry_on?: string[]
  backoff_ms?: number
}

export interface FailureHandler {
  strategy?: 'FAIL' | 'SKIP' | 'GOTO' | 'RECOVERY'
  target?: string | null
}

export interface ActionDefinition {
  id: string
  ordinal: number
  node_key: string
  node_type: NodeType
  kind: string
  label: string
  target: Record<string, unknown>
  input: Record<string, unknown>
  preconditions: Record<string, unknown>[]
  postconditions: Record<string, unknown>[]
  depends_on: string[]
  channel: ActionChannel
  side_effect_class: string
  requires_authorization: boolean
  timeout_ms: number
  retry_policy: RetryPolicy
  failure_handler: FailureHandler
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown>
  redact_paths: string[]
}

export interface InvariantDefinition {
  id: string
  name: string
  description: string
  kind: string
  severity: string
  expression: Record<string, unknown>
}

export interface VersionDetail extends VersionSummary {
  actions: ActionDefinition[]
  invariants: InvariantDefinition[]
}

export interface WorkflowDetail extends WorkflowSummary {
  versions: VersionSummary[]
}

export interface RunSummary {
  id: string
  workflow_id: string
  capture_session_id?: string | null
  workflow_name: string
  inputs: Record<string, unknown>
  outputs: Record<string, unknown>
  workflow_version_id: string
  version: number | null
  status: RunStatus
  verdict: RunVerdict
  trigger: RunTrigger
  origin: Origin
  driver: string
  seed: number
  current_ordinal: number
  fault_scenario_id: string | null
  failure_code: string | null
  failure_message: string | null
  failure_ordinal: number | null
  actor: string
  started_at: string | null
  ended_at: string | null
  created_at: string
  event_count: number
  demo: boolean
}

export interface ExecutionEvent {
  id: string
  seq: number
  type: string
  actor: string
  actor_id: string
  action_ordinal: number | null
  node_key: string | null
  payload: Record<string, unknown>
  prev_hash: string
  hash: string
  recorded_at: string
}

export interface Verdict {
  invariant_id: string
  invariant_name: string
  severity: string
  verdict: InvariantVerdict
  detail: string
  observed: Record<string, unknown>
}

export interface NodeState {
  node_key: string
  node_type: NodeType
  ordinal: number
  label: string
  status: NodeStatus
  attempt: number
  max_attempts: number
  started_at: string | null
  ended_at: string | null
  duration_ms: number | null
  error_class: string | null
  error_code: string | null
  error_message: string | null
  output: Record<string, unknown>
  idempotency_key: string | null
  reconciled: boolean
  depends_on: string[]
  checkpoint_id: string | null
}

export interface RunDetail extends RunSummary {
  actions: ActionDefinition[]
  events: ExecutionEvent[]
  verdicts: Verdict[]
  evidence: Evidence[]
  chain_valid: boolean
  node_states: NodeState[]
  graph: Record<string, unknown>
}

export interface TimelineEntry {
  seq: number
  type: string
  node_key: string | null
  action_ordinal: number | null
  actor: string
  actor_id: string
  recorded_at: string
  summary: string
  payload: Record<string, unknown>
}

export interface Timeline {
  run_id: string
  status: string
  verdict: string
  generated_at: string
  nodes: NodeState[]
  entries: TimelineEntry[]
  chain_valid: boolean
}

export interface ChainVerification {
  run_id: string
  valid: boolean
  event_count: number
  head_hash: string
  broken_at_seq: number | null
  reason: string | null
}

export interface Evidence {
  id: string
  run_id: string
  kind: string
  uri: string
  sha256: string
  mime_type: string
  captured_at: string
  verified: boolean
}

export interface Decision {
  id: string
  run_id: string
  action_ordinal: number
  node_key: string | null
  question: string
  options: { value: string; label: string; consequence?: string }[]
  recommended_option: string | null
  reason: string
  policy_citation: string
  status: DecisionStatus
  origin: Origin
  requested_at: string
  decided_at: string | null
  decided_by: string | null
  decided_role: string | null
  expires_at: string
  deferred_until: string | null
  capability_token_id: string | null
  workflow_name: string
  workflow_id: string
  version: number | null
  run_status: string
  kind: BlockerKind | null
  required_role: ActorRole | null
  resume_conditions: Record<string, unknown>[]
  input_spec: DecisionInputSpec[]
  provided_input: Record<string, unknown>
  permitted_actions: string[]
  can_decide: boolean
  actor_role: string | null
  blocker: Blocker | null
  evidence: Evidence[]
  evidence_items: BlockerEvidence[]
  history: DecisionHistory[]
}

export interface Scenario {
  id: string
  workflow_id: string
  workflow_name: string
  workflow_version_id: string
  name: string
  description: string
  target_ordinal: number
  fault_type: FaultType
  parameters: Record<string, unknown>
  input_fixtures: Record<string, unknown>
  seed: number
  status: string
  origin: Origin
  baseline_run_id: string | null
  last_run_id: string | null
  created_at: string
}

export interface ChaosEvent {
  seq: number
  type: string
  node_key: string | null
  action_ordinal: number | null
  actor: string
  actor_id: string
  recorded_at: string
}

export interface ChaosRunOutcome {
  run_id: string
  status: RunStatus
  verdict: RunVerdict
  driver: string
  seed: number
  inputs: Record<string, unknown>
  failure_code: string | null
  failure_message: string | null
  failure_ordinal: number | null
  started_at: string | null
  ended_at: string | null
  duration_ms: number
  event_count: number
  reconciled_actions: number
  events: ChaosEvent[]
  invariant_verdicts: Verdict[]
  first_violated_invariant: Verdict | null
}

export interface ChaosFailureAction {
  ordinal: number | null
  node_key: string | null
  label: string
  node_type: string
  status: string | null
  error_class: string | null
  error_code: string | null
  error_message: string | null
  output: Record<string, unknown>
  evidence: Evidence[]
}

export interface ChaosRecovery {
  attempted: boolean
  verified: boolean
  attempt_id: string | null
  status: string | null
  detail: string | null
}

export type ChaosEvaluation =
  | 'BASELINE_NOT_VERIFIED'
  | 'INCONCLUSIVE'
  | 'FAULT_DETECTED'
  | 'NO_FAULT_EFFECT'
  | 'SAFE_FAILURE_WITHOUT_VIOLATION'

export interface ChaosReport {
  scenario_id: string
  scenario_name: string
  fault_type: FaultType
  target_ordinal: number
  seed: number
  parameters: Record<string, unknown>
  input_fixtures: Record<string, unknown>
  generated_at: string
  baseline: ChaosRunOutcome
  fault: ChaosRunOutcome
  first_violated_invariant: Verdict | null
  failure_action: ChaosFailureAction | null
  failed_safely: boolean
  recovery: ChaosRecovery
  regression_test_id: string | null
  evaluation: ChaosEvaluation
  baseline_verified: boolean
  scenario_status: string
}

export interface RecoveryCheck {
  check: string
  ok: boolean
  detail: string
}

export interface Recovery {
  id: string
  run_id: string
  checkpoint_id: string | null
  strategy: RecoveryStrategy
  status:
    | 'PLANNED'
    | 'APPLYING'
    | 'APPLIED'
    | 'VERIFIED'
    | 'PARTIAL'
    | 'FAILED'
    | 'MANUAL_INTERVENTION'
    | 'ESCALATED'
  steps: Record<string, unknown>[]
  detail: string
  verification_event_id: string | null
  started_at: string
  ended_at: string | null
  action_ordinal: number | null
  node_key: string | null
  recovery_policy: Record<string, unknown>
  contract: Record<string, unknown>[]
  before_state_hash: string | null
  after_state_hash: string | null
  verification: {
    checks?: RecoveryCheck[]
    probe?: Record<string, unknown> | null
    status?: string
  }
  evidence_id: string | null
  failure_code: string | null
  manual_note: string
}

export interface RecoveryPlan {
  run_id: string
  supported: boolean
  action_ordinal: number | null
  node_key: string | null
  action_label: string | null
  strategy: RecoveryStrategy
  reversible: boolean
  allowed_strategies: string[]
  contract: Record<string, unknown>[]
  compensate: Record<string, unknown>[]
  probe: Record<string, unknown> | null
  idempotency_path: string | null
  notes: string
  reason: string
  code: string | null
  ambiguous: boolean
  run_status: string
  recoverable: boolean
}

export interface RegressionSpec {
  template_version: number
  workflow_id: string
  workflow_name: string | null
  workflow_version_id: string
  version: number | null
  source_run_id: string
  seed: number
  driver: string
  scenario: {
    id: string
    name: string
    fault_type: string
    target_ordinal: number
    parameters: Record<string, unknown>
    seed: number
  } | null
  expected: {
    kind: string
    invariant_id: string | null
    invariant_name: string | null
    description: string
    failure_code: string | null
  }
  observed: {
    run_status: string
    verdict: string
    failure_code: string | null
    failure_message: string | null
    failed_ordinal: number | null
    violated_invariants: {
      id: string
      name: string
      severity: string
      detail: string
    }[]
  }
  reproduction: Record<string, string>
}

export interface RegressionTest {
  id: string
  source_run_id: string
  fault_scenario_id: string | null
  invariant_id: string | null
  name: string
  spec: RegressionSpec
  status: string
  last_result: string | null
  last_run_at: string | null
  created_at: string
  notes: string
  reviewed_by: string | null
  reviewed_at: string | null
}

export interface RegressionResult {
  id: string
  test_id: string
  workflow_id: string
  workflow_version_id: string
  version: number
  run_id: string
  result: 'PASSED' | 'FAILED'
  failure_code: string | null
  violated_invariant: string | null
  detail: string
  created_at: string
}

export interface RegressionCompare {
  test_id: string
  test: RegressionTest
  results: RegressionResult[]
  resolved_in: number[]
  reproduces_in: number[]
}

export interface FailureMode {
  test_id: string
  test_name: string
  test_status: string
  workflow_id: string
  workflow_name: string
  failure_mode: string
  scenario_fault_type: string | null
  detected_in_version: number | null
  current_version: number | null
  status: 'RESOLVED' | 'STILL_FAILING' | 'UNTESTED'
  resolved_in: number[]
  reproduces_in: number[]
  last_checked_at: string | null
  source_run_id: string
  notes: string
}

export interface AuditOverview {
  checked: number
  valid: number
  broken: number
  checked_at: string
  runs: {
    run_id: string
    workflow_name: string
    events: number
    valid: boolean
    head_hash: string
    broken_at_seq: number | null
  }[]
}

export interface ProblemDocument {
  type: string
  title: string
  status: number
  code: string
  detail: string
  instance?: string
  errors?: Record<string, string>
}

export interface WorkflowCreatePayload {
  name: string
  description: string
  publish: boolean
  input_schema?: Record<string, unknown>
  output_schema?: Record<string, unknown>
  redact_paths?: string[]
  actions: {
    ordinal?: number
    node_key?: string
    node_type?: NodeType
    kind: string
    label: string
    target?: Record<string, unknown>
    input?: Record<string, unknown>
    preconditions?: Record<string, unknown>[]
    postconditions?: Record<string, unknown>[]
    depends_on?: string[]
    channel?: ActionChannel
    side_effect_class?: string
    requires_authorization?: boolean
    timeout_ms?: number
    retry_policy?: RetryPolicy
    failure_handler?: FailureHandler
  }[]
  invariants: {
    name: string
    description?: string
    kind: string
    severity: string
    expression: Record<string, unknown>
  }[]
}

export interface ScenarioCreatePayload {
  workflow_id: string
  name: string
  description: string
  target_ordinal: number
  fault_type: FaultType
  parameters: Record<string, unknown>
  input_fixtures: Record<string, unknown>
  seed: number
}
export type CaptureStatus =
  | 'RECORDING'
  | 'PAUSED'
  | 'REVIEW'
  | 'VALIDATED'
  | 'SAVED'
  | 'FAILED'

export type CapturedActionStatus =
  | 'CAPTURED'
  | 'AMBIGUOUS'
  | 'RESOLVED'
  | 'VALIDATED'
  | 'UNRESOLVED'
  | 'REJECTED'

export interface SelectorCandidate {
  strategy: string
  value: string
  role?: string | null
  name?: string | null
  score?: number
  matches?: number
}

export interface CapturedAction {
  id: string
  session_id: string
  ordinal: number
  kind: string
  label: string
  selector: SelectorCandidate[]
  accessible_name: string
  input_value: string | null
  input_param: string | null
  page_before: {
    url?: string
    route?: string
    page?: string
    heading?: string
    state?: Record<string, unknown>
  }
  page_after: {
    url?: string
    route?: string
    page?: string
    heading?: string
    state?: Record<string, unknown>
  }
  expected_result: string
  observed_result: string
  preconditions: Record<string, unknown>[]
  postconditions: Record<string, unknown>[]
  failure_conditions: string[]
  redacted_fields: string[]
  ambiguous: boolean
  ambiguity_reason: string
  status: CapturedActionStatus
  resolution: { accepted?: boolean; note?: string }
  created_at: string
}

export interface CaptureValidationEntry {
  ordinal: number | null
  node_key?: string
  label?: string
  ok: boolean
  matches?: number
  strategy?: string
  value?: string
  code?: string
  detail?: string
}

export interface CaptureValidation {
  ok?: boolean
  checked?: number
  passed?: number
  checked_at?: string
  demo_url?: string
  evidence_id?: string
  actions?: CaptureValidationEntry[]
}

export interface CaptureRunRef {
  id: string
  status: RunStatus
  verdict: RunVerdict
  driver: string
  created_at: string
  failure_code: string | null
}

export interface CaptureSession {
  id: string
  label: string
  status: CaptureStatus
  demo_url: string
  variant: string
  origin: Origin
  created_by: string
  workflow_id: string | null
  workflow_version_id: string | null
  version_status: string | null
  event_count: number
  validation: CaptureValidation
  notes: string
  created_at: string
  started_at: string | null
  ended_at: string | null
  validated_at: string | null
  actions: CapturedAction[]
  suggested_inputs: Record<string, unknown>
  unresolved_ambiguities: number[]
  recent_runs: CaptureRunRef[]
}

export interface CaptureWorkflowResponse {
  session: CaptureSession
  workflow: WorkflowDetail
  version: VersionDetail
}

export interface CaptureActionPatch {
  label?: string
  kind?: string
  input_param?: string | null
  input_value?: string | null
  expected_result?: string
  resolution_note?: string
  accept_ambiguity?: boolean
}
