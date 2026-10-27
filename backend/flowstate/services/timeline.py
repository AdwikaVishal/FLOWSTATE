"""Queryable execution timeline.

The timeline is a read model over the append-only event log plus the per-node
projection. It never changes run state; the console uses it to render progress
and to explain why a node stopped.
"""

from __future__ import annotations

from typing import Any

from flowstate.domain.enums import EventType
from flowstate.repositories import UnitOfWork
from flowstate.services.audit import verify_chain


def _summary(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == EventType.RUN_CREATED:
        return (
            f"Run created for {payload.get('workflow_name', 'workflow')} "
            f"v{payload.get('version', '?')}"
        )
    if event_type == EventType.RUN_STARTED:
        return f"Execution started across {payload.get('nodes', '?')} nodes"
    if event_type == EventType.WORKFLOW_VALIDATED:
        return "Workflow graph and handlers validated"
    if event_type == EventType.RUN_INPUTS_RECORDED:
        return "Run inputs recorded (sensitive paths redacted)"
    if event_type == EventType.RUN_OUTPUTS_RECORDED:
        return "Node outputs recorded for the run"
    if event_type in {EventType.ACTION_STARTED, EventType.NODE_STARTED}:
        attempt = payload.get("attempt", 1)
        suffix = f" (attempt {attempt})" if attempt and attempt > 1 else ""
        return f"{payload.get('label') or payload.get('kind') or 'Node'} started{suffix}"
    if event_type == EventType.ACTION_SUCCEEDED:
        return (
            f"{payload.get('label') or 'Action'} passed postconditions in "
            f"{payload.get('duration_ms', 0)} ms"
        )
    if event_type == EventType.ACTION_UNKNOWN:
        return f"Outcome unknown: {payload.get('detail', 'no response')}"
    if event_type == EventType.ACTION_CONFIRMED:
        return f"Reconciled with a read-only probe: {payload.get('probe', 'effect present')}"
    if event_type == EventType.ACTION_FAILED:
        return f"Probe could not confirm the effect: {payload.get('probe', 'no effect observed')}"
    if event_type == EventType.NODE_PASSED:
        return f"{payload.get('label') or payload.get('node_type', 'Node')} passed"
    if event_type == EventType.NODE_FAILED:
        return f"{payload.get('code', 'Failure')}: {payload.get('detail', '')}".strip()
    if event_type == EventType.NODE_SKIPPED:
        return f"Node skipped: {payload.get('detail') or payload.get('reason', 'not needed')}"
    if event_type == EventType.NODE_BLOCKED:
        return f"Node blocked: {payload.get('detail', 'waiting')}"
    if event_type == EventType.NODE_RECOVERED:
        return f"Recovered by {payload.get('recovery_node', 'recovery handler')}"
    if event_type == EventType.NODE_RECONCILED:
        return "Ambiguous outcome reconciled without a retry"
    if event_type == EventType.NODE_RETRY_SCHEDULED:
        return (
            f"Retry {payload.get('next_attempt')} of {payload.get('max_attempts')} scheduled "
            f"after {payload.get('error_class', 'error')}"
        )
    if event_type == EventType.FAULT_INJECTED:
        return f"Fault injected: {payload.get('fault_type', 'fault')}"
    if event_type == EventType.CHECKPOINT_SAVED:
        return f"Checkpoint {payload.get('kind', '')} saved"
    if event_type == EventType.DECISION_REQUESTED:
        return f"Human decision requested: {payload.get('question', '')}"
    if event_type == EventType.DECISION_GRANTED:
        return "Human authorized the blocked node"
    if event_type == EventType.DECISION_DENIED:
        return "Human denied the blocked node"
    if event_type == EventType.DECISION_EXPIRED:
        return "Decision expired before a human responded"
    if event_type == EventType.DECISION_CANCELLED:
        return "Decision cancelled with the run"
    if event_type == EventType.INVARIANT_EVALUATED:
        return f"Invariant {payload.get('name', '')}: {payload.get('verdict', '')}"
    if event_type == EventType.RUN_VERIFIED:
        return "Run verified: all invariants satisfied"
    if event_type == EventType.RUN_FAILED:
        return f"Run failed: {payload.get('code', 'error')}"
    if event_type == EventType.RUN_CANCELLED:
        return f"Run cancelled: {payload.get('reason', 'operator request')}"
    if event_type == EventType.RUN_TIMED_OUT:
        return f"Run timed out: {payload.get('detail', 'budget exhausted')}"
    if event_type == EventType.RECOVERY_STARTED:
        return f"Recovery started ({payload.get('strategy', 'RESUME')})"
    if event_type == EventType.RECOVERY_APPLIED:
        return "Checkpoint restored"
    if event_type == EventType.RECOVERY_VERIFIED:
        return "Recovery verified by invariants"
    if event_type == EventType.RECOVERY_FAILED:
        return f"Recovery did not verify: {payload.get('detail', '')}"
    return event_type.replace("_", " ").title()


def build_timeline(
    uow: UnitOfWork,
    *,
    run_id: str,
    node_states: list[dict[str, Any]],
    limit: int = 1000,
) -> dict[str, Any]:
    run = uow.runs.get(run_id)
    if run is None:
        raise ValueError(f"run {run_id} not found")
    events = list(uow.events.read(run_id, limit=limit))
    chain = verify_chain(events)
    entries = [
        {
            "seq": event.seq,
            "type": event.type,
            "node_key": event.node_key,
            "action_ordinal": event.action_ordinal,
            "actor": event.actor,
            "actor_id": event.actor_id,
            "recorded_at": event.recorded_at,
            "summary": _summary(event.type, event.payload or {}),
            "payload": event.payload or {},
        }
        for event in events
    ]
    return {
        "run_id": run_id,
        "status": run.status,
        "verdict": run.verdict,
        "generated_at": run.ended_at or run.created_at,
        "nodes": node_states,
        "entries": entries,
        "chain_valid": chain.valid,
    }
