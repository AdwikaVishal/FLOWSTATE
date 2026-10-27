"""Idempotency-Key behavior for consequential adapter retries."""

from __future__ import annotations

from fastapi.testclient import TestClient

HEADERS = {"Idempotency-Key": "alan-test-run-0001"}


def test_same_key_and_payload_returns_the_same_run(client: TestClient, demo_workflow: dict) -> None:
    before = len(client.get("/api/v1/runs").json())
    body = {"workflow_id": demo_workflow["id"]}
    first = client.post("/api/v1/runs", json=body, headers=HEADERS)
    second = client.post("/api/v1/runs", json=body, headers=HEADERS)
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["id"] == second.json()["id"]
    assert len(client.get("/api/v1/runs").json()) == before + 1


def test_same_key_with_different_payload_is_a_conflict(
    client: TestClient, demo_workflow: dict
) -> None:
    first = client.post(
        "/api/v1/runs",
        json={"workflow_id": demo_workflow["id"], "inputs": {"source": "a"}},
        headers={"Idempotency-Key": "alan-conflict-0001"},
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/api/v1/runs",
        json={"workflow_id": demo_workflow["id"], "inputs": {"source": "b"}},
        headers={"Idempotency-Key": "alan-conflict-0001"},
    )
    assert second.status_code == 409
    assert second.json()["code"] == "IDEMPOTENCY_CONFLICT"


def test_background_run_retry_returns_current_run_state(
    client: TestClient, demo_workflow: dict
) -> None:
    headers = {"Idempotency-Key": "alan-background-run-0001"}
    body = {"workflow_id": demo_workflow["id"], "background": True, "seed": 41}
    first = client.post("/api/v1/runs", json=body, headers=headers)
    assert first.status_code == 201, first.text

    streamed = client.get(f"/api/v1/runs/{first.json()['id']}/events/stream", params={"once": True})
    assert streamed.status_code == 200
    retry = client.post("/api/v1/runs", json=body, headers=headers)

    assert retry.status_code == 201, retry.text
    assert retry.json()["id"] == first.json()["id"]
    assert retry.json()["status"] == "AWAITING_DECISION"


def test_invalid_idempotency_key_is_rejected(client: TestClient, demo_workflow: dict) -> None:
    response = client.post(
        "/api/v1/runs",
        json={"workflow_id": demo_workflow["id"]},
        headers={"Idempotency-Key": "bad key"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_IDEMPOTENCY_KEY"


def test_scenario_evaluation_is_idempotent(client: TestClient) -> None:
    scenario = next(
        item
        for item in client.get("/api/v1/scenarios").json()
        if item["fault_type"] == "DUPLICATE_RESPONSE"
    )
    headers = {"Idempotency-Key": "alan-evaluate-0001"}
    first = client.post(f"/api/v1/scenarios/{scenario['id']}/evaluate", headers=headers)
    second = client.post(f"/api/v1/scenarios/{scenario['id']}/evaluate", headers=headers)
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["baseline"]["run_id"] == second.json()["baseline"]["run_id"]
    assert first.json()["fault"]["run_id"] == second.json()["fault"]["run_id"]


def test_same_key_can_be_used_in_a_different_scope(client: TestClient, demo_workflow: dict) -> None:
    run = client.post(
        "/api/v1/runs",
        json={"workflow_id": demo_workflow["id"]},
        headers={"Idempotency-Key": "alan-shared-0001"},
    )
    assert run.status_code == 201, run.text
    scenario = next(
        item
        for item in client.get("/api/v1/scenarios").json()
        if item["fault_type"] == "DUPLICATE_RESPONSE"
    )
    report = client.post(
        f"/api/v1/scenarios/{scenario['id']}/evaluate",
        headers={"Idempotency-Key": "alan-shared-0001"},
    )
    assert report.status_code == 201, report.text
