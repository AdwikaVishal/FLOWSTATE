"""Human decision engine.

A decision is not an override switch. Every action re-checks authority, evidence
freshness and node preconditions before a run is allowed to move. When a check
fails the run stays blocked and the reason is audited.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import update

from flowstate.clock import Clock
from flowstate.config import Settings
from flowstate.db.models import (
    Blocker,
    BlockerEvidence,
    CapabilityToken,
    DecisionRecord,
    ExecutionEvent,
    ExecutionRun,
    HumanDecision,
)
from flowstate.domain.conditions import evaluate_condition
from flowstate.domain.enums import (
    ActorKind,
    ActorRole,
    DecisionStatus,
    EventType,
    Origin,
    RunStatus,
    RunTrigger,
    RunVerdict,
)
from flowstate.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from flowstate.ids import IdFactory
from flowstate.repositories import UnitOfWork
from flowstate.services import blockers as blocker_service
from flowstate.services.audit import AuditWriter
from flowstate.services.execution import ExecuteOptions, ExecutionEngine
from flowstate.services.handlers import evaluate_preconditions

OPEN_STATUSES = frozenset(
    {
        DecisionStatus.REQUESTED,
        DecisionStatus.NOTIFIED,
        DecisionStatus.DEFERRED,
        DecisionStatus.INFO_PROVIDED,
    }
)

CLOSED_STATUSES = frozenset(
    {DecisionStatus.DENIED, DecisionStatus.EXPIRED, DecisionStatus.CANCELLED}
)


def _signature(
    settings: Settings, *, decision_id: str, run_id: str, ordinal: int, expires_at: str
) -> str:
    material = f"{decision_id}|{run_id}|{ordinal}|{expires_at}"
    return hmac.new(
        settings.secret_key.encode("utf-8"), material.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _load_actions_and_invariants(uow: UnitOfWork, version_id: str) -> tuple[list[Any], list[Any]]:
    actions = list(uow.actions.list(workflow_version_id=version_id, order_by="ordinal"))
    invariants = list(uow.invariants.list(workflow_version_id=version_id))
    return actions, invariants


def get_decision(uow: UnitOfWork, decision_id: str) -> HumanDecision:
    decision = uow.decisions.get(decision_id)
    if decision is None:
        raise NotFoundError(f"Decision {decision_id} was not found.", code="DECISION_NOT_FOUND")
    return decision


def get_blocker(uow: UnitOfWork, decision: HumanDecision) -> Blocker | None:
    if not decision.blocker_id:
        return None
    return uow.blockers.get(decision.blocker_id)


def evidence_rows(uow: UnitOfWork, decision: HumanDecision) -> list[BlockerEvidence]:
    blocker = get_blocker(uow, decision)
    if blocker is None:
        return []
    return list(uow.blocker_evidence.list(blocker_id=blocker.id))


def history_rows(uow: UnitOfWork, decision_id: str) -> list[DecisionRecord]:
    rows = list(uow.decision_records.list(decision_id=decision_id))
    rows.sort(key=lambda item: item.created_at)
    return rows


def permitted_actions(decision: HumanDecision, blocker: Blocker | None) -> list[str]:
    actions = ["approve", "reject", "defer"]
    if blocker is not None and blocker.input_spec:
        actions.insert(2, "provide_info")
    return actions


def _record(
    uow: UnitOfWork,
    ids: IdFactory,
    clock: Clock,
    decision: HumanDecision,
    *,
    action: str,
    actor: str,
    role: ActorRole,
    note: str,
    outcome: str,
    payload: dict[str, Any] | None = None,
) -> DecisionRecord:
    row = DecisionRecord(
        id=ids.new(),
        decision_id=decision.id,
        run_id=decision.run_id,
        action=action,
        actor=actor,
        actor_role=str(role),
        note=note,
        outcome=outcome,
        payload=payload or {},
        created_at=clock.now(),
    )
    uow.decision_records.add(row)
    return row


def _assert_human(actor_kind: ActorKind) -> None:
    if actor_kind != ActorKind.HUMAN:
        raise ForbiddenError(
            "Only a human actor may decide a request.", code="HUMAN_ACTOR_REQUIRED"
        )


def _assert_authorized(decision: HumanDecision, role: ActorRole) -> None:
    required = decision.required_role or str(ActorRole.ADMIN)
    if not blocker_service.role_satisfies(role, required):
        raise ForbiddenError(
            f"This decision requires the {required} role; the acting role is {role}.",
            code="ROLE_NOT_AUTHORIZED",
            details={"required_role": required, "actor_role": str(role)},
        )


def _assert_open(decision: HumanDecision, now: datetime) -> None:
    if decision.status in CLOSED_STATUSES or decision.status == DecisionStatus.APPLIED:
        raise ConflictError("This decision is closed.", code="DECISION_CLOSED")
    if decision.status == DecisionStatus.AUTHORIZED:
        raise ConflictError("This decision has already been authorized.", code="DECISION_CLOSED")
    if now >= decision.expires_at:
        raise ConflictError(
            "This decision expired. Evidence must be re-collected before it can be decided.",
            code="DECISION_EXPIRED",
            details={"expires_at": decision.expires_at.isoformat()},
        )


def _set_path(state: dict[str, Any], path: str, value: Any) -> None:
    parts = [part for part in path.split(".") if part]
    if not parts:
        return
    cursor: Any = state
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value


def _apply_input(state: dict[str, Any], blocker: Blocker, values: dict[str, Any]) -> dict[str, Any]:
    spec = blocker.input_spec or []
    if not spec:
        raise ValidationError(
            "This blocker does not declare any missing input.", code="NO_INPUT_REQUIRED"
        )
    allowed = {str(item.get("key")): item for item in spec if isinstance(item, dict)}
    unknown = sorted(set(values) - set(allowed))
    if unknown:
        raise ValidationError(
            "Unknown input field(s) supplied.",
            code="UNKNOWN_INPUT_FIELD",
            details={"fields": unknown},
        )
    for key, item in allowed.items():
        if key not in values:
            continue
        value = values[key]
        if item.get("required") and (value is None or value == ""):
            raise ValidationError(
                f"{item.get('label', key)} is required.",
                code="MISSING_INPUT_VALUE",
                details={"field": key},
            )
        _set_path(state, str(item.get("path") or key), value)
    return state


@dataclass
class Revalidation:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stale: bool = False
    changed: bool = False
    state: dict[str, Any] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append({"check": name, "ok": ok, "detail": detail})
        if not ok:
            self.reasons.append(f"{name}: {detail}")


def _node_for(actions: Sequence[Any], decision: HumanDecision) -> Any:
    from flowstate.domain.graph import build_graph

    graph = build_graph(actions)
    for node in graph.execution_nodes:
        if node.key == decision.node_key:
            return node
    for node in graph.execution_nodes:
        if node.ordinal == decision.action_ordinal:
            return node
    return None


def _revalidate(
    uow: UnitOfWork,
    clock: Clock,
    engine: ExecutionEngine,
    decision: HumanDecision,
    *,
    role: ActorRole,
    provided_input: dict[str, Any] | None = None,
) -> Revalidation:
    now = clock.now()
    result = Revalidation(ok=True)
    run = uow.runs.get(decision.run_id)
    if run is None:
        raise NotFoundError("The run for this decision no longer exists.", code="RUN_NOT_FOUND")

    result.check("decision_open", decision.status in OPEN_STATUSES, f"status is {decision.status}")

    checkpoint = engine.latest_checkpoint(run.id, upto_ordinal=decision.action_ordinal)
    if checkpoint is None:
        result.check("checkpoint", False, "no checkpoint is available to restore")
        result.ok = False
        return result

    state = engine.restore_state(checkpoint)
    blocker = get_blocker(uow, decision)
    if blocker is not None and provided_input:
        state = _apply_input(state, blocker, provided_input)

    events = list(uow.events.read(run.id))
    if blocker is not None:
        items, digest = blocker_service.current_evidence(
            uow, blocker, state, now=now, events=events
        )
        expired = [item for item in items if item.get("validation") in {"EXPIRED", "MISSING"}]
        if expired:
            result.stale = True
            result.check(
                "evidence_fresh",
                False,
                "; ".join(f"{item['label']} is {item['validation'].lower()}" for item in expired),
            )
        else:
            result.check("evidence_fresh", True, "all evidence is within its validity window")

        if decision.evidence_hash and digest != decision.evidence_hash and not provided_input:
            result.changed = True
            result.check(
                "evidence_unchanged",
                False,
                "the underlying records changed after the request was raised",
            )
        else:
            result.check("evidence_unchanged", True, "evidence hash matches the request")

        conditions = blocker.resume_conditions or []
        for condition in conditions:
            if not isinstance(condition, dict):
                continue
            check = evaluate_condition(condition, state, now=now)
            result.check(
                f"resume_condition:{condition.get('code') or condition.get('path', 'condition')}",
                check.ok,
                check.detail,
            )

    version = uow.versions.get(run.workflow_version_id)
    if version is None:
        result.check("version", False, "the workflow version no longer exists")
        result.ok = False
        return result
    actions, _invariants = _load_actions_and_invariants(uow, version.id)
    node = _node_for(actions, decision)
    if node is None:
        result.check("node", False, "the blocked node is no longer in this version")
        result.ok = False
        return result

    failures = evaluate_preconditions(node, state)
    result.check(
        "preconditions",
        not failures,
        "; ".join(failures) if failures else "all node preconditions hold",
    )

    result.state = state
    result.ok = not result.reasons
    return result


def _refresh_evidence(
    uow: UnitOfWork,
    ids: IdFactory,
    clock: Clock,
    *,
    blocker: Blocker,
    decision: HumanDecision,
    state: dict[str, Any],
    events: Sequence[ExecutionEvent],
) -> None:
    now = clock.now()
    items = blocker_service.build_evidence(
        blocker.evidence_spec or [], state, now=now, events=events
    )
    for row in list(uow.blocker_evidence.list(blocker_id=blocker.id)):
        uow.blocker_evidence.delete(row)
    for item in items:
        uow.blocker_evidence.add(
            BlockerEvidence(
                id=ids.new(),
                blocker_id=blocker.id,
                run_id=blocker.run_id,
                source_kind=str(item["source_kind"]),
                source_path=str(item["source_path"]),
                source_ref=item.get("source_ref"),
                label=str(item["label"]),
                relation=str(item["relation"]),
                value_json=item.get("value") or {},
                validation=str(item["validation"]),
                detail=str(item["detail"]),
                observed_at=now,
                fresh_until=(
                    datetime.fromisoformat(item["fresh_until"]) if item.get("fresh_until") else None
                ),
            )
        )
    blocker.evidence_hash = blocker_service.evidence_hash(items)
    decision.evidence_hash = blocker.evidence_hash


def _blocked_response(
    uow: UnitOfWork,
    clock: Clock,
    ids: IdFactory,
    decision: HumanDecision,
    *,
    code: str,
    message: str,
    revalidation: Revalidation | None = None,
) -> None:
    audit = AuditWriter(uow, clock, ids)
    payload: dict[str, Any] = {"decision_id": decision.id, "code": code, "detail": message}
    if revalidation is not None:
        payload["checks"] = revalidation.checks
        payload["reasons"] = revalidation.reasons
    audit.append(
        decision.run_id,
        EventType.DECISION_EVIDENCE_CHANGED
        if code == "EVIDENCE_STALE"
        else EventType.BLOCKER_DETECTED,
        action_ordinal=decision.action_ordinal,
        node_key=decision.node_key,
        payload=payload,
    )
    # This refusal is a persisted outcome: the refreshed packet and the audit
    # event must survive the problem response the client receives.
    uow.commit()
    raise ConflictError(message, code=code, details=payload)


def approve(
    uow: UnitOfWork,
    clock: Clock,
    ids: IdFactory,
    settings: Settings,
    engine: ExecutionEngine,
    *,
    decision_id: str,
    actor_id: str,
    actor_role: ActorRole = ActorRole.ADMIN,
    actor_kind: ActorKind = ActorKind.HUMAN,
    note: str = "",
) -> HumanDecision:
    _assert_human(actor_kind)
    decision = get_decision(uow, decision_id)
    now = clock.now()
    if decision.status == DecisionStatus.APPLIED:
        _assert_authorized(decision, actor_role)
        return decision

    try:
        _assert_open(decision, now)
    except ConflictError as exc:
        if exc.code == "DECISION_EXPIRED":
            expire_if_due(uow, clock, ids, decision)
            uow.commit()
        raise

    try:
        _assert_authorized(decision, actor_role)
    except ForbiddenError as exc:
        audit = AuditWriter(uow, clock, ids)
        audit.append(
            decision.run_id,
            EventType.DECISION_ROLE_DENIED,
            actor=actor_kind,
            actor_id=actor_id,
            action_ordinal=decision.action_ordinal,
            node_key=decision.node_key,
            payload={
                "decision_id": decision.id,
                "required_role": decision.required_role,
                "actor_role": str(actor_role),
            },
        )
        _record(
            uow,
            ids,
            clock,
            decision,
            action="APPROVE",
            actor=actor_id,
            role=actor_role,
            note=note,
            outcome="DENIED_ROLE",
            payload={"code": "ROLE_NOT_AUTHORIZED"},
        )
        uow.commit()
        raise exc

    revalidation = _revalidate(uow, clock, engine, decision, role=actor_role)
    if not revalidation.ok:
        blocker = get_blocker(uow, decision)
        if blocker is not None:
            _refresh_evidence(
                uow,
                ids,
                clock,
                blocker=blocker,
                decision=decision,
                state=revalidation.state,
                events=list(uow.events.read(decision.run_id)),
            )
        decision.status = str(DecisionStatus.NOTIFIED)
        refreshed_blocker = get_blocker(uow, decision)
        if refreshed_blocker is not None:
            decision.evidence_hash = refreshed_blocker.evidence_hash
        _record(
            uow,
            ids,
            clock,
            decision,
            action="APPROVE",
            actor=actor_id,
            role=actor_role,
            note=note,
            outcome="BLOCKED",
            payload={"reasons": revalidation.reasons},
        )
        code = (
            "EVIDENCE_STALE"
            if (revalidation.stale or revalidation.changed)
            else "RESUME_VALIDATION_FAILED"
        )
        _blocked_response(
            uow,
            clock,
            ids,
            decision,
            code=code,
            message=(
                "The run stays blocked: "
                + "; ".join(revalidation.reasons)
                + ". The evidence packet was refreshed for a new decision."
            ),
            revalidation=revalidation,
        )

    token = CapabilityToken(
        id=ids.new(),
        decision_id=decision.id,
        run_id=decision.run_id,
        action_ordinal=decision.action_ordinal,
        signature=_signature(
            settings,
            decision_id=decision.id,
            run_id=decision.run_id,
            ordinal=decision.action_ordinal,
            expires_at=decision.expires_at.isoformat(),
        ),
        issued_at=now,
        expires_at=decision.expires_at,
    )
    claimed = uow.session.execute(
        update(HumanDecision)
        .where(
            HumanDecision.id == decision.id,
            HumanDecision.status == str(decision.status),
            HumanDecision.expires_at > now,
        )
        .values(
            status=str(DecisionStatus.AUTHORIZED),
            decided_at=now,
            decided_by=actor_id,
            decided_role=str(actor_role),
            capability_token_id=token.id,
        )
        .returning(HumanDecision.id)
        .execution_options(synchronize_session=False)
    )
    if claimed.scalar_one_or_none() is None:
        raise ConflictError(
            "Another request has already claimed this decision.",
            code="DECISION_CLAIMED",
        )
    uow.tokens.add(token)

    decision.status = str(DecisionStatus.AUTHORIZED)
    decision.decided_at = now
    decision.decided_by = actor_id
    decision.decided_role = str(actor_role)
    decision.capability_token_id = token.id
    audit = AuditWriter(uow, clock, ids)
    audit.append(
        decision.run_id,
        EventType.DECISION_GRANTED,
        actor=actor_kind,
        actor_id=actor_id,
        action_ordinal=decision.action_ordinal,
        node_key=decision.node_key,
        payload={
            "decision_id": decision.id,
            "token_id": token.id,
            "actor_role": str(actor_role),
            "evidence_hash": decision.evidence_hash,
            "checks": revalidation.checks,
            "note": note,
        },
    )
    _record(
        uow,
        ids,
        clock,
        decision,
        action="APPROVE",
        actor=actor_id,
        role=actor_role,
        note=note,
        outcome="AUTHORIZED",
        payload={"token_id": token.id, "checks": revalidation.checks},
    )

    _consume_and_resume(
        uow,
        clock,
        ids,
        engine,
        decision,
        token,
        actor_id,
        actor_role,
        state=revalidation.state,
    )
    decision.status = str(DecisionStatus.APPLIED)
    blocker = get_blocker(uow, decision)
    if blocker is not None:
        blocker_service.resolve_blockers(
            uow, run_id=decision.run_id, now=now, node_key=blocker.node_key, decision_id=decision.id
        )
        audit.append(
            decision.run_id,
            EventType.BLOCKER_RESOLVED,
            action_ordinal=decision.action_ordinal,
            node_key=blocker.node_key,
            payload={
                "blocker_id": blocker.id,
                "decision_id": decision.id,
                "resolution": "APPROVED",
            },
        )
    resumed_run = uow.runs.get(decision.run_id)
    _record(
        uow,
        ids,
        clock,
        decision,
        action="RESUME",
        actor=actor_id,
        role=actor_role,
        note="",
        outcome="APPLIED",
        payload={"run_status": resumed_run.status if resumed_run is not None else None},
    )
    uow.flush()
    return decision


def reject(
    uow: UnitOfWork,
    clock: Clock,
    ids: IdFactory,
    *,
    decision_id: str,
    actor_id: str,
    actor_role: ActorRole = ActorRole.ADMIN,
    reason: str | None = None,
    actor_kind: ActorKind = ActorKind.HUMAN,
) -> HumanDecision:
    _assert_human(actor_kind)
    decision = get_decision(uow, decision_id)
    now = clock.now()
    _assert_open(decision, now)
    try:
        _assert_authorized(decision, actor_role)
    except ForbiddenError:
        _record(
            uow,
            ids,
            clock,
            decision,
            action="REJECT",
            actor=actor_id,
            role=actor_role,
            note=reason or "",
            outcome="DENIED_ROLE",
            payload={"code": "ROLE_NOT_AUTHORIZED"},
        )
        uow.commit()
        raise

    decision.status = str(DecisionStatus.DENIED)
    decision.decided_at = now
    decision.decided_by = actor_id
    decision.decided_role = str(actor_role)
    audit = AuditWriter(uow, clock, ids)
    audit.append(
        decision.run_id,
        EventType.DECISION_DENIED,
        actor=actor_kind,
        actor_id=actor_id,
        action_ordinal=decision.action_ordinal,
        node_key=decision.node_key,
        payload={
            "decision_id": decision.id,
            "reason": reason or "Rejected by operator",
            "actor_role": str(actor_role),
        },
    )

    run = uow.runs.get(decision.run_id)
    if run is not None:
        run.status = str(RunStatus.FAILED)
        run.verdict = str(RunVerdict.FAILED)
        run.failure_code = "DECISION_DENIED"
        run.failure_message = f"Human decision denied: {reason or 'no reason given'}."
        run.ended_at = now
        audit.append(
            run.id,
            EventType.RUN_FAILED,
            action_ordinal=decision.action_ordinal,
            payload={"code": "DECISION_DENIED", "reason": reason or ""},
        )
    blocker_service.resolve_blockers(uow, run_id=decision.run_id, now=now, decision_id=decision.id)
    _record(
        uow,
        ids,
        clock,
        decision,
        action="REJECT",
        actor=actor_id,
        role=actor_role,
        note=reason or "",
        outcome="REJECTED",
        payload={},
    )
    uow.flush()
    return decision


def provide_info(
    uow: UnitOfWork,
    clock: Clock,
    ids: IdFactory,
    engine: ExecutionEngine,
    *,
    decision_id: str,
    actor_id: str,
    values: dict[str, Any],
    actor_role: ActorRole = ActorRole.ADMIN,
    note: str = "",
    actor_kind: ActorKind = ActorKind.HUMAN,
) -> HumanDecision:
    _assert_human(actor_kind)
    decision = get_decision(uow, decision_id)
    now = clock.now()
    _assert_open(decision, now)
    _assert_authorized(decision, actor_role)
    blocker = get_blocker(uow, decision)
    if blocker is None:
        raise ConflictError("This decision is not attached to a blocker.", code="NO_BLOCKER")

    decision.provided_input = {**dict(decision.provided_input or {}), **values}
    decision.status = str(DecisionStatus.INFO_PROVIDED)
    decision.decided_by = actor_id
    decision.decided_role = str(actor_role)
    decision.decided_at = now

    audit = AuditWriter(uow, clock, ids)
    audit.append(
        decision.run_id,
        EventType.DECISION_INFO_PROVIDED,
        actor=actor_kind,
        actor_id=actor_id,
        action_ordinal=decision.action_ordinal,
        node_key=decision.node_key,
        payload={
            "decision_id": decision.id,
            "blocker_id": blocker.id,
            "fields": sorted(values),
            "actor_role": str(actor_role),
        },
    )

    revalidation = _revalidate(
        uow, clock, engine, decision, role=actor_role, provided_input=decision.provided_input
    )

    if not revalidation.ok:
        _refresh_evidence(
            uow,
            ids,
            clock,
            blocker=blocker,
            decision=decision,
            state=revalidation.state,
            events=list(uow.events.read(decision.run_id)),
        )
        decision.status = str(DecisionStatus.NOTIFIED)
        decision.evidence_hash = blocker.evidence_hash
        _record(
            uow,
            ids,
            clock,
            decision,
            action="PROVIDE_INFO",
            actor=actor_id,
            role=actor_role,
            note=note,
            outcome="STILL_BLOCKED",
            payload={"reasons": revalidation.reasons, "values": values},
        )
        _blocked_response(
            uow,
            clock,
            ids,
            decision,
            code="INFO_INSUFFICIENT",
            message=(
                "The supplied information did not clear the blocker: "
                + "; ".join(revalidation.reasons)
            ),
            revalidation=revalidation,
        )

    _consume_and_resume(
        uow,
        clock,
        ids,
        engine,
        decision,
        None,
        actor_id,
        actor_role,
        state=revalidation.state,
    )
    decision.status = str(DecisionStatus.APPLIED)
    blocker_service.resolve_blockers(
        uow, run_id=decision.run_id, now=now, node_key=blocker.node_key, decision_id=decision.id
    )
    audit.append(
        decision.run_id,
        EventType.BLOCKER_RESOLVED,
        action_ordinal=decision.action_ordinal,
        node_key=decision.node_key,
        payload={
            "blocker_id": blocker.id,
            "decision_id": decision.id,
            "resolution": "INFO_PROVIDED",
        },
    )
    _record(
        uow,
        ids,
        clock,
        decision,
        action="PROVIDE_INFO",
        actor=actor_id,
        role=actor_role,
        note=note,
        outcome="APPLIED",
        payload={"values": values},
    )
    uow.flush()
    return decision


def defer(
    uow: UnitOfWork,
    clock: Clock,
    ids: IdFactory,
    *,
    decision_id: str,
    actor_id: str,
    reason: str = "",
    until: datetime | None = None,
    actor_role: ActorRole = ActorRole.OPERATOR,
    actor_kind: ActorKind = ActorKind.HUMAN,
) -> HumanDecision:
    _assert_human(actor_kind)
    decision = get_decision(uow, decision_id)
    now = clock.now()
    _assert_open(decision, now)

    decision.status = str(DecisionStatus.DEFERRED)
    decision.deferred_until = until or (now + timedelta(hours=4))
    decision.decided_by = actor_id
    decision.decided_role = str(actor_role)
    audit = AuditWriter(uow, clock, ids)
    audit.append(
        decision.run_id,
        EventType.DECISION_DEFERRED,
        actor=actor_kind,
        actor_id=actor_id,
        action_ordinal=decision.action_ordinal,
        node_key=decision.node_key,
        payload={
            "decision_id": decision.id,
            "reason": reason,
            "deferred_until": decision.deferred_until.isoformat(),
        },
    )
    _record(
        uow,
        ids,
        clock,
        decision,
        action="DEFER",
        actor=actor_id,
        role=actor_role,
        note=reason,
        outcome="DEFERRED",
        payload={"deferred_until": decision.deferred_until.isoformat()},
    )
    uow.flush()
    return decision


def _consume_and_resume(
    uow: UnitOfWork,
    clock: Clock,
    ids: IdFactory,
    engine: ExecutionEngine,
    decision: HumanDecision,
    token: CapabilityToken | None,
    actor_id: str,
    actor_role: ActorRole,
    *,
    state: dict[str, Any] | None = None,
) -> None:
    run = uow.runs.get(decision.run_id)
    if run is None:
        raise NotFoundError("The run for this decision no longer exists.", code="RUN_NOT_FOUND")
    if run.status != RunStatus.AWAITING_DECISION:
        raise ConflictError(
            f"Run is {run.status}, not waiting on a decision.", code="RUN_NOT_WAITING"
        )
    if token is not None:
        if token.consumed_at is not None:
            raise ConflictError(
                "This capability token has already been used.", code="TOKEN_CONSUMED"
            )
        if clock.now() >= token.expires_at:
            raise ConflictError("This capability token has expired.", code="TOKEN_EXPIRED")
        token.consumed_at = clock.now()

    version = uow.versions.get(run.workflow_version_id)
    if version is None:
        raise NotFoundError("Version for this run no longer exists.", code="VERSION_NOT_FOUND")
    actions, invariants = _load_actions_and_invariants(uow, version.id)
    channels = {
        str(getattr(action, "channel", "SIMULATED") or "SIMULATED").upper() for action in actions
    }
    browser_replay = "BROWSER" in channels

    if browser_replay:
        # Browser page state is deliberately not persisted. Approval therefore
        # rebuilds the page by replaying the read-only prefix from ordinal zero,
        # then runs the authorized node in the same run and audit chain.
        state = {"page": {}, "node_outputs": {}}
    elif state is None:
        checkpoint = engine.latest_checkpoint(run.id, upto_ordinal=decision.action_ordinal)
        if checkpoint is None:
            raise ConflictError(
                "No checkpoint is available for this decision.", code="NO_CHECKPOINT"
            )
        state = engine.restore_state(checkpoint)

    engine.resume_run(
        run=run,
        actions=actions,
        invariants=invariants,
        state=state,
        start_ordinal=0 if browser_replay else decision.action_ordinal,
        options=ExecuteOptions(
            trigger=RunTrigger(decision_trigger(run)),
            origin=Origin(run.origin),
            seed=run.seed,
            actor=actor_id,
            actor_kind=ActorKind.HUMAN,
            actor_role=actor_role,
            authorized_ordinals=_granted_ordinals(uow, run, decision.action_ordinal),
            inputs=dict(run.inputs or {}),
        ),
    )


def _granted_ordinals(uow: UnitOfWork, run: ExecutionRun, current: int) -> set[int]:
    """Keep every human grant this run already holds.

    A browser replay re-enters earlier nodes, so carrying prior approvals
    forward avoids asking the same question twice while still requiring the
    current decision before the blocked node can execute.
    """
    ordinals = {current}
    for event in uow.events.read(run.id):
        if event.type == EventType.DECISION_GRANTED and event.action_ordinal is not None:
            ordinals.add(int(event.action_ordinal))
        elif event.type == EventType.AUTHORIZATION_PRE_GRANTED:
            for value in (event.payload or {}).get("ordinals") or []:
                try:
                    ordinals.add(int(value))
                except (TypeError, ValueError):
                    continue
    return ordinals


def decision_trigger(run: ExecutionRun) -> str:
    if run.trigger == RunTrigger.CHAOS:
        return RunTrigger.CHAOS
    if run.trigger == RunTrigger.DEMO:
        return RunTrigger.DEMO
    if run.trigger == RunTrigger.REGRESSION:
        return RunTrigger.REGRESSION
    return RunTrigger.MANUAL


def expire_if_due(uow: UnitOfWork, clock: Clock, ids: IdFactory, decision: HumanDecision) -> bool:
    if decision.status not in OPEN_STATUSES:
        return False
    if clock.now() < decision.expires_at:
        return False
    decision.status = str(DecisionStatus.EXPIRED)
    decision.decided_at = clock.now()
    audit = AuditWriter(uow, clock, ids)
    audit.append(
        decision.run_id,
        EventType.DECISION_EXPIRED,
        action_ordinal=decision.action_ordinal,
        node_key=decision.node_key,
        payload={"decision_id": decision.id, "expires_at": decision.expires_at.isoformat()},
    )
    run = uow.runs.get(decision.run_id)
    if run is not None and run.status == RunStatus.AWAITING_DECISION:
        run.status = str(RunStatus.FAILED)
        run.verdict = str(RunVerdict.FAILED)
        run.failure_code = "DECISION_EXPIRED"
        run.failure_message = "The authorization request expired before a human responded."
        run.ended_at = clock.now()
        audit.append(
            run.id,
            EventType.RUN_FAILED,
            action_ordinal=decision.action_ordinal,
            payload={"code": "DECISION_EXPIRED"},
        )
    blocker_service.resolve_blockers(
        uow, run_id=decision.run_id, now=clock.now(), decision_id=decision.id
    )
    return True


def blocker_state(uow: UnitOfWork, decision: HumanDecision) -> dict[str, Any]:
    blocker = get_blocker(uow, decision)
    if blocker is None:
        return {}
    return {
        "id": blocker.id,
        "kind": blocker.kind,
        "code": blocker.code,
        "title": blocker.title,
        "detail": blocker.detail,
        "status": blocker.status,
        "required_role": blocker.required_role,
        "node_key": blocker.node_key,
        "action_ordinal": blocker.action_ordinal,
        "resume_conditions": blocker.resume_conditions,
        "input_spec": blocker.input_spec,
        "evidence_hash": blocker.evidence_hash,
        "created_at": blocker.created_at,
    }
