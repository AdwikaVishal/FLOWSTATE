"""Verified recovery.

A recovery is only supported when the failed action declares an explicit,
tested recovery policy. Three rules drive this module:

1. **Never retry blindly.** An ambiguous outcome is resolved by inspecting the
   captured attempt state first. A retry requires a declared idempotency key.
2. **A recovery contract decides success.** Compensating effects are not trusted
   on their own; the target state must satisfy the declared contract and the
   resumed node's postconditions.
3. **Partial is a first-class outcome.** If some inverses applied but the
   contract does not hold, the attempt is ``PARTIAL`` and no dependent action
   resumes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from flowstate.clock import Clock
from flowstate.db.models import (
    Checkpoint,
    ExecutionRun,
    RecoveryAttempt,
)
from flowstate.domain.conditions import (
    evaluate_all,
    evaluate_condition,
    path_exists,
    resolve_path,
)
from flowstate.domain.enums import (
    ActorKind,
    CheckpointKind,
    EventType,
    EvidenceKind,
    Origin,
    RecoveryStatus,
    RecoveryStrategy,
    RunStatus,
    RunTrigger,
    RunVerdict,
)
from flowstate.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from flowstate.ids import IdFactory
from flowstate.repositories import UnitOfWork
from flowstate.sandbox import invoice as sandbox
from flowstate.services.audit import AuditWriter, canonical_json
from flowstate.services.evidence import EvidenceStore
from flowstate.services.execution import ExecuteOptions, ExecutionEngine

RECOVERABLE_STATUSES = frozenset(
    {RunStatus.AWAITING_RECOVERY, RunStatus.FAILED, RunStatus.TIMED_OUT}
)


def _hash_state(state: dict[str, Any]) -> str:
    body = canonical_json({key: value for key, value in state.items() if key != "__engine__"})
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def get_attempt(uow: UnitOfWork, attempt_id: str) -> RecoveryAttempt:
    attempt = uow.recoveries.get(attempt_id)
    if attempt is None:
        raise NotFoundError(
            f"Recovery attempt {attempt_id} was not found.", code="RECOVERY_NOT_FOUND"
        )
    return attempt


@dataclass
class RecoveryPlan:
    supported: bool
    run_id: str
    action_ordinal: int | None = None
    node_key: str | None = None
    action_label: str | None = None
    strategy: RecoveryStrategy = RecoveryStrategy.MANUAL
    reversible: bool = False
    allowed_strategies: tuple[str, ...] = ()
    contract: tuple[dict[str, Any], ...] = ()
    compensate_contract: tuple[dict[str, Any], ...] = ()
    compensate: tuple[dict[str, Any], ...] = ()
    probe: dict[str, Any] | None = None
    idempotency_path: str | None = None
    notes: str = ""
    reason: str = ""
    code: str | None = None
    ambiguous: bool = False
    max_attempts: int = 1
    policy: dict[str, Any] = field(default_factory=dict)


def _load_actions(uow: UnitOfWork, run: ExecutionRun) -> list[Any]:
    return list(uow.actions.list(workflow_version_id=run.workflow_version_id, order_by="ordinal"))


def _failed_action(uow: UnitOfWork, run: ExecutionRun) -> Any | None:
    actions = _load_actions(uow, run)
    if not actions:
        return None
    if run.failure_ordinal is None:
        # The run failed verification, not a single node. Target the most recent
        # action that actually declares a recovery policy, so bookkeeping steps
        # such as "record the result" do not hide the consequential mutation.
        declared = [item for item in actions if recovery_policy(item)]
        if declared:
            return max(declared, key=lambda item: item.ordinal)
        return max(actions, key=lambda item: item.ordinal)
    exact = next((item for item in actions if item.ordinal == run.failure_ordinal), None)
    if exact is not None:
        return exact
    candidates = [item for item in actions if item.ordinal <= run.failure_ordinal]
    return max(candidates, key=lambda item: item.ordinal) if candidates else None


def recovery_policy(action: Any | None) -> dict[str, Any]:
    if action is None:
        return {}
    payload = getattr(action, "input", None) or {}
    policy = payload.get("recovery")
    return policy if isinstance(policy, dict) else {}


def build_plan(uow: UnitOfWork, run: ExecutionRun) -> RecoveryPlan:
    action = _failed_action(uow, run)
    if action is None:
        return RecoveryPlan(
            supported=False,
            run_id=run.id,
            reason="No action could be identified for this failure.",
            code="NO_RECOVERABLE_ACTION",
        )

    policy = recovery_policy(action)
    ambiguous = run.failure_code in {"ACTION_OUTCOME_UNKNOWN", "AMBIGUOUS_OUTCOME"}
    base = RecoveryPlan(
        supported=False,
        run_id=run.id,
        action_ordinal=action.ordinal,
        node_key=getattr(action, "node_key", None) or None,
        action_label=action.label,
        ambiguous=ambiguous,
        policy=policy,
    )

    if not policy:
        base.reason = (
            "This action declares no recovery policy. Recovery is only supported for "
            "actions with an explicit, tested strategy."
        )
        base.code = "NO_RECOVERY_POLICY"
        return base

    reversible = bool(policy.get("reversible", False))
    strategy_raw = str(policy.get("strategy", "MANUAL")).upper()
    try:
        strategy = RecoveryStrategy(strategy_raw)
    except ValueError:
        base.reason = f"Unknown recovery strategy {strategy_raw!r}."
        base.code = "INVALID_RECOVERY_POLICY"
        return base

    if not reversible and strategy != RecoveryStrategy.MANUAL:
        base.reason = (
            "This action is marked irreversible but declares an automated strategy. "
            "Irreversible operations require a MANUAL recovery policy."
        )
        base.code = "IRREVERSIBLE_REQUIRES_MANUAL"
        return base

    base.supported = True
    base.strategy = strategy
    base.reversible = reversible
    base.allowed_strategies = tuple(policy.get("allowed_strategies") or [strategy.value])
    base.contract = tuple(policy.get("contract") or [])
    base.compensate_contract = tuple(policy.get("compensate_contract") or [])
    base.compensate = tuple(policy.get("compensate") or [])
    base.probe = policy.get("probe") if isinstance(policy.get("probe"), dict) else None
    base.idempotency_path = policy.get("idempotency_path")
    base.notes = str(policy.get("notes") or "")
    base.max_attempts = int(policy.get("max_attempts", 1))
    if not reversible:
        base.strategy = RecoveryStrategy.MANUAL
        base.allowed_strategies = (RecoveryStrategy.MANUAL.value,)
    return base


def plan_for_run(uow: UnitOfWork, run: ExecutionRun) -> dict[str, Any]:
    plan = build_plan(uow, run)
    return {
        "run_id": plan.run_id,
        "supported": plan.supported,
        "action_ordinal": plan.action_ordinal,
        "node_key": plan.node_key,
        "action_label": plan.action_label,
        "strategy": str(plan.strategy),
        "reversible": plan.reversible,
        "allowed_strategies": list(plan.allowed_strategies),
        "contract": list(plan.contract),
        "compensate_contract": list(plan.compensate_contract),
        "compensate": list(plan.compensate),
        "probe": plan.probe,
        "idempotency_path": plan.idempotency_path,
        "notes": plan.notes,
        "reason": plan.reason,
        "code": plan.code,
        "ambiguous": plan.ambiguous,
        "run_status": run.status,
        "recoverable": run.status in RECOVERABLE_STATUSES,
    }


def _resolve_checkpoint(
    engine: ExecutionEngine,
    run: ExecutionRun,
    plan: RecoveryPlan,
    strategy: RecoveryStrategy,
) -> tuple[Checkpoint | None, str]:
    """Choose the restore point and describe why."""
    ordinal = plan.action_ordinal or 0
    if plan.ambiguous:
        attempt_state = engine.checkpoint_at(run.id, ordinal, CheckpointKind.ATTEMPT_STATE)
        if attempt_state is not None:
            return attempt_state, "attempt_state"
        pre = engine.checkpoint_at(run.id, ordinal, CheckpointKind.PRE_ACTION)
        if pre is not None:
            return pre, "pre_action"
        return engine.latest_checkpoint(run.id, upto_ordinal=ordinal), "latest"

    if strategy == RecoveryStrategy.COMPENSATE:
        mutated = engine.checkpoint_at(run.id, ordinal, CheckpointKind.POST_ACTION)
        if mutated is not None:
            return mutated, "post_action"
        return engine.latest_checkpoint(run.id, upto_ordinal=ordinal), "latest"

    pre = engine.checkpoint_at(run.id, ordinal, CheckpointKind.PRE_ACTION)
    if pre is not None:
        return pre, "pre_action"
    return engine.latest_checkpoint(run.id, upto_ordinal=max(ordinal - 1, 0)), "latest_before"


def _evidence(
    uow: UnitOfWork,
    evidence: EvidenceStore,
    run: ExecutionRun,
    attempt: RecoveryAttempt,
    *,
    state: dict[str, Any],
    checks: list[dict[str, Any]],
    probe: dict[str, Any] | None,
) -> str:
    record = evidence.write_json(
        run_id=run.id,
        kind=EvidenceKind.INVARIANT_REPORT,
        payload={
            "kind": "recovery_report",
            "attempt_id": attempt.id,
            "run_id": run.id,
            "strategy": attempt.strategy,
            "status": attempt.status,
            "action_ordinal": attempt.action_ordinal,
            "node_key": attempt.node_key,
            "before_state_hash": attempt.before_state_hash,
            "after_state_hash": attempt.after_state_hash,
            "contract": attempt.contract,
            "checks": checks,
            "probe": probe,
            "steps": attempt.steps,
            "state": {
                key: state.get(key)
                for key in ("invoice", "purchase_order", "policy", "review")
                if key in state
            },
        },
    )
    uow.evidence.add(record)
    return record.id


def _finish(
    uow: UnitOfWork,
    clock: Clock,
    ids: IdFactory,
    evidence: EvidenceStore,
    attempt: RecoveryAttempt,
    run: ExecutionRun,
    *,
    status: RecoveryStatus,
    detail: str,
    state: dict[str, Any] | None,
    checks: list[dict[str, Any]],
    probe: dict[str, Any] | None = None,
    failure_code: str | None = None,
    actor: str,
) -> RecoveryAttempt:
    now = clock.now()
    attempt.status = str(status)
    attempt.detail = detail
    attempt.failure_code = failure_code
    attempt.ended_at = now
    attempt.verification = {"checks": checks, "probe": probe, "status": str(status)}
    if state is not None:
        attempt.after_state_hash = _hash_state(state)
        attempt.evidence_id = _evidence(
            uow, evidence, run, attempt, state=state, checks=checks, probe=probe
        )
    audit = AuditWriter(uow, clock, ids)
    event_type = {
        RecoveryStatus.VERIFIED: EventType.RECOVERY_VERIFIED,
        RecoveryStatus.PARTIAL: EventType.RECOVERY_PARTIAL,
        RecoveryStatus.MANUAL_INTERVENTION: EventType.RECOVERY_MANUAL_INTERVENTION,
    }.get(status, EventType.RECOVERY_FAILED)
    event = audit.append(
        run.id,
        event_type,
        actor=ActorKind.HUMAN,
        actor_id=actor,
        payload={
            "attempt_id": attempt.id,
            "status": str(status),
            "detail": detail,
            "failure_code": failure_code,
            "before_state_hash": attempt.before_state_hash,
            "after_state_hash": attempt.after_state_hash,
            "evidence_id": attempt.evidence_id,
            "checks": checks,
        },
    )
    attempt.verification_event_id = event.id
    # A failed or partial recovery ends the run. A manual-intervention or
    # escalation outcome leaves it blocked, because a human still has to act.
    if status in {RecoveryStatus.FAILED, RecoveryStatus.PARTIAL} and run.status in {
        RunStatus.AWAITING_RECOVERY,
        RunStatus.TIMED_OUT,
    }:
        run.status = str(RunStatus.FAILED)
        run.verdict = str(RunVerdict.FAILED)
    uow.flush()
    return attempt


def recover(
    uow: UnitOfWork,
    clock: Clock,
    ids: IdFactory,
    engine: ExecutionEngine,
    evidence: EvidenceStore,
    *,
    run_id: str,
    actor: str = "local-operator",
    strategy: RecoveryStrategy | None = None,
    actor_kind: ActorKind = ActorKind.HUMAN,
    allow_irreversible: bool = False,
) -> RecoveryAttempt:
    if actor_kind not in {ActorKind.HUMAN, ActorKind.SYSTEM}:
        raise ForbiddenError(
            "Recovery requires a human or the system actor.", code="HUMAN_ACTOR_REQUIRED"
        )

    run = uow.runs.get(run_id)
    if run is None:
        raise NotFoundError(f"Run {run_id} was not found.", code="RUN_NOT_FOUND")
    if run.status not in RECOVERABLE_STATUSES:
        raise ConflictError(
            f"Run is {run.status}; only a failed, timed-out or awaiting-recovery run "
            "can be recovered.",
            code="RUN_NOT_RECOVERABLE",
        )
    if any(item.status == RecoveryStatus.VERIFIED for item in uow.recoveries.list(run_id=run.id)):
        raise ConflictError(
            "This run already has a verified recovery. Recovering it again would apply "
            "compensating actions twice.",
            code="ALREADY_RECOVERED",
        )

    plan = build_plan(uow, run)
    now = clock.now()
    attempt = RecoveryAttempt(
        id=ids.new(),
        run_id=run.id,
        strategy=str(strategy or plan.strategy),
        status=str(RecoveryStatus.PLANNED),
        steps=[],
        started_at=now,
        action_ordinal=plan.action_ordinal,
        node_key=plan.node_key,
        recovery_policy=plan.policy,
        contract=list(plan.contract),
        failure_code=plan.code,
    )
    uow.recoveries.add(attempt)
    audit = AuditWriter(uow, clock, ids)
    audit.append(
        run.id,
        EventType.RECOVERY_STARTED,
        actor=actor_kind,
        actor_id=actor,
        payload={
            "attempt_id": attempt.id,
            "strategy": attempt.strategy,
            "action_ordinal": plan.action_ordinal,
            "node_key": plan.node_key,
            "recoverable": plan.supported,
        },
    )

    if not plan.supported:
        manual = plan.code in {
            "NO_RECOVERY_POLICY",
            "IRREVERSIBLE_REQUIRES_MANUAL",
            "INVALID_RECOVERY_POLICY",
        }
        audit.append(
            run.id,
            EventType.RECOVERY_REFUSED,
            actor=actor_kind,
            actor_id=actor,
            payload={"attempt_id": attempt.id, "code": plan.code, "detail": plan.reason},
        )
        return _finish(
            uow,
            clock,
            ids,
            evidence,
            attempt,
            run,
            status=(RecoveryStatus.MANUAL_INTERVENTION if manual else RecoveryStatus.FAILED),
            detail=plan.reason,
            state=None,
            checks=[{"check": "recovery_policy", "ok": False, "detail": plan.reason}],
            failure_code=plan.code,
            actor=actor,
        )

    chosen = strategy or plan.strategy
    if not plan.reversible and not allow_irreversible:
        return _finish(
            uow,
            clock,
            ids,
            evidence,
            attempt,
            run,
            status=RecoveryStatus.MANUAL_INTERVENTION,
            detail="This action is irreversible; a human must decide the recovery.",
            state=None,
            checks=[{"check": "reversible", "ok": False, "detail": "action is irreversible"}],
            failure_code="IRREVERSIBLE_ACTION",
            actor=actor,
        )
    if chosen.value not in plan.allowed_strategies and chosen != plan.strategy:
        raise ValidationError(
            f"Strategy {chosen.value} is not permitted by this action's recovery policy.",
            code="STRATEGY_NOT_PERMITTED",
            details={"allowed": list(plan.allowed_strategies)},
        )
    if chosen == RecoveryStrategy.MANUAL:
        return _finish(
            uow,
            clock,
            ids,
            evidence,
            attempt,
            run,
            status=RecoveryStatus.MANUAL_INTERVENTION,
            detail=plan.notes or "This operation requires manual intervention.",
            state=None,
            checks=[{"check": "manual", "ok": True, "detail": "escalated to a human"}],
            failure_code="MANUAL_REQUIRED",
            actor=actor,
        )

    version = uow.versions.get(run.workflow_version_id)
    if version is None:
        return _escalate(attempt, audit, run, "Version for this run no longer exists.", clock)
    actions = _load_actions(uow, run)

    checkpoint, source = _resolve_checkpoint(engine, run, plan, chosen)
    if checkpoint is None:
        return _escalate(
            attempt,
            audit,
            run,
            "No checkpoint is available for this run.",
            clock,
        )
    attempt.checkpoint_id = checkpoint.id
    attempt.steps.append(
        {
            "step": "select_checkpoint",
            "source": source,
            "checkpoint_id": checkpoint.id,
            "kind": checkpoint.kind,
            "action_ordinal": checkpoint.action_ordinal,
            "outcome": "SELECTED",
        }
    )
    try:
        state = engine.restore_state(checkpoint)
    except ValueError as exc:
        attempt.steps.append(
            {"step": "restore_checkpoint", "outcome": "FAILED", "detail": str(exc)}
        )
        return _finish(
            uow,
            clock,
            ids,
            evidence,
            attempt,
            run,
            status=RecoveryStatus.FAILED,
            detail=(f"The checkpoint failed its integrity check, so it was not applied. {exc}"),
            state=None,
            checks=[{"check": "checkpoint_integrity", "ok": False, "detail": str(exc)}],
            failure_code="STALE_CHECKPOINT",
            actor=actor,
        )

    attempt.before_state_hash = _hash_state(state)
    attempt.steps.append(
        {
            "step": "restore_checkpoint",
            "outcome": "SUCCEEDED",
            "state_hash": attempt.before_state_hash,
        }
    )
    attempt.status = str(RecoveryStatus.APPLYING)

    probe_result: dict[str, Any] | None = None
    resume_at = checkpoint.action_ordinal
    reconciled = False

    if plan.ambiguous:
        attempt_state = engine.checkpoint_at(
            run.id, plan.action_ordinal or 0, CheckpointKind.ATTEMPT_STATE
        )
        probe = plan.probe
        if attempt_state is None or probe is None:
            # No way to inspect the target; require an idempotency mechanism.
            key_path = plan.idempotency_path
            key_value = (
                resolve_path(state, key_path) if key_path and path_exists(state, key_path) else None
            )
            if not key_path or key_value is None:
                return _finish(
                    uow,
                    clock,
                    ids,
                    evidence,
                    attempt,
                    run,
                    status=RecoveryStatus.FAILED,
                    detail=(
                        "The outcome is ambiguous and no probe or idempotency key is "
                        "available. Refusing to retry blindly."
                    ),
                    state=state,
                    checks=[
                        {
                            "check": "ambiguous_outcome",
                            "ok": False,
                            "detail": "no probe state or idempotency key",
                        }
                    ],
                    failure_code="NO_IDEMPOTENCY_MECHANISM",
                    actor=actor,
                )
            probe_result = {
                "effect_present": False,
                "mechanism": "idempotency_key",
                "idempotency_path": key_path,
                "idempotency_value": key_value,
            }
            resume_at = plan.action_ordinal or 0
            attempt.steps.append(
                {
                    "step": "probe_outcome",
                    "outcome": "EFFECT_ABSENT",
                    "detail": f"retry is idempotent on {key_path}={key_value!r}",
                }
            )
        else:
            attempted = engine.restore_state(attempt_state)
            check = evaluate_condition(probe, attempted)
            probe_result = {
                "effect_present": check.ok,
                "mechanism": "target_state_probe",
                "detail": check.detail,
                "state_hash": _hash_state(attempted),
            }
            attempt.steps.append(
                {
                    "step": "probe_outcome",
                    "outcome": "EFFECT_PRESENT" if check.ok else "EFFECT_ABSENT",
                    "detail": check.detail,
                }
            )
            if check.ok:
                state = attempted
                resume_at = (plan.action_ordinal or 0) + 1
                reconciled = True

    if chosen == RecoveryStrategy.COMPENSATE:
        if not plan.compensate:
            return _finish(
                uow,
                clock,
                ids,
                evidence,
                attempt,
                run,
                status=RecoveryStatus.MANUAL_INTERVENTION,
                detail="This action declares no compensating effects.",
                state=state,
                checks=[{"check": "compensating_actions", "ok": False, "detail": "none declared"}],
                failure_code="NO_COMPENSATING_ACTION",
                actor=actor,
            )
        applied = 0
        for effect in plan.compensate:
            try:
                sandbox.apply_effects(state, [effect])
                applied += 1
                attempt.steps.append({"step": "compensate", "outcome": "APPLIED", "effect": effect})
            except Exception as exc:
                attempt.steps.append(
                    {
                        "step": "compensate",
                        "outcome": "FAILED",
                        "effect": effect,
                        "detail": str(exc),
                    }
                )
        checks = _contract_checks(plan.compensate_contract or plan.contract, state)
        ok = applied == len(plan.compensate) and all(item["ok"] for item in checks)
        status = (
            RecoveryStatus.VERIFIED
            if ok
            else (RecoveryStatus.PARTIAL if applied else RecoveryStatus.FAILED)
        )
        detail = (
            "Compensating actions restored the target state and the recovery contract holds."
            if ok
            else "Compensation applied only partly; the recovery contract does not hold."
        )
        if not ok and run.status in {RunStatus.AWAITING_RECOVERY, RunStatus.TIMED_OUT}:
            run.status = str(RunStatus.FAILED)
            run.verdict = str(RunVerdict.FAILED)
        return _finish(
            uow,
            clock,
            ids,
            evidence,
            attempt,
            run,
            status=status,
            detail=detail,
            state=state,
            checks=checks,
            probe=probe_result,
            failure_code=None if ok else "RECOVERY_CONTRACT_FAILED",
            actor=actor,
        )

    # RESUME (and REPLAY, which resumes from the beginning)
    if chosen == RecoveryStrategy.REPLAY:
        resume_at = 0
        state = sandbox.initial_state("standard")

    checks = _contract_checks(plan.contract, state)
    if not all(item["ok"] for item in checks):
        return _finish(
            uow,
            clock,
            ids,
            evidence,
            attempt,
            run,
            status=RecoveryStatus.PARTIAL,
            detail="The restored state does not satisfy the declared recovery contract.",
            state=state,
            checks=checks,
            probe=probe_result,
            failure_code="RECOVERY_CONTRACT_FAILED",
            actor=actor,
        )

    resume_action = next((item for item in actions if item.ordinal == resume_at), None)
    preconditions = (
        evaluate_all(resume_action.preconditions, state) if resume_action is not None else []
    )
    failed = [item for item in preconditions if not item.ok]
    checks.append(
        {
            "check": "resume_preconditions",
            "ok": not failed,
            "detail": "; ".join(item.detail for item in failed)
            if failed
            else "all declared preconditions are true",
        }
    )
    if failed:
        return _finish(
            uow,
            clock,
            ids,
            evidence,
            attempt,
            run,
            status=RecoveryStatus.FAILED,
            detail="The checkpoint restored, but the resume preconditions are false.",
            state=state,
            checks=checks,
            probe=probe_result,
            failure_code="RESUME_PRECONDITION_FAILED",
            actor=actor,
        )

    attempt.steps.append(
        {
            "step": "resume",
            "resume_ordinal": resume_at,
            "reconciled": reconciled,
            "outcome": "APPLYING",
        }
    )
    attempt.status = str(RecoveryStatus.APPLIED)
    audit.append(
        run.id,
        EventType.RECOVERY_APPLIED,
        actor=actor_kind,
        actor_id=actor,
        payload={
            "attempt_id": attempt.id,
            "checkpoint_id": checkpoint.id,
            "resume_ordinal": resume_at,
            "reconciled": reconciled,
            "checks": checks,
            # The run-level "recovery verifies resume preconditions" invariant reads
            # this flag; it is the machine-checkable part of the recovery contract.
            "preconditions_satisfied": True,
        },
    )

    invariants = list(uow.invariants.list(workflow_version_id=version.id))
    engine.resume_run(
        run=run,
        actions=actions,
        invariants=invariants,
        state=state,
        start_ordinal=resume_at,
        options=ExecuteOptions(
            trigger=RunTrigger(trigger_value(run)),
            origin=Origin(run.origin),
            seed=run.seed,
            actor=actor,
            actor_kind=actor_kind,
            authorized_ordinals=_authorized(uow, run, actions),
            inputs=dict(run.inputs or {}),
        ),
    )

    verified = run.verdict == RunVerdict.VERIFIED
    checks.append(
        {
            "check": "run_verification",
            "ok": verified,
            "detail": f"run verdict is {run.verdict}"
            + ("" if verified else f" ({run.failure_code})"),
        }
    )
    return _finish(
        uow,
        clock,
        ids,
        evidence,
        attempt,
        run,
        status=RecoveryStatus.VERIFIED if verified else RecoveryStatus.FAILED,
        detail=(
            "The run resumed from the verified checkpoint and all invariants passed."
            if verified
            else "The run resumed but did not verify; dependent actions were not completed."
        ),
        state=state,
        checks=checks,
        probe=probe_result,
        failure_code=None if verified else run.failure_code,
        actor=actor,
    )


def _contract_checks(
    conditions: Sequence[dict[str, Any]], state: dict[str, Any]
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for condition in conditions:
        result = evaluate_condition(condition, state)
        checks.append(
            {
                "check": f"contract:{condition.get('path', 'condition')}",
                "ok": result.ok,
                "detail": result.detail,
            }
        )
    if not conditions:
        checks.append(
            {
                "check": "contract",
                "ok": True,
                "detail": "no explicit contract declared; preconditions and verification apply",
            }
        )
    return checks


def _authorized(uow: UnitOfWork, run: ExecutionRun, actions: list[Any]) -> set[int]:
    """Preserve a grant this run already holds.

    A sandbox pre-authorization is recorded in the audit chain and remains valid
    for the run it was issued for. Recovering a run must not silently drop it and
    stall on an approval the operator already gave.
    """
    ordinals: set[int] = set()
    for event in uow.events.read(run.id):
        if event.type != EventType.AUTHORIZATION_PRE_GRANTED:
            continue
        for value in (event.payload or {}).get("ordinals") or []:
            try:
                ordinals.add(int(value))
            except (TypeError, ValueError):
                continue
    if run.trigger == RunTrigger.CHAOS:
        ordinals |= {item.ordinal for item in actions if item.requires_authorization}
    return ordinals


def _escalate(
    attempt: RecoveryAttempt,
    audit: AuditWriter,
    run: ExecutionRun,
    detail: str,
    clock: Clock,
) -> RecoveryAttempt:
    attempt.status = str(RecoveryStatus.ESCALATED)
    attempt.detail = detail
    attempt.ended_at = clock.now()
    attempt.steps = [*attempt.steps, {"step": "escalate", "outcome": "ESCALATED", "detail": detail}]
    if run.status != RunStatus.FAILED:
        run.status = RunStatus.FAILED.value
        run.verdict = RunVerdict.FAILED.value
    audit.append(
        run.id,
        EventType.RECOVERY_FAILED,
        actor=ActorKind.SYSTEM,
        actor_id="recovery",
        payload={"attempt_id": attempt.id, "detail": detail, "escalated": True},
    )
    return attempt


def record_manual_note(
    uow: UnitOfWork,
    clock: Clock,
    ids: IdFactory,
    *,
    attempt_id: str,
    note: str,
    actor: str,
) -> RecoveryAttempt:
    attempt = get_attempt(uow, attempt_id)
    if attempt.status not in {
        RecoveryStatus.MANUAL_INTERVENTION,
        RecoveryStatus.ESCALATED,
        RecoveryStatus.PARTIAL,
        RecoveryStatus.FAILED,
    }:
        raise ConflictError(
            "Only a failed, partial, escalated or manual recovery accepts a manual note.",
            code="RECOVERY_NOT_OPEN",
        )
    attempt.manual_note = note
    attempt.ended_at = clock.now()
    audit = AuditWriter(uow, clock, ids)
    audit.append(
        attempt.run_id,
        EventType.RECOVERY_MANUAL_INTERVENTION,
        actor=ActorKind.HUMAN,
        actor_id=actor,
        payload={"attempt_id": attempt.id, "note": note},
    )
    uow.flush()
    return attempt


def trigger_value(run: ExecutionRun) -> str:
    if run.trigger == RunTrigger.CHAOS:
        return RunTrigger.CHAOS
    if run.trigger == RunTrigger.DEMO:
        return RunTrigger.DEMO
    if run.trigger == RunTrigger.REGRESSION:
        return RunTrigger.REGRESSION
    return RunTrigger.MANUAL
