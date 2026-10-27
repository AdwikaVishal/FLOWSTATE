"""The human decision engine.

These tests exercise the blocker taxonomy, the evidence packet, role-based
authorization and the rule that approval alone does not make a run safe.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def role_headers(role: str, actor: str | None = None) -> dict[str, str]:
    return {
        "X-Flowstate-Role": role,
        "X-Flowstate-Actor": actor or f"{role.lower()}-operator",
    }


def open_decisions(client: TestClient) -> list[dict]:
    response = client.get("/api/v1/decisions", params={"open_only": "true", "limit": 200})
    assert response.status_code == 200, response.text
    return response.json()


def decision_of_kind(client: TestClient, kind: str) -> dict:
    for item in open_decisions(client):
        if item["kind"] == kind:
            return item
    raise AssertionError(f"no open decision with kind {kind}")


def run_status(client: TestClient, run_id: str) -> dict:
    response = client.get(f"/api/v1/runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------- taxonomy


def test_blocker_causes_are_classified_and_evidence_is_persisted(
    client: TestClient,
) -> None:
    decisions = open_decisions(client)
    kinds = {item["kind"] for item in decisions}
    assert {
        "CONFLICTING_EVIDENCE",
        "MISSING_INPUT",
        "VALIDATION_FAILED",
        "HUMAN_APPROVAL_REQUIRED",
    } <= kinds

    conflicting = decision_of_kind(client, "CONFLICTING_EVIDENCE")
    assert conflicting["required_role"] == "FINANCE_APPROVER"
    operator_view = client.get(
        f"/api/v1/decisions/{conflicting['id']}", headers=role_headers("OPERATOR")
    ).json()
    finance_view = client.get(
        f"/api/v1/decisions/{conflicting['id']}", headers=role_headers("FINANCE_APPROVER")
    ).json()
    assert operator_view["can_decide"] is False
    assert finance_view["can_decide"] is True

    blocker = conflicting["blocker"]
    assert blocker["status"] == "OPEN"
    assert blocker["code"] == "INVOICE_PO_VARIANCE"
    assert blocker["evidence_hash"]
    assert blocker["resume_conditions"], "resumption conditions must be declared"

    evidence = conflicting["evidence_items"]
    assert len(evidence) == 4
    by_label = {item["label"]: item for item in evidence}

    invoice = by_label["Invoice total (cents)"]
    assert invoice["source_kind"] == "STATE_FIELD"
    assert invoice["source_path"] == "invoice.amount_cents"
    assert invoice["value_json"] == {"value": 184500}
    assert invoice["validation"] == "FAIL", "the invoice does not match the PO"
    assert invoice["record_ref"] == "INV-2041"

    purchase_order = by_label["Purchase order total (cents)"]
    assert purchase_order["validation"] == "PASS"
    assert purchase_order["record_ref"] == "PO-8841"

    audit_item = by_label["Comparison step"]
    assert audit_item["source_kind"] == "AUDIT_EVENT"
    assert audit_item["value_json"]["event_type"] == "ACTION_SUCCEEDED"
    assert audit_item["value_json"]["hash"], "audit evidence carries the event hash"


def test_blocker_filters_and_run_navigation(client: TestClient) -> None:
    only_missing = client.get("/api/v1/decisions", params={"kind": "MISSING_INPUT"}).json()
    assert only_missing
    assert {item["kind"] for item in only_missing} == {"MISSING_INPUT"}

    decision = only_missing[0]
    assert decision["workflow_id"], "the inbox can navigate to the workflow"
    assert decision["workflow_name"] == "Invoice Discrepancy Review"
    assert decision["run_id"]

    blockers = client.get("/api/v1/blockers", params={"status": "OPEN"}).json()
    assert {item["kind"] for item in blockers} >= {"MISSING_INPUT", "CONFLICTING_EVIDENCE"}

    run_blockers = client.get(f"/api/v1/runs/{decision['run_id']}/blockers").json()
    assert run_blockers and run_blockers[0]["run_id"] == decision["run_id"]


# --------------------------------------------------------------- authorization


def test_unauthorized_approval_keeps_the_run_blocked(client: TestClient) -> None:
    decision = decision_of_kind(client, "CONFLICTING_EVIDENCE")

    response = client.post(
        f"/api/v1/decisions/{decision['id']}/approve",
        json={"note": "operator override"},
        headers=role_headers("OPERATOR"),
    )

    assert response.status_code == 403, response.text
    assert response.json()["code"] == "ROLE_NOT_AUTHORIZED"
    assert response.json()["errors"]["required_role"] == "FINANCE_APPROVER"

    run = run_status(client, decision["run_id"])
    assert run["status"] == "AWAITING_DECISION"
    assert run["verdict"] == "PENDING"

    events = [item["type"] for item in run["events"]]
    assert "DECISION_ROLE_DENIED" in events

    history = client.get(f"/api/v1/decisions/{decision['id']}/history").json()
    assert history[-1]["outcome"] == "DENIED_ROLE"
    assert history[-1]["actor_role"] == "OPERATOR"


def test_agent_identity_cannot_decide(client: TestClient) -> None:
    decision = decision_of_kind(client, "CONFLICTING_EVIDENCE")
    response = client.post(
        f"/api/v1/decisions/{decision['id']}/approve",
        json={},
        headers={
            "X-Flowstate-Actor-Kind": "AGENT",
            "X-Flowstate-Role": "ADMIN",
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "HUMAN_ACTOR_REQUIRED"


# --------------------------------------------------------------- missing input


def test_missing_information_resolves_and_resumes(client: TestClient) -> None:
    decision = decision_of_kind(client, "MISSING_INPUT")
    assert decision["required_role"] == "OPERATOR"
    assert "provide_info" in decision["permitted_actions"]
    assert decision["input_spec"][0]["key"] == "cost_center"
    # The missing field is genuinely absent in persisted state, not fabricated.
    evidence = {item["label"]: item for item in decision["evidence_items"]}
    assert evidence["Cost centre on the invoice"]["validation"] == "MISSING"

    response = client.post(
        f"/api/v1/decisions/{decision['id']}/provide-info",
        json={"values": {"cost_center": "CC-200"}, "note": "booked to platform"},
        headers=role_headers("OPERATOR"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "APPLIED"
    assert body["provided_input"] == {"cost_center": "CC-200"}
    assert body["blocker"]["status"] == "RESOLVED"

    run = run_status(client, decision["run_id"])
    assert run["status"] == "SUCCEEDED"
    assert run["verdict"] == "VERIFIED"
    assert run["chain_valid"] is True

    history = client.get(f"/api/v1/decisions/{decision['id']}/history").json()
    actions = [item["action"] for item in history]
    assert "PROVIDE_INFO" in actions


def test_unknown_input_field_is_rejected(client: TestClient) -> None:
    decision = decision_of_kind(client, "MISSING_INPUT")
    response = client.post(
        f"/api/v1/decisions/{decision['id']}/provide-info",
        json={"values": {"gl_code": "9999"}},
        headers=role_headers("OPERATOR"),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "UNKNOWN_INPUT_FIELD"

    run = run_status(client, decision["run_id"])
    assert run["status"] == "AWAITING_DECISION"


def test_supplied_information_that_does_not_clear_the_blocker_keeps_it_blocked(
    client: TestClient,
) -> None:
    decision = decision_of_kind(client, "MISSING_INPUT")
    # An empty string satisfies "present" as a field but is not a valid booking.
    response = client.post(
        f"/api/v1/decisions/{decision['id']}/provide-info",
        json={"values": {"cost_center": ""}},
        headers=role_headers("OPERATOR"),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "MISSING_INPUT_VALUE"

    run = run_status(client, decision["run_id"])
    assert run["status"] == "AWAITING_DECISION"


# ---------------------------------------------------------- contradictory data


def test_contradictory_records_can_be_approved_by_finance(client: TestClient) -> None:
    decision = decision_of_kind(client, "CONFLICTING_EVIDENCE")
    operator_view = client.get(
        f"/api/v1/decisions/{decision['id']}", headers=role_headers("OPERATOR")
    ).json()
    assert operator_view["can_decide"] is False

    response = client.post(
        f"/api/v1/decisions/{decision['id']}/approve",
        json={"note": "variance accepted against PO-8841"},
        headers=role_headers("FINANCE_APPROVER", "ada.finance"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "APPLIED"
    assert body["decided_role"] == "FINANCE_APPROVER"
    assert body["blocker"]["status"] == "RESOLVED"
    assert body["capability_token_id"]

    run = run_status(client, decision["run_id"])
    assert run["status"] == "SUCCEEDED"
    assert run["verdict"] == "VERIFIED"

    event_types = [item["type"] for item in run["events"]]
    assert "BLOCKER_DETECTED" in event_types
    assert "DECISION_GRANTED" in event_types
    assert "BLOCKER_RESOLVED" in event_types

    history = client.get(f"/api/v1/decisions/{decision['id']}/history").json()
    assert [item["action"] for item in history] == ["APPROVE", "RESUME"]
    assert history[0]["actor"] == "ada.finance"
    assert history[0]["outcome"] == "AUTHORIZED"


# ------------------------------------------------------------- expired evidence


def test_expired_evidence_blocks_resumption_until_refreshed(client: TestClient) -> None:
    decision = decision_of_kind(client, "VALIDATION_FAILED")
    assert decision["required_role"] == "REVIEWER"

    evidence = {item["label"]: item for item in decision["evidence_items"]}
    assert evidence["Policy expiry"]["validation"] == "EXPIRED"

    refused = client.post(
        f"/api/v1/decisions/{decision['id']}/approve",
        json={"note": "just approve"},
        headers=role_headers("REVIEWER"),
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "EVIDENCE_STALE"

    blocked = run_status(client, decision["run_id"])
    assert blocked["status"] == "AWAITING_DECISION"
    assert "DECISION_EVIDENCE_CHANGED" in [item["type"] for item in blocked["events"]]

    # The refusal must not close the decision; the refreshed packet is actionable.
    refreshed = client.get(f"/api/v1/decisions/{decision['id']}").json()
    assert refreshed["status"] == "NOTIFIED"

    fixed = client.post(
        f"/api/v1/decisions/{decision['id']}/provide-info",
        json={
            "values": {
                "policy_expires_at": "2027-04-01T00:00:00Z",
                "policy_renewal_reference": "AP-2027-01",
            }
        },
        headers=role_headers("REVIEWER"),
    )
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["status"] == "APPLIED"

    run = run_status(client, decision["run_id"])
    assert run["status"] == "SUCCEEDED"
    assert run["verdict"] == "VERIFIED"
    assert run["chain_valid"] is True


# --------------------------------------------------------------------- reject


def test_rejection_stops_the_run_safely(client: TestClient) -> None:
    decision = decision_of_kind(client, "CONFLICTING_EVIDENCE")

    response = client.post(
        f"/api/v1/decisions/{decision['id']}/reject",
        json={"reason": "vendor has not explained the variance"},
        headers=role_headers("FINANCE_APPROVER"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "DENIED"
    assert body["blocker"]["status"] == "RESOLVED"

    run = run_status(client, decision["run_id"])
    assert run["status"] == "FAILED"
    assert run["verdict"] == "FAILED"
    assert run["failure_code"] == "DECISION_DENIED"
    assert run["failure_message"].startswith("Human decision denied")

    events = [item["type"] for item in run["events"]]
    assert "DECISION_DENIED" in events
    assert "RUN_VERIFIED" not in events

    retry = client.post(
        f"/api/v1/decisions/{decision['id']}/approve",
        json={},
        headers=role_headers("FINANCE_APPROVER"),
    )
    assert retry.status_code == 409
    assert retry.json()["code"] == "DECISION_CLOSED"


# ---------------------------------------------------------------------- defer


def test_deferral_keeps_the_run_blocked_and_is_reversible(client: TestClient) -> None:
    decision = decision_of_kind(client, "CONFLICTING_EVIDENCE")

    deferred = client.post(
        f"/api/v1/decisions/{decision['id']}/defer",
        json={"reason": "waiting for the controller"},
        headers=role_headers("OPERATOR"),
    )
    assert deferred.status_code == 200, deferred.text
    body = deferred.json()
    assert body["status"] == "DEFERRED"
    assert body["deferred_until"]

    run = run_status(client, decision["run_id"])
    assert run["status"] == "AWAITING_DECISION"
    assert run["verdict"] == "PENDING"

    still_open = {item["id"] for item in open_decisions(client)}
    assert decision["id"] in still_open, "a deferral stays in the inbox"

    history = client.get(f"/api/v1/decisions/{decision['id']}/history").json()
    assert history[-1]["action"] == "DEFER"

    approved = client.post(
        f"/api/v1/decisions/{decision['id']}/approve",
        json={"note": "controller approved"},
        headers=role_headers("FINANCE_APPROVER"),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPLIED"

    resolved = run_status(client, decision["run_id"])
    assert resolved["status"] == "SUCCEEDED"
    assert resolved["verdict"] == "VERIFIED"


# ------------------------------------------------------------------ auditability


def test_every_decision_action_is_persisted_and_auditable(client: TestClient) -> None:
    decision = decision_of_kind(client, "CONFLICTING_EVIDENCE")
    client.post(
        f"/api/v1/decisions/{decision['id']}/defer",
        json={"reason": "checking"},
        headers=role_headers("OPERATOR"),
    )
    client.post(
        f"/api/v1/decisions/{decision['id']}/reject",
        json={"reason": "not payable"},
        headers=role_headers("FINANCE_APPROVER"),
    )

    history = client.get(f"/api/v1/decisions/{decision['id']}/history").json()
    assert [item["action"] for item in history] == ["DEFER", "REJECT"]
    assert all(item["created_at"] for item in history)
    assert history[0]["actor_role"] == "OPERATOR"
    assert history[1]["actor_role"] == "FINANCE_APPROVER"

    run = run_status(client, decision["run_id"])
    verification = client.get(f"/api/v1/runs/{decision['run_id']}/events/verify").json()
    assert verification["valid"] is True
    assert verification["event_count"] == len(run["events"])

    types = [item["type"] for item in run["events"]]
    assert types.count("BLOCKER_DETECTED") >= 1
    assert types.count("DECISION_REQUESTED") == 1
    assert "DECISION_DEFERRED" in types
    assert "DECISION_DENIED" in types
