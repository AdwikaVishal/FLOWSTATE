"""Fault scenario lifecycle and chaos runs."""

from __future__ import annotations

from typing import Any

from flowstate.clock import Clock
from flowstate.db.models import ExecutionRun, FaultScenario, Workflow
from flowstate.domain.enums import (
    EventType,
    FaultType,
    Origin,
    RunTrigger,
    ScenarioStatus,
)
from flowstate.errors import NotFoundError, ValidationError
from flowstate.ids import IdFactory
from flowstate.repositories import UnitOfWork
from flowstate.services.audit import AuditWriter
from flowstate.services.execution import ExecuteOptions, ExecutionEngine


def create_scenario(
    uow: UnitOfWork,
    ids: IdFactory,
    clock: Clock,
    *,
    workflow: Workflow,
    name: str,
    description: str,
    target_ordinal: int,
    fault_type: FaultType,
    parameters: dict[str, Any] | None = None,
    input_fixtures: dict[str, Any] | None = None,
    seed: int = 0,
    origin: Origin = Origin.LIVE,
    seed_batch: str | None = None,
) -> FaultScenario:
    version_id = workflow.current_version_id
    if version_id is None:
        raise ValidationError(
            "The workflow has no published version to target.", code="NO_PUBLISHED_VERSION"
        )
    actions = list(uow.actions.list(workflow_version_id=version_id))
    target = next((item for item in actions if item.ordinal == target_ordinal), None)
    if target is None:
        raise ValidationError(
            f"Action ordinal {target_ordinal} does not exist in this version.",
            code="UNKNOWN_TARGET_ACTION",
        )
    _validate_target(fault_type, target)

    scenario = FaultScenario(
        id=ids.new(),
        workflow_id=workflow.id,
        workflow_version_id=version_id,
        name=name,
        description=description,
        target_ordinal=target_ordinal,
        fault_type=str(fault_type),
        parameters=parameters or {},
        input_fixtures=input_fixtures or {},
        seed=seed,
        status=str(ScenarioStatus.READY),
        origin=str(origin),
        seed_batch=seed_batch,
        created_at=clock.now(),
    )
    uow.scenarios.add(scenario)
    uow.flush()
    return scenario


def _validate_target(fault_type: FaultType, target: Any) -> None:
    channel = str(getattr(target, "channel", "SIMULATED") or "SIMULATED")
    node_type = str(getattr(target, "node_type", "ACTION"))
    if fault_type == FaultType.SELECTOR_DRIFT and channel != "BROWSER":
        raise ValidationError(
            "Selector drift can only target a BROWSER action.",
            code="FAULT_REQUIRES_BROWSER_ACTION",
        )
    if channel == "BROWSER" and fault_type != FaultType.SELECTOR_DRIFT:
        raise ValidationError(
            "A browser action currently supports only the SELECTOR_DRIFT fault.",
            code="FAULT_UNSUPPORTED_FOR_BROWSER",
        )
    if fault_type == FaultType.DELAYED_APPROVAL:
        requires_auth = bool(getattr(target, "requires_authorization", False))
        if node_type != "HUMAN_DECISION" and not requires_auth:
            raise ValidationError(
                "Delayed approval can only target a human decision or an "
                "authorization-gated action.",
                code="FAULT_REQUIRES_DECISION",
            )


def get_scenario(uow: UnitOfWork, scenario_id: str) -> FaultScenario:
    scenario = uow.scenarios.get(scenario_id)
    if scenario is None:
        raise NotFoundError(f"Scenario {scenario_id} was not found.", code="SCENARIO_NOT_FOUND")
    return scenario


def run_scenario(
    uow: UnitOfWork,
    clock: Clock,
    ids: IdFactory,
    engine: ExecutionEngine,
    *,
    scenario_id: str,
    actor: str = "local-operator",
) -> str:
    scenario = get_scenario(uow, scenario_id)
    workflow = uow.workflows.get(scenario.workflow_id)
    version = uow.versions.get(scenario.workflow_version_id)
    if workflow is None or version is None:
        raise NotFoundError(
            "The workflow for this scenario no longer exists.", code="WORKFLOW_NOT_FOUND"
        )

    actions = list(uow.actions.list(workflow_version_id=version.id, order_by="ordinal"))
    invariants = list(uow.invariants.list(workflow_version_id=version.id))

    scenario.status = str(ScenarioStatus.READY)
    audit = AuditWriter(uow, clock, ids)
    # A scenario may deliberately withhold the sandbox pre-authorization so the
    # run blocks on the human decision it is meant to demonstrate.
    pre_authorize = bool((scenario.parameters or {}).get("pre_authorize", True))
    gated = (
        {item.ordinal for item in actions if item.requires_authorization}
        if pre_authorize
        else set()
    )
    if scenario.fault_type == FaultType.DELAYED_APPROVAL:
        gated.discard(scenario.target_ordinal)
    run = engine.start_run(
        workflow=workflow,
        version=version,
        actions=actions,
        invariants=invariants,
        options=ExecuteOptions(
            trigger=RunTrigger.CHAOS,
            origin=Origin(scenario.origin),
            seed=scenario.seed,
            scenario=scenario,
            actor=actor,
            authorized_ordinals=gated,
            inputs=dict(scenario.input_fixtures or {}),
            pre_authorization_reason=(
                "Chaos runs execute in the sandbox only. The operator launching this "
                "scenario pre-authorizes its authorization-gated actions."
                if pre_authorize
                else None
            ),
        ),
    )
    audit.append(
        run.id,
        EventType.FAULT_INJECTED,
        action_ordinal=scenario.target_ordinal,
        payload={
            "scenario_id": scenario.id,
            "fault_type": scenario.fault_type,
            "armed": True,
            "note": "scenario armed; injection occurs at the target action",
        },
    )
    scenario.last_run_id = run.id
    uow.flush()
    return run.id


def _duration_ms(run: ExecutionRun) -> int:
    if run.started_at is None:
        return 0
    end = run.ended_at or run.started_at
    return max(int((end - run.started_at).total_seconds() * 1000), 0)


def _invariant_order(uow: UnitOfWork, version_id: str) -> dict[str, int]:
    rows = list(uow.invariants.list(workflow_version_id=version_id))
    return {item.id: index for index, item in enumerate(rows)}


def _verdict_payload(record: Any) -> dict[str, Any]:
    return {
        "invariant_id": record.invariant_id,
        "invariant_name": record.invariant_name,
        "severity": record.severity,
        "verdict": record.verdict,
        "detail": record.detail,
        "observed": record.observed or {},
    }


def _run_outcome(uow: UnitOfWork, run: ExecutionRun, order: dict[str, int]) -> dict[str, Any]:
    verdicts = sorted(
        uow.verdicts.list(run_id=run.id),
        key=lambda item: order.get(item.invariant_id, 10_000),
    )
    violated = [item for item in verdicts if item.verdict == "VIOLATED"]
    first = _verdict_payload(violated[0]) if violated else None
    events = list(uow.events.read(run.id, limit=500))
    node_states = list(uow.node_states.list(run_id=run.id))
    reconciled = sum(1 for item in node_states if item.reconciled)
    return {
        "run_id": run.id,
        "status": run.status,
        "verdict": run.verdict,
        "driver": run.driver,
        "seed": run.seed,
        "inputs": dict(run.inputs or {}),
        "failure_code": run.failure_code,
        "failure_message": run.failure_message,
        "failure_ordinal": run.failure_ordinal,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "duration_ms": _duration_ms(run),
        "event_count": len(events),
        "reconciled_actions": reconciled,
        "events": [
            {
                "seq": item.seq,
                "type": item.type,
                "node_key": item.node_key,
                "action_ordinal": item.action_ordinal,
                "actor": item.actor,
                "actor_id": item.actor_id,
                "recorded_at": item.recorded_at,
            }
            for item in events
        ],
        "invariant_verdicts": [_verdict_payload(item) for item in verdicts],
        "first_violated_invariant": first,
    }


def _evidence_payload(record: Any) -> dict[str, Any]:
    from flowstate.services.evidence import EvidenceStore

    return {
        "id": record.id,
        "run_id": record.run_id,
        "kind": record.kind,
        "uri": record.uri,
        "sha256": record.sha256,
        "mime_type": record.mime_type,
        "captured_at": record.captured_at,
        "verified": EvidenceStore.verify(record),
    }


def _failure_action(
    uow: UnitOfWork, run: ExecutionRun, scenario: FaultScenario
) -> dict[str, Any] | None:
    ordinal = run.failure_ordinal
    if ordinal is None:
        ordinal = scenario.target_ordinal
    actions = list(uow.actions.list(workflow_version_id=run.workflow_version_id))
    action = next((item for item in actions if item.ordinal == ordinal), None)
    node_states = list(uow.node_states.list(run_id=run.id))
    state = next((item for item in node_states if item.ordinal == ordinal), None)
    if action is None and state is None:
        return None
    evidence = list(uow.evidence.list(run_id=run.id))
    return {
        "ordinal": ordinal,
        "node_key": state.node_key if state else getattr(action, "node_key", None),
        "label": action.label if action is not None else f"Node {ordinal}",
        "node_type": state.node_type if state else getattr(action, "node_type", "ACTION"),
        "status": state.status if state else None,
        "error_class": state.error_class if state else None,
        "error_code": state.error_code if state else run.failure_code,
        "error_message": state.error_message if state else run.failure_message,
        "output": dict(state.output or {}) if state else {},
        "evidence": [_evidence_payload(item) for item in evidence],
    }


def _recovery(uow: UnitOfWork, run: ExecutionRun) -> dict[str, Any]:
    attempts = sorted(
        uow.recoveries.list(run_id=run.id),
        key=lambda item: item.started_at,
    )
    latest = attempts[-1] if attempts else None
    return {
        "attempted": bool(attempts),
        "verified": any(item.status == "VERIFIED" for item in attempts),
        "attempt_id": latest.id if latest else None,
        "status": latest.status if latest else None,
        "detail": latest.detail if latest else None,
    }


def _failed_safely(outcome: dict[str, Any]) -> bool:
    violated = bool(outcome["first_violated_invariant"])
    status = str(outcome["status"])
    verdict = str(outcome["verdict"])
    if violated:
        return status != "SUCCEEDED" and verdict != "VERIFIED"
    if status == "SUCCEEDED":
        return verdict == "VERIFIED"
    return status in {
        "FAILED",
        "AWAITING_RECOVERY",
        "AWAITING_DECISION",
        "TIMED_OUT",
        "CANCELLED",
        "VERIFYING",
    }


def _evaluation(baseline: dict[str, Any], fault: dict[str, Any]) -> str:
    baseline_verified = baseline["status"] == "SUCCEEDED" and baseline["verdict"] == "VERIFIED"
    if not baseline_verified:
        return "BASELINE_NOT_VERIFIED"
    verdicts = [item["verdict"] for item in fault["invariant_verdicts"]]
    if any(item == "INCONCLUSIVE" for item in verdicts):
        return "INCONCLUSIVE"
    if fault["first_violated_invariant"]:
        return "FAULT_DETECTED"
    if fault["status"] == "SUCCEEDED" and fault["verdict"] == "VERIFIED":
        return "NO_FAULT_EFFECT"
    return "SAFE_FAILURE_WITHOUT_VIOLATION"


def build_report(uow: UnitOfWork, clock: Clock, scenario: FaultScenario) -> dict[str, Any]:
    if not scenario.last_run_id:
        raise NotFoundError("This scenario has not been evaluated yet.", code="NO_CHAOS_REPORT")
    baseline_run = uow.runs.get(scenario.baseline_run_id) if scenario.baseline_run_id else None
    fault_run = uow.runs.get(scenario.last_run_id)
    if fault_run is None:
        raise NotFoundError("The fault run no longer exists.", code="RUN_NOT_FOUND")
    if baseline_run is None:
        raise NotFoundError(
            "This scenario has no baseline run. Evaluate it again.",
            code="NO_CHAOS_REPORT",
        )
    order = _invariant_order(uow, scenario.workflow_version_id)
    baseline = _run_outcome(uow, baseline_run, order)
    fault = _run_outcome(uow, fault_run, order)
    regression = uow.regressions.first(source_run_id=fault_run.id)
    return {
        "scenario_id": scenario.id,
        "scenario_name": scenario.name,
        "fault_type": scenario.fault_type,
        "target_ordinal": scenario.target_ordinal,
        "seed": scenario.seed,
        "parameters": dict(scenario.parameters or {}),
        "input_fixtures": dict(scenario.input_fixtures or {}),
        "generated_at": clock.now(),
        "baseline": baseline,
        "fault": fault,
        "first_violated_invariant": fault["first_violated_invariant"],
        "failure_action": _failure_action(uow, fault_run, scenario),
        "failed_safely": _failed_safely(fault),
        "recovery": _recovery(uow, fault_run),
        "regression_test_id": regression.id if regression else None,
        "evaluation": _evaluation(baseline, fault),
        "baseline_verified": baseline["status"] == "SUCCEEDED"
        and baseline["verdict"] == "VERIFIED",
        "scenario_status": scenario.status,
    }


def evaluate_scenario(
    uow: UnitOfWork,
    clock: Clock,
    ids: IdFactory,
    engine: ExecutionEngine,
    *,
    scenario_id: str,
    actor: str = "local-operator",
) -> dict[str, Any]:
    """Run an authorized baseline and the fault-injected scenario.

    Both runs use the same workflow version, seed and input fixtures. Nothing is
    declared a pass: the report is derived from persisted verdicts, run status,
    recovery attempts and regression records.
    """
    scenario = get_scenario(uow, scenario_id)
    workflow = uow.workflows.get(scenario.workflow_id)
    version = uow.versions.get(scenario.workflow_version_id)
    if workflow is None or version is None:
        raise NotFoundError(
            "The workflow for this scenario no longer exists.", code="WORKFLOW_NOT_FOUND"
        )
    actions = list(uow.actions.list(workflow_version_id=version.id, order_by="ordinal"))
    invariants = list(uow.invariants.list(workflow_version_id=version.id))
    pre_authorize = bool((scenario.parameters or {}).get("pre_authorize", True))
    gated = (
        {item.ordinal for item in actions if item.requires_authorization}
        if pre_authorize
        else set()
    )
    fixtures = dict(scenario.input_fixtures or {})

    baseline = engine.start_run(
        workflow=workflow,
        version=version,
        actions=actions,
        invariants=invariants,
        options=ExecuteOptions(
            trigger=RunTrigger.CHAOS,
            origin=Origin(scenario.origin),
            seed=scenario.seed,
            scenario=None,
            actor=actor,
            authorized_ordinals=gated,
            inputs=fixtures,
            pre_authorization_reason=(
                "Chaos baseline: the operator pre-authorizes gated actions so the "
                "comparison measures the fault, not the approval step."
            ),
        ),
    )
    scenario.baseline_run_id = baseline.id

    fault_authorized = set(gated)
    if scenario.fault_type == FaultType.DELAYED_APPROVAL:
        fault_authorized.discard(scenario.target_ordinal)
    scenario.status = str(ScenarioStatus.READY)
    fault = engine.start_run(
        workflow=workflow,
        version=version,
        actions=actions,
        invariants=invariants,
        options=ExecuteOptions(
            trigger=RunTrigger.CHAOS,
            origin=Origin(scenario.origin),
            seed=scenario.seed,
            scenario=scenario,
            actor=actor,
            authorized_ordinals=fault_authorized,
            inputs=fixtures,
            pre_authorization_reason=(
                (
                    "Chaos run: gated actions are pre-authorized except the action "
                    "targeted by a delayed-approval fault."
                    if scenario.fault_type == FaultType.DELAYED_APPROVAL
                    else "Chaos run: the operator pre-authorizes gated actions."
                )
                if pre_authorize
                else None
            ),
        ),
    )
    scenario.last_run_id = fault.id
    uow.flush()
    return build_report(uow, clock, scenario)


def report_scenario(uow: UnitOfWork, clock: Clock, scenario_id: str) -> dict[str, Any]:
    return build_report(uow, clock, get_scenario(uow, scenario_id))
