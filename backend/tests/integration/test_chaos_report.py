"""Baseline-versus-fault reports, reproducibility, recovery and regression."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from flowstate.clock import FrozenClock
from flowstate.config import Settings
from flowstate.main import create_app
from tests.fakes import FakeBrowserSession


def scenario_for(client: TestClient, fault_type: str) -> dict:
    scenarios = client.get("/api/v1/scenarios").json()
    preferred = [
        item
        for item in scenarios
        if item["fault_type"] == fault_type and item["workflow_name"] == "Invoice Approval"
    ]
    return (preferred or [item for item in scenarios if item["fault_type"] == fault_type])[0]


def evaluate(client: TestClient, scenario_id: str) -> dict:
    response = client.post(f"/api/v1/scenarios/{scenario_id}/evaluate")
    assert response.status_code == 201, response.text
    return response.json()


def test_evaluate_records_baseline_fault_and_first_violated_invariant(
    client: TestClient,
) -> None:
    scenario = scenario_for(client, "DUPLICATE_RESPONSE")
    report = evaluate(client, scenario["id"])

    assert report["baseline"]["status"] == "SUCCEEDED"
    assert report["baseline"]["verdict"] == "VERIFIED"
    assert report["fault"]["status"] == "FAILED"
    assert report["fault"]["verdict"] == "FAILED"
    assert report["fault"]["driver"] == "simulated-v1"
    assert report["fault"]["seed"] == scenario["seed"]
    assert report["first_violated_invariant"]["invariant_name"] == "Exactly one ledger entry"
    assert report["failure_action"]["ordinal"] == 7
    assert report["failure_action"]["label"] == "Disburse funds (sandbox)"
    assert report["failed_safely"] is True
    assert report["recovery"]["attempted"] is False
    assert report["evaluation"] == "FAULT_DETECTED"
    assert report["baseline_verified"] is True
    assert report["fault"]["failure_code"] == "INVARIANT_VIOLATED"

    stored = client.get(f"/api/v1/scenarios/{scenario['id']}/report")
    assert stored.status_code == 200
    assert stored.json()["fault"]["run_id"] == report["fault"]["run_id"]

    updated = client.get(f"/api/v1/scenarios/{scenario['id']}").json()
    assert updated["baseline_run_id"] == report["baseline"]["run_id"]
    assert updated["last_run_id"] == report["fault"]["run_id"]


def test_evaluation_is_reproducible_from_recorded_configuration(client: TestClient) -> None:
    scenario = scenario_for(client, "PARTIAL_COMMIT")
    first = evaluate(client, scenario["id"])
    second = evaluate(client, scenario["id"])

    assert first["fault"]["run_id"] != second["fault"]["run_id"]
    assert first["baseline"]["status"] == second["baseline"]["status"]
    assert first["fault"]["status"] == second["fault"]["status"]
    assert first["fault"]["failure_code"] == second["fault"]["failure_code"]
    assert (
        first["first_violated_invariant"]["invariant_name"]
        == second["first_violated_invariant"]["invariant_name"]
    )
    assert first["seed"] == second["seed"] == scenario["seed"]
    assert first["parameters"] == second["parameters"] == scenario["parameters"]
    assert first["input_fixtures"] == second["input_fixtures"]


def test_unknown_outcome_is_safe_and_recovery_updates_the_report(
    client: TestClient,
) -> None:
    scenario = scenario_for(client, "NETWORK_DROP")
    report = evaluate(client, scenario["id"])
    assert report["fault"]["status"] == "AWAITING_RECOVERY"
    assert report["fault"]["verdict"] == "PENDING"
    assert report["failed_safely"] is True
    assert report["evaluation"] == "SAFE_FAILURE_WITHOUT_VIOLATION"
    assert report["recovery"]["attempted"] is False

    recovered = client.post(
        f"/api/v1/runs/{report['fault']['run_id']}/recover", json={"strategy": "RESUME"}
    )
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["status"] == "VERIFIED"

    after = client.get(f"/api/v1/scenarios/{scenario['id']}/report").json()
    assert after["fault"]["status"] == "SUCCEEDED"
    assert after["fault"]["verdict"] == "VERIFIED"
    assert after["recovery"]["attempted"] is True
    assert after["recovery"]["verified"] is True
    assert after["recovery"]["status"] == "VERIFIED"


def test_report_saves_a_reproducible_regression_test(client: TestClient) -> None:
    scenario = scenario_for(client, "DUPLICATE_RESPONSE")
    report = evaluate(client, scenario["id"])
    assert report["regression_test_id"] is None

    created = client.post(
        "/api/v1/regression-tests",
        json={"run_id": report["fault"]["run_id"], "name": "Ledger must stay single"},
    )
    assert created.status_code == 201, created.text
    test = created.json()
    assert test["fault_scenario_id"] == scenario["id"]
    assert test["spec"]["expected"]["invariant_name"] == "Exactly one ledger entry"

    updated = client.get(f"/api/v1/scenarios/{scenario['id']}/report").json()
    assert updated["regression_test_id"] == test["id"]

    replay = client.post(f"/api/v1/regression-tests/{test['id']}/replay")
    assert replay.status_code == 200, replay.text
    assert replay.json()["status"] == "FAILING"
    assert replay.json()["last_result"] == "FAILED"

    regression_runs = [
        item for item in client.get("/api/v1/runs").json() if item["trigger"] == "REGRESSION"
    ]
    assert regression_runs, "the regression replay must create a real run"
    reproduced = client.get(f"/api/v1/runs/{regression_runs[0]['id']}").json()
    assert reproduced["status"] == "FAILED"
    assert reproduced["failure_code"] == "INVARIANT_VIOLATED"
    assert reproduced["fault_scenario_id"] == scenario["id"]


def test_postcondition_failure_is_attributed_to_the_action(client: TestClient) -> None:
    scenario = scenario_for(client, "POSTCONDITION_FAILURE")
    report = evaluate(client, scenario["id"])
    assert report["fault"]["status"] == "FAILED"
    assert report["fault"]["failure_code"] == "POSTCONDITION_FAILED"
    assert report["failure_action"]["ordinal"] == 6
    assert report["failure_action"]["error_code"] == "POSTCONDITION_FAILED"
    assert report["failed_safely"] is True


def test_totals_policy_and_authorization_invariants_catch_faults(client: TestClient) -> None:
    workflows = client.get("/api/v1/workflows").json()
    workflow_id = next(item["id"] for item in workflows if item["slug"] == "invoice-approval")
    scenario = client.post(
        "/api/v1/scenarios",
        json={
            "workflow_id": workflow_id,
            "name": "Totals policy coverage",
            "description": "The invoice total contradicts the purchase order.",
            "target_ordinal": 7,
            "fault_type": "CONTRADICTORY_TOTALS",
            "parameters": {"po_amount_cents": 180000},
            "seed": 49,
        },
    )
    assert scenario.status_code == 201, scenario.text
    totals = evaluate(client, scenario.json()["id"])
    assert totals["fault"]["status"] == "FAILED"
    assert (
        totals["first_violated_invariant"]["invariant_name"]
        == "Financial status requires matching totals"
    )

    rate_limited = evaluate(client, scenario_for(client, "RATE_LIMITED")["id"])
    assert rate_limited["fault"]["status"] == "FAILED"
    assert rate_limited["fault"]["failure_code"] == "RATE_LIMITED"
    assert rate_limited["failed_safely"] is True


def test_delayed_approval_expires_and_fails_the_run(tmp_path) -> None:
    clock = FrozenClock(datetime(2026, 2, 1, 9, 0, tzinfo=UTC))
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'chaos.db'}",
        log_json=False,
        auto_seed_demo=True,
        decision_ttl_seconds=5,
    )
    app = create_app(settings, clock=clock)
    with TestClient(app) as client:
        scenario = scenario_for(client, "DELAYED_APPROVAL")
        report = evaluate(client, scenario["id"])
        assert report["fault"]["status"] == "AWAITING_DECISION"
        assert report["failed_safely"] is True

        decisions = client.get(
            "/api/v1/decisions", params={"run_id": report["fault"]["run_id"]}
        ).json()
        assert len(decisions) == 1
        decision = decisions[0]
        assert "Injected approval delay" in decision["reason"]

        clock.advance(6)
        denied = client.post(
            f"/api/v1/decisions/{decision['id']}/approve", json={"note": "too late"}
        )
        assert denied.status_code == 409
        assert denied.json()["code"] == "DECISION_EXPIRED"

        after = client.get(f"/api/v1/scenarios/{scenario['id']}/report").json()
        assert after["fault"]["status"] == "FAILED"
        assert after["fault"]["failure_code"] == "DECISION_EXPIRED"
        assert after["failed_safely"] is True


def test_selector_drift_on_a_browser_workflow(client: TestClient) -> None:
    client.app.state.browser_factory = lambda: FakeBrowserSession()
    workflow = client.post(
        "/api/v1/workflows",
        json={
            "name": "Browser chaos fixture",
            "description": "Two browser actions for selector-drift injection.",
            "publish": True,
            "actions": [
                {
                    "ordinal": 0,
                    "node_key": "start",
                    "node_type": "START",
                    "kind": "ASSERT",
                    "label": "Start",
                },
                {
                    "ordinal": 1,
                    "node_key": "open_demo",
                    "node_type": "ACTION",
                    "kind": "NAVIGATE",
                    "label": "Open the invoice demo",
                    "target": {
                        "url": "http://127.0.0.1:8000/demo-app/",
                        "origin": "http://127.0.0.1:8000",
                    },
                    "input": {"action": "NAVIGATE", "wait_for": {"page": "queue"}},
                    "channel": "BROWSER",
                    "side_effect_class": "READ_ONLY",
                    "postconditions": [{"path": "page.page", "op": "eq", "value": "queue"}],
                },
                {
                    "ordinal": 2,
                    "node_key": "load_invoice",
                    "node_type": "ACTION",
                    "kind": "CLICK",
                    "label": "Load the invoice",
                    "target": {
                        "url": "http://127.0.0.1:8000/demo-app/",
                        "origin": "http://127.0.0.1:8000",
                        "locator": [
                            {
                                "strategy": "role",
                                "role": "button",
                                "name": "Load invoice",
                                "value": "Load invoice",
                            }
                        ],
                    },
                    "input": {"action": "CLICK", "wait_for": {"page": "invoice"}},
                    "channel": "BROWSER",
                    "side_effect_class": "READ_ONLY",
                    "postconditions": [{"path": "page.page", "op": "eq", "value": "invoice"}],
                },
                {
                    "ordinal": 3,
                    "node_key": "end",
                    "node_type": "END",
                    "kind": "ASSERT",
                    "label": "End",
                    "depends_on": ["load_invoice"],
                },
            ],
            "invariants": [
                {
                    "name": "The invoice page is open",
                    "kind": "TERMINAL_STATE",
                    "severity": "MAJOR",
                    "expression": {
                        "kind": "state",
                        "path": "page.page",
                        "op": "eq",
                        "value": "invoice",
                    },
                }
            ],
            "input_schema": {
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "string"},
                    "supplier_id": {"type": "string"},
                },
                "required": ["invoice_id", "supplier_id"],
            },
        },
    )
    assert workflow.status_code == 201, workflow.text
    workflow_id = workflow.json()["id"]
    scenario = client.post(
        "/api/v1/scenarios",
        json={
            "workflow_id": workflow_id,
            "name": "Changed browser selector",
            "description": "The demo layout changes before the recorded click.",
            "target_ordinal": 2,
            "fault_type": "SELECTOR_DRIFT",
            "parameters": {},
            "input_fixtures": {"invoice_id": "INV-1001", "supplier_id": "SUP-77"},
            "seed": 61,
        },
    )
    assert scenario.status_code == 201, scenario.text
    report = evaluate(client, scenario.json()["id"])
    assert report["baseline"]["status"] == "SUCCEEDED"
    assert report["fault"]["status"] == "FAILED"
    assert report["fault"]["failure_code"] == "SELECTOR_NOT_FOUND"
    assert report["failure_action"]["error_code"] == "SELECTOR_NOT_FOUND"
    assert report["failed_safely"] is True


@pytest.mark.parametrize(
    ("fault_type", "target_ordinal", "expected_status", "expected_code"),
    [
        ("TIMEOUT", 7, "SUCCEEDED", None),
        ("TIMEOUT_BEFORE_EXECUTION", 7, "FAILED", "TIMEOUT_BEFORE_EXECUTION"),
        ("RESPONSE_LOST", 7, "SUCCEEDED", None),
        ("HTTP_500", 7, "SUCCEEDED", None),
        ("MALFORMED_RESPONSE", 7, "SUCCEEDED", None),
        ("MISSING_FIELD", 6, "FAILED", "MISSING_REQUIRED_FIELD"),
        ("STALE_READ", 7, "AWAITING_RECOVERY", "ACTION_OUTCOME_UNKNOWN"),
        ("NETWORK_DROP", 7, "AWAITING_RECOVERY", "ACTION_OUTCOME_UNKNOWN"),
        ("RATE_LIMITED", 7, "FAILED", "RATE_LIMITED"),
        ("TRANSIENT_ERROR", 7, "FAILED", "TRANSIENT_ERROR"),
        ("LATENCY", 7, "SUCCEEDED", None),
    ],
)
def test_every_invoice_fault_has_a_reported_outcome(
    client: TestClient,
    fault_type: str,
    target_ordinal: int,
    expected_status: str,
    expected_code: str | None,
) -> None:
    workflows = client.get("/api/v1/workflows").json()
    workflow_id = next(item["id"] for item in workflows if item["slug"] == "invoice-approval")
    scenario = client.post(
        "/api/v1/scenarios",
        json={
            "workflow_id": workflow_id,
            "name": f"Coverage: {fault_type}",
            "description": f"Every-fault coverage for {fault_type}.",
            "target_ordinal": target_ordinal,
            "fault_type": fault_type,
            "parameters": {},
            "seed": 80,
        },
    )
    assert scenario.status_code == 201, scenario.text
    report = evaluate(client, scenario.json()["id"])
    assert report["baseline"]["status"] == "SUCCEEDED"
    assert report["fault"]["status"] == expected_status, report["fault"]
    if expected_code is not None:
        assert report["fault"]["failure_code"] == expected_code
    assert report["failed_safely"] is True


def test_scenario_target_validation_rejects_mismatched_faults(client: TestClient) -> None:
    workflows = client.get("/api/v1/workflows").json()
    workflow_id = next(item["id"] for item in workflows if item["slug"] == "invoice-approval")

    browser_on_simulated = client.post(
        "/api/v1/scenarios",
        json={
            "workflow_id": workflow_id,
            "name": "Invalid selector drift",
            "description": "Not allowed on a simulated action.",
            "target_ordinal": 7,
            "fault_type": "SELECTOR_DRIFT",
            "parameters": {},
            "seed": 1,
        },
    )
    assert browser_on_simulated.status_code == 422
    assert browser_on_simulated.json()["code"] == "FAULT_REQUIRES_BROWSER_ACTION"

    delayed_on_payment = client.post(
        "/api/v1/scenarios",
        json={
            "workflow_id": workflow_id,
            "name": "Invalid delayed approval",
            "description": "Payment is not a decision node.",
            "target_ordinal": 7,
            "fault_type": "DELAYED_APPROVAL",
            "parameters": {},
            "seed": 2,
        },
    )
    assert delayed_on_payment.status_code == 422
    assert delayed_on_payment.json()["code"] == "FAULT_REQUIRES_DECISION"


def test_recovery_refuses_false_resume_preconditions(client: TestClient) -> None:
    from hashlib import sha256

    from flowstate.db.models import Checkpoint
    from flowstate.services.audit import canonical_json

    workflow = client.post(
        "/api/v1/workflows",
        json={
            "name": "Checkpoint precondition guard",
            "description": "A gated action must not resume from a false checkpoint.",
            "publish": True,
            "actions": [
                {
                    "ordinal": 0,
                    "node_key": "start",
                    "node_type": "START",
                    "kind": "ASSERT",
                    "label": "Start",
                },
                {
                    "ordinal": 1,
                    "node_key": "set_gate",
                    "node_type": "ACTION",
                    "kind": "ASSERT",
                    "label": "Open the gate",
                    "input": {"effects": [{"op": "set", "path": "gate.open", "value": True}]},
                    "postconditions": [{"path": "gate.open", "op": "eq", "value": True}],
                    "side_effect_class": "READ_ONLY",
                },
                {
                    "ordinal": 2,
                    "node_key": "gated_action",
                    "node_type": "ACTION",
                    "kind": "ASSERT",
                    "label": "Run the gated action",
                    "input": {
                        "effects": [{"op": "set", "path": "gated.done", "value": True}],
                        "recovery": {
                            "reversible": True,
                            "strategy": "RESUME",
                            "allowed_strategies": ["RESUME"],
                            "probe": {
                                "path": "gated.done",
                                "op": "eq",
                                "value": True,
                            },
                            "contract": [{"path": "gate.open", "op": "exists"}],
                            "notes": "Resume only when the gate precondition still holds.",
                        },
                    },
                    "preconditions": [{"path": "gate.open", "op": "eq", "value": True}],
                    "postconditions": [{"path": "gated.done", "op": "eq", "value": True}],
                    "side_effect_class": "READ_ONLY",
                },
                {
                    "ordinal": 3,
                    "node_key": "end",
                    "node_type": "END",
                    "kind": "ASSERT",
                    "label": "End",
                    "depends_on": ["gated_action"],
                },
            ],
            "invariants": [
                {
                    "name": "Gated action completed",
                    "kind": "TERMINAL_STATE",
                    "severity": "MAJOR",
                    "expression": {
                        "kind": "state",
                        "path": "gated.done",
                        "op": "eq",
                        "value": True,
                    },
                }
            ],
        },
    )
    assert workflow.status_code == 201, workflow.text
    workflow_id = workflow.json()["id"]

    scenario = client.post(
        "/api/v1/scenarios",
        json={
            "workflow_id": workflow_id,
            "name": "Drop the gated response",
            "description": "No effect, no probe confirmation, then a false precondition.",
            "target_ordinal": 2,
            "fault_type": "NETWORK_DROP",
            "parameters": {},
            "seed": 71,
        },
    )
    assert scenario.status_code == 201, scenario.text
    report = evaluate(client, scenario.json()["id"])
    run_id = report["fault"]["run_id"]
    assert report["fault"]["status"] == "AWAITING_RECOVERY"

    session = client.app.state.db.session_factory()
    try:
        checkpoint = (
            session.query(Checkpoint)
            .filter(Checkpoint.run_id == run_id)
            .order_by(Checkpoint.action_ordinal.desc(), Checkpoint.event_seq.desc())
            .first()
        )
        assert checkpoint is not None
        state = dict(checkpoint.state_json)
        state["gate"] = {"open": False}
        session.query(Checkpoint).filter(Checkpoint.id == checkpoint.id).update(
            {
                Checkpoint.state_json: state,
                Checkpoint.state_hash: sha256(canonical_json(state).encode()).hexdigest(),
            }
        )
        session.commit()
    finally:
        session.close()

    recovery = client.post(f"/api/v1/runs/{run_id}/recover", json={"strategy": "RESUME"})
    assert recovery.status_code == 200, recovery.text
    attempt = recovery.json()
    assert attempt["status"] == "FAILED"
    assert "precondition" in attempt["detail"].lower()
    checks = (attempt.get("verification") or {}).get("checks") or []
    assert any(
        check["check"] == "resume_preconditions" and check["ok"] is False for check in checks
    )

    events = client.get(f"/api/v1/runs/{run_id}/events").json()
    assert any(event["type"] == "RECOVERY_FAILED" for event in events)
    assert not any(event["type"] == "RECOVERY_APPLIED" for event in events)
    run = client.get(f"/api/v1/runs/{run_id}").json()
    assert run["status"] == "FAILED"
    assert run["verdict"] != "VERIFIED"
