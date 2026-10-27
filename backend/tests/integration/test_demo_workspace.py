"""The demonstration workspace is labelled, isolated and reversible."""

from __future__ import annotations

from fastapi.testclient import TestClient
from flowstate.demo import fixtures


def test_demo_workspace_is_seeded_and_labelled(client: TestClient) -> None:
    info = client.get("/api/v1/demo").json()
    assert info["seeded"] is True
    assert info["enabled"] is True
    assert info["batch"] == fixtures.DEMO_BATCH

    workflows = client.get("/api/v1/workflows").json()
    demo = next(item for item in workflows if item["slug"] == fixtures.DEMO_SLUG)
    assert demo["origin"] == "DEMO"
    assert demo["name"] == "Invoice Approval"
    assert demo["version_count"] == 1
    assert demo["latest_status"] == "PUBLISHED"


def test_sample_runs_are_historical_fixtures_not_fabricated_live_data(
    client: TestClient,
) -> None:
    runs = client.get("/api/v1/runs").json()

    # Every seeded workflow contributes runs; assert the shape rather than a
    # brittle total, because the demo now covers learning, decisions, chaos,
    # recovery and regression.
    assert len(runs) >= (
        len(fixtures.HISTORICAL_RUNS) + len(fixtures.DISCREPANCY_RUNS) + len(fixtures.VENDOR_RUNS)
    )
    assert {run["origin"] for run in runs} == {"DEMO"}
    assert {run["driver"] for run in runs} == {"simulated-v1"}
    statuses = {run["status"] for run in runs}
    assert "AWAITING_DECISION" in statuses
    assert "AWAITING_RECOVERY" in statuses
    assert "SUCCEEDED" in statuses
    assert "FAILED" in statuses

    overview = client.get("/api/v1/system/overview").json()
    assert overview["counts"]["demo_runs"] == len(runs)
    assert overview["counts"]["live_runs"] == 0


def test_demo_seed_is_idempotent(client: TestClient) -> None:
    second = client.post("/api/v1/demo/seed")

    assert second.status_code == 201
    assert second.json()["created"] is False
    assert len(client.get("/api/v1/workflows").json()) == 4


def test_demo_reset_then_reseed(client: TestClient) -> None:
    reset = client.delete("/api/v1/demo/reset")

    assert reset.status_code == 200
    assert reset.json()["deleted"] == 4
    assert client.get("/api/v1/workflows").json() == []
    assert client.get("/api/v1/runs").json() == []

    reseeded = client.post("/api/v1/demo/seed")
    assert reseeded.status_code == 201
    assert reseeded.json()["created"] is True
    assert len(client.get("/api/v1/workflows").json()) == 4
    assert client.get("/api/v1/demo").json()["seeded"] is True


def test_seeded_workflow_has_actions_invariants_and_scenarios(client: TestClient) -> None:
    workflows = client.get("/api/v1/workflows").json()
    workflow_id = next(item["id"] for item in workflows if item["slug"] == fixtures.DEMO_SLUG)

    detail = client.get(f"/api/v1/workflows/{workflow_id}").json()
    assert len(detail["versions"]) == 1

    version = client.get(
        f"/api/v1/workflows/{workflow_id}/versions/{detail['versions'][0]['id']}"
    ).json()
    assert version["status"] == "PUBLISHED"
    assert [action["ordinal"] for action in version["actions"]] == list(range(8))
    assert len(version["invariants"]) == 7

    scenarios = client.get("/api/v1/scenarios").json()
    assert len(scenarios) == (
        len(fixtures.SCENARIOS)
        + len(fixtures.VENDOR_SCENARIOS)
        + len(fixtures.INVOICE_PROCESSING_SCENARIOS)
    )
    assert {item["origin"] for item in scenarios} == {"DEMO"}
