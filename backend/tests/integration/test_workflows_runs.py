"""Workflow, version, run and audit contract tests."""

from __future__ import annotations

from fastapi.testclient import TestClient
from flowstate.db.models import ExecutionEvent


def _workflow_payload() -> dict:
    return {
        "name": "Refund review",
        "description": "A small deterministic workflow used by the API tests.",
        "publish": True,
        "actions": [
            {
                "ordinal": 0,
                "kind": "NAVIGATE",
                "label": "Open the refund queue",
                "input": {"effects": [{"op": "set", "path": "queue.loaded", "value": True}]},
                "postconditions": [{"path": "queue.loaded", "op": "eq", "value": True}],
                "side_effect_class": "READ_ONLY",
            },
            {
                "ordinal": 1,
                "kind": "CLICK",
                "label": "Mark the refund reviewed",
                "input": {
                    "effects": [{"op": "set", "path": "invoice.status", "value": "APPROVED"}]
                },
                "postconditions": [{"path": "invoice.status", "op": "eq", "value": "APPROVED"}],
                "side_effect_class": "IDEMPOTENT",
            },
        ],
        "invariants": [
            {
                "name": "Refund is approved",
                "kind": "TERMINAL_STATE",
                "severity": "CRITICAL",
                "expression": {
                    "kind": "state",
                    "path": "invoice.status",
                    "op": "eq",
                    "value": "APPROVED",
                },
            }
        ],
    }


def test_create_publish_run_and_verify(client: TestClient) -> None:
    created = client.post("/api/v1/workflows", json=_workflow_payload())
    assert created.status_code == 201, created.text
    workflow = created.json()
    assert workflow["origin"] == "LIVE"
    assert workflow["latest_status"] == "PUBLISHED"
    assert workflow["current_version_id"]

    run = client.post("/api/v1/runs", json={"workflow_id": workflow["id"], "seed": 11})
    assert run.status_code == 201, run.text
    body = run.json()
    assert body["status"] == "SUCCEEDED"
    assert body["verdict"] == "VERIFIED"
    assert body["origin"] == "LIVE"
    assert body["event_count"] > 0
    assert body["chain_valid"] is True
    assert len(body["events"]) == body["event_count"]

    events = client.get(f"/api/v1/runs/{body['id']}/events").json()
    assert [event["seq"] for event in events] == list(range(len(events)))
    assert {event["type"] for event in events} >= {"RUN_CREATED", "RUN_STARTED", "RUN_VERIFIED"}

    chain = client.get(f"/api/v1/runs/{body['id']}/events/verify").json()
    assert chain["valid"] is True
    assert chain["event_count"] == len(events)


def test_draft_versions_cannot_execute(client: TestClient) -> None:
    workflow = client.post("/api/v1/workflows", json=_workflow_payload()).json()
    draft = client.post(
        f"/api/v1/workflows/{workflow['id']}/versions",
        json={**_workflow_payload(), "publish": False},
    )
    assert draft.status_code == 201
    assert draft.json()["status"] == "DRAFT"

    before = len(client.get("/api/v1/runs").json())
    response = client.post(
        "/api/v1/runs",
        json={"workflow_id": workflow["id"], "version_id": draft.json()["id"]},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "VERSION_NOT_PUBLISHED"
    assert len(client.get("/api/v1/runs").json()) == before, "no run was created"


def test_publish_a_draft_version(client: TestClient) -> None:
    workflow = client.post("/api/v1/workflows", json=_workflow_payload()).json()
    draft = client.post(
        f"/api/v1/workflows/{workflow['id']}/versions",
        json={**_workflow_payload(), "publish": False},
    ).json()

    published = client.post(f"/api/v1/workflows/{workflow['id']}/versions/{draft['id']}/publish")

    assert published.status_code == 200
    assert published.json()["status"] == "PUBLISHED"
    refreshed = client.get(f"/api/v1/workflows/{workflow['id']}").json()
    assert refreshed["current_version_id"] == draft["id"]


def test_tampering_with_an_event_breaks_the_chain(client: TestClient) -> None:
    workflow = client.post("/api/v1/workflows", json=_workflow_payload()).json()
    run = client.post("/api/v1/runs", json={"workflow_id": workflow["id"]}).json()

    with client.app.state.db.session_factory() as session:
        event = (
            session.query(ExecutionEvent)
            .filter(ExecutionEvent.run_id == run["id"])
            .order_by(ExecutionEvent.seq)
            .first()
        )
        assert event is not None
        event.payload = {**event.payload, "tampered": True}
        session.commit()

    verification = client.get(f"/api/v1/runs/{run['id']}/events/verify").json()
    assert verification["valid"] is False
    assert verification["broken_at_seq"] == 0
    assert verification["reason"]


def test_audit_overview_covers_recent_runs(client: TestClient) -> None:
    overview = client.get("/api/v1/audit").json()

    runs = client.get("/api/v1/runs", params={"limit": 200}).json()
    expected = min(len(runs), 25)
    assert overview["checked"] == expected
    assert overview["valid"] == expected
    assert overview["broken"] == 0
    assert all(item["head_hash"] for item in overview["runs"])

    verify = client.get(f"/api/v1/audit/verify/{overview['runs'][0]['run_id']}").json()
    assert verify["valid"] is True
