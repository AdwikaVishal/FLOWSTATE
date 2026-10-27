"""Seed and reset the demonstration workspace.

Seeding is explicit and idempotent. Demo rows are tagged with
``origin=DEMO`` and a seed batch, and are physically separate from anything the
engine produces for a user, which is tagged ``origin=LIVE``.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import delete

from flowstate.clock import Clock, FrozenClock
from flowstate.config import Settings
from flowstate.db.models import (
    Blocker,
    BlockerEvidence,
    CapabilityToken,
    Checkpoint,
    DecisionRecord,
    EvidenceRecord,
    ExecutionEvent,
    ExecutionNodeState,
    ExecutionRun,
    FaultScenario,
    HumanDecision,
    InvariantVerdictRecord,
    RecoveryAttempt,
    RegressionTest,
    Workflow,
    WorkflowVersion,
)
from flowstate.demo import fixtures
from flowstate.domain.enums import (
    DecisionStatus,
    FaultType,
    Origin,
    RunTrigger,
    ScenarioStatus,
)
from flowstate.ids import IdFactory
from flowstate.repositories import UnitOfWork
from flowstate.sandbox import invoice as sandbox_invoice
from flowstate.services import regression as regression_service
from flowstate.services.evidence import EvidenceStore
from flowstate.services.execution import ExecuteOptions, ExecutionEngine
from flowstate.services.workflows import add_version, create_workflow, publish_version

logger = logging.getLogger(__name__)


def seed_demo(
    uow: UnitOfWork,
    clock: Clock,
    ids: IdFactory,
    settings: Settings,
    evidence: EvidenceStore,
) -> dict[str, Any]:
    now = clock.now()
    existing_approval = uow.workflows.first(slug=fixtures.DEMO_SLUG)
    existing_discrepancy = uow.workflows.first(slug=fixtures.DISCREPANCY_SLUG)
    existing_vendor = uow.workflows.first(slug=fixtures.VENDOR_SLUG)
    existing_processing = uow.workflows.first(slug=fixtures.PROCESSING_SLUG)

    created = False
    run_ids: list[str] = []
    decision_ids: list[str] = []
    scenario_ids: list[str] = []
    anchor_workflow: Workflow | None = existing_approval or existing_discrepancy
    anchor_version: WorkflowVersion | None = None

    if existing_processing is None:
        processing_workflow, _processing_version = _seed_processing(
            uow, clock, ids, settings, evidence, now
        )
        created = True
        run_ids.extend(_runs_for(uow, processing_workflow.id))
        scenario_ids.extend(_scenarios_for(uow, processing_workflow.id))

    if existing_vendor is None:
        vendor_workflow, _vendor_version = _seed_vendor(uow, clock, ids, settings, evidence, now)
        created = True
        run_ids.extend(_runs_for(uow, vendor_workflow.id))

    if existing_discrepancy is None:
        workflow, version = _seed_discrepancy(uow, clock, ids, settings, evidence, now)
        created = True
        discrepancy_runs = _runs_for(uow, workflow.id)
        run_ids.extend(discrepancy_runs)
        decision_ids.extend(_decisions_for(uow, discrepancy_runs, now))

    if existing_approval is None:
        workflow, version = _seed_invoice_approval(uow, clock, ids, settings, evidence)
        anchor_workflow = workflow
        anchor_version = version
        created = True
        approval_runs = _runs_for(uow, workflow.id)
        run_ids.extend(approval_runs)
        decision_ids.extend(_decisions_for(uow, approval_runs, now))
        scenario_ids.extend(_scenarios_for(uow, workflow.id))

    uow.flush()
    if not created:
        return {
            "created": False,
            "reason": "demo workspaces already seeded",
            "workflow_id": anchor_workflow.id if anchor_workflow else None,
            "batch": fixtures.DEMO_BATCH,
            "runs": [],
            "decisions": [],
            "scenarios": [],
        }

    logger.info(
        "demo workspaces seeded",
        extra={
            "run_id": anchor_workflow.id if anchor_workflow else "",
            "status_code": len(run_ids),
        },
    )
    return {
        "created": True,
        "reason": "demo workspaces created",
        "workflow_id": anchor_workflow.id if anchor_workflow else None,
        "workflow_slug": anchor_workflow.slug if anchor_workflow else None,
        "version_id": anchor_version.id if anchor_version else None,
        "batch": fixtures.DEMO_BATCH,
        "runs": run_ids,
        "decisions": decision_ids,
        "scenarios": scenario_ids,
    }


def _seed_invoice_approval(
    uow: UnitOfWork,
    clock: Clock,
    ids: IdFactory,
    settings: Settings,
    evidence: EvidenceStore,
) -> tuple[Workflow, WorkflowVersion]:
    workflow, version = create_workflow(
        uow,
        ids,
        clock,
        name=fixtures.WORKFLOW["name"],
        description=fixtures.WORKFLOW["description"],
        owner=fixtures.WORKFLOW["owner"],
        origin=Origin.DEMO,
        seed_batch=fixtures.DEMO_BATCH,
        actions=fixtures.WORKFLOW["actions"],
        invariants=fixtures.WORKFLOW["invariants"],
        publish=True,
    )

    scenarios: list[FaultScenario] = []
    for spec in fixtures.SCENARIOS:
        scenario = FaultScenario(
            id=ids.new(),
            workflow_id=workflow.id,
            workflow_version_id=version.id,
            name=spec["name"],
            description=spec["description"],
            target_ordinal=int(spec["target_ordinal"]),
            fault_type=str(FaultType(spec["fault_type"])),
            parameters=dict(spec["parameters"]),
            input_fixtures=dict(spec.get("input_fixtures") or {}),
            seed=int(spec["seed"]),
            status=str(ScenarioStatus.READY),
            origin=str(Origin.DEMO),
            seed_batch=fixtures.DEMO_BATCH,
            created_at=clock.now(),
        )
        uow.scenarios.add(scenario)
        scenarios.append(scenario)
    uow.flush()

    actions = list(uow.actions.list(workflow_version_id=version.id, order_by="ordinal"))
    invariants = list(uow.invariants.list(workflow_version_id=version.id))
    now = clock.now()

    for spec in fixtures.HISTORICAL_RUNS:
        run_clock = FrozenClock(now - timedelta(minutes=int(spec["minutes_ago"])))
        engine = ExecutionEngine(uow, run_clock, ids, settings, evidence)
        selected_scenario: FaultScenario | None = (
            scenarios[spec["scenario"]] if spec["scenario"] is not None else None
        )
        if selected_scenario is not None:
            selected_scenario.status = str(ScenarioStatus.READY)

        run = engine.start_run(
            workflow=workflow,
            version=version,
            actions=actions,
            invariants=invariants,
            options=ExecuteOptions(
                trigger=RunTrigger.DEMO,
                origin=Origin.DEMO,
                seed=selected_scenario.seed if selected_scenario else 7,
                scenario=selected_scenario,
                actor="demo-workspace",
                authorized_ordinals={5} if spec["authorized"] else set(),
                pre_authorization_reason=(
                    "Historical demo fixture: the reviewer pre-authorized the "
                    "invoice approval so the sample reflects downstream behavior."
                    if spec["authorized"]
                    else None
                ),
                seed_batch=fixtures.DEMO_BATCH,
            ),
        )
        run.trigger = "DEMO"
    uow.flush()
    return workflow, version


def _seed_discrepancy(
    uow: UnitOfWork,
    clock: Clock,
    ids: IdFactory,
    settings: Settings,
    evidence: EvidenceStore,
    now: Any,
) -> tuple[Workflow, WorkflowVersion]:
    workflow, version = create_workflow(
        uow,
        ids,
        clock,
        name=fixtures.DISCREPANCY_WORKFLOW["name"],
        description=fixtures.DISCREPANCY_WORKFLOW["description"],
        owner=fixtures.DISCREPANCY_WORKFLOW["owner"],
        origin=Origin.DEMO,
        seed_batch=fixtures.DISCREPANCY_BATCH,
        actions=fixtures.DISCREPANCY_WORKFLOW["actions"],
        invariants=fixtures.DISCREPANCY_WORKFLOW["invariants"],
        publish=True,
    )
    actions = list(uow.actions.list(workflow_version_id=version.id, order_by="ordinal"))
    invariants = list(uow.invariants.list(workflow_version_id=version.id))

    for index, spec in enumerate(fixtures.DISCREPANCY_RUNS):
        run_clock = FrozenClock(now - timedelta(minutes=int(spec["minutes_ago"])))
        engine = ExecutionEngine(uow, run_clock, ids, settings, evidence)
        run = engine.start_run(
            workflow=workflow,
            version=version,
            actions=actions,
            invariants=invariants,
            state=sandbox_invoice.initial_state(str(spec["profile"])),
            options=ExecuteOptions(
                trigger=RunTrigger.DEMO,
                origin=Origin.DEMO,
                seed=100 + index,
                actor="demo-workspace",
                seed_batch=fixtures.DISCREPANCY_BATCH,
            ),
        )
        run.trigger = "DEMO"
    uow.flush()
    return workflow, version


def _seed_processing(
    uow: UnitOfWork,
    clock: Clock,
    ids: IdFactory,
    settings: Settings,
    evidence: EvidenceStore,
    now: Any,
) -> tuple[Workflow, WorkflowVersion]:
    """Seed the end-to-end invoice slice: scenarios, a normal run and two blocks."""
    workflow, version = create_workflow(
        uow,
        ids,
        clock,
        name=fixtures.INVOICE_PROCESSING_WORKFLOW["name"],
        description=fixtures.INVOICE_PROCESSING_WORKFLOW["description"],
        owner=fixtures.INVOICE_PROCESSING_WORKFLOW["owner"],
        origin=Origin.DEMO,
        seed_batch=fixtures.PROCESSING_BATCH,
        actions=fixtures.INVOICE_PROCESSING_WORKFLOW["actions"],
        invariants=fixtures.INVOICE_PROCESSING_WORKFLOW["invariants"],
        publish=True,
    )

    scenarios: list[FaultScenario] = []
    for spec in fixtures.INVOICE_PROCESSING_SCENARIOS:
        created_scenario = FaultScenario(
            id=ids.new(),
            workflow_id=workflow.id,
            workflow_version_id=version.id,
            name=spec["name"],
            description=spec["description"],
            target_ordinal=int(spec["target_ordinal"]),
            fault_type=str(FaultType(spec["fault_type"])),
            parameters=dict(spec["parameters"]),
            seed=int(spec["seed"]),
            status=str(ScenarioStatus.READY),
            origin=str(Origin.DEMO),
            seed_batch=fixtures.PROCESSING_BATCH,
            created_at=clock.now(),
        )
        uow.scenarios.add(created_scenario)
        scenarios.append(created_scenario)
    uow.flush()

    actions = list(uow.actions.list(workflow_version_id=version.id, order_by="ordinal"))
    invariants = list(uow.invariants.list(workflow_version_id=version.id))

    for spec in fixtures.INVOICE_PROCESSING_RUNS:
        run_clock = FrozenClock(now - timedelta(minutes=int(spec["minutes_ago"])))
        engine = ExecutionEngine(uow, run_clock, ids, settings, evidence)
        scenario = scenarios[spec["scenario"]] if spec["scenario"] is not None else None
        if scenario is not None:
            scenario.status = str(ScenarioStatus.READY)
        run = engine.start_run(
            workflow=workflow,
            version=version,
            actions=actions,
            invariants=invariants,
            state=sandbox_invoice.initial_state(str(spec["profile"])),
            options=ExecuteOptions(
                trigger=RunTrigger.DEMO,
                origin=Origin.DEMO,
                seed=scenario.seed if scenario else 100,
                scenario=scenario,
                actor="demo-workspace",
                authorized_ordinals={4} if spec["authorized"] else set(),
                pre_authorization_reason=(
                    "Seeded demonstration run: the fixture records that a human "
                    "authorized this sandbox run."
                    if spec["authorized"]
                    else None
                ),
                seed_batch=fixtures.PROCESSING_BATCH,
            ),
        )
        run.trigger = "DEMO"

    uow.flush()
    return workflow, version


def _seed_vendor(
    uow: UnitOfWork,
    clock: Clock,
    ids: IdFactory,
    settings: Settings,
    evidence: EvidenceStore,
    now: Any,
) -> tuple[Workflow, WorkflowVersion]:
    """Seed the broken v1 payout, its fix in v2, a regression test and its matrix.

    This is the recovery + feedback-loop demonstration: v1 violates its invariant,
    the run is recoverable through a compensating policy, and the generated
    regression test reproduces on v1 but passes on v2.
    """
    workflow, v1 = create_workflow(
        uow,
        ids,
        clock,
        name=fixtures.VENDOR_WORKFLOW_BROKEN["name"],
        description=fixtures.VENDOR_WORKFLOW_BROKEN["description"],
        owner=fixtures.VENDOR_WORKFLOW_BROKEN["owner"],
        origin=Origin.DEMO,
        seed_batch=fixtures.VENDOR_BATCH,
        actions=fixtures.VENDOR_WORKFLOW_BROKEN["actions"],
        invariants=fixtures.VENDOR_WORKFLOW_BROKEN["invariants"],
        publish=True,
    )

    v1_actions = list(uow.actions.list(workflow_version_id=v1.id, order_by="ordinal"))
    v1_invariants = list(uow.invariants.list(workflow_version_id=v1.id))
    broken_clock = FrozenClock(now - timedelta(minutes=55))
    broken_engine = ExecutionEngine(uow, broken_clock, ids, settings, evidence)
    broken_run = broken_engine.start_run(
        workflow=workflow,
        version=v1,
        actions=v1_actions,
        invariants=v1_invariants,
        options=ExecuteOptions(
            trigger=RunTrigger.DEMO,
            origin=Origin.DEMO,
            seed=70,
            actor="demo-workspace",
            seed_batch=fixtures.VENDOR_BATCH,
        ),
    )
    broken_run.trigger = "DEMO"

    v2 = add_version(
        uow,
        ids,
        clock,
        workflow=workflow,
        actions=fixtures.VENDOR_WORKFLOW_FIXED["actions"],
        invariants=fixtures.VENDOR_WORKFLOW_FIXED["invariants"],
    )
    publish_version(uow, clock, v2)
    workflow.current_version_id = v2.id

    scenario = FaultScenario(
        id=ids.new(),
        workflow_id=workflow.id,
        workflow_version_id=v2.id,
        name=fixtures.VENDOR_SCENARIOS[0]["name"],
        description=fixtures.VENDOR_SCENARIOS[0]["description"],
        target_ordinal=int(fixtures.VENDOR_SCENARIOS[0]["target_ordinal"]),
        fault_type=str(FaultType(fixtures.VENDOR_SCENARIOS[0]["fault_type"])),
        parameters=dict(fixtures.VENDOR_SCENARIOS[0]["parameters"]),
        seed=int(fixtures.VENDOR_SCENARIOS[0]["seed"]),
        status=str(ScenarioStatus.READY),
        origin=str(Origin.DEMO),
        seed_batch=fixtures.VENDOR_BATCH,
        created_at=clock.now(),
    )
    uow.scenarios.add(scenario)
    uow.flush()

    notify_clock = FrozenClock(now - timedelta(minutes=12))
    notify_engine = ExecutionEngine(uow, notify_clock, ids, settings, evidence)
    v2_actions = list(uow.actions.list(workflow_version_id=v2.id, order_by="ordinal"))
    v2_invariants = list(uow.invariants.list(workflow_version_id=v2.id))
    notify_run = notify_engine.start_run(
        workflow=workflow,
        version=v2,
        actions=v2_actions,
        invariants=v2_invariants,
        options=ExecuteOptions(
            trigger=RunTrigger.DEMO,
            origin=Origin.DEMO,
            seed=scenario.seed,
            scenario=scenario,
            actor="demo-workspace",
            seed_batch=fixtures.VENDOR_BATCH,
        ),
    )
    notify_run.trigger = "DEMO"

    test = regression_service.create_from_run(
        uow, ids, clock, run_id=broken_run.id, name="Vendor payout must mark the invoice PAID"
    )
    regression_service.review(
        uow,
        clock,
        test,
        actor="demo-workspace",
        notes=(
            "Captured from the broken v1 payout. The disbursement wrote the ledger entry "
            "but never set the invoice to PAID."
        ),
    )
    regression_service.activate(uow, test)

    compare_clock = FrozenClock(now - timedelta(minutes=5))
    compare_engine = ExecutionEngine(uow, compare_clock, ids, settings, evidence)
    regression_service.compare(
        uow, compare_clock, ids, compare_engine, test_id=test.id, actor="demo-workspace"
    )

    uow.flush()
    return workflow, v2


def _runs_for(uow: UnitOfWork, workflow_id: str) -> list[str]:
    rows = list(uow.runs.list(workflow_id=workflow_id))
    rows.sort(key=lambda item: item.created_at)
    return [item.id for item in rows]


def _decisions_for(uow: UnitOfWork, run_ids: list[str], now: Any) -> list[str]:
    created: list[str] = []
    for run_id in run_ids:
        for decision in uow.decisions.list(run_id=run_id):
            decision.status = str(DecisionStatus.NOTIFIED)
            decision.expires_at = now + timedelta(hours=24)
            created.append(decision.id)
    return created


def _scenarios_for(uow: UnitOfWork, workflow_id: str) -> list[str]:
    return [item.id for item in uow.scenarios.list(workflow_id=workflow_id)]


def reset_demo(uow: UnitOfWork) -> dict[str, Any]:
    workflows = [item for item in uow.workflows.list() if item.origin == Origin.DEMO]
    workflow_ids = [item.id for item in workflows]
    if not workflow_ids:
        return {"deleted": 0, "runs": 0}

    runs = [item for item in uow.runs.list() if item.workflow_id in workflow_ids]
    run_ids = [item.id for item in runs]
    decisions = [item for item in uow.decisions.list() if item.run_id in run_ids]
    decision_ids = [item.id for item in decisions]
    session = uow.session

    if decision_ids:
        session.execute(
            delete(CapabilityToken).where(CapabilityToken.decision_id.in_(decision_ids))
        )
        session.execute(delete(DecisionRecord).where(DecisionRecord.decision_id.in_(decision_ids)))
    if run_ids:
        session.execute(delete(BlockerEvidence).where(BlockerEvidence.run_id.in_(run_ids)))
        session.execute(delete(Blocker).where(Blocker.run_id.in_(run_ids)))
        for model in (
            EvidenceRecord,
            InvariantVerdictRecord,
            Checkpoint,
            RecoveryAttempt,
            HumanDecision,
            ExecutionNodeState,
            ExecutionEvent,
        ):
            session.execute(delete(model).where(model.run_id.in_(run_ids)))
        session.execute(delete(RegressionTest).where(RegressionTest.source_run_id.in_(run_ids)))
        session.execute(delete(ExecutionRun).where(ExecutionRun.id.in_(run_ids)))
    session.execute(delete(FaultScenario).where(FaultScenario.workflow_id.in_(workflow_ids)))
    session.execute(delete(WorkflowVersion).where(WorkflowVersion.workflow_id.in_(workflow_ids)))
    session.execute(delete(Workflow).where(Workflow.id.in_(workflow_ids)))
    session.flush()
    return {"deleted": len(workflow_ids), "runs": len(run_ids)}
