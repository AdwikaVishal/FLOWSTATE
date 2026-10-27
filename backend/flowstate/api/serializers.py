"""ORM to API-schema mapping.

Kept in one place so a route never performs business logic while shaping a
response, and so the ``demo`` flag is impossible to forget.
"""

from __future__ import annotations

from flowstate.db.models import (
    ActionDefinition,
    Blocker,
    CapturedAction,
    CaptureSession,
    EvidenceRecord,
    ExecutionRun,
    FaultScenario,
    HumanDecision,
    Invariant,
    InvariantVerdictRecord,
    Workflow,
    WorkflowVersion,
)
from flowstate.domain.enums import ActorRole, NodeStatus, Origin
from flowstate.domain.graph import GraphValidationError, build_graph, graph_summary
from flowstate.domain.schemas import (
    ActionOut,
    BlockerEvidenceOut,
    BlockerOut,
    CapturedActionOut,
    CaptureRunSummary,
    CaptureSessionOut,
    DecisionHistoryOut,
    DecisionOut,
    EventOut,
    EvidenceOut,
    InvariantOut,
    NodeStateOut,
    RunDetail,
    RunSummary,
    ScenarioOut,
    VerdictOut,
    VersionDetail,
    VersionSummary,
    WorkflowDetail,
    WorkflowSummary,
)
from flowstate.repositories import UnitOfWork
from flowstate.services import blockers as blocker_service
from flowstate.services import decisions as decision_service
from flowstate.services.audit import verify_chain
from flowstate.services.evidence import EvidenceStore


def action_out(action: ActionDefinition) -> ActionOut:
    return ActionOut.model_validate(action)


def invariant_out(invariant: Invariant) -> InvariantOut:
    return InvariantOut.model_validate(invariant)


def version_summary(uow: UnitOfWork, version: WorkflowVersion) -> VersionSummary:
    return VersionSummary(
        id=version.id,
        workflow_id=version.workflow_id,
        version=version.version,
        status=version.status,
        label=version.label,
        content_hash=version.content_hash,
        origin=version.origin,
        created_at=version.created_at,
        published_at=version.published_at,
        action_count=uow.actions.count(workflow_version_id=version.id),
        invariant_count=uow.invariants.count(workflow_version_id=version.id),
        input_schema=version.input_schema or {},
        output_schema=version.output_schema or {},
        redact_paths=list(version.redact_paths or []),
    )


def version_detail(uow: UnitOfWork, version: WorkflowVersion) -> VersionDetail:
    base = version_summary(uow, version).model_dump()
    actions = sorted(
        uow.actions.list(workflow_version_id=version.id, order_by="ordinal"),
        key=lambda item: item.ordinal,
    )
    invariants = uow.invariants.list(workflow_version_id=version.id)
    return VersionDetail(
        **base,
        actions=[action_out(item) for item in actions],
        invariants=[invariant_out(item) for item in invariants],
    )


def workflow_summary(uow: UnitOfWork, workflow: Workflow) -> WorkflowSummary:
    versions = list(uow.versions.list(workflow_id=workflow.id))
    latest = max(versions, key=lambda item: item.version) if versions else None
    return WorkflowSummary(
        id=workflow.id,
        slug=workflow.slug,
        name=workflow.name,
        description=workflow.description,
        owner=workflow.owner,
        origin=workflow.origin,
        seed_batch=workflow.seed_batch,
        current_version_id=workflow.current_version_id,
        created_at=workflow.created_at,
        archived_at=workflow.archived_at,
        version_count=len(versions),
        latest_version=latest.version if latest else None,
        latest_status=latest.status if latest else None,
        run_count=uow.runs.count(workflow_id=workflow.id),
    )


def workflow_detail(uow: UnitOfWork, workflow: Workflow) -> WorkflowDetail:
    versions = sorted(
        uow.versions.list(workflow_id=workflow.id), key=lambda item: item.version, reverse=True
    )
    base = workflow_summary(uow, workflow).model_dump()
    return WorkflowDetail(
        **base,
        versions=[version_summary(uow, item) for item in versions],
    )


def run_summary(uow: UnitOfWork, run: ExecutionRun) -> RunSummary:
    workflow = uow.workflows.get(run.workflow_id)
    version = uow.versions.get(run.workflow_version_id)
    return RunSummary(
        id=run.id,
        workflow_id=run.workflow_id,
        capture_session_id=run.capture_session_id,
        workflow_name=workflow.name if workflow else "Unknown workflow",
        workflow_version_id=run.workflow_version_id,
        version=version.version if version else None,
        status=run.status,
        verdict=run.verdict,
        trigger=run.trigger,
        origin=run.origin,
        driver=run.driver,
        seed=run.seed,
        current_ordinal=run.current_ordinal,
        fault_scenario_id=run.fault_scenario_id,
        failure_code=run.failure_code,
        failure_message=run.failure_message,
        failure_ordinal=run.failure_ordinal,
        actor=run.actor,
        started_at=run.started_at,
        ended_at=run.ended_at,
        created_at=run.created_at,
        event_count=uow.events.count(run.id),
        demo=run.origin == Origin.DEMO,
        inputs=run.inputs or {},
        outputs=run.outputs or {},
    )


def run_detail(uow: UnitOfWork, run: ExecutionRun) -> RunDetail:
    base = run_summary(uow, run).model_dump()
    events = list(uow.events.read(run.id))
    chain = verify_chain(events)
    actions = sorted(
        uow.actions.list(workflow_version_id=run.workflow_version_id, order_by="ordinal"),
        key=lambda item: item.ordinal,
    )
    verdicts = uow.verdicts.list(run_id=run.id)
    node_states = node_state_out(uow, run.id, actions)
    try:
        graph = graph_summary(build_graph(actions))
    except GraphValidationError:
        graph = {"nodes": len(actions), "edges": 0, "valid": False}
    checkpoints = list(uow.checkpoints.list(run_id=run.id))
    checkpoints.sort(key=lambda item: (item.action_ordinal, item.event_seq), reverse=True)
    final_state = (
        {
            key: value
            for key, value in (checkpoints[0].state_json or {}).items()
            if key != "__engine__"
        }
        if checkpoints
        else {}
    )
    return RunDetail(
        **base,
        actions=[action_out(item) for item in actions],
        events=[EventOut.model_validate(item) for item in events],
        verdicts=[VerdictOut.model_validate(item) for item in verdicts],
        evidence=[
            evidence_out(item) for item in uow.evidence.list(run_id=run.id, order_by="captured_at")
        ],
        chain_valid=chain.valid,
        node_states=node_states,
        graph=graph,
        final_state=final_state,
    )


def node_state_out(
    uow: UnitOfWork, run_id: str, actions: list[ActionDefinition]
) -> list[NodeStateOut]:
    """Project persisted node state; derive labels/dependencies from the version."""
    by_key = {item.node_key: item for item in actions}
    rows = sorted(
        uow.node_states.list(run_id=run_id),
        key=lambda item: (item.ordinal, item.node_key),
    )
    result: list[NodeStateOut] = []
    for row in rows:
        action = by_key.get(row.node_key)
        result.append(
            NodeStateOut(
                node_key=row.node_key,
                node_type=row.node_type,
                ordinal=row.ordinal,
                label=action.label if action else row.node_key,
                status=NodeStatus(row.status),
                attempt=row.attempt,
                max_attempts=row.max_attempts,
                started_at=row.started_at,
                ended_at=row.ended_at,
                duration_ms=row.duration_ms,
                error_class=row.error_class,
                error_code=row.error_code,
                error_message=row.error_message,
                output=row.output or {},
                idempotency_key=row.idempotency_key,
                reconciled=row.reconciled,
                depends_on=list(action.depends_on or []) if action else [],
                checkpoint_id=row.checkpoint_id,
            )
        )
    return result


def evidence_out(record: EvidenceRecord) -> EvidenceOut:
    return EvidenceOut(
        id=record.id,
        run_id=record.run_id,
        kind=record.kind,
        uri=record.uri,
        sha256=record.sha256,
        mime_type=record.mime_type,
        captured_at=record.captured_at,
        verified=EvidenceStore.verify(record),
    )


def blocker_out(uow: UnitOfWork, blocker: Blocker) -> BlockerOut:
    evidence = [
        BlockerEvidenceOut.model_validate(item)
        for item in uow.blocker_evidence.list(blocker_id=blocker.id)
    ]
    return BlockerOut(
        id=blocker.id,
        run_id=blocker.run_id,
        node_key=blocker.node_key,
        action_ordinal=blocker.action_ordinal,
        kind=blocker.kind,
        code=blocker.code,
        title=blocker.title,
        detail=blocker.detail,
        status=blocker.status,
        required_role=blocker.required_role,
        resume_conditions=blocker.resume_conditions,
        input_spec=blocker.input_spec,
        evidence_hash=blocker.evidence_hash,
        decision_id=blocker.decision_id,
        origin=blocker.origin,
        created_at=blocker.created_at,
        resolved_at=blocker.resolved_at,
        evidence=evidence,
    )


def decision_out(
    uow: UnitOfWork,
    decision: HumanDecision,
    *,
    actor_role: ActorRole | None = None,
) -> DecisionOut:
    run = uow.runs.get(decision.run_id)
    workflow_name = ""
    workflow_id = ""
    version_number: int | None = None
    run_status = ""
    if run is not None:
        run_status = run.status
        workflow = uow.workflows.get(run.workflow_id)
        if workflow is not None:
            workflow_name = workflow.name
            workflow_id = workflow.id
        version = uow.versions.get(run.workflow_version_id)
        version_number = version.version if version else None

    blocker = uow.blockers.get(decision.blocker_id) if decision.blocker_id else None
    evidence_items = (
        [
            BlockerEvidenceOut.model_validate(item)
            for item in uow.blocker_evidence.list(blocker_id=blocker.id)
        ]
        if blocker is not None
        else []
    )
    history = [
        DecisionHistoryOut.model_validate(item)
        for item in sorted(
            uow.decision_records.list(decision_id=decision.id),
            key=lambda row: row.created_at,
        )
    ]
    permitted = decision_service.permitted_actions(decision, blocker)
    role = actor_role or ActorRole.ADMIN
    can_decide = (
        decision.status in decision_service.OPEN_STATUSES
        and blocker_service.role_satisfies(role, decision.required_role or ActorRole.ADMIN)
    )

    return DecisionOut(
        id=decision.id,
        run_id=decision.run_id,
        action_ordinal=decision.action_ordinal,
        node_key=decision.node_key,
        question=decision.question,
        options=decision.options,
        recommended_option=decision.recommended_option,
        reason=decision.reason,
        policy_citation=decision.policy_citation,
        status=decision.status,
        origin=decision.origin,
        requested_at=decision.requested_at,
        decided_at=decision.decided_at,
        decided_by=decision.decided_by,
        decided_role=decision.decided_role,
        expires_at=decision.expires_at,
        deferred_until=decision.deferred_until,
        capability_token_id=decision.capability_token_id,
        workflow_name=workflow_name,
        workflow_id=workflow_id,
        version=version_number,
        run_status=run_status,
        kind=decision.kind,
        required_role=decision.required_role,
        resume_conditions=decision.resume_conditions,
        input_spec=blocker.input_spec if blocker is not None else [],
        provided_input=decision.provided_input,
        permitted_actions=permitted,
        can_decide=can_decide,
        actor_role=str(role),
        blocker=blocker_out(uow, blocker) if blocker is not None else None,
        evidence=[
            evidence_out(item)
            for item in uow.evidence.list(run_id=decision.run_id, kind="DECISION_BUNDLE")
        ],
        evidence_items=evidence_items,
        history=history,
    )


def scenario_out(uow: UnitOfWork, scenario: FaultScenario) -> ScenarioOut:
    workflow = uow.workflows.get(scenario.workflow_id)
    return ScenarioOut(
        id=scenario.id,
        workflow_id=scenario.workflow_id,
        workflow_name=workflow.name if workflow else "",
        workflow_version_id=scenario.workflow_version_id,
        name=scenario.name,
        description=scenario.description,
        target_ordinal=scenario.target_ordinal,
        fault_type=scenario.fault_type,
        parameters=scenario.parameters,
        input_fixtures=dict(scenario.input_fixtures or {}),
        seed=scenario.seed,
        status=scenario.status,
        origin=scenario.origin,
        baseline_run_id=scenario.baseline_run_id,
        last_run_id=scenario.last_run_id,
        created_at=scenario.created_at,
    )


def verdict_out(record: InvariantVerdictRecord) -> VerdictOut:
    return VerdictOut.model_validate(record)


def captured_action_out(action: CapturedAction) -> CapturedActionOut:
    return CapturedActionOut(
        id=action.id,
        session_id=action.session_id,
        ordinal=action.ordinal,
        kind=action.kind,
        label=action.label,
        selector=list(action.selector or []),
        accessible_name=action.accessible_name,
        input_value=action.input_value,
        input_param=action.input_param,
        page_before=dict(action.page_before or {}),
        page_after=dict(action.page_after or {}),
        expected_result=action.expected_result,
        observed_result=action.observed_result,
        preconditions=list(action.preconditions or []),
        postconditions=list(action.postconditions or []),
        failure_conditions=list(action.failure_conditions or []),
        redacted_fields=list(action.redacted_fields or []),
        ambiguous=action.ambiguous,
        ambiguity_reason=action.ambiguity_reason,
        status=action.status,
        resolution=dict(action.resolution or {}),
        created_at=action.created_at,
    )


def capture_summary(uow: UnitOfWork, session: CaptureSession) -> CaptureSessionOut:
    actions = sorted(
        uow.captured_actions.list(session_id=session.id, order_by="ordinal"),
        key=lambda item: item.ordinal,
    )
    runs = [
        CaptureRunSummary(
            id=run.id,
            status=run.status,
            verdict=run.verdict,
            driver=run.driver,
            created_at=run.created_at,
            failure_code=run.failure_code,
        )
        for run in uow.runs.list(
            capture_session_id=session.id, order_by="created_at", descending=True, limit=10
        )
    ]
    from flowstate.capture.recording import suggested_inputs

    action_dicts = [
        {
            "input_param": item.input_param,
            "input_value": item.input_value,
        }
        for item in actions
    ]
    unresolved = [
        item.ordinal
        for item in actions
        if item.status == "AMBIGUOUS" and not (item.resolution or {}).get("accepted")
    ]
    linked_version = (
        uow.versions.get(session.workflow_version_id) if session.workflow_version_id else None
    )
    return CaptureSessionOut(
        id=session.id,
        label=session.label,
        status=session.status,
        demo_url=session.demo_url,
        variant=session.variant,
        origin=session.origin,
        created_by=session.created_by,
        workflow_id=session.workflow_id,
        workflow_version_id=session.workflow_version_id,
        version_status=linked_version.status if linked_version else None,
        event_count=session.event_count,
        validation=dict(session.validation or {}),
        notes=session.notes,
        created_at=session.created_at,
        started_at=session.started_at,
        ended_at=session.ended_at,
        validated_at=session.validated_at,
        actions=[captured_action_out(item) for item in actions],
        suggested_inputs=suggested_inputs(action_dicts),
        unresolved_ambiguities=unresolved,
        recent_runs=runs,
    )
