"""Cancellation registry and the user-facing cancel transition.

Execution is synchronous inside the API process. The registry gives a second
request a way to interrupt a run at the next safe node boundary without
mutating engine-local state.
"""

from __future__ import annotations

import threading

from flowstate.clock import Clock
from flowstate.db.models import ExecutionRun
from flowstate.domain.enums import (
    ActorKind,
    DecisionStatus,
    EventType,
    NodeStatus,
    RunStatus,
    RunVerdict,
)
from flowstate.errors import ConflictError, NotFoundError
from flowstate.ids import IdFactory
from flowstate.repositories import UnitOfWork
from flowstate.services.audit import AuditWriter


class CancellationRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requested: dict[str, str] = {}

    def request(self, run_id: str, reason: str = "") -> None:
        with self._lock:
            self._requested[run_id] = reason or "cancelled by operator"

    def is_requested(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._requested

    def reason(self, run_id: str) -> str:
        with self._lock:
            return self._requested.get(run_id, "cancelled by operator")

    def clear(self, run_id: str) -> None:
        with self._lock:
            self._requested.pop(run_id, None)


def is_terminal(run: ExecutionRun) -> bool:
    return run.status in {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.TIMED_OUT,
    }


def cancel_run(
    uow: UnitOfWork,
    clock: Clock,
    ids: IdFactory,
    *,
    run_id: str,
    actor: str = "local-operator",
    reason: str = "",
    registry: CancellationRegistry | None = None,
) -> ExecutionRun:
    run = uow.runs.get(run_id)
    if run is None:
        raise NotFoundError(f"Run {run_id} was not found.", code="RUN_NOT_FOUND")
    if run.status == RunStatus.CANCELLED:
        return run
    if is_terminal(run):
        raise ConflictError(
            f"Run is {run.status}; a terminal run cannot be cancelled.",
            code="RUN_NOT_CANCELLABLE",
        )

    if registry is not None:
        registry.request(run.id, reason)

    audit = AuditWriter(uow, clock, ids)
    now = clock.now()
    run.status = str(RunStatus.CANCELLED)
    run.verdict = str(RunVerdict.CANCELLED)
    run.ended_at = now
    run.failure_code = "CANCELLED"
    run.failure_message = reason or "Cancelled by operator."

    for node_state in uow.node_states.list(run_id=run.id):
        if node_state.status in {
            NodeStatus.PENDING,
            NodeStatus.RUNNING,
            NodeStatus.BLOCKED,
        }:
            node_state.status = str(NodeStatus.CANCELLED)
            node_state.ended_at = now
            if node_state.error_class is None:
                node_state.error_class = "CANCELLED"
                node_state.error_code = "RUN_CANCELLED"
                node_state.error_message = run.failure_message

    for decision in uow.decisions.list(run_id=run.id):
        if decision.status in {
            DecisionStatus.REQUESTED,
            DecisionStatus.NOTIFIED,
            DecisionStatus.DEFERRED,
            DecisionStatus.INFO_PROVIDED,
        }:
            decision.status = str(DecisionStatus.CANCELLED)
            decision.decided_at = now
            decision.decided_by = actor
            audit.append(
                run.id,
                EventType.DECISION_CANCELLED,
                actor=ActorKind.HUMAN,
                actor_id=actor,
                action_ordinal=decision.action_ordinal,
                payload={"decision_id": decision.id, "reason": run.failure_message},
            )

    audit.append(
        run.id,
        EventType.RUN_CANCELLED,
        actor=ActorKind.HUMAN,
        actor_id=actor,
        payload={"reason": run.failure_message, "status": str(RunStatus.CANCELLED)},
    )
    uow.flush()
    return run
