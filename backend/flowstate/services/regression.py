"""Regression tests generated from reproducible failures.

A test is a structured, reviewable artefact — not a model's summary. It pins the
workflow version, the exact scenario configuration, the invariant that broke and
the expected behaviour, so the same failure can be replayed against any published
version of the workflow.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from flowstate.clock import Clock
from flowstate.db.models import (
    ExecutionRun,
    FaultScenario,
    RegressionResult,
    RegressionTest,
    WorkflowVersion,
)
from flowstate.domain.enums import (
    Origin,
    RegressionStatus,
    RunTrigger,
    ScenarioStatus,
    VersionStatus,
)
from flowstate.errors import ConflictError, NotFoundError
from flowstate.ids import IdFactory
from flowstate.repositories import UnitOfWork
from flowstate.services.execution import ExecuteOptions, ExecutionEngine

TEMPLATE_VERSION = 1


def get_test(uow: UnitOfWork, test_id: str) -> RegressionTest:
    test = uow.regressions.get(test_id)
    if test is None:
        raise NotFoundError(
            f"Regression test {test_id} was not found.", code="REGRESSION_NOT_FOUND"
        )
    return test


def _violated(uow: UnitOfWork, run: ExecutionRun) -> list[Any]:
    return [item for item in uow.verdicts.list(run_id=run.id) if item.verdict == "VIOLATED"]


def build_spec(uow: UnitOfWork, run: ExecutionRun) -> dict[str, Any]:
    workflow = uow.workflows.get(run.workflow_id)
    version = uow.versions.get(run.workflow_version_id)
    scenario = uow.scenarios.get(run.fault_scenario_id) if run.fault_scenario_id else None
    violated = _violated(uow, run)
    primary = violated[0] if violated else None

    return {
        "template_version": TEMPLATE_VERSION,
        "workflow_id": run.workflow_id,
        "workflow_name": workflow.name if workflow else None,
        "workflow_version_id": run.workflow_version_id,
        "version": version.version if version else None,
        "source_run_id": run.id,
        "seed": run.seed,
        "inputs": dict(run.inputs or {}),
        "driver": run.driver,
        "scenario": (
            {
                "id": scenario.id,
                "name": scenario.name,
                "fault_type": scenario.fault_type,
                "target_ordinal": scenario.target_ordinal,
                "parameters": dict(scenario.parameters or {}),
                "seed": scenario.seed,
            }
            if scenario is not None
            else None
        ),
        "expected": {
            "kind": "INVARIANTS_SATISFIED",
            "invariant_id": primary.invariant_id if primary else None,
            "invariant_name": primary.invariant_name if primary else None,
            "description": (
                f"Replaying this scenario must leave '{primary.invariant_name}' satisfied "
                "and produce no VIOLATED invariant verdicts."
                if primary
                else "Replaying this scenario must produce no VIOLATED invariant verdicts."
            ),
            "failure_code": run.failure_code,
        },
        "observed": {
            "run_status": run.status,
            "verdict": run.verdict,
            "failure_code": run.failure_code,
            "failure_message": run.failure_message,
            "failed_ordinal": run.failure_ordinal,
            "violated_invariants": [
                {
                    "id": item.invariant_id,
                    "name": item.invariant_name,
                    "severity": item.severity,
                    "detail": item.detail,
                }
                for item in violated
            ],
        },
        "reproduction": {
            "replay": "POST /api/v1/regression-tests/{test_id}/replay",
            "compare": "POST /api/v1/regression-tests/{test_id}/compare",
            "trigger": RunTrigger.REGRESSION.value,
        },
    }


def create_from_run(
    uow: UnitOfWork,
    ids: IdFactory,
    clock: Clock,
    *,
    run_id: str,
    name: str | None = None,
) -> RegressionTest:
    run = uow.runs.get(run_id)
    if run is None:
        raise NotFoundError(f"Run {run_id} was not found.", code="RUN_NOT_FOUND")
    if run.verdict == "VERIFIED":
        raise ConflictError(
            "A verified run has no failure to turn into a regression test.",
            code="NO_FAILURE_TO_CAPTURE",
        )

    spec = build_spec(uow, run)
    expected = spec["expected"]
    test = RegressionTest(
        id=ids.new(),
        source_run_id=run.id,
        fault_scenario_id=run.fault_scenario_id,
        invariant_id=expected["invariant_id"],
        name=name or f"Regression: {expected['invariant_name'] or run.failure_code or run.id[:8]}",
        spec=spec,
        status=str(RegressionStatus.DRAFT),
        created_at=clock.now(),
    )
    uow.regressions.add(test)
    uow.flush()
    return test


def review(
    uow: UnitOfWork,
    clock: Clock,
    test: RegressionTest,
    *,
    actor: str,
    name: str | None = None,
    notes: str | None = None,
    expected_description: str | None = None,
) -> RegressionTest:
    """Persist a human review without activating the test."""
    if name is not None:
        test.name = name
    if notes is not None:
        test.notes = notes
    if expected_description is not None:
        spec = dict(test.spec or {})
        expected = dict(spec.get("expected") or {})
        expected["description"] = expected_description
        spec["expected"] = expected
        test.spec = spec
    test.reviewed_by = actor
    test.reviewed_at = clock.now()
    uow.flush()
    return test


def activate(uow: UnitOfWork, test: RegressionTest) -> RegressionTest:
    test.status = str(RegressionStatus.ACTIVE)
    uow.flush()
    return test


def quarantine(uow: UnitOfWork, test: RegressionTest, reason: str) -> RegressionTest:
    test.status = str(RegressionStatus.QUARANTINED)
    test.notes = reason
    uow.flush()
    return test


def _scenario_from_spec(uow: UnitOfWork, spec: dict[str, Any]) -> FaultScenario | None:
    """Rebuild the scenario from the saved snapshot so later edits cannot drift."""
    snapshot = spec.get("scenario")
    if not isinstance(snapshot, dict):
        return None
    original = uow.scenarios.get(str(snapshot.get("id"))) if snapshot.get("id") else None
    return FaultScenario(
        id=str(snapshot.get("id") or (original.id if original else "inline")),
        workflow_id=str(spec.get("workflow_id", "")),
        workflow_version_id=str(spec.get("workflow_version_id", "")),
        name=str(snapshot.get("name", "recorded scenario")),
        description="Reconstructed from the regression spec snapshot.",
        target_ordinal=int(snapshot.get("target_ordinal", 0)),
        fault_type=str(snapshot.get("fault_type", "TIMEOUT")),
        parameters=dict(snapshot.get("parameters") or {}),
        seed=int(snapshot.get("seed", spec.get("seed", 0))),
        status=str(ScenarioStatus.READY),
        origin=str(original.origin if original else Origin.LIVE),
    )


def _run_against_version(
    uow: UnitOfWork,
    engine: ExecutionEngine,
    test: RegressionTest,
    version: WorkflowVersion,
    *,
    actor: str,
) -> ExecutionRun:
    spec = test.spec or {}
    workflow = uow.workflows.get(str(spec.get("workflow_id", "")))
    if workflow is None:
        raise NotFoundError(
            "The workflow for this regression test no longer exists.", code="WORKFLOW_NOT_FOUND"
        )
    actions = list(uow.actions.list(workflow_version_id=version.id, order_by="ordinal"))
    invariants = list(uow.invariants.list(workflow_version_id=version.id))
    scenario = _scenario_from_spec(uow, spec)
    authorized = {item.ordinal for item in actions if item.requires_authorization}

    return engine.start_run(
        workflow=workflow,
        version=version,
        actions=actions,
        invariants=invariants,
        options=ExecuteOptions(
            trigger=RunTrigger.REGRESSION,
            origin=Origin(workflow.origin),
            seed=int(spec.get("seed", 0)),
            scenario=scenario,
            actor=actor,
            authorized_ordinals=authorized if scenario is not None else set(),
            inputs=dict(spec.get("inputs") or {}),
            pre_authorization_reason=(
                "Regression replay: gated actions are pre-authorized inside the sandbox "
                "to reproduce the recorded path."
                if scenario is not None
                else None
            ),
        ),
    )


def _record_result(
    uow: UnitOfWork,
    ids: IdFactory,
    clock: Clock,
    test: RegressionTest,
    version: WorkflowVersion,
    run: ExecutionRun,
) -> RegressionResult:
    satisfied = run.verdict == "VERIFIED"
    violated = _violated(uow, run)
    result = RegressionResult(
        id=ids.new(),
        test_id=test.id,
        workflow_id=run.workflow_id,
        workflow_version_id=version.id,
        version=version.version,
        run_id=run.id,
        result="PASSED" if satisfied else "FAILED",
        failure_code=run.failure_code,
        violated_invariant=violated[0].invariant_name if violated else None,
        detail=(
            "All invariants satisfied on replay."
            if satisfied
            else (run.failure_message or "Replay did not verify.")
        ),
        created_at=clock.now(),
    )
    uow.regression_results.add(result)
    return result


def replay(
    uow: UnitOfWork,
    clock: Clock,
    ids: IdFactory,
    engine: ExecutionEngine,
    *,
    test_id: str,
    version_id: str | None = None,
    actor: str = "local-operator",
) -> tuple[RegressionTest, str]:
    """Replay against the pinned version (or an explicit one) and record the result."""
    test = get_test(uow, test_id)
    spec = test.spec or {}
    pinned = str(spec.get("workflow_version_id", ""))
    version = uow.versions.get(version_id or pinned)
    if version is None:
        raise NotFoundError(
            "The workflow version for this regression test no longer exists.",
            code="VERSION_NOT_FOUND",
        )

    run = _run_against_version(uow, engine, test, version, actor=actor)
    result = _record_result(uow, ids, clock, test, version, run)

    if version.id == pinned:
        test.last_result = result.result
        test.last_run_at = clock.now()
        test.status = str(
            RegressionStatus.PASSING if result.result == "PASSED" else RegressionStatus.FAILING
        )
    uow.flush()
    return test, run.id


def compare(
    uow: UnitOfWork,
    clock: Clock,
    ids: IdFactory,
    engine: ExecutionEngine,
    *,
    test_id: str,
    actor: str = "local-operator",
) -> list[RegressionResult]:
    """Run the same spec against every published version of the workflow."""
    test = get_test(uow, test_id)
    spec = test.spec or {}
    workflow_id = str(spec.get("workflow_id", ""))
    versions: Sequence[WorkflowVersion] = sorted(
        uow.versions.list(workflow_id=workflow_id, status=VersionStatus.PUBLISHED),
        key=lambda item: item.version,
    )
    if not versions:
        raise ConflictError(
            "The workflow has no published versions to compare.", code="NO_PUBLISHED_VERSION"
        )

    results: list[RegressionResult] = []
    for version in versions:
        existing = [
            item
            for item in uow.regression_results.list(test_id=test.id)
            if item.workflow_version_id == version.id
        ]
        if existing:
            results.append(max(existing, key=lambda item: item.created_at))
            continue
        run = _run_against_version(uow, engine, test, version, actor=actor)
        results.append(_record_result(uow, ids, clock, test, version, run))

    latest = max(results, key=lambda item: item.version)
    test.last_result = latest.result
    test.last_run_at = clock.now()
    test.status = str(
        RegressionStatus.PASSING if latest.result == "PASSED" else RegressionStatus.FAILING
    )
    uow.flush()
    return results


def results_for(uow: UnitOfWork, test_id: str) -> list[RegressionResult]:
    get_test(uow, test_id)
    rows = list(uow.regression_results.list(test_id=test_id))
    rows.sort(key=lambda item: (item.version, item.created_at))
    return rows


def failure_modes(uow: UnitOfWork) -> list[dict[str, Any]]:
    """The feedback loop: detected failure modes and whether versions resolve them."""
    tests = list(uow.regressions.list(order_by="created_at", descending=True))
    modes: list[dict[str, Any]] = []
    for test in tests:
        spec = test.spec or {}
        workflow = uow.workflows.get(str(spec.get("workflow_id", "")))
        if workflow is None:
            continue
        rows = list(uow.regression_results.list(test_id=test.id))
        rows.sort(key=lambda item: item.version)
        passed = [item for item in rows if item.result == "PASSED"]
        failed = [item for item in rows if item.result == "FAILED"]
        detected_in = spec.get("version")
        latest_version = max(
            (item.version for item in uow.versions.list(workflow_id=workflow.id)),
            default=None,
        )

        if not rows:
            status = "UNTESTED"
        elif passed and (
            not failed
            or max(item.version for item in passed) > max(item.version for item in failed)
        ):
            status = "RESOLVED"
        elif failed:
            status = "STILL_FAILING"
        else:
            status = "UNTESTED"

        modes.append(
            {
                "test_id": test.id,
                "test_name": test.name,
                "test_status": test.status,
                "workflow_id": workflow.id,
                "workflow_name": workflow.name,
                "failure_mode": (
                    (spec.get("expected") or {}).get("invariant_name")
                    or (spec.get("observed") or {}).get("failure_code")
                    or "unknown failure"
                ),
                "scenario_fault_type": (spec.get("scenario") or {}).get("fault_type"),
                "detected_in_version": detected_in,
                "current_version": latest_version,
                "status": status,
                "resolved_in": [item.version for item in passed],
                "reproduces_in": [item.version for item in failed],
                "last_checked_at": max((item.created_at for item in rows), default=None),
                "source_run_id": test.source_run_id,
                "notes": test.notes,
            }
        )
    return modes
