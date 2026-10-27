"""Machine-checkable invariants: policy, authorization, postcondition, checkpoint."""

from __future__ import annotations

from datetime import UTC, datetime

from flowstate.db.models import ExecutionEvent, Invariant
from flowstate.domain.enums import EventType, InvariantVerdict
from flowstate.domain.invariants import evaluate_invariant


def event(seq: int, event_type: EventType, payload: dict, *, ordinal: int | None = None):
    return ExecutionEvent(
        id=f"event-{seq}",
        run_id="run-1",
        seq=seq,
        type=str(event_type),
        actor="SYSTEM",
        actor_id="test",
        action_ordinal=ordinal,
        node_key=None,
        payload=payload,
        prev_hash="",
        hash=f"hash-{seq}",
        recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def invariant(expression: dict, *, name: str = "check") -> Invariant:
    return Invariant(
        id="inv-1",
        workflow_version_id="version-1",
        name=name,
        description="",
        kind="POLICY",
        expression=expression,
        severity="CRITICAL",
    )


def test_implies_compare_enforces_matching_totals() -> None:
    expression = {
        "kind": "implies",
        "if": {
            "path": "invoice.status",
            "op": "in",
            "value": ["APPROVED", "PAID"],
        },
        "then": {
            "kind": "compare",
            "left": "invoice.amount_cents",
            "op": "eq",
            "right_path": "purchase_order.amount_cents",
        },
    }
    good = {
        "invoice": {"status": "PAID", "amount_cents": 100},
        "purchase_order": {"amount_cents": 100},
    }
    bad = {
        "invoice": {"status": "PAID", "amount_cents": 100},
        "purchase_order": {"amount_cents": 99},
    }
    assert evaluate_invariant(invariant(expression), good, []).verdict == InvariantVerdict.SATISFIED
    assert evaluate_invariant(invariant(expression), bad, []).verdict == InvariantVerdict.VIOLATED


def test_policy_antecedent_inactive_is_satisfied() -> None:
    expression = {
        "kind": "implies",
        "if": {"path": "invoice.status", "op": "eq", "value": "PAID"},
        "then": {"path": "invoice.ledger_count", "op": "gte", "value": 1},
    }
    state = {"invoice": {"status": "SUBMITTED", "ledger_count": 0}}
    result = evaluate_invariant(invariant(expression), state, [])
    assert result.verdict == InvariantVerdict.SATISFIED
    assert "antecedent not active" in result.detail


def test_authorization_requires_a_prior_grant_event() -> None:
    expression = {"kind": "authorization"}
    success = event(
        2,
        EventType.ACTION_SUCCEEDED,
        {"gated": True, "authorized": True},
        ordinal=5,
    )
    ungraded = evaluate_invariant(invariant(expression), {}, [success])
    assert ungraded.verdict == InvariantVerdict.VIOLATED

    grant = event(1, EventType.AUTHORIZATION_PRE_GRANTED, {"ordinals": [5]})
    granted = evaluate_invariant(invariant(expression), {}, [grant, success])
    assert granted.verdict == InvariantVerdict.SATISFIED


def test_authorization_ignores_ungated_actions() -> None:
    success = event(
        2,
        EventType.ACTION_SUCCEEDED,
        {"gated": False, "authorized": True},
        ordinal=7,
    )
    result = evaluate_invariant(invariant({"kind": "authorization"}), {}, [success])
    assert result.verdict == InvariantVerdict.SATISFIED


def test_completed_action_with_a_postcondition_failure_is_violated() -> None:
    expression = {
        "kind": "postcondition",
        "action_ordinal": 7,
        "conditions": [{"path": "invoice.ledger_count", "op": "gte", "value": 1}],
    }
    success = event(2, EventType.ACTION_SUCCEEDED, {}, ordinal=7)
    failed = event(3, EventType.POSTCONDITION_FAILED, {}, ordinal=7)
    state = {"invoice": {"ledger_count": 1}}
    result = evaluate_invariant(invariant(expression), state, [success, failed])
    assert result.verdict == InvariantVerdict.VIOLATED

    clean = evaluate_invariant(invariant(expression), state, [success])
    assert clean.verdict == InvariantVerdict.SATISFIED


def test_checkpoint_without_true_preconditions_is_violated() -> None:
    expression = {"kind": "checkpoint"}
    unsafe = event(2, EventType.RECOVERY_APPLIED, {"preconditions_satisfied": False})
    safe = event(3, EventType.RECOVERY_APPLIED, {"preconditions_satisfied": True})
    unsafe_result = evaluate_invariant(invariant(expression), {}, [unsafe])
    safe_result = evaluate_invariant(invariant(expression), {}, [safe])
    assert unsafe_result.verdict == InvariantVerdict.VIOLATED
    assert safe_result.verdict == InvariantVerdict.SATISFIED


def test_unknown_invariant_kind_is_inconclusive_not_a_pass() -> None:
    result = evaluate_invariant(invariant({"kind": "not_a_real_check"}), {}, [])
    assert result.verdict == InvariantVerdict.INCONCLUSIVE
