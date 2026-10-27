"""Canonical enums shared by domain, persistence and API layers."""

from __future__ import annotations

from enum import StrEnum


class Origin(StrEnum):
    """Where a record came from.

    ``DEMO`` records are fixtures loaded by the explicit seed command. ``LIVE``
    records were produced by this application. The distinction is surfaced in
    every API response so demo data can never be mistaken for real activity.
    """

    DEMO = "DEMO"
    LIVE = "LIVE"


class VersionStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    DEPRECATED = "DEPRECATED"
    ARCHIVED = "ARCHIVED"


class NodeType(StrEnum):
    """Typed graph nodes. A version is a DAG of these, not a linear script."""

    START = "START"
    END = "END"
    ACTION = "ACTION"
    VALIDATION = "VALIDATION"
    BRANCH = "BRANCH"
    HUMAN_DECISION = "HUMAN_DECISION"
    CHECKPOINT = "CHECKPOINT"
    RECOVERY_HANDLER = "RECOVERY_HANDLER"


class ActionChannel(StrEnum):
    """Where an ACTION node executes.

    Only ``SIMULATED`` has a registered driver in this build. The format accepts
    ``BROWSER`` and ``API`` so a driver can be added without changing workflows.
    """

    SIMULATED = "SIMULATED"
    BROWSER = "BROWSER"
    API = "API"


class NodeStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"
    RECOVERED = "RECOVERED"


# Node states that satisfy a dependency.
NODE_SUCCESS_STATUSES = frozenset({NodeStatus.PASSED, NodeStatus.SKIPPED, NodeStatus.RECOVERED})
NODE_TERMINAL_STATUSES = frozenset(
    {
        NodeStatus.PASSED,
        NodeStatus.FAILED,
        NodeStatus.SKIPPED,
        NodeStatus.CANCELLED,
        NodeStatus.RECOVERED,
    }
)


class ErrorClass(StrEnum):
    """Stable classification used for retry rules and the timeline."""

    PRECONDITION = "PRECONDITION"
    POSTCONDITION = "POSTCONDITION"
    TIMEOUT = "TIMEOUT"
    AMBIGUOUS_OUTCOME = "AMBIGUOUS_OUTCOME"
    HANDLER_ERROR = "HANDLER_ERROR"
    VALIDATION = "VALIDATION"
    POLICY = "POLICY"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    INPUT_SCHEMA = "INPUT_SCHEMA"
    OUTPUT_SCHEMA = "OUTPUT_SCHEMA"
    GRAPH_INVALID = "GRAPH_INVALID"
    DEPENDENCY_FAILED = "DEPENDENCY_FAILED"
    CANCELLED = "CANCELLED"
    RATE_LIMITED = "RATE_LIMITED"
    UNKNOWN = "UNKNOWN"


class RunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    AWAITING_DECISION = "AWAITING_DECISION"
    AWAITING_RECOVERY = "AWAITING_RECOVERY"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.TIMED_OUT}
)

# Statuses from which a user-visible recovery can begin.
RECOVERABLE_RUN_STATUSES = frozenset(
    {RunStatus.AWAITING_RECOVERY, RunStatus.FAILED, RunStatus.TIMED_OUT}
)


class RunVerdict(StrEnum):
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    PENDING = "PENDING"


class RunTrigger(StrEnum):
    MANUAL = "MANUAL"
    CHAOS = "CHAOS"
    SCHEDULED = "SCHEDULED"
    REGRESSION = "REGRESSION"
    DEMO = "DEMO"


class ActionKind(StrEnum):
    NAVIGATE = "NAVIGATE"
    CLICK = "CLICK"
    TYPE = "TYPE"
    SELECT = "SELECT"
    SUBMIT = "SUBMIT"
    WAIT_FOR = "WAIT_FOR"
    ASSERT = "ASSERT"
    EXTRACT = "EXTRACT"
    AUTHORIZE = "AUTHORIZE"
    PAYMENT_SIMULATED = "PAYMENT_SIMULATED"
    EMAIL_SEND = "EMAIL_SEND"
    API_CALL = "API_CALL"


class SideEffectClass(StrEnum):
    READ_ONLY = "READ_ONLY"
    IDEMPOTENT = "IDEMPOTENT"
    COMPENSABLE = "COMPENSABLE"
    IRREVERSIBLE = "IRREVERSIBLE"


class ActionState(StrEnum):
    PENDING = "PENDING"
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    CONFIRMED = "CONFIRMED"
    COMPENSATED = "COMPENSATED"
    SKIPPED = "SKIPPED"


class EventType(StrEnum):
    RUN_CREATED = "RUN_CREATED"
    RUN_STARTED = "RUN_STARTED"
    WORKFLOW_VALIDATED = "WORKFLOW_VALIDATED"
    RUN_INPUTS_RECORDED = "RUN_INPUTS_RECORDED"
    RUN_OUTPUTS_RECORDED = "RUN_OUTPUTS_RECORDED"
    NODE_STARTED = "NODE_STARTED"
    NODE_PASSED = "NODE_PASSED"
    NODE_FAILED = "NODE_FAILED"
    NODE_SKIPPED = "NODE_SKIPPED"
    NODE_BLOCKED = "NODE_BLOCKED"
    NODE_RECOVERED = "NODE_RECOVERED"
    NODE_RETRY_SCHEDULED = "NODE_RETRY_SCHEDULED"
    NODE_RECONCILED = "NODE_RECONCILED"
    ACTION_STARTED = "ACTION_STARTED"
    ACTION_SUCCEEDED = "ACTION_SUCCEEDED"
    ACTION_FAILED = "ACTION_FAILED"
    ACTION_UNKNOWN = "ACTION_UNKNOWN"
    ACTION_CONFIRMED = "ACTION_CONFIRMED"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    POSTCONDITION_FAILED = "POSTCONDITION_FAILED"
    DECISION_REQUESTED = "DECISION_REQUESTED"
    DECISION_GRANTED = "DECISION_GRANTED"
    DECISION_DENIED = "DECISION_DENIED"
    DECISION_EXPIRED = "DECISION_EXPIRED"
    DECISION_CANCELLED = "DECISION_CANCELLED"
    DECISION_DEFERRED = "DECISION_DEFERRED"
    DECISION_INFO_PROVIDED = "DECISION_INFO_PROVIDED"
    DECISION_EVIDENCE_CHANGED = "DECISION_EVIDENCE_CHANGED"
    DECISION_ROLE_DENIED = "DECISION_ROLE_DENIED"
    BLOCKER_DETECTED = "BLOCKER_DETECTED"
    BLOCKER_RESOLVED = "BLOCKER_RESOLVED"
    AUTHORIZATION_PRE_GRANTED = "AUTHORIZATION_PRE_GRANTED"
    CHECKPOINT_SAVED = "CHECKPOINT_SAVED"
    FAULT_INJECTED = "FAULT_INJECTED"
    INVARIANT_EVALUATED = "INVARIANT_EVALUATED"
    RECOVERY_STARTED = "RECOVERY_STARTED"
    RECOVERY_APPLIED = "RECOVERY_APPLIED"
    RECOVERY_VERIFIED = "RECOVERY_VERIFIED"
    RECOVERY_PARTIAL = "RECOVERY_PARTIAL"
    RECOVERY_FAILED = "RECOVERY_FAILED"
    RECOVERY_REFUSED = "RECOVERY_REFUSED"
    RECOVERY_MANUAL_INTERVENTION = "RECOVERY_MANUAL_INTERVENTION"
    RUN_VERIFIED = "RUN_VERIFIED"
    RUN_FAILED = "RUN_FAILED"
    RUN_CANCELLED = "RUN_CANCELLED"
    RUN_TIMED_OUT = "RUN_TIMED_OUT"


class ActorKind(StrEnum):
    SYSTEM = "SYSTEM"
    AGENT = "AGENT"
    HUMAN = "HUMAN"
    POLICY = "POLICY"


class FaultType(StrEnum):
    """Boundary faults. Every fault is deterministic and local to the sandbox.

    The names are stable API values: scenarios store them, regression specs pin
    them, and the report groups by them.
    """

    # Response faults: the request reached the operation.
    TIMEOUT = "TIMEOUT"
    RESPONSE_LOST = "RESPONSE_LOST"
    HTTP_500 = "HTTP_500"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    MISSING_FIELD = "MISSING_FIELD"
    DUPLICATE_RESPONSE = "DUPLICATE_RESPONSE"
    STALE_READ = "STALE_READ"
    LATENCY = "LATENCY"
    PARTIAL_COMMIT = "PARTIAL_COMMIT"
    POSTCONDITION_FAILURE = "POSTCONDITION_FAILURE"

    # Request faults: the operation must not have executed.
    TIMEOUT_BEFORE_EXECUTION = "TIMEOUT_BEFORE_EXECUTION"
    NETWORK_DROP = "NETWORK_DROP"
    RATE_LIMITED = "RATE_LIMITED"
    TRANSIENT_ERROR = "TRANSIENT_ERROR"

    # Data and coordination faults.
    CONTRADICTORY_TOTALS = "CONTRADICTORY_TOTALS"
    DELAYED_APPROVAL = "DELAYED_APPROVAL"

    # Browser faults.
    SELECTOR_DRIFT = "SELECTOR_DRIFT"


class ScenarioStatus(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    INJECTING = "INJECTING"
    OBSERVING = "OBSERVING"
    COMPLETED = "COMPLETED"


class InvariantKind(StrEnum):
    POSTCONDITION = "POSTCONDITION"
    HISTORY = "HISTORY"
    CROSS_ACTION = "CROSS_ACTION"
    TERMINAL_STATE = "TERMINAL_STATE"
    AUTHORIZATION = "AUTHORIZATION"
    CHECKPOINT = "CHECKPOINT"
    POLICY = "POLICY"
    IMPLIES = "IMPLIES"
    COMPARE = "COMPARE"


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    MAJOR = "MAJOR"
    MINOR = "MINOR"


class InvariantVerdict(StrEnum):
    SATISFIED = "SATISFIED"
    VIOLATED = "VIOLATED"
    INCONCLUSIVE = "INCONCLUSIVE"


class DecisionStatus(StrEnum):
    REQUESTED = "REQUESTED"
    NOTIFIED = "NOTIFIED"
    AUTHORIZED = "AUTHORIZED"
    DENIED = "DENIED"
    DEFERRED = "DEFERRED"
    INFO_PROVIDED = "INFO_PROVIDED"
    EXPIRED = "EXPIRED"
    APPLIED = "APPLIED"
    CANCELLED = "CANCELLED"


class BlockerKind(StrEnum):
    """Why a node cannot proceed.

    Every blocker maps to exactly one remediation shape, which is what lets the
    inbox ask the smallest useful question instead of dumping a failure log.
    """

    MISSING_INPUT = "MISSING_INPUT"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    PERMISSION_REQUIRED = "PERMISSION_REQUIRED"
    HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    RECOVERY_PREREQUISITE_MISSING = "RECOVERY_PREREQUISITE_MISSING"


class BlockerStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    SUPERSEDED = "SUPERSEDED"


class ActorRole(StrEnum):
    OPERATOR = "OPERATOR"
    REVIEWER = "REVIEWER"
    FINANCE_APPROVER = "FINANCE_APPROVER"
    ADMIN = "ADMIN"


class DecisionAction(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    PROVIDE_INFO = "PROVIDE_INFO"
    DEFER = "DEFER"


# The role a blocker requires by default. A workflow may widen this in its
# declaration, but it can never narrow it below the default.
BLOCKER_DEFAULT_ROLE: dict[BlockerKind, ActorRole] = {
    BlockerKind.MISSING_INPUT: ActorRole.OPERATOR,
    BlockerKind.VALIDATION_FAILED: ActorRole.REVIEWER,
    BlockerKind.CONFLICTING_EVIDENCE: ActorRole.FINANCE_APPROVER,
    BlockerKind.PERMISSION_REQUIRED: ActorRole.ADMIN,
    BlockerKind.HUMAN_APPROVAL_REQUIRED: ActorRole.FINANCE_APPROVER,
    BlockerKind.TOOL_UNAVAILABLE: ActorRole.ADMIN,
    BlockerKind.UNKNOWN_OUTCOME: ActorRole.REVIEWER,
    BlockerKind.RECOVERY_PREREQUISITE_MISSING: ActorRole.OPERATOR,
}


class CheckpointKind(StrEnum):
    PRE_ACTION = "PRE_ACTION"
    POST_ACTION = "POST_ACTION"
    SAFE_POINT = "SAFE_POINT"
    # Captured after a failed/ambiguous attempt so recovery can inspect what the
    # attempt actually did without re-executing it.
    ATTEMPT_STATE = "ATTEMPT_STATE"


class RecoveryStrategy(StrEnum):
    RESUME = "RESUME"
    COMPENSATE = "COMPENSATE"
    REPLAY = "REPLAY"
    ESCALATE = "ESCALATE"
    # No automated inverse exists or is allowed; a human must act.
    MANUAL = "MANUAL"


class RecoveryStatus(StrEnum):
    PLANNED = "PLANNED"
    APPLYING = "APPLYING"
    APPLIED = "APPLIED"
    VERIFIED = "VERIFIED"
    # Some compensating steps applied, but the contract does not hold.
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    # The operation is irreversible or has no tested inverse: a human must act.
    MANUAL_INTERVENTION = "MANUAL_INTERVENTION"
    ESCALATED = "ESCALATED"


class RegressionStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PASSING = "PASSING"
    FAILING = "FAILING"
    QUARANTINED = "QUARANTINED"


class EvidenceKind(StrEnum):
    SCREENSHOT = "SCREENSHOT"
    DOM_SNAPSHOT = "DOM_SNAPSHOT"
    HTTP_TRACE = "HTTP_TRACE"
    LOG = "LOG"
    INVARIANT_REPORT = "INVARIANT_REPORT"
    DECISION_BUNDLE = "DECISION_BUNDLE"
    CAPTURE_ACTION = "CAPTURE_ACTION"
    REPLAY_STEP = "REPLAY_STEP"
    SELECTOR_REPORT = "SELECTOR_REPORT"


class CaptureStatus(StrEnum):
    """Lifecycle of a controlled recording session."""

    RECORDING = "RECORDING"
    PAUSED = "PAUSED"
    REVIEW = "REVIEW"
    VALIDATED = "VALIDATED"
    SAVED = "SAVED"
    FAILED = "FAILED"


class CapturedActionStatus(StrEnum):
    CAPTURED = "CAPTURED"
    AMBIGUOUS = "AMBIGUOUS"
    RESOLVED = "RESOLVED"
    VALIDATED = "VALIDATED"
    UNRESOLVED = "UNRESOLVED"
    REJECTED = "REJECTED"
