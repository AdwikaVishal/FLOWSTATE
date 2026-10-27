"""SQLAlchemy models.

Timestamps are stored as naive UTC and returned as timezone-aware UTC, so the
same code behaves identically on SQLite and PostgreSQL.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dict[str, Any]: JSON,
        list[dict[str, Any]]: JSON,
    }


def _ts() -> Mapped[datetime]:
    return mapped_column(UTCDateTime, nullable=False)


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    owner: Mapped[str] = mapped_column(String(120), nullable=False, default="local-operator")
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    seed_batch: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = _ts()
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (UniqueConstraint("slug", name="uq_workflows_slug"),)


class WorkflowVersion(Base):
    __tablename__ = "workflow_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="DRAFT")
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    parent_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    redact_paths: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = _ts()
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("workflow_id", "version", name="uq_version_number"),
        Index("ix_versions_workflow", "workflow_id"),
    )


class ActionDefinition(Base):
    """A graph node.

    The table keeps its historical name so existing versions, serializers and
    the console continue to work. ``node_type`` and ``depends_on`` turn the
    ordered list into a typed DAG.
    """

    __tablename__ = "action_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflow_versions.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    target: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    preconditions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    postconditions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    side_effect_class: Mapped[str] = mapped_column(String(24), nullable=False, default="READ_ONLY")
    requires_authorization: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=5000)

    node_key: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    node_type: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTION")
    depends_on: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="SIMULATED")
    retry_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    failure_handler: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    redact_paths: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    __table_args__ = (
        UniqueConstraint("workflow_version_id", "ordinal", name="uq_action_ordinal"),
        UniqueConstraint("workflow_version_id", "node_key", name="uq_action_node_key"),
        Index("ix_actions_version_node", "workflow_version_id", "node_key"),
    )


class Invariant(Base):
    __tablename__ = "invariants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflow_versions.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    expression: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="MAJOR")


class ExecutionRun(Base):
    __tablename__ = "execution_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    workflow_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING")
    verdict: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="MANUAL")
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    driver: Mapped[str] = mapped_column(String(32), nullable=False, default="simulated-v1")
    seed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fault_scenario_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    parent_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor: Mapped[str] = mapped_column(String(120), nullable=False, default="local-operator")
    seed_batch: Mapped[str | None] = mapped_column(String(64), nullable=True)
    capture_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    outputs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        Index("ix_runs_workflow", "workflow_id"),
        Index("ix_runs_created", "created_at"),
    )


class ExecutionJob(Base):
    """Persisted queue entry for an asynchronously executed run."""

    __tablename__ = "execution_jobs"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("execution_runs.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="QUEUED")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = _ts()
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ExecutionEvent(Base):
    __tablename__ = "execution_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("execution_runs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(120), nullable=False, default="system")
    action_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    node_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_event_seq"),
        Index("ix_events_run", "run_id"),
    )


class ExecutionNodeState(Base):
    """Per-run node projection. The event log remains the source of truth."""

    __tablename__ = "execution_node_states"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("execution_runs.id", ondelete="CASCADE"), nullable=False
    )
    node_key: Mapped[str] = mapped_column(String(80), nullable=False)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    output: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reconciled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    checkpoint_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "node_key", name="uq_node_state_key"),
        Index("ix_node_states_run", "run_id"),
    )


class FaultScenario(Base):
    __tablename__ = "fault_scenarios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(String(36), nullable=False)
    workflow_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    target_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    fault_type: Mapped[str] = mapped_column(String(24), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    input_fixtures: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    seed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    seed_batch: Mapped[str | None] = mapped_column(String(64), nullable=True)
    baseline_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = _ts()


class EvidenceRecord(Base):
    __tablename__ = "evidence_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    uri: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False, default="application/json")
    meta_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    captured_at: Mapped[datetime] = _ts()


class HumanDecision(Base):
    __tablename__ = "human_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    node_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    recommended_option: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    policy_citation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="REQUESTED")
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    seed_batch: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_at: Mapped[datetime] = _ts()
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    expires_at: Mapped[datetime] = _ts()
    capability_token_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Blocker-aware decision fields (additive, see the SQLite migrations).
    blocker_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str | None] = mapped_column(String(40), nullable=True)
    required_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resume_conditions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    evidence_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provided_input: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    deferred_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    decided_role: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Blocker(Base):
    """A persisted reason a run node cannot proceed."""

    __tablename__ = "run_blockers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("execution_runs.id", ondelete="CASCADE"), nullable=False
    )
    node_key: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    action_ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    required_role: Mapped[str] = mapped_column(String(32), nullable=False, default="ADMIN")
    resume_conditions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    input_spec: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    evidence_spec: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    decision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    created_at: Mapped[datetime] = _ts()
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (Index("ix_blockers_run", "run_id"),)


class BlockerEvidence(Base):
    """One persisted fact supporting a blocker.

    Values are read from run state or the audit log at detection time. Nothing in
    this table is model-generated prose; ``label`` and ``relation`` come from the
    workflow declaration.
    """

    __tablename__ = "blocker_evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    blocker_id: Mapped[str] = mapped_column(String(36), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(24), nullable=False, default="STATE_FIELD")
    source_path: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    source_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    relation: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    value_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    validation: Mapped[str] = mapped_column(String(16), nullable=False, default="INFO")
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    observed_at: Mapped[datetime] = _ts()
    fresh_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    @property
    def record_ref(self) -> str | None:
        """The source record this fact came from (e.g. PO-8841)."""
        return self.source_ref

    __table_args__ = (Index("ix_blocker_evidence_blocker", "blocker_id"),)


class DecisionRecord(Base):
    """Immutable history entry for every decision action."""

    __tablename__ = "decision_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    decision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    actor: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (Index("ix_decision_records_decision", "decision_id"),)


class CapabilityToken(Base):
    __tablename__ = "capability_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    decision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    signature: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[datetime] = _ts()
    expires_at: Mapped[datetime] = _ts()
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (Index("ix_tokens_decision", "decision_id"),)


class Checkpoint(Base):
    __tablename__ = "checkpoints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    event_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    action_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (Index("ix_checkpoints_run", "run_id"),)


class RecoveryAttempt(Base):
    __tablename__ = "recovery_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    checkpoint_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    strategy: Mapped[str] = mapped_column(String(16), nullable=False, default="RESUME")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PLANNED")
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    verification_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    started_at: Mapped[datetime] = _ts()
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    # Recovery contract and evidence (additive, see the SQLite migrations).
    action_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    node_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    recovery_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    contract: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    before_state_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    after_state_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    evidence_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manual_note: Mapped[str] = mapped_column(Text, nullable=False, default="")


class RegressionTest(Base):
    __tablename__ = "regression_tests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    fault_scenario_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    invariant_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    last_result: Mapped[str | None] = mapped_column(String(24), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = _ts()

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reviewed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class RegressionResult(Base):
    """One replay of a regression test against one workflow version."""

    __tablename__ = "regression_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    test_id: Mapped[str] = mapped_column(String(36), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(36), nullable=False)
    workflow_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    violated_invariant: Mapped[str | None] = mapped_column(String(240), nullable=True)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (Index("ix_regression_results_test", "test_id"),)


class InvariantVerdictRecord(Base):
    __tablename__ = "invariant_verdicts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("execution_runs.id", ondelete="CASCADE"), nullable=False
    )
    invariant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    invariant_name: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    observed: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (Index("ix_verdicts_run", "run_id"),)


class CaptureSession(Base):
    """A controlled recording of interactions with the demo site."""

    __tablename__ = "capture_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="RECORDING")
    demo_url: Mapped[str] = mapped_column(String(500), nullable=False)
    variant: Mapped[str] = mapped_column(String(32), nullable=False, default="default")
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    seed_batch: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str] = mapped_column(String(120), nullable=False, default="local-operator")
    workflow_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    workflow_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    validation: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = _ts()
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (Index("ix_captures_created", "created_at"),)


class CapturedAction(Base):
    """One reviewed candidate action derived from a recorded interaction."""

    __tablename__ = "captured_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("capture_sessions.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    selector: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    accessible_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    input_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_param: Mapped[str | None] = mapped_column(String(80), nullable=True)
    page_before: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    page_after: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    expected_result: Mapped[str] = mapped_column(Text, nullable=False, default="")
    observed_result: Mapped[str] = mapped_column(Text, nullable=False, default="")
    preconditions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    postconditions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    failure_conditions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    redacted_fields: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    ambiguous: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ambiguity_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="CAPTURED")
    resolution: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("session_id", "ordinal", name="uq_captured_action_ordinal"),
        Index("ix_captured_actions_session", "session_id"),
    )


class IdempotencyRecord(Base):
    """Stored response for a caller-supplied Idempotency-Key.

    The adapter uses this to make retries of consequential operations return the
    original result instead of creating a second run.
    """

    __tablename__ = "idempotency_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(200), nullable=False)
    key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    created_at: Mapped[datetime] = _ts()
    expires_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("scope", "key", name="uq_idempotency_scope_key"),
        Index("ix_idempotency_expires", "expires_at"),
    )


class SchemaMeta(Base):
    __tablename__ = "schema_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(200), nullable=False)
