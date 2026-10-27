"""Pydantic request and response schemas.

These are the API contract. ORM models never leak through a route signature.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from flowstate.domain.enums import (
    ActionChannel,
    ActionKind,
    ErrorClass,
    FaultType,
    InvariantKind,
    NodeStatus,
    NodeType,
    Origin,
    Severity,
    SideEffectClass,
)


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------- inputs


class ConditionIn(BaseModel):
    path: str = ""
    op: str = "eq"
    value: Any = None


class RetryPolicyIn(BaseModel):
    max_attempts: int = Field(default=1, ge=1, le=10)
    retry_on: list[ErrorClass] = Field(default_factory=list)
    backoff_ms: int = Field(default=0, ge=0, le=60_000)


class FailureHandlerIn(BaseModel):
    strategy: Literal["FAIL", "SKIP", "GOTO", "RECOVERY"] = "FAIL"
    target: str | None = None


class ActionIn(BaseModel):
    """A graph node. ``node_type`` defaults to ACTION for older clients."""

    ordinal: int | None = None
    node_key: str = Field(default="", max_length=80)
    node_type: NodeType = NodeType.ACTION
    kind: ActionKind = ActionKind.CLICK
    label: str = Field(min_length=1, max_length=200)
    target: dict[str, Any] = Field(default_factory=dict)
    input: dict[str, Any] = Field(default_factory=dict)
    preconditions: list[dict[str, Any]] = Field(default_factory=list)
    postconditions: list[dict[str, Any]] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    channel: ActionChannel = ActionChannel.SIMULATED
    side_effect_class: SideEffectClass = SideEffectClass.READ_ONLY
    requires_authorization: bool = False
    timeout_ms: int = Field(default=5000, ge=100, le=120_000)
    retry_policy: RetryPolicyIn = Field(default_factory=RetryPolicyIn)
    failure_handler: FailureHandlerIn = Field(default_factory=FailureHandlerIn)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    redact_paths: list[str] = Field(default_factory=list)

    @field_validator("node_key")
    @classmethod
    def _node_key_shape(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned and not all(char.isalnum() or char in {"_", "-"} for char in cleaned):
            raise ValueError("node_key may contain letters, digits, underscores and hyphens only")
        return cleaned


class InvariantIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    kind: InvariantKind = InvariantKind.TERMINAL_STATE
    severity: Severity = Severity.MAJOR
    expression: dict[str, Any] = Field(default_factory=dict)


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=2000)
    actions: list[ActionIn] = Field(default_factory=list)
    invariants: list[InvariantIn] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    redact_paths: list[str] = Field(default_factory=list)
    publish: bool = True


class VersionCreate(BaseModel):
    actions: list[ActionIn] = Field(min_length=1)
    invariants: list[InvariantIn] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    redact_paths: list[str] = Field(default_factory=list)
    publish: bool = True


class RunCreate(BaseModel):
    workflow_id: str
    version_id: str | None = None
    seed: int = 0
    trigger: Literal["MANUAL"] = "MANUAL"
    inputs: dict[str, Any] = Field(default_factory=dict)
    background: bool = False


class ScenarioCreate(BaseModel):
    workflow_id: str
    name: str = Field(min_length=3, max_length=200)
    description: str = ""
    target_ordinal: int = Field(ge=0)
    fault_type: FaultType
    parameters: dict[str, Any] = Field(default_factory=dict)
    input_fixtures: dict[str, Any] = Field(default_factory=dict)
    seed: int = 0


class DecisionApprove(BaseModel):
    note: str = ""


class DecisionReject(BaseModel):
    reason: str = ""


class DecisionDeny(DecisionReject):
    """Backwards-compatible alias for the original deny endpoint."""


class DecisionProvideInfo(BaseModel):
    values: dict[str, Any] = Field(min_length=1)
    note: str = ""


class DecisionDefer(BaseModel):
    reason: str = ""
    until: datetime | None = None


class RecoverRequest(BaseModel):
    strategy: Literal["RESUME", "COMPENSATE", "REPLAY", "ESCALATE", "MANUAL"] | None = None
    allow_irreversible: bool = False


class RecoveryNote(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


class RegressionCreate(BaseModel):
    run_id: str
    name: str | None = None


# -------------------------------------------------------------------- outputs


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str
    environment: str
    time: datetime


class ReadyResponse(BaseModel):
    status: Literal["ready", "degraded"]
    database: bool
    schema_version: int
    checks: dict[str, str]


class ActionOut(APIModel):
    id: str
    ordinal: int
    node_key: str
    node_type: str
    kind: str
    label: str
    target: dict[str, Any]
    input: dict[str, Any]
    preconditions: list[dict[str, Any]]
    postconditions: list[dict[str, Any]]
    depends_on: list[str] = Field(default_factory=list)
    channel: str = "SIMULATED"
    side_effect_class: str
    requires_authorization: bool
    timeout_ms: int
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    failure_handler: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    redact_paths: list[str] = Field(default_factory=list)


class InvariantOut(APIModel):
    id: str
    name: str
    description: str
    kind: str
    severity: str
    expression: dict[str, Any]


class VersionSummary(APIModel):
    id: str
    workflow_id: str
    version: int
    status: str
    label: str
    content_hash: str
    origin: str
    created_at: datetime
    published_at: datetime | None = None
    action_count: int = 0
    invariant_count: int = 0
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    redact_paths: list[str] = Field(default_factory=list)


class VersionDetail(VersionSummary):
    actions: list[ActionOut] = Field(default_factory=list)
    invariants: list[InvariantOut] = Field(default_factory=list)


class WorkflowSummary(APIModel):
    id: str
    slug: str
    name: str
    description: str
    owner: str
    origin: str
    seed_batch: str | None = None
    current_version_id: str | None = None
    created_at: datetime
    archived_at: datetime | None = None
    version_count: int = 0
    latest_version: int | None = None
    latest_status: str | None = None
    run_count: int = 0


class WorkflowDetail(WorkflowSummary):
    versions: list[VersionSummary] = Field(default_factory=list)


class EventOut(APIModel):
    id: str
    seq: int
    type: str
    actor: str
    actor_id: str
    action_ordinal: int | None = None
    node_key: str | None = None
    payload: dict[str, Any]
    prev_hash: str
    hash: str
    recorded_at: datetime


class VerdictOut(APIModel):
    invariant_id: str
    invariant_name: str
    severity: str
    verdict: str
    detail: str
    observed: dict[str, Any]


class RunSummary(APIModel):
    id: str
    workflow_id: str
    capture_session_id: str | None = None
    workflow_name: str = ""
    workflow_version_id: str
    version: int | None = None
    status: str
    verdict: str
    trigger: str
    origin: str
    driver: str
    seed: int
    current_ordinal: int
    fault_scenario_id: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    failure_ordinal: int | None = None
    actor: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime
    event_count: int = 0
    demo: bool = False
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)


class NodeStateOut(APIModel):
    node_key: str
    node_type: str
    ordinal: int
    label: str = ""
    status: NodeStatus
    attempt: int = 0
    max_attempts: int = 1
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int | None = None
    error_class: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    reconciled: bool = False
    depends_on: list[str] = Field(default_factory=list)
    checkpoint_id: str | None = None


class TimelineEntry(BaseModel):
    seq: int
    type: str
    node_key: str | None = None
    action_ordinal: int | None = None
    actor: str
    actor_id: str
    recorded_at: datetime
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)


class TimelineOut(BaseModel):
    run_id: str
    status: str
    verdict: str
    generated_at: datetime
    nodes: list[NodeStateOut]
    entries: list[TimelineEntry]
    chain_valid: bool


class RunDetail(RunSummary):
    actions: list[ActionOut] = Field(default_factory=list)
    events: list[EventOut] = Field(default_factory=list)
    verdicts: list[VerdictOut] = Field(default_factory=list)
    evidence: list[EvidenceOut] = Field(default_factory=list)
    chain_valid: bool = True
    node_states: list[NodeStateOut] = Field(default_factory=list)
    graph: dict[str, Any] = Field(default_factory=dict)
    final_state: dict[str, Any] = Field(default_factory=dict)


class ChainVerificationOut(BaseModel):
    run_id: str
    valid: bool
    event_count: int
    head_hash: str
    broken_at_seq: int | None = None
    reason: str | None = None


class EvidenceOut(APIModel):
    id: str
    run_id: str
    kind: str
    uri: str
    sha256: str
    mime_type: str
    captured_at: datetime
    verified: bool = True


class BlockerEvidenceOut(APIModel):
    id: str
    blocker_id: str
    run_id: str
    source_kind: str
    source_path: str
    source_ref: str | None = None
    record_ref: str | None = None
    label: str
    relation: str
    value_json: dict[str, Any]
    validation: str
    detail: str
    observed_at: datetime
    fresh_until: datetime | None = None


class BlockerOut(APIModel):
    id: str
    run_id: str
    node_key: str
    action_ordinal: int
    kind: str
    code: str
    title: str
    detail: str
    status: str
    required_role: str
    resume_conditions: list[dict[str, Any]]
    input_spec: list[dict[str, Any]]
    evidence_hash: str
    decision_id: str | None = None
    origin: str
    created_at: datetime
    resolved_at: datetime | None = None
    evidence: list[BlockerEvidenceOut] = Field(default_factory=list)


class DecisionHistoryOut(APIModel):
    id: str
    decision_id: str
    run_id: str
    action: str
    actor: str
    actor_role: str
    note: str
    outcome: str
    payload: dict[str, Any]
    created_at: datetime


class DecisionOut(APIModel):
    id: str
    run_id: str
    action_ordinal: int
    node_key: str | None = None
    question: str
    options: list[dict[str, Any]]
    recommended_option: str | None = None
    reason: str
    policy_citation: str
    status: str
    origin: str
    requested_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None
    decided_role: str | None = None
    expires_at: datetime
    deferred_until: datetime | None = None
    capability_token_id: str | None = None
    workflow_name: str = ""
    workflow_id: str = ""
    version: int | None = None
    run_status: str = ""
    kind: str | None = None
    required_role: str | None = None
    resume_conditions: list[dict[str, Any]] = Field(default_factory=list)
    input_spec: list[dict[str, Any]] = Field(default_factory=list)
    provided_input: dict[str, Any] = Field(default_factory=dict)
    permitted_actions: list[str] = Field(default_factory=list)
    can_decide: bool = False
    actor_role: str | None = None
    blocker: BlockerOut | None = None
    evidence: list[EvidenceOut] = Field(default_factory=list)
    evidence_items: list[BlockerEvidenceOut] = Field(default_factory=list)
    history: list[DecisionHistoryOut] = Field(default_factory=list)


class ScenarioOut(APIModel):
    id: str
    workflow_id: str
    workflow_name: str = ""
    workflow_version_id: str
    name: str
    description: str
    target_ordinal: int
    fault_type: str
    parameters: dict[str, Any]
    input_fixtures: dict[str, Any] = Field(default_factory=dict)
    seed: int
    status: str
    origin: str
    baseline_run_id: str | None = None
    last_run_id: str | None = None
    created_at: datetime


class ChaosEventOut(BaseModel):
    seq: int
    type: str
    node_key: str | None = None
    action_ordinal: int | None = None
    actor: str
    actor_id: str
    recorded_at: datetime


class ChaosRunOutcome(BaseModel):
    run_id: str
    status: str
    verdict: str
    driver: str
    seed: int
    inputs: dict[str, Any] = Field(default_factory=dict)
    failure_code: str | None = None
    failure_message: str | None = None
    failure_ordinal: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int
    event_count: int = 0
    reconciled_actions: int = 0
    events: list[ChaosEventOut] = Field(default_factory=list)
    invariant_verdicts: list[VerdictOut] = Field(default_factory=list)
    first_violated_invariant: VerdictOut | None = None


class ChaosFailureAction(BaseModel):
    ordinal: int | None = None
    node_key: str | None = None
    label: str = ""
    node_type: str = "ACTION"
    status: str | None = None
    error_class: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceOut] = Field(default_factory=list)


class ChaosRecoveryOut(BaseModel):
    attempted: bool
    verified: bool
    attempt_id: str | None = None
    status: str | None = None
    detail: str | None = None


class ChaosReportOut(BaseModel):
    scenario_id: str
    scenario_name: str
    fault_type: str
    target_ordinal: int
    seed: int
    parameters: dict[str, Any] = Field(default_factory=dict)
    input_fixtures: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime
    baseline: ChaosRunOutcome
    fault: ChaosRunOutcome
    first_violated_invariant: VerdictOut | None = None
    failure_action: ChaosFailureAction | None = None
    failed_safely: bool
    recovery: ChaosRecoveryOut
    regression_test_id: str | None = None
    evaluation: str
    baseline_verified: bool
    scenario_status: str


class RecoveryOut(APIModel):
    id: str
    run_id: str
    checkpoint_id: str | None = None
    strategy: str
    status: str
    steps: list[dict[str, Any]]
    detail: str
    verification_event_id: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    action_ordinal: int | None = None
    node_key: str | None = None
    recovery_policy: dict[str, Any] = Field(default_factory=dict)
    contract: list[dict[str, Any]] = Field(default_factory=list)
    before_state_hash: str | None = None
    after_state_hash: str | None = None
    verification: dict[str, Any] = Field(default_factory=dict)
    evidence_id: str | None = None
    failure_code: str | None = None
    manual_note: str = ""


class RecoveryPlanOut(BaseModel):
    run_id: str
    supported: bool
    action_ordinal: int | None = None
    node_key: str | None = None
    action_label: str | None = None
    strategy: str
    reversible: bool
    allowed_strategies: list[str]
    contract: list[dict[str, Any]]
    compensate: list[dict[str, Any]]
    probe: dict[str, Any] | None = None
    idempotency_path: str | None = None
    notes: str = ""
    reason: str = ""
    code: str | None = None
    ambiguous: bool = False
    run_status: str
    recoverable: bool


class RegressionOut(APIModel):
    id: str
    source_run_id: str
    fault_scenario_id: str | None = None
    invariant_id: str | None = None
    name: str
    spec: dict[str, Any]
    status: str
    last_result: str | None = None
    last_run_at: datetime | None = None
    created_at: datetime
    notes: str = ""
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


class RegressionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)
    expected_description: str | None = Field(default=None, max_length=2000)


class RegressionResultOut(APIModel):
    id: str
    test_id: str
    workflow_id: str
    workflow_version_id: str
    version: int
    run_id: str
    result: str
    failure_code: str | None = None
    violated_invariant: str | None = None
    detail: str
    created_at: datetime


class RegressionCompareOut(BaseModel):
    test_id: str
    test: RegressionOut
    results: list[RegressionResultOut]
    resolved_in: list[int]
    reproduces_in: list[int]


class FeedbackFailureModeOut(BaseModel):
    test_id: str
    test_name: str
    test_status: str
    workflow_id: str
    workflow_name: str
    failure_mode: str
    scenario_fault_type: str | None = None
    detected_in_version: int | None = None
    current_version: int | None = None
    status: Literal["RESOLVED", "STILL_FAILING", "UNTESTED"]
    resolved_in: list[int]
    reproduces_in: list[int]
    last_checked_at: datetime | None = None
    source_run_id: str
    notes: str = ""


class AuditRunStatus(BaseModel):
    run_id: str
    workflow_name: str
    events: int
    valid: bool
    head_hash: str
    broken_at_seq: int | None = None


class AuditOverview(BaseModel):
    checked: int
    valid: int
    broken: int
    checked_at: datetime
    runs: list[AuditRunStatus]


class OverviewCounts(BaseModel):
    workflows: int
    demo_workflows: int
    published_versions: int
    runs: int
    demo_runs: int
    live_runs: int
    open_decisions: int
    scenarios: int
    invariant_violations: int


class OverviewTerminal(BaseModel):
    total: int
    verified: int
    success_rate: float | None = None


class OverviewAudit(BaseModel):
    checked: int
    valid: int
    broken: int


class DriverInfo(BaseModel):
    name: str
    kind: str
    browser: bool
    note: str | None = None


class OverviewResponse(BaseModel):
    generated_at: datetime
    uptime_seconds: float
    database_healthy: bool
    counts: OverviewCounts
    runs_by_status: dict[str, int]
    terminal: OverviewTerminal
    audit: OverviewAudit
    recent_runs: list[RunSummary]
    driver: DriverInfo


class DatabaseInfo(BaseModel):
    url: str
    schema_version: int
    healthy: bool


class SystemSettings(BaseModel):
    auth_mode: str
    cors_origins: list[str]
    demo_seed_enabled: bool
    decision_ttl_seconds: int
    run_action_budget: int
    log_level: str
    log_json: bool


class SystemInfoResponse(BaseModel):
    app: str
    version: str
    environment: str
    api_prefix: str
    server_time: datetime
    uptime_seconds: float
    database: DatabaseInfo
    checks: dict[str, str]
    settings: SystemSettings
    driver: dict[str, Any]


class DemoSeedResponse(BaseModel):
    created: bool
    reason: str
    workflow_id: str | None = None
    workflow_slug: str | None = None
    version_id: str | None = None
    batch: str
    runs: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    scenarios: list[str] = Field(default_factory=list)


class DeleteResponse(BaseModel):
    deleted: int
    runs: int = 0


class RunResponse(BaseModel):
    run: RunSummary


class Paginated(BaseModel):
    items: list[Any]
    total: int
    limit: int
    offset: int


class CaptureCreate(BaseModel):
    label: str = Field(default="", max_length=200)
    demo_url: str | None = Field(default=None, max_length=500)


class CaptureEventBatch(BaseModel):
    events: list[dict[str, Any]] = Field(default_factory=list, max_length=100)


class CaptureActionPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=200)
    kind: str | None = Field(default=None, max_length=24)
    input_param: str | None = Field(default=None, max_length=80)
    input_value: str | None = Field(default=None, max_length=2000)
    expected_result: str | None = Field(default=None, max_length=500)
    selector: list[dict[str, Any]] | None = None
    resolution_note: str | None = Field(default=None, max_length=500)
    accept_ambiguity: bool | None = None


class CaptureWorkflowCreate(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=200)
    description: str = Field(default="", max_length=2000)
    publish: bool = True


class CaptureReplayRequest(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    variant: Literal["default", "changed"] = "default"


class SelectorOut(BaseModel):
    strategy: str
    value: str = ""
    role: str | None = None
    name: str | None = None
    score: int = 0


class CapturedActionOut(APIModel):
    id: str
    session_id: str
    ordinal: int
    kind: str
    label: str
    selector: list[dict[str, Any]] = Field(default_factory=list)
    accessible_name: str
    input_value: str | None = None
    input_param: str | None = None
    page_before: dict[str, Any] = Field(default_factory=dict)
    page_after: dict[str, Any] = Field(default_factory=dict)
    expected_result: str
    observed_result: str
    preconditions: list[dict[str, Any]] = Field(default_factory=list)
    postconditions: list[dict[str, Any]] = Field(default_factory=list)
    failure_conditions: list[str] = Field(default_factory=list)
    redacted_fields: list[str] = Field(default_factory=list)
    ambiguous: bool
    ambiguity_reason: str
    status: str
    resolution: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CaptureRunSummary(BaseModel):
    id: str
    status: str
    verdict: str
    driver: str
    created_at: datetime
    failure_code: str | None = None


class CaptureSessionOut(APIModel):
    id: str
    label: str
    status: str
    demo_url: str
    variant: str
    origin: str
    created_by: str
    workflow_id: str | None = None
    workflow_version_id: str | None = None
    version_status: str | None = None
    event_count: int
    validation: dict[str, Any] = Field(default_factory=dict)
    notes: str
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    validated_at: datetime | None = None
    actions: list[CapturedActionOut] = Field(default_factory=list)
    suggested_inputs: dict[str, Any] = Field(default_factory=dict)
    unresolved_ambiguities: list[int] = Field(default_factory=list)
    recent_runs: list[CaptureRunSummary] = Field(default_factory=list)


class CaptureWorkflowResponse(BaseModel):
    session: CaptureSessionOut
    workflow: WorkflowDetail
    version: VersionDetail


class DemoInfo(BaseModel):
    batch: str
    seeded: bool
    workflow_id: str | None = None
    enabled: bool
    origin: Origin = Origin.DEMO
