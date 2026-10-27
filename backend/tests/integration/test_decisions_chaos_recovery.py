"""Human decisions, chaos scenarios and recovery through the HTTP API."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi.testclient import TestClient
from flowstate.db.models import ExecutionJob, ExecutionNodeState, ExecutionRun
from flowstate.main import create_app
from sqlalchemy import select


def _run_demo_workflow(client: TestClient, workflow: dict) -> dict:
    response = client.post("/api/v1/runs", json={"workflow_id": workflow["id"]})
    assert response.status_code == 201, response.text
    return response.json()


def _decisions_for_run(client: TestClient, run_id: str) -> list[dict]:
    decisions = client.get("/api/v1/decisions", params={"limit": 200}).json()
    return [decision for decision in decisions if decision["run_id"] == run_id]


def _decision_for_run(client: TestClient, run_id: str) -> dict:
    decisions = _decisions_for_run(client, run_id)
    if decisions:
        return decisions[0]
    raise AssertionError(f"no decision found for run {run_id}")


def test_authorization_pauses_then_approval_resumes(
    client: TestClient, demo_workflow: dict
) -> None:
    run = _run_demo_workflow(client, demo_workflow)
    assert run["status"] == "AWAITING_DECISION"
    assert run["verdict"] == "PENDING"

    decision = _decision_for_run(client, run["id"])
    assert decision["status"] in {"REQUESTED", "NOTIFIED"}
    assert decision["action_ordinal"] == 5
    assert decision["evidence"], "decision must ship an evidence bundle"
    assert decision["evidence"][0]["verified"] is True

    approved = client.post(f"/api/v1/decisions/{decision['id']}/approve", json={"note": "ok"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPLIED"
    assert approved.json()["capability_token_id"]

    resumed = client.get(f"/api/v1/runs/{run['id']}").json()
    assert resumed["status"] == "SUCCEEDED"
    assert resumed["verdict"] == "VERIFIED"
    event_types = {event["type"] for event in resumed["events"]}
    assert {"DECISION_GRANTED", "RUN_VERIFIED"} <= event_types
    terminal_stream = client.get(f"/api/v1/runs/{run['id']}/events/stream")
    assert terminal_stream.status_code == 200
    assert "event: complete" in terminal_stream.text
    assert '"status": "SUCCEEDED"' in terminal_stream.text


def test_concurrent_approvals_claim_one_decision_once(
    client: TestClient, demo_workflow: dict, monkeypatch
) -> None:
    from flowstate.services import decisions

    run = _run_demo_workflow(client, demo_workflow)
    decision = _decision_for_run(client, run["id"])
    barrier = Barrier(2)
    original_revalidate = decisions._revalidate

    def synchronized_revalidation(*args, **kwargs):
        result = original_revalidate(*args, **kwargs)
        barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(decisions, "_revalidate", synchronized_revalidation)

    def approve(_: int):
        return client.post(f"/api/v1/decisions/{decision['id']}/approve", json={"note": "ok"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(approve, range(2)))

    assert sorted(response.status_code for response in responses) in ([200, 200], [200, 409])
    assert all(response.status_code != 500 for response in responses)
    resumed = client.get(f"/api/v1/runs/{run['id']}").json()
    assert resumed["status"] == "SUCCEEDED"
    assert sum(event["type"] == "DECISION_GRANTED" for event in resumed["events"]) == 1
    assert len(_decisions_for_run(client, run["id"])) == 1
    started = [
        event
        for event in resumed["events"]
        if event["type"] in {"NODE_STARTED", "ACTION_STARTED"} and event["action_ordinal"] == 5
    ]
    assert len(started) == 2


def test_resume_exception_rolls_back_approval_claim(app, monkeypatch) -> None:
    from flowstate.services.execution import ExecutionEngine

    def fail_resume(*args, **kwargs):
        raise RuntimeError("controlled resume failure")

    monkeypatch.setattr(ExecutionEngine, "resume_run", fail_resume)
    with TestClient(app, raise_server_exceptions=False) as failing_client:
        workflow_response = failing_client.get("/api/v1/workflows")
        workflow = next(
            item for item in workflow_response.json() if item["slug"] == "invoice-approval"
        )
        run = _run_demo_workflow(failing_client, workflow)
        decision = _decision_for_run(failing_client, run["id"])

        response = failing_client.post(
            f"/api/v1/decisions/{decision['id']}/approve", json={"note": "test rollback"}
        )
        assert response.status_code == 500
        current_decision = failing_client.get(f"/api/v1/decisions/{decision['id']}").json()
        current_run = failing_client.get(f"/api/v1/runs/{run['id']}").json()
        assert current_decision["status"] in {"REQUESTED", "NOTIFIED"}
        assert current_run["status"] == "AWAITING_DECISION"
        assert all(event["type"] != "DECISION_GRANTED" for event in current_run["events"])


def test_background_run_persists_progress_and_sse_replays_from_cursor(
    client: TestClient, demo_workflow: dict
) -> None:
    response = client.post(
        "/api/v1/runs",
        json={"workflow_id": demo_workflow["id"], "background": True, "seed": 41},
    )
    assert response.status_code == 201, response.text
    run_id = response.json()["id"]
    assert response.json()["status"] in {"PENDING", "RUNNING", "AWAITING_DECISION", "SUCCEEDED"}

    streamed = client.get(f"/api/v1/runs/{run_id}/events/stream", params={"once": True})
    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("text/event-stream")
    event_lines = [
        line.removeprefix("id: ") for line in streamed.text.splitlines() if line.startswith("id: ")
    ]
    assert event_lines
    sequences = [int(item) for item in event_lines]
    assert sequences == sorted(set(sequences))

    finished = client.get(f"/api/v1/runs/{run_id}").json()
    assert finished["status"] == "AWAITING_DECISION"
    assert finished["node_states"]
    assert any(event["type"] == "NODE_PASSED" for event in finished["events"])

    resumed_stream = client.get(
        f"/api/v1/runs/{run_id}/events/stream",
        params={"after": sequences[-2], "once": True},
    )
    replay_sequences = [
        int(line.removeprefix("id: "))
        for line in resumed_stream.text.splitlines()
        if line.startswith("id: ")
    ]
    assert replay_sequences
    assert min(replay_sequences) > sequences[-2]


def test_restart_marks_an_interrupted_background_run_unknown(settings) -> None:
    first_app = create_app(settings)
    with TestClient(first_app) as first_client:
        workflow = next(
            item
            for item in first_client.get("/api/v1/workflows").json()
            if item["slug"] == "invoice-approval"
        )
        response = first_client.post(
            "/api/v1/runs",
            json={"workflow_id": workflow["id"], "background": True, "seed": 41},
        )
        assert response.status_code == 201, response.text
        run_id = response.json()["id"]
        first_client.get(f"/api/v1/runs/{run_id}/events/stream", params={"once": True})

        session = first_app.state.db.session_factory()
        try:
            run = session.get(ExecutionRun, run_id)
            job = session.get(ExecutionJob, run_id)
            node_state = session.scalars(
                select(ExecutionNodeState).where(ExecutionNodeState.run_id == run_id)
            ).first()
            assert run is not None and job is not None and node_state is not None
            run.status = "RUNNING"
            run.current_ordinal = node_state.ordinal
            job.status = "RUNNING"
            node_state.status = "RUNNING"
            session.commit()
        finally:
            session.close()

    restarted_app = create_app(settings)
    with TestClient(restarted_app) as restarted_client:
        recovered = restarted_client.get(f"/api/v1/runs/{run_id}").json()
        assert recovered["status"] == "AWAITING_RECOVERY"
        assert recovered["failure_code"] == "WORKER_INTERRUPTED"
        interruption = next(event for event in recovered["events"] if event["type"] == "RUN_FAILED")
        assert interruption["payload"]["outcome"] == "UNKNOWN"

        session = restarted_app.state.db.session_factory()
        try:
            job = session.get(ExecutionJob, run_id)
            node_state = session.scalars(
                select(ExecutionNodeState).where(ExecutionNodeState.run_id == run_id)
            ).first()
            assert job is not None and job.status == "INTERRUPTED"
            assert node_state is not None and node_state.status == "BLOCKED"
        finally:
            session.close()


def test_denial_fails_the_run_safely(client: TestClient, demo_workflow: dict) -> None:
    run = _run_demo_workflow(client, demo_workflow)
    decision = _decision_for_run(client, run["id"])

    denied = client.post(
        f"/api/v1/decisions/{decision['id']}/deny", json={"reason": "amount mismatch"}
    )
    assert denied.status_code == 200
    assert denied.json()["status"] == "DENIED"

    failed = client.get(f"/api/v1/runs/{run['id']}").json()
    assert failed["status"] == "FAILED"
    assert failed["failure_code"] == "DECISION_DENIED"

    retry = client.post(f"/api/v1/decisions/{decision['id']}/approve", json={})
    assert retry.status_code == 409
    assert retry.json()["code"] == "DECISION_CLOSED"


def test_duplicate_response_scenario_violates_ledger_invariant(client: TestClient) -> None:
    scenarios = client.get("/api/v1/scenarios").json()
    duplicate = next(item for item in scenarios if item["fault_type"] == "DUPLICATE_RESPONSE")

    started = client.post(f"/api/v1/scenarios/{duplicate['id']}/run")
    assert started.status_code == 201, started.text
    run = started.json()
    assert run["status"] == "FAILED"
    assert run["trigger"] == "CHAOS"
    assert run["failure_code"] == "INVARIANT_VIOLATED"
    assert run["verdicts"], "invariant verdicts must be persisted"

    violated = [item for item in run["verdicts"] if item["verdict"] == "VIOLATED"]
    assert violated
    assert any(item["invariant_name"] == "Exactly one ledger entry" for item in violated)
    assert any(event["type"] == "FAULT_INJECTED" for event in run["events"])


def test_recovery_restores_a_checkpoint_and_verifies_the_run(client: TestClient) -> None:
    scenario = next(
        item
        for item in client.get("/api/v1/scenarios").json()
        if item["fault_type"] == "NETWORK_DROP"
    )
    failed = client.post(f"/api/v1/scenarios/{scenario['id']}/run").json()
    assert failed["status"] == "AWAITING_RECOVERY"

    recovery = client.post(f"/api/v1/runs/{failed['id']}/recover", json={"strategy": "RESUME"})

    assert recovery.status_code == 200, recovery.text
    attempt = recovery.json()
    assert attempt["status"] == "VERIFIED"
    assert attempt["checkpoint_id"]
    assert any(step["step"] == "restore_checkpoint" for step in attempt["steps"])

    recovered = client.get(f"/api/v1/runs/{failed['id']}").json()
    assert recovered["status"] == "SUCCEEDED"
    assert recovered["verdict"] == "VERIFIED"

    attempts = client.get(f"/api/v1/runs/{failed['id']}/recoveries").json()
    assert len(attempts) == 1
    assert attempts[0]["status"] == "VERIFIED"


def test_unrecoverable_run_returns_a_problem(client: TestClient, demo_workflow: dict) -> None:
    run = _run_demo_workflow(client, demo_workflow)
    assert run["status"] == "AWAITING_DECISION"

    response = client.post(f"/api/v1/runs/{run['id']}/recover", json={"strategy": "RESUME"})

    assert response.status_code == 409
    assert response.json()["code"] == "RUN_NOT_RECOVERABLE"


def test_recovery_refuses_to_verify_a_corrupted_checkpoint(client: TestClient) -> None:
    scenario = next(
        item
        for item in client.get("/api/v1/scenarios").json()
        if item["fault_type"] == "DUPLICATE_RESPONSE"
    )
    failed = client.post(f"/api/v1/scenarios/{scenario['id']}/run").json()
    assert failed["status"] == "FAILED"

    recovery = client.post(
        f"/api/v1/runs/{failed['id']}/recover", json={"strategy": "RESUME"}
    ).json()

    assert recovery["status"] == "FAILED"
    still_failed = client.get(f"/api/v1/runs/{failed['id']}").json()
    assert still_failed["verdict"] == "FAILED"
    assert still_failed["failure_code"] == "INVARIANT_VIOLATED"


def _decision_workflow(
    client: TestClient,
    *,
    effects: list[dict],
    postconditions: list[dict],
    invariants: list[dict] | None = None,
) -> dict:
    response = client.post(
        "/api/v1/workflows",
        json={
            "name": "Human decision execution",
            "description": "A HUMAN_DECISION node that wraps a recorded action.",
            "publish": True,
            "actions": [
                {
                    "ordinal": 0,
                    "node_key": "approve_payment",
                    "node_type": "HUMAN_DECISION",
                    "kind": "SUBMIT",
                    "label": "Approve the payment",
                    "target": {"surface": "sandbox"},
                    "input": {"action": "SUBMIT", "effects": effects},
                    "postconditions": postconditions,
                    "side_effect_class": "IRREVERSIBLE",
                    "requires_authorization": True,
                }
            ],
            "invariants": invariants or [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_human_decision_executes_the_gated_action_once(client: TestClient) -> None:
    workflow = _decision_workflow(
        client,
        effects=[{"op": "set", "path": "review.approved", "value": True}],
        postconditions=[{"path": "review.approved", "op": "eq", "value": True}],
        invariants=[
            {
                "name": "Payment approval was applied",
                "kind": "POSTCONDITION",
                "expression": {
                    "kind": "postcondition",
                    "action_ordinal": 0,
                    "conditions": [{"path": "review.approved", "op": "eq", "value": True}],
                },
            }
        ],
    )

    run = _run_demo_workflow(client, workflow)
    assert run["status"] == "AWAITING_DECISION"
    assert len(_decisions_for_run(client, run["id"])) == 1

    decision = _decision_for_run(client, run["id"])
    approved = client.post(f"/api/v1/decisions/{decision['id']}/approve", json={"note": "ok"})
    assert approved.status_code == 200, approved.text

    duplicate = client.post(
        f"/api/v1/decisions/{decision['id']}/approve", json={"note": "duplicate"}
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["status"] == "APPLIED"
    assert duplicate.json()["capability_token_id"] == approved.json()["capability_token_id"]

    resumed = client.get(f"/api/v1/runs/{run['id']}").json()
    assert resumed["status"] == "SUCCEEDED", (
        resumed["failure_code"],
        resumed["failure_message"],
        resumed["failure_ordinal"],
    )
    assert resumed["verdict"] == "VERIFIED"
    assert len(_decisions_for_run(client, run["id"])) == 1
    starts = [
        event
        for event in resumed["events"]
        if event["type"] == "NODE_STARTED" and event["action_ordinal"] == 0
    ]
    assert len(starts) == 2, "the node is entered before blocking and again after approval"
    assert len(_decisions_for_run(client, run["id"])) == 1


def test_human_decision_postcondition_failure_is_not_reported_as_success(
    client: TestClient,
) -> None:
    workflow = _decision_workflow(
        client,
        effects=[],
        postconditions=[{"path": "review.approved", "op": "eq", "value": True}],
    )

    run = _run_demo_workflow(client, workflow)
    decision = _decision_for_run(client, run["id"])
    approved = client.post(f"/api/v1/decisions/{decision['id']}/approve", json={"note": "ok"})
    assert approved.status_code == 200, approved.text

    failed = client.get(f"/api/v1/runs/{run['id']}").json()
    assert failed["status"] == "FAILED"
    assert failed["failure_code"] == "POSTCONDITION_FAILED"
