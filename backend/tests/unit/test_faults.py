"""Every boundary fault has a deterministic, classified outcome."""

from __future__ import annotations

import pytest
from flowstate.domain.enums import FaultType
from flowstate.sandbox import invoice

PAYMENT_INPUT = {
    "effects": [
        {"op": "append", "path": "invoice.ledger", "value": {"entry": "debit"}},
        {"op": "increment", "path": "invoice.ledger_count", "value": 1},
        {"op": "set", "path": "invoice.status", "value": "PAID"},
    ],
    "probe": {"path": "invoice.status", "op": "eq", "value": "PAID"},
}

INVOICE_FAULTS = {
    FaultType.TIMEOUT_BEFORE_EXECUTION,
    FaultType.RATE_LIMITED,
    FaultType.NETWORK_DROP,
    FaultType.TRANSIENT_ERROR,
    FaultType.TIMEOUT,
    FaultType.RESPONSE_LOST,
    FaultType.HTTP_500,
    FaultType.MALFORMED_RESPONSE,
    FaultType.STALE_READ,
    FaultType.LATENCY,
    FaultType.DUPLICATE_RESPONSE,
    FaultType.PARTIAL_COMMIT,
    FaultType.POSTCONDITION_FAILURE,
    FaultType.MISSING_FIELD,
    FaultType.CONTRADICTORY_TOTALS,
}
NON_INVOICE_FAULTS = {FaultType.DELAYED_APPROVAL, FaultType.SELECTOR_DRIFT}


def run(fault: FaultType, *, attempt: int = 1, parameters: dict | None = None):
    state = invoice.initial_state()
    result = invoice.execute(
        PAYMENT_INPUT,
        state,
        fault={"type": str(fault), "parameters": parameters or {}},
        attempt=attempt,
    )
    return result, state


def test_every_fault_is_accounted_for() -> None:
    assert set(FaultType) == INVOICE_FAULTS | NON_INVOICE_FAULTS


@pytest.mark.parametrize(
    "fault",
    [FaultType.TIMEOUT_BEFORE_EXECUTION, FaultType.RATE_LIMITED, FaultType.NETWORK_DROP],
)
def test_request_phase_faults_never_apply_the_effect(fault: FaultType) -> None:
    result, state = run(fault)
    assert result.outcome in {invoice.OUTCOME_FAILED, invoice.OUTCOME_UNKNOWN}
    assert state["invoice"]["ledger_count"] == 0
    assert state["invoice"]["status"] == "SUBMITTED"


def test_timeout_before_execution_is_retryable() -> None:
    result, state = run(FaultType.TIMEOUT_BEFORE_EXECUTION, attempt=1)
    assert result.outcome == invoice.OUTCOME_FAILED
    assert result.error_class == "TIMEOUT"
    assert result.error_code == "TIMEOUT_BEFORE_EXECUTION"
    assert result.retryable is True
    assert state["invoice"]["ledger_count"] == 0


def test_rate_limit_is_retryable_before_the_effect() -> None:
    result, state = run(FaultType.RATE_LIMITED, attempt=1, parameters={"failures": 1})
    assert result.outcome == invoice.OUTCOME_FAILED
    assert result.error_class == "RATE_LIMITED"
    assert result.retryable is True
    assert state["invoice"]["ledger_count"] == 0

    retried, retried_state = run(FaultType.RATE_LIMITED, attempt=2, parameters={"failures": 1})
    assert retried.outcome == invoice.OUTCOME_SUCCEEDED
    assert retried_state["invoice"]["ledger_count"] == 1


def test_transient_error_succeeds_only_after_its_budget() -> None:
    first, _ = run(FaultType.TRANSIENT_ERROR, attempt=1, parameters={"failures": 1})
    second, state = run(FaultType.TRANSIENT_ERROR, attempt=2, parameters={"failures": 1})
    assert first.outcome == invoice.OUTCOME_FAILED
    assert second.outcome == invoice.OUTCOME_SUCCEEDED
    assert state["invoice"]["ledger_count"] == 1


@pytest.mark.parametrize(
    "fault",
    [
        FaultType.TIMEOUT,
        FaultType.RESPONSE_LOST,
        FaultType.HTTP_500,
        FaultType.MALFORMED_RESPONSE,
        FaultType.STALE_READ,
    ],
)
def test_response_phase_faults_apply_but_are_unknown(fault: FaultType) -> None:
    result, state = run(fault)
    assert result.outcome == invoice.OUTCOME_UNKNOWN
    assert state["invoice"]["ledger_count"] == 1
    assert state["invoice"]["status"] == "PAID"


def test_stale_read_probe_cannot_confirm_the_effect() -> None:
    before = invoice.initial_state()
    state = invoice.initial_state()
    invoice.execute(
        PAYMENT_INPUT,
        state,
        fault={"type": "STALE_READ", "parameters": {}},
    )
    ok, detail = invoice.probe(
        PAYMENT_INPUT,
        state,
        before,
        fault={"type": "STALE_READ", "parameters": {}},
    )
    assert ok is False
    assert "stale" in detail


def test_duplicate_response_creates_duplicate_effects() -> None:
    result, state = run(FaultType.DUPLICATE_RESPONSE, parameters={"duplicates": 2})
    assert result.outcome == invoice.OUTCOME_SUCCEEDED
    assert state["invoice"]["ledger_count"] == 2


def test_partial_commit_acknowledges_without_terminal_state() -> None:
    result, state = run(FaultType.PARTIAL_COMMIT)
    assert result.outcome == invoice.OUTCOME_SUCCEEDED
    assert state["invoice"]["ledger_count"] == 1
    assert state["invoice"]["status"] == "SUBMITTED"


def test_postcondition_failure_corrupts_the_declared_state() -> None:
    result, state = run(
        FaultType.POSTCONDITION_FAILURE,
        parameters={"path": "invoice.status", "value": "SUBMITTED"},
    )
    assert result.outcome == invoice.OUTCOME_SUCCEEDED
    assert state["invoice"]["status"] == "SUBMITTED"


def test_missing_field_is_reported_not_swallowed() -> None:
    result, state = run(
        FaultType.MISSING_FIELD,
        parameters={"field": "invoice.payment_reference"},
    )
    assert result.outcome == invoice.OUTCOME_SUCCEEDED
    assert result.missing_fields == ["invoice.payment_reference"]
    assert "payment_reference" not in state["invoice"]


def test_contradictory_totals_mutates_the_purchase_order() -> None:
    result, state = run(
        FaultType.CONTRADICTORY_TOTALS,
        parameters={"po_amount_cents": 180000},
    )
    assert result.outcome == invoice.OUTCOME_SUCCEEDED
    assert state["invoice"]["amount_cents"] == 184500
    assert state["purchase_order"]["amount_cents"] == 180000


def test_latency_changes_timing_not_outcome() -> None:
    result, state = run(FaultType.LATENCY)
    assert result.outcome == invoice.OUTCOME_SUCCEEDED
    assert state["invoice"]["ledger_count"] == 1
