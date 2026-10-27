"""Workflow learning API: capture, review, generation, validation and replay."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from tests.fakes import (
    FakeBrowserSession,
    invoice_lookup_events,
    queue_snapshot,
    raw_event,
)


def start_capture(client: TestClient, label: str = "Invoice lookup") -> dict:
    response = client.post("/api/v1/captures", json={"label": label})
    assert response.status_code == 201, response.text
    return response.json()


def record(client: TestClient, session_id: str, events: list[dict]) -> dict:
    response = client.post(f"/api/v1/captures/{session_id}/events", json={"events": events})
    assert response.status_code == 200, response.text
    return response.json()


def stop(client: TestClient, session_id: str) -> dict:
    response = client.post(f"/api/v1/captures/{session_id}/stop")
    assert response.status_code == 200, response.text
    return response.json()


def use_fake_browser(client: TestClient, **kwargs) -> None:
    client.app.state.browser_factory = lambda: FakeBrowserSession(**kwargs)


def prepared_capture(client: TestClient, **kwargs) -> tuple[dict, dict]:
    use_fake_browser(client, **kwargs)
    session = start_capture(client)
    record(client, session["id"], invoice_lookup_events())
    session = stop(client, session["id"])
    return session, client.get(f"/api/v1/captures/{session['id']}").json()


def save_published(
    client: TestClient, session_id: str, name: str = "Learned invoice lookup"
) -> dict:
    response = client.post(
        f"/api/v1/captures/{session_id}/workflow",
        json={"name": name, "publish": True},
    )
    assert response.status_code == 201, response.text
    return response.json()


# ----------------------------------------------------------------- lifecycle


def test_capture_lifecycle_and_ordered_candidates(client: TestClient) -> None:
    session = start_capture(client)
    assert session["status"] == "RECORDING"
    assert session["demo_url"].endswith("/demo-app/")

    paused = client.post(f"/api/v1/captures/{session['id']}/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "PAUSED"
    rejected = client.post(
        f"/api/v1/captures/{session['id']}/events",
        json={"events": invoice_lookup_events()[:1]},
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "CAPTURE_NOT_RECORDING"

    resumed = client.post(f"/api/v1/captures/{session['id']}/resume")
    assert resumed.json()["status"] == "RECORDING"

    record(client, session["id"], invoice_lookup_events())
    session = stop(client, session["id"])
    assert session["status"] == "REVIEW"
    assert session["event_count"] == 4
    assert [item["ordinal"] for item in session["actions"]] == [0, 1, 2, 3]
    assert all(item["selector"] for item in session["actions"])
    assert session["actions"][0]["selector"][0]["strategy"] == "role"
    assert session["actions"][0]["selector"][0]["score"] == 100
    assert session["actions"][2]["selector"][0]["strategy"] == "role"
    assert session["suggested_inputs"] == {"supplier_id": "SUP-77", "invoice_id": "INV-1001"}

    again = client.post(
        f"/api/v1/captures/{session['id']}/events", json={"events": invoice_lookup_events()[:1]}
    )
    assert again.status_code == 409


def test_sensitive_values_are_redacted_and_never_persisted(client: TestClient) -> None:
    session = start_capture(client)
    event = raw_event(
        0,
        "TYPE",
        role="textbox",
        name="API token",
        label="API token",
        css="#api-token",
        input_name="api_token",
        param="api_token",
        value="super-secret-token",
        sensitive=True,
    )
    event["kind"] = "TYPE"
    response = client.post(f"/api/v1/captures/{session['id']}/events", json={"events": [event]})
    assert response.status_code == 200, response.text
    assert "super-secret-token" not in response.text
    action = response.json()["actions"][0]
    assert action["input_value"] is None
    assert action["redacted_fields"] == ["value"]
    assert action["status"] == "AMBIGUOUS"
    assert action["ambiguity_reason"] == "sensitive_value_redacted"


def test_ambiguous_action_blocks_save_until_reviewed(client: TestClient) -> None:
    use_fake_browser(client)
    session = start_capture(client)
    mystery = raw_event(
        0,
        "CLICK",
        role="",
        name="",
        test_id="",
        css="#mystery",
        before=queue_snapshot(),
        after=queue_snapshot(),
    )
    record(client, session["id"], [mystery])
    session = stop(client, session["id"])
    action = session["actions"][0]
    assert action["ambiguous"] is True
    assert action["status"] == "AMBIGUOUS"
    assert session["unresolved_ambiguities"] == [0]

    blocked = client.post(f"/api/v1/captures/{session['id']}/workflow", json={"publish": False})
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "CAPTURE_AMBIGUITIES_UNRESOLVED"
    assert blocked.json()["errors"]["ordinals"] == [0]

    patched = client.patch(
        f"/api/v1/captures/{session['id']}/actions/{action['id']}",
        json={
            "label": "Open the hidden panel",
            "accept_ambiguity": True,
            "resolution_note": "Reviewed by an operator; the CSS locator is intentional.",
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["unresolved_ambiguities"] == []

    saved = client.post(f"/api/v1/captures/{session['id']}/workflow", json={"publish": False})
    assert saved.status_code == 201, saved.text
    assert saved.json()["version"]["status"] == "DRAFT"


# --------------------------------------------------------------- happy path


def test_record_validate_save_and_replay_with_different_inputs(client: TestClient) -> None:
    session, detail = prepared_capture(client)
    assert detail["status"] == "REVIEW"

    validated = client.post(f"/api/v1/captures/{session['id']}/validate")
    assert validated.status_code == 200, validated.text
    body = validated.json()
    assert body["status"] == "VALIDATED"
    assert body["validation"]["ok"] is True
    assert body["validation"]["passed"] == body["validation"]["checked"]
    assert all(item["status"] == "VALIDATED" for item in body["actions"])

    saved = save_published(client, session["id"])
    assert saved["version"]["status"] == "PUBLISHED"
    assert saved["workflow"]["current_version_id"] == saved["version"]["id"]
    assert saved["version"]["input_schema"]["required"] == ["supplier_id", "invoice_id"]
    assert all(
        item["channel"] == "BROWSER"
        for item in saved["version"]["actions"]
        if item["node_type"] == "ACTION"
    )
    assert [item["node_type"] for item in saved["version"]["actions"]] == [
        "START",
        "VALIDATION",
        "ACTION",
        "ACTION",
        "ACTION",
        "ACTION",
        "ACTION",
        "END",
    ]

    first = client.post(
        f"/api/v1/captures/{session['id']}/replay",
        json={"inputs": {"supplier_id": "SUP-77", "invoice_id": "INV-1003"}},
    )
    assert first.status_code == 201, first.text
    run = first.json()
    assert run["status"] == "SUCCEEDED", run
    assert run["verdict"] == "VERIFIED"
    assert run["driver"] == "fake-browser-v1"
    assert run["capture_session_id"] == session["id"]
    assert run["chain_valid"] is True
    assert len(run["evidence"]) >= 1
    assert all(item["verified"] is True for item in run["evidence"])
    screenshot = run["evidence"][0]
    download = client.get(f"/api/v1/runs/{run['id']}/evidence/{screenshot['id']}")
    assert download.status_code == 200
    assert download.headers["content-type"] == "image/png"
    assert download.content.startswith(b"fake-")
    Path(screenshot["uri"]).write_bytes(b"tampered")
    tampered = client.get(f"/api/v1/runs/{run['id']}/evidence/{screenshot['id']}")
    assert tampered.status_code == 409
    assert tampered.json()["code"] == "EVIDENCE_TAMPERED"

    states = {item["node_key"]: item for item in run["node_states"]}
    assert states["open_demo_app"]["status"] == "PASSED"
    assert states["captured_4"]["status"] == "PASSED"

    timeline = client.get(f"/api/v1/runs/{run['id']}/timeline")
    assert timeline.status_code == 200
    payload = timeline.json()
    assert payload["chain_valid"] is True
    assert any(entry["type"] == "RUN_STARTED" for entry in payload["entries"])
    assert any(entry["node_key"] == "captured_4" for entry in payload["entries"])

    second = client.post(
        f"/api/v1/captures/{session['id']}/replay",
        json={"inputs": {"supplier_id": "SUP-88", "invoice_id": "INV-1002"}},
    )
    assert second.status_code == 201, second.text
    assert second.json()["status"] == "SUCCEEDED"
    assert second.json()["id"] != run["id"]

    listed = client.get(f"/api/v1/captures/{session['id']}/runs")
    assert listed.status_code == 200
    assert {item["id"] for item in listed.json()} == {run["id"], second.json()["id"]}


def test_replay_requires_declared_inputs(client: TestClient) -> None:
    session, _ = prepared_capture(client)
    client.post(f"/api/v1/captures/{session['id']}/validate")
    save_published(client, session["id"])

    missing = client.post(
        f"/api/v1/captures/{session['id']}/replay",
        json={"inputs": {"supplier_id": "SUP-77"}},
    )
    assert missing.status_code == 422
    assert missing.json()["code"] == "INPUT_SCHEMA_VALIDATION_FAILED"
    assert any("invoice_id" in error for error in missing.json()["errors"]["errors"])


# ------------------------------------------------------------ safe failures


def test_selector_drift_fails_safely_and_is_not_success(client: TestClient) -> None:
    session, _ = prepared_capture(client)
    client.post(f"/api/v1/captures/{session['id']}/validate")
    save_published(client, session["id"])

    drifted = client.post(
        f"/api/v1/captures/{session['id']}/replay",
        json={
            "inputs": {"supplier_id": "SUP-77", "invoice_id": "INV-1001"},
            "variant": "changed",
        },
    )
    assert drifted.status_code == 201, drifted.text
    run = drifted.json()
    assert run["status"] == "FAILED"
    assert run["verdict"] == "FAILED"
    assert run["failure_code"] == "SELECTOR_NOT_FOUND"
    states = {item["node_key"]: item for item in run["node_states"]}
    failed = [item for item in states.values() if item["status"] == "FAILED"]
    assert failed and failed[0]["error_code"] == "SELECTOR_NOT_FOUND"
    assert all(item["status"] != "PASSED" for item in states.values() if item["ordinal"] >= 3)


def test_missing_selector_blocks_validation_and_publish(client: TestClient) -> None:
    session, _ = prepared_capture(client, fail_selectors=True)
    validated = client.post(f"/api/v1/captures/{session['id']}/validate")
    assert validated.status_code == 200, validated.text
    body = validated.json()
    assert body["status"] == "REVIEW"
    assert body["validation"]["ok"] is False
    assert any(item["status"] == "UNRESOLVED" for item in body["actions"])
    assert any(entry.get("code") == "SELECTOR_NOT_FOUND" for entry in body["validation"]["actions"])

    blocked = client.post(f"/api/v1/captures/{session['id']}/workflow", json={"publish": True})
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "CAPTURE_NOT_VALIDATED"


def test_timeout_after_action_is_unknown_not_success(client: TestClient) -> None:
    session, _ = prepared_capture(client)
    client.post(f"/api/v1/captures/{session['id']}/validate")
    save_published(client, session["id"])

    use_fake_browser(client, break_validation=True)
    response = client.post(
        f"/api/v1/captures/{session['id']}/replay",
        json={"inputs": {"supplier_id": "SUP-77", "invoice_id": "INV-1001"}},
    )
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "AWAITING_RECOVERY"
    assert run["status"] != "SUCCEEDED"
    assert run["failure_code"] == "ACTION_OUTCOME_UNKNOWN"
    states = {item["node_key"]: item for item in run["node_states"]}
    assert states["captured_4"]["status"] == "BLOCKED"
    assert states["captured_4"]["error_class"] == "AMBIGUOUS_OUTCOME"


def test_field_value_timeout_is_not_confirmed_by_a_page_probe(client: TestClient) -> None:
    session, _ = prepared_capture(client)
    client.post(f"/api/v1/captures/{session['id']}/validate")
    save_published(client, session["id"])

    use_fake_browser(client, break_typing=True)
    response = client.post(
        f"/api/v1/captures/{session['id']}/replay",
        json={"inputs": {"supplier_id": "SUP-77", "invoice_id": "INV-1001"}},
    )
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "AWAITING_RECOVERY"
    assert run["status"] != "SUCCEEDED"
    assert run["failure_code"] == "ACTION_OUTCOME_UNKNOWN"
    states = {item["node_key"]: item for item in run["node_states"]}
    assert states["captured_1"]["status"] == "BLOCKED"
    assert states["captured_1"]["error_class"] == "AMBIGUOUS_OUTCOME"


def test_browser_driver_unavailable_is_rejected(client: TestClient) -> None:
    session, _ = prepared_capture(client)
    client.post(f"/api/v1/captures/{session['id']}/validate")
    save_published(client, session["id"])

    client.app.state.browser_factory = None
    response = client.post(
        f"/api/v1/captures/{session['id']}/replay",
        json={"inputs": {"supplier_id": "SUP-77", "invoice_id": "INV-1001"}},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "BROWSER_DRIVER_UNAVAILABLE"


# --------------------------------------------------------------- versioning


def test_saving_again_creates_a_new_version(client: TestClient) -> None:
    session, _ = prepared_capture(client)
    client.post(f"/api/v1/captures/{session['id']}/validate")
    first = save_published(client, session["id"])

    later = client.post(
        f"/api/v1/captures/{session['id']}/workflow",
        json={"publish": True},
    )
    assert later.status_code == 201, later.text
    second = later.json()
    assert second["version"]["version"] == 2
    assert second["version"]["id"] != first["version"]["id"]
    workflow = client.get(f"/api/v1/workflows/{first['workflow']['id']}").json()
    assert workflow["version_count"] == 2
    assert workflow["current_version_id"] == second["version"]["id"]


def test_draft_can_be_validated_then_published_as_a_new_version(client: TestClient) -> None:
    session, _ = prepared_capture(client)
    client.post(f"/api/v1/captures/{session['id']}/validate")
    draft = client.post(f"/api/v1/captures/{session['id']}/workflow", json={"publish": False})
    assert draft.status_code == 201, draft.text
    assert draft.json()["version"]["status"] == "DRAFT"
    assert draft.json()["session"]["version_status"] == "DRAFT"

    revalidated = client.post(f"/api/v1/captures/{session['id']}/validate")
    assert revalidated.status_code == 200, revalidated.text
    assert revalidated.json()["status"] == "SAVED"
    assert revalidated.json()["validation"]["ok"] is True

    published = client.post(f"/api/v1/captures/{session['id']}/workflow", json={"publish": True})
    assert published.status_code == 201, published.text
    body = published.json()
    assert body["version"]["version"] == 2
    assert body["version"]["status"] == "PUBLISHED"
    assert body["session"]["version_status"] == "PUBLISHED"

    replay = client.post(
        f"/api/v1/captures/{session['id']}/replay",
        json={"inputs": {"supplier_id": "SUP-77", "invoice_id": "INV-1001"}},
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["status"] == "SUCCEEDED"


def test_learned_workflow_runs_through_the_normal_execution_api(client: TestClient) -> None:
    session, _ = prepared_capture(client)
    client.post(f"/api/v1/captures/{session['id']}/validate")
    saved = save_published(client, session["id"])

    response = client.post(
        "/api/v1/runs",
        json={
            "workflow_id": saved["workflow"]["id"],
            "version_id": saved["version"]["id"],
            "inputs": {"supplier_id": "SUP-77", "invoice_id": "INV-1003"},
        },
    )
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "SUCCEEDED", run
    assert run["verdict"] == "VERIFIED"
    assert run["driver"] == "fake-browser-v1"
    assert run["capture_session_id"] == session["id"]
    assert run["inputs"] == {"supplier_id": "SUP-77", "invoice_id": "INV-1003"}

    linked = client.get(f"/api/v1/captures/{session['id']}/runs").json()
    assert any(item["id"] == run["id"] for item in linked)


def test_unsaved_edits_are_never_replayed(client: TestClient) -> None:
    session, _ = prepared_capture(client)
    client.post(f"/api/v1/captures/{session['id']}/validate")
    save_published(client, session["id"])

    detail = client.get(f"/api/v1/captures/{session['id']}").json()
    action = detail["actions"][0]
    edited = client.patch(
        f"/api/v1/captures/{session['id']}/actions/{action['id']}",
        json={"label": "Supplier identifier", "input_param": "supplier_id"},
    )
    assert edited.status_code == 200
    assert edited.json()["status"] == "REVIEW"

    blocked = client.post(
        f"/api/v1/captures/{session['id']}/replay",
        json={"inputs": {"supplier_id": "SUP-77", "invoice_id": "INV-1001"}},
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "CAPTURE_NOT_SAVED"


def test_capture_session_is_listed(client: TestClient) -> None:
    session = start_capture(client, "Queue review")
    listed = client.get("/api/v1/captures")
    assert listed.status_code == 200
    assert any(item["id"] == session["id"] for item in listed.json())


def test_learned_financial_step_resumes_after_approval(client: TestClient) -> None:
    session, detail = prepared_capture(client)
    financial = next(item for item in detail["actions"] if item["ordinal"] == 3)
    patched = client.patch(
        f"/api/v1/captures/{session['id']}/actions/{financial['id']}",
        json={"label": "Approve invoice"},
    )
    assert patched.status_code == 200, patched.text

    validated = client.post(f"/api/v1/captures/{session['id']}/validate")
    assert validated.status_code == 200, validated.text
    saved = save_published(client, session["id"])
    decision_action = next(
        item for item in saved["version"]["actions"] if item["node_type"] == "HUMAN_DECISION"
    )
    assert decision_action["requires_authorization"] is True

    replay = client.post(
        f"/api/v1/captures/{session['id']}/replay",
        json={"inputs": {"supplier_id": "SUP-77", "invoice_id": "INV-1001"}},
    )
    assert replay.status_code == 201, replay.text
    run = replay.json()
    assert run["status"] == "AWAITING_DECISION"
    assert run["current_ordinal"] == decision_action["ordinal"]

    decisions = [
        item
        for item in client.get("/api/v1/decisions", params={"limit": 200}).json()
        if item["run_id"] == run["id"]
    ]
    assert len(decisions) == 1

    approved = client.post(f"/api/v1/decisions/{decisions[0]['id']}/approve", json={"note": "ok"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPLIED"

    resumed = client.get(f"/api/v1/runs/{run['id']}").json()
    assert resumed["status"] == "SUCCEEDED", (
        resumed["failure_code"],
        resumed["failure_message"],
    )
    assert resumed["verdict"] == "VERIFIED"
    assert resumed["driver"] == "fake-browser-v1"
    assert (
        len(
            [
                item
                for item in client.get("/api/v1/decisions", params={"limit": 200}).json()
                if item["run_id"] == run["id"]
            ]
        )
        == 1
    )
    states = {item["node_key"]: item for item in resumed["node_states"]}
    assert states[decision_action["node_key"]]["status"] == "PASSED"
    assert not any(item["status"] == "BLOCKED" for item in states.values())
