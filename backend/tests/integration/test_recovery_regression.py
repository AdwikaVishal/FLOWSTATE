"""Verified recovery, regression generation and the learning feedback loop."""

from __future__ import annotations

from fastapi.testclient import TestClient
from flowstate.db.models import Checkpoint
from sqlalchemy import select


def runs(client: TestClient, **params: object) -> list[dict]:
    response = client.get("/api/v1/runs", params={"limit": 200, **params})
    assert response.status_code == 200, response.text
    return response.json()


def workflow_by_slug(client: TestClient, slug: str) -> dict:
    for item in client.get("/api/v1/workflows").json():
        if item["slug"] == slug:
            return item
    raise AssertionError(f"workflow {slug} not found")


def scenario_run(client: TestClient, workflow: dict, fault_type: str, ordinal: int) -> dict:
    scenario = client.post(
        "/api/v1/scenarios",
        json={
            "workflow_id": workflow["id"],
            "name": f"test {fault_type}",
            "description": "created by the recovery test",
            "target_ordinal": ordinal,
            "fault_type": fault_type,
            "seed": 99,
        },
    )
    assert scenario.status_code == 201, scenario.text
    started = client.post(f"/api/v1/scenarios/{scenario.json()['id']}/run")
    assert started.status_code == 201, started.text
    return started.json()


def action_starts(run: dict, ordinal: int) -> int:
    return len(
        [
            event
            for event in run["events"]
            if event["type"] == "ACTION_STARTED" and event["action_ordinal"] == ordinal
        ]
    )


def custom_workflow(client: TestClient, payload: dict) -> dict:
    response = client.post("/api/v1/workflows", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------- restoration


def test_compensating_recovery_restores_the_original_state(client: TestClient) -> None:
    broken = next(
        run
        for run in runs(client)
        if run["workflow_name"] == "Vendor Payout"
        and run["status"] == "FAILED"
        and run["failure_code"] == "INVARIANT_VIOLATED"
    )

    plan = client.get(f"/api/v1/runs/{broken['id']}/recovery-plan").json()
    assert plan["supported"] is True
    assert plan["strategy"] == "COMPENSATE"
    assert plan["reversible"] is True
    assert plan["contract"], "an explicit recovery contract is required"
    assert "COMPENSATE" in plan["allowed_strategies"]
    assert plan["idempotency_path"] == "invoice.payment_reference"

    response = client.post(f"/api/v1/runs/{broken['id']}/recover", json={"strategy": "COMPENSATE"})
    assert response.status_code == 200, response.text
    attempt = response.json()
    assert attempt["status"] == "VERIFIED"
    assert attempt["action_ordinal"] == 3
    assert attempt["before_state_hash"] != attempt["after_state_hash"]
    assert all(check["ok"] for check in attempt["verification"]["checks"])
    assert attempt["evidence_id"]
    steps = [step["step"] for step in attempt["steps"]]
    assert steps[0] == "select_checkpoint"
    assert "compensate" in steps

    # Compensation restores state; it does not pretend the run succeeded.
    after = client.get(f"/api/v1/runs/{broken['id']}").json()
    assert after["status"] == "FAILED"
    assert after["chain_valid"] is True

    # Artifact is content-addressed and readable through the hash-checked route.
    evidence = client.get(f"/api/v1/runs/{broken['id']}/evidence/{attempt['evidence_id']}")
    assert evidence.status_code == 200, evidence.text
    body = evidence.json()
    assert body["kind"] == "recovery_report"
    assert body["attempt_id"] == attempt["id"]
    assert body["before_state_hash"] == attempt["before_state_hash"]
    assert body["after_state_hash"] == attempt["after_state_hash"]

    # Compensating twice would remove two ledger entries.
    again = client.post(f"/api/v1/runs/{broken['id']}/recover", json={"strategy": "COMPENSATE"})
    assert again.status_code == 409
    assert again.json()["code"] == "ALREADY_RECOVERED"


def test_stale_checkpoint_is_rejected(client: TestClient, app) -> None:
    broken = next(
        run
        for run in runs(client)
        if run["workflow_name"] == "Vendor Payout"
        and run["status"] == "FAILED"
        and run["failure_code"] == "INVARIANT_VIOLATED"
    )

    session = app.state.db.session_factory()
    try:
        checkpoint = session.scalars(
            select(Checkpoint).where(
                Checkpoint.run_id == broken["id"],
                Checkpoint.kind == "POST_ACTION",
                Checkpoint.action_ordinal == 3,
            )
        ).first()
        assert checkpoint is not None
        tampered = dict(checkpoint.state_json)
        invoice = dict(tampered.get("invoice") or {})
        invoice["ledger_count"] = 0
        tampered["invoice"] = invoice
        checkpoint.state_json = tampered  # hash intentionally not updated
        session.commit()
    finally:
        session.close()

    response = client.post(f"/api/v1/runs/{broken['id']}/recover", json={"strategy": "COMPENSATE"})
    assert response.status_code == 200, response.text
    attempt = response.json()
    assert attempt["status"] == "FAILED"
    assert attempt["failure_code"] == "STALE_CHECKPOINT"
    assert attempt["after_state_hash"] is None
    assert any(
        check["check"] == "checkpoint_integrity" and not check["ok"]
        for check in attempt["verification"]["checks"]
    )


def test_partial_compensation_is_not_reported_as_success(client: TestClient) -> None:
    workflow = workflow_by_slug(client, "invoice-approval")
    duplicated = scenario_run(client, workflow, "DUPLICATE_RESPONSE", 7)
    assert duplicated["status"] == "FAILED"

    attempt = client.post(
        f"/api/v1/runs/{duplicated['id']}/recover", json={"strategy": "COMPENSATE"}
    ).json()
    assert attempt["status"] == "PARTIAL"
    assert attempt["failure_code"] == "RECOVERY_CONTRACT_FAILED"
    failed = [item for item in attempt["verification"]["checks"] if not item["ok"]]
    assert failed, "the unsatisfied contract must be reported"

    run = client.get(f"/api/v1/runs/{duplicated['id']}").json()
    assert run["status"] == "FAILED"
    assert run["verdict"] == "FAILED"


def test_irreversible_action_requires_manual_intervention(client: TestClient) -> None:
    notify = next(run for run in runs(client) if run["status"] == "AWAITING_RECOVERY")
    plan = client.get(f"/api/v1/runs/{notify['id']}/recovery-plan").json()
    assert plan["supported"] is True
    assert plan["strategy"] == "MANUAL"
    assert plan["reversible"] is False
    assert plan["action_label"] == "Notify the vendor"

    attempt = client.post(f"/api/v1/runs/{notify['id']}/recover", json={}).json()
    assert attempt["status"] == "MANUAL_INTERVENTION"
    assert attempt["failure_code"] == "IRREVERSIBLE_ACTION"

    # The run stays blocked: a human still has to act.
    blocked = client.get(f"/api/v1/runs/{notify['id']}").json()
    assert blocked["status"] == "AWAITING_RECOVERY"

    noted = client.post(
        f"/api/v1/recoveries/{attempt['id']}/note",
        json={"note": "Vendor confirmed by phone that no email arrived."},
    )
    assert noted.status_code == 200, noted.text
    assert "Vendor confirmed" in noted.json()["manual_note"]


def test_replay_reapplies_an_earlier_human_decision_effect(client: TestClient) -> None:
    workflow = custom_workflow(
        client,
        {
            "name": "Replay gated mutation",
            "description": "REPLAY must re-execute the earlier gated action, not assume it ran.",
            "publish": True,
            "actions": [
                {
                    "ordinal": 0,
                    "node_key": "approve",
                    "node_type": "HUMAN_DECISION",
                    "kind": "SUBMIT",
                    "label": "Approve the invoice",
                    "target": {"surface": "sandbox"},
                    "input": {"effects": [{"op": "set", "path": "review.approved", "value": True}]},
                    "postconditions": [{"path": "review.approved", "op": "eq", "value": True}],
                    "side_effect_class": "IRREVERSIBLE",
                    "requires_authorization": True,
                },
                {
                    "ordinal": 1,
                    "node_key": "finalize",
                    "kind": "SUBMIT",
                    "label": "Finalize the invoice",
                    "target": {"surface": "sandbox"},
                    "input": {
                        "effects": [{"op": "set", "path": "review.finalized", "value": True}],
                        "recovery": {
                            "strategy": "REPLAY",
                            "reversible": True,
                            "allowed_strategies": ["REPLAY"],
                            "probe": {
                                "path": "review.finalized",
                                "op": "eq",
                                "value": True,
                            },
                        },
                    },
                    "postconditions": [{"path": "review.finalized", "op": "eq", "value": True}],
                    "side_effect_class": "IDEMPOTENT",
                },
            ],
            "invariants": [
                {
                    "name": "Replay reaches the finalized state",
                    "kind": "TERMINAL_STATE",
                    "expression": {
                        "kind": "state",
                        "path": "review.finalized",
                        "op": "eq",
                        "value": True,
                    },
                }
            ],
        },
    )
    failed = scenario_run(client, workflow, "NETWORK_DROP", 1)
    assert failed["status"] == "AWAITING_RECOVERY"

    plan = client.get(f"/api/v1/runs/{failed['id']}/recovery-plan").json()
    assert "REPLAY" in plan["allowed_strategies"]

    response = client.post(f"/api/v1/runs/{failed['id']}/recover", json={"strategy": "REPLAY"})
    assert response.status_code == 200, response.text
    attempt = response.json()
    assert attempt["status"] == "VERIFIED", attempt

    replayed = client.get(f"/api/v1/runs/{failed['id']}").json()
    assert replayed["status"] == "SUCCEEDED"
    assert replayed["verdict"] == "VERIFIED"
    assert replayed["final_state"]["review"]["approved"] is True
    assert replayed["final_state"]["review"]["finalized"] is True


# ---------------------------------------------------------------- ambiguity


def test_ambiguous_outcome_is_probed_instead_of_retried(client: TestClient) -> None:
    workflow = workflow_by_slug(client, "invoice-approval")
    stale = scenario_run(client, workflow, "STALE_READ", 7)
    assert stale["status"] == "AWAITING_RECOVERY"
    assert stale["failure_code"] == "ACTION_OUTCOME_UNKNOWN"
    before = action_starts(stale, 7)
    assert before == 1

    attempt = client.post(f"/api/v1/runs/{stale['id']}/recover", json={}).json()
    assert attempt["status"] == "VERIFIED"
    probe = attempt["verification"]["probe"]
    assert probe["mechanism"] == "target_state_probe"
    assert probe["effect_present"] is True

    resumed = client.get(f"/api/v1/runs/{stale['id']}").json()
    assert resumed["status"] == "SUCCEEDED"
    assert resumed["verdict"] == "VERIFIED"
    # The effect was already applied; the action was not executed a second time.
    assert action_starts(resumed, 7) == before
    assert any(
        step["step"] == "probe_outcome" and step["outcome"] == "EFFECT_PRESENT"
        for step in attempt["steps"]
    )


def test_ambiguous_retry_requires_an_idempotency_mechanism(client: TestClient) -> None:
    workflow = custom_workflow(
        client,
        {
            "name": "Ambiguous retry probe",
            "description": "An action with no probe and no idempotency key.",
            "publish": True,
            "actions": [
                {
                    "ordinal": 0,
                    "node_key": "mutate",
                    "kind": "SUBMIT",
                    "label": "Mutate without a recovery probe",
                    "target": {"surface": "sandbox"},
                    "input": {
                        "effects": [{"op": "set", "path": "review.mutated", "value": True}],
                        "recovery": {
                            "strategy": "RESUME",
                            "reversible": True,
                            "contract": [{"path": "review.mutated", "op": "exists"}],
                        },
                    },
                    "postconditions": [{"path": "review.mutated", "op": "eq", "value": True}],
                    "side_effect_class": "IDEMPOTENT",
                }
            ],
        },
    )
    run = scenario_run(client, workflow, "NETWORK_DROP", 0)
    assert run["status"] == "AWAITING_RECOVERY"

    attempt = client.post(f"/api/v1/runs/{run['id']}/recover", json={}).json()
    assert attempt["status"] == "FAILED"
    assert attempt["failure_code"] == "NO_IDEMPOTENCY_MECHANISM"
    resumed = client.get(f"/api/v1/runs/{run['id']}").json()
    assert resumed["status"] == "FAILED"
    assert action_starts(resumed, 0) == 1, "the action must not be retried blindly"


def test_action_without_a_recovery_policy_requires_a_human(client: TestClient) -> None:
    workflow = custom_workflow(
        client,
        {
            "name": "No recovery policy",
            "description": "An irreversible action with no tested inverse.",
            "publish": True,
            "actions": [
                {
                    "ordinal": 0,
                    "node_key": "charge",
                    "kind": "SUBMIT",
                    "label": "Charge without a recovery policy",
                    "target": {"surface": "sandbox"},
                    "input": {"effects": []},
                    "postconditions": [{"path": "review.missing", "op": "exists"}],
                    "side_effect_class": "IRREVERSIBLE",
                }
            ],
        },
    )
    run = client.post("/api/v1/runs", json={"workflow_id": workflow["id"]}).json()
    assert run["status"] == "FAILED"
    assert run["failure_code"] == "POSTCONDITION_FAILED"

    plan = client.get(f"/api/v1/runs/{run['id']}/recovery-plan").json()
    assert plan["supported"] is False
    assert plan["code"] == "NO_RECOVERY_POLICY"

    attempt = client.post(f"/api/v1/runs/{run['id']}/recover", json={}).json()
    assert attempt["status"] == "MANUAL_INTERVENTION"
    assert attempt["failure_code"] == "NO_RECOVERY_POLICY"


# --------------------------------------------------------------- regression


def test_regression_reproduces_on_v1_and_passes_on_v2(client: TestClient) -> None:
    tests = client.get("/api/v1/regression-tests").json()
    test = next(item for item in tests if item["name"].startswith("Vendor payout"))
    spec = test["spec"]

    # Structured template: scenario snapshot, pinned version, invariant, expected.
    assert spec["template_version"] == 1
    assert spec["version"] == 1
    assert spec["expected"]["kind"] == "INVARIANTS_SATISFIED"
    assert spec["expected"]["invariant_name"] == "Invoice reaches PAID"
    assert spec["observed"]["failure_code"] == "INVARIANT_VIOLATED"
    assert spec["observed"]["violated_invariants"]
    assert spec["source_run_id"]
    assert test["status"] == "PASSING"  # latest comparison was v2

    pinned = client.post(f"/api/v1/regression-tests/{test['id']}/replay")
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["last_result"] == "FAILED", "the pinned v1 still reproduces"

    compared = client.post(f"/api/v1/regression-tests/{test['id']}/compare")
    assert compared.status_code == 200, compared.text
    payload = compared.json()
    by_version = {item["version"]: item["result"] for item in payload["results"]}
    assert by_version == {1: "FAILED", 2: "PASSED"}
    assert payload["resolved_in"] == [2]
    assert payload["reproduces_in"] == [1]

    stored = client.get(f"/api/v1/regression-tests/{test['id']}/results").json()
    assert {item["version"] for item in stored} == {1, 2}


def test_regression_can_be_reviewed_saved_and_activated(client: TestClient) -> None:
    workflow = workflow_by_slug(client, "invoice-approval")
    failed = scenario_run(client, workflow, "PARTIAL_COMMIT", 7)
    assert failed["status"] == "FAILED"

    created = client.post(
        "/api/v1/regression-tests",
        json={"run_id": failed["id"], "name": "Partial ledger commit"},
    )
    assert created.status_code == 201, created.text
    test = created.json()
    assert test["status"] == "DRAFT"
    assert test["spec"]["expected"]["invariant_name"] in {
        "Invoice reaches PAID",
        "Exactly one ledger entry",
    }

    reviewed = client.patch(
        f"/api/v1/regression-tests/{test['id']}",
        json={
            "name": "Partial ledger commit (reviewed)",
            "notes": "Reproduces with a 50% partial commit.",
            "expected_description": "No VIOLATED verdicts on replay.",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    body = reviewed.json()
    assert body["name"] == "Partial ledger commit (reviewed)"
    assert body["notes"].startswith("Reproduces")
    assert body["reviewed_by"]
    assert body["spec"]["expected"]["description"] == "No VIOLATED verdicts on replay."
    assert body["status"] == "DRAFT", "review must not silently activate the test"

    activated = client.post(f"/api/v1/regression-tests/{test['id']}/activate")
    assert activated.status_code == 200
    assert activated.json()["status"] == "ACTIVE"

    replayed = client.post(f"/api/v1/regression-tests/{test['id']}/replay")
    assert replayed.status_code == 200
    assert replayed.json()["last_result"] in {"PASSED", "FAILED"}


def test_verified_run_cannot_become_a_regression_test(client: TestClient) -> None:
    verified = next(run for run in runs(client) if run["verdict"] == "VERIFIED")
    response = client.post("/api/v1/regression-tests", json={"run_id": verified["id"]})
    assert response.status_code == 409
    assert response.json()["code"] == "NO_FAILURE_TO_CAPTURE"


# -------------------------------------------------------------- feedback loop


def test_feedback_loop_reports_resolution_without_rewriting_workflows(
    client: TestClient,
) -> None:
    before = {item["id"]: item for item in client.get("/api/v1/workflows").json()}

    modes = client.get("/api/v1/feedback/failure-modes").json()
    vendor = next(item for item in modes if item["workflow_name"] == "Vendor Payout")
    assert vendor["failure_mode"] == "Invoice reaches PAID"
    assert vendor["detected_in_version"] == 1
    assert vendor["status"] == "RESOLVED"
    assert vendor["resolved_in"] == [2]
    assert vendor["reproduces_in"] == [1]
    assert vendor["source_run_id"]

    after = {item["id"]: item for item in client.get("/api/v1/workflows").json()}
    assert {key: value["version_count"] for key, value in after.items()} == {
        key: value["version_count"] for key, value in before.items()
    }
    assert {key: value["current_version_id"] for key, value in after.items()} == {
        key: value["current_version_id"] for key, value in before.items()
    }
