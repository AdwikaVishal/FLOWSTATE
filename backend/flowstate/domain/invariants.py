"""Invariant evaluation over final state and execution history."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from flowstate.db.models import ExecutionEvent, Invariant
from flowstate.domain.conditions import (
    MISSING,
    ConditionResult,
    evaluate_condition,
    resolve_path,
)
from flowstate.domain.enums import EventType, InvariantVerdict

HISTORY_OPS = frozenset({"exists", "absent", "count_eq", "count_gte", "count_lte"})


@dataclass(frozen=True)
class InvariantResult:
    invariant_id: str
    name: str
    severity: str
    verdict: InvariantVerdict
    detail: str
    observed: dict[str, Any] = field(default_factory=dict)


def _history_events(
    events: Sequence[ExecutionEvent], key: str, ordinal: int | None
) -> list[ExecutionEvent]:
    matched = [item for item in events if item.type == key]
    if ordinal is not None:
        matched = [item for item in matched if item.action_ordinal == ordinal]
    return matched


def _compare(expression: dict[str, Any], state: dict[str, Any]) -> ConditionResult:
    left_path = str(expression.get("left", ""))
    right_path = str(expression.get("right_path", ""))
    left = resolve_path(state, left_path)
    if right_path:
        right = resolve_path(state, right_path)
        right_label = right_path
    else:
        right = expression.get("right", expression.get("value"))
        right_label = repr(right)
    left_label = f"{left_path}={left!r}"
    if left is MISSING or right is MISSING:
        return ConditionResult(
            False, f"missing comparison path ({left_label}, {right_label})", None
        )
    op = str(expression.get("op", "eq"))
    checks = {
        "eq": left == right,
        "ne": left != right,
        "gt": left > right,
        "gte": left >= right,
        "lt": left < right,
        "lte": left <= right,
    }
    if op not in checks:
        return ConditionResult(False, f"unsupported comparison operator {op!r}", left)
    return ConditionResult(checks[op], f"{left_label} {op} {right_label}", left)


def _clause(clause: Any, state: dict[str, Any]) -> ConditionResult:
    if not isinstance(clause, dict):
        return ConditionResult(False, "invariant clause is not an object", None)
    if str(clause.get("kind", "state")) == "compare":
        return _compare(clause, state)
    return evaluate_condition(clause, state)


def evaluate_invariant(
    invariant: Invariant,
    state: dict[str, Any],
    events: Sequence[ExecutionEvent],
) -> InvariantResult:
    expression = invariant.expression or {}
    kind = str(expression.get("kind", "state"))
    observed: dict[str, Any] = {}

    if kind == "state":
        result = evaluate_condition(expression, state)
        observed = {"path": expression.get("path"), "value": result.observed}
        verdict = InvariantVerdict.SATISFIED if result.ok else InvariantVerdict.VIOLATED
        return InvariantResult(
            invariant.id, invariant.name, invariant.severity, verdict, result.detail, observed
        )

    if kind == "compare":
        result = _compare(expression, state)
        return InvariantResult(
            invariant.id,
            invariant.name,
            invariant.severity,
            InvariantVerdict.SATISFIED if result.ok else InvariantVerdict.VIOLATED,
            result.detail,
            {"left_path": expression.get("left"), "observed": result.observed},
        )

    if kind in {"implies", "policy"}:
        antecedent = _clause(expression.get("if"), state)
        if not antecedent.ok:
            return InvariantResult(
                invariant.id,
                invariant.name,
                invariant.severity,
                InvariantVerdict.SATISFIED,
                f"policy antecedent not active: {antecedent.detail}",
                {"antecedent": antecedent.detail},
            )
        consequent = _clause(expression.get("then"), state)
        return InvariantResult(
            invariant.id,
            invariant.name,
            invariant.severity,
            InvariantVerdict.SATISFIED if consequent.ok else InvariantVerdict.VIOLATED,
            f"{antecedent.detail}; {consequent.detail}",
            {"antecedent": antecedent.detail, "consequent": consequent.detail},
        )

    if kind == "authorization":
        ordinal = expression.get("action_ordinal")
        successes = [
            item
            for item in _history_events(events, EventType.ACTION_SUCCEEDED, ordinal)
            if item.payload.get("gated")
        ]
        violations: list[dict[str, Any]] = []
        for event in successes:
            grants = [
                grant
                for grant in events
                if grant.type in {EventType.DECISION_GRANTED, EventType.AUTHORIZATION_PRE_GRANTED}
                and grant.seq < event.seq
                and (
                    grant.action_ordinal == event.action_ordinal
                    or event.action_ordinal in (grant.payload.get("ordinals") or [])
                )
            ]
            if not event.payload.get("authorized") or not grants:
                violations.append(
                    {
                        "event_seq": event.seq,
                        "action_ordinal": event.action_ordinal,
                        "authorized": event.payload.get("authorized"),
                        "grants": len(grants),
                    }
                )
        detail = (
            "all authorization-gated successes have a prior grant"
            if not violations
            else f"{len(violations)} authorization-gated success(es) had no prior grant"
        )
        return InvariantResult(
            invariant.id,
            invariant.name,
            invariant.severity,
            InvariantVerdict.VIOLATED if violations else InvariantVerdict.SATISFIED,
            detail,
            {"checked": len(successes), "violations": violations},
        )

    if kind == "postcondition":
        ordinal = expression.get("action_ordinal")
        successes = _history_events(events, EventType.ACTION_SUCCEEDED, ordinal)
        failures = _history_events(events, EventType.POSTCONDITION_FAILED, ordinal)
        problems: list[str] = []
        if successes and failures:
            problems.append("an action reported success after a postcondition failure")
        conditions = expression.get("conditions")
        if isinstance(conditions, list) and conditions and successes:
            for condition in conditions:
                result = _clause(condition, state)
                if not result.ok:
                    problems.append(result.detail)
        detail = (
            "; ".join(problems) if problems else "no completed action violated its postcondition"
        )
        return InvariantResult(
            invariant.id,
            invariant.name,
            invariant.severity,
            InvariantVerdict.VIOLATED if problems else InvariantVerdict.SATISFIED,
            detail,
            {"successes": len(successes), "failures": len(failures)},
        )

    if kind == "checkpoint":
        applied = _history_events(events, EventType.RECOVERY_APPLIED, None)

        def _safe_resume(payload: dict[str, Any]) -> bool:
            if payload.get("preconditions_satisfied") is True:
                return True
            checks = payload.get("checks") or []
            match = next(
                (
                    check
                    for check in checks
                    if isinstance(check, dict) and check.get("check") == "resume_preconditions"
                ),
                None,
            )
            return bool(match and match.get("ok") is True)

        unsafe = [item for item in applied if not _safe_resume(item.payload or {})]
        detail = (
            "every recovery verified its resume preconditions"
            if not unsafe
            else f"{len(unsafe)} recovery attempt(s) resumed without true preconditions"
        )
        return InvariantResult(
            invariant.id,
            invariant.name,
            invariant.severity,
            InvariantVerdict.VIOLATED if unsafe else InvariantVerdict.SATISFIED,
            detail,
            {"recoveries": len(applied), "unsafe": len(unsafe)},
        )

    if kind == "history":
        event_type = str(expression.get("event_type", ""))
        ordinal = expression.get("action_ordinal")
        matched = _history_events(events, event_type, ordinal)
        count = len(matched)
        op = str(expression.get("op", "exists"))
        if op not in HISTORY_OPS:
            return InvariantResult(
                invariant.id,
                invariant.name,
                invariant.severity,
                InvariantVerdict.INCONCLUSIVE,
                f"unsupported history operator {op!r}",
                {"count": count},
            )
        expected = expression.get("value")
        checks = {
            "exists": count >= 1,
            "absent": count == 0,
            "count_eq": count == int(expected or 0),
            "count_gte": count >= int(expected or 0),
            "count_lte": count <= int(expected or 0),
        }
        ok = checks[op]
        detail = f"{event_type} count={count} ({op} {expected!r})"
        verdict = InvariantVerdict.SATISFIED if ok else InvariantVerdict.VIOLATED
        return InvariantResult(
            invariant.id,
            invariant.name,
            invariant.severity,
            verdict,
            detail,
            {"count": count, "event_type": event_type},
        )

    return InvariantResult(
        invariant.id,
        invariant.name,
        invariant.severity,
        InvariantVerdict.INCONCLUSIVE,
        f"unknown invariant kind {kind!r}",
        {},
    )


def aggregate(verdicts: Sequence[InvariantVerdict]) -> InvariantVerdict:
    """VIOLATED dominates. Anything without evidence is not a pass."""
    if any(item == InvariantVerdict.VIOLATED for item in verdicts):
        return InvariantVerdict.VIOLATED
    if not verdicts or any(item == InvariantVerdict.INCONCLUSIVE for item in verdicts):
        return InvariantVerdict.INCONCLUSIVE
    return InvariantVerdict.SATISFIED
