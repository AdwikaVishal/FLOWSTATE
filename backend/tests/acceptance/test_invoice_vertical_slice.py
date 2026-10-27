"""End-to-end acceptance for the invoice-processing vertical slice.

The test walks the same path a new user follows and prints a step-by-step report.
It asserts only against backend state: run verdicts, persisted decisions, recovery
contracts, regression results and audit chains.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

REPORT_PATH = Path(__file__).resolve().parents[3] / "artifacts" / "invoice-slice-step-report.json"


@dataclass
class StepReport:
    steps: list[dict[str, Any]] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.steps.append({"step": name, "status": "PASS" if ok else "FAIL", "detail": detail})
        return ok

    def fail(self, name: str, detail: str) -> None:
        self.check(name, False, detail)

    def finish(self) -> None:
        width = max(len(item["step"]) for item in self.steps) + 2
        lines = ["", "INVOICE PROCESSING — END-TO-END STEP REPORT", "-" * 78]
        for index, item in enumerate(self.steps, start=1):
            lines.append(
                f"{index:>2}. {item['status']:<4}  {item['step']:<{width}}  {item['detail']}"
            )
        failures = [item for item in self.steps if item["status"] == "FAIL"]
        lines.append("-" * 78)
        lines.append(f"{len(self.steps) - len(failures)}/{len(self.steps)} steps passed")
        report = "\n".join(lines)
        print(report)

        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(
            json.dumps({"steps": self.steps, "passed": len(self.steps) - len(failures)}, indent=2),
            encoding="utf-8",
        )
        assert not failures, report


def workflow(client: TestClient, slug: str) -> dict:
    for item in client.get("/api/v1/workflows").json():
        if item["slug"] == slug:
            return item
    raise AssertionError(f"workflow {slug} is not seeded")


def role_headers(role: str) -> dict[str, str]:
    return {"X-Flowstate-Role": role, "X-Flowstate-Actor": f"{role.lower()}-operator"}


def scenario(client: TestClient, workflow_id: str, fault_type: str) -> dict:
    for item in client.get("/api/v1/scenarios", params={"workflow_id": workflow_id}).json():
        if item["fault_type"] == fault_type:
            return item
    raise AssertionError(f"scenario {fault_type} is not seeded")


def run_scenario(client: TestClient, scenario_id: str) -> dict:
    response = client.post(f"/api/v1/scenarios/{scenario_id}/run")
    assert response.status_code == 201, response.text
    return response.json()


def open_decision(client: TestClient, run_id: str) -> dict:
    for item in client.get("/api/v1/decisions", params={"open_only": "true", "limit": 200}).json():
        if item["run_id"] == run_id:
            return item
    raise AssertionError(f"no open decision for run {run_id}")


def run_events(client: TestClient, run_id: str) -> list[dict]:
    return client.get(f"/api/v1/runs/{run_id}/events").json()


@pytest.mark.acceptance
def test_invoice_processing_vertical_slice(client: TestClient) -> None:
    report = StepReport()
    wf = workflow(client, "invoice-processing")

    # ---------------------------------------------------------------- 1. model
    detail = client.get(f"/api/v1/workflows/{wf['id']}").json()
    version = detail["versions"][0]
    graph = client.get(f"/api/v1/workflows/{wf['id']}/versions/{version['id']}").json()
    node_keys = [action["node_key"] for action in graph["actions"]]
    report.check(
        "workflow version is published",
        version["status"] == "PUBLISHED",
        f"v{version['version']} · {version['status']}",
    )
    report.check(
        "required steps are present",
        node_keys
        == [
            "retrieve_invoice",
            "retrieve_purchase_order",
            "validate_fields",
            "compare_totals",
            "approve_invoice",
            "schedule_payment",
            "disburse",
            "record_result",
        ],
        " → ".join(node_keys),
    )

    # ------------------------------------------------------ 2. normal execution
    seeded_normal = next(
        run
        for run in client.get("/api/v1/runs", params={"workflow_id": wf["id"]}).json()
        if run["verdict"] == "VERIFIED" and run["trigger"] == "DEMO"
    )
    normal = client.get(f"/api/v1/runs/{seeded_normal['id']}").json()
    seqs = [event["seq"] for event in normal["events"]]
    node_states = {item["node_key"]: item["status"] for item in normal["node_states"]}
    final_state = normal["final_state"]
    report.check(
        "normal execution verified",
        normal["status"] == "SUCCEEDED" and normal["verdict"] == "VERIFIED",
        f"{normal['status']}/{normal['verdict']}",
    )
    report.check(
        "timeline is ordered and contiguous",
        seqs == list(range(len(seqs))),
        f"{len(seqs)} events, seq 0..{seqs[-1] if seqs else '—'}",
    )
    report.check(
        "validation and result nodes passed",
        node_states.get("validate_fields") == "PASSED"
        and node_states.get("record_result") == "PASSED",
        "validate_fields="
        f"{node_states.get('validate_fields')} "
        f"record_result={node_states.get('record_result')}",
    )
    report.check(
        "final state is recorded",
        final_state.get("invoice", {}).get("status") == "PAID"
        and final_state.get("review", {}).get("final_result") == "PAID"
        and final_state.get("invoice", {}).get("ledger_count") == 1,
        "invoice.status=PAID, final_result=PAID, ledger_count=1",
    )
    report.check(
        "run inputs and outputs are recorded",
        isinstance(normal.get("inputs"), dict)
        and isinstance(normal.get("outputs"), dict)
        and bool(normal["outputs"]),
        f"{len(normal.get('outputs') or {})} outputs",
    )

    # ------------------------------------------------------- 3. PO API timeout
    po_run = run_scenario(client, scenario(client, wf["id"], "TIMEOUT_BEFORE_EXECUTION")["id"])
    report.check(
        "PO timeout fails at the retrieval node",
        po_run["status"] == "FAILED" and po_run["failure_ordinal"] == 1,
        f"{po_run['status']} · {po_run['failure_code']}",
    )
    plan = client.get(f"/api/v1/runs/{po_run['id']}/recovery-plan").json()
    report.check(
        "PO timeout is recoverable from an explicit policy",
        plan["supported"] and plan["strategy"] == "RESUME" and plan["reversible"],
        f"{plan['strategy']} · idempotent on {plan['idempotency_path']}",
    )
    recovered = client.post(
        f"/api/v1/runs/{po_run['id']}/recover", json={"strategy": "RESUME"}
    ).json()
    resumed = client.get(f"/api/v1/runs/{po_run['id']}").json()
    report.check(
        "recovery resumes and verifies",
        recovered["status"] == "VERIFIED" and resumed["verdict"] == "VERIFIED",
        f"attempt={recovered['status']} run={resumed['status']}/{resumed['verdict']}",
    )

    # --------------------------------------------------- 4. total mismatch
    mismatch = run_scenario(client, scenario(client, wf["id"], "CONTRADICTORY_TOTALS")["id"])
    report.check(
        "mismatch blocks before approval",
        mismatch["status"] == "AWAITING_DECISION",
        f"{mismatch['status']} at ordinal {mismatch['current_ordinal']}",
    )
    decision = open_decision(client, mismatch["id"])
    validation = {item["label"]: item["validation"] for item in decision["evidence_items"]}
    report.check(
        "blocker is a genuine discrepancy with evidence",
        decision["kind"] == "CONFLICTING_EVIDENCE"
        and validation.get("Invoice total (cents)") == "FAIL"
        and validation.get("Purchase order total (cents)") == "PASS",
        f"{decision['kind']} · finance required · {len(decision['evidence_items'])} evidence rows",
    )
    refused = client.post(
        f"/api/v1/decisions/{decision['id']}/approve",
        json={},
        headers=role_headers("OPERATOR"),
    )
    report.check(
        "the wrong role cannot authorize",
        refused.status_code == 403,
        f"{refused.status_code} {refused.json().get('code')}",
    )
    approved = client.post(
        f"/api/v1/decisions/{decision['id']}/approve",
        json={"note": "variance accepted"},
        headers=role_headers("FINANCE_APPROVER"),
    )
    approved_body = approved.json()
    after_approval = client.get(f"/api/v1/runs/{mismatch['id']}").json()
    report.check(
        "authorized decision resumes to a verified result",
        approved.status_code == 200
        and approved_body["status"] == "APPLIED"
        and after_approval["verdict"] == "VERIFIED",
        f"decision={approved_body['status']} run={after_approval['verdict']}",
    )

    # ------------------------------------------------ 5. lost response
    lost = run_scenario(client, scenario(client, wf["id"], "RESPONSE_LOST")["id"])
    lost_events = run_events(client, lost["id"])
    lost_types = [event["type"] for event in lost_events]
    disbursements = [
        event
        for event in lost_events
        if event["type"] == "ACTION_STARTED" and event["action_ordinal"] == 6
    ]
    report.check(
        "lost response is reconciled without a second payment",
        lost["verdict"] == "VERIFIED"
        and "NODE_RECONCILED" in lost_types
        and len(disbursements) == 1,
        f"reconciled · {len(disbursements)} disbursement attempt(s)",
    )

    # ------------------------------------------------ 6. missing approval
    late = run_scenario(client, scenario(client, wf["id"], "DELAYED_APPROVAL")["id"])
    late_decision = open_decision(client, late["id"])
    expired = client.post(
        f"/api/v1/decisions/{late_decision['id']}/approve",
        json={},
        headers=role_headers("FINANCE_APPROVER"),
    )
    late_run = client.get(f"/api/v1/runs/{late['id']}").json()
    report.check(
        "missing approval is refused and fails safely",
        expired.status_code == 409
        and expired.json().get("code") == "DECISION_EXPIRED"
        and late_run["status"] == "FAILED"
        and late_run["verdict"] != "VERIFIED",
        f"{expired.json().get('code')} · run {late_run['status']}",
    )

    # ------------------------------------------- 7. partial commit + recovery
    partial = run_scenario(client, scenario(client, wf["id"], "PARTIAL_COMMIT")["id"])
    report.check(
        "partial commit is detected by invariants",
        partial["status"] == "FAILED" and partial["failure_code"] == "INVARIANT_VIOLATED",
        f"{partial['status']} · {partial['failure_code']}",
    )
    compensation = client.post(
        f"/api/v1/runs/{partial['id']}/recover", json={"strategy": "COMPENSATE"}
    ).json()
    contract = compensation["verification"]["checks"]
    report.check(
        "compensation restores the contract",
        compensation["status"] == "VERIFIED"
        and all(item["ok"] for item in contract)
        and compensation["after_state_hash"] != compensation["before_state_hash"],
        f"{compensation['status']} · {len(contract)} contract checks",
    )

    # ---------------------------------------------------- 8. regression test
    regression = client.post(
        "/api/v1/regression-tests",
        json={"run_id": partial["id"], "name": "Invoice slice: partial ledger commit"},
    ).json()
    report.check(
        "regression captures the scenario and expectation",
        regression["status"] == "DRAFT"
        and regression["spec"]["template_version"] == 1
        and regression["spec"]["scenario"]["fault_type"] == "PARTIAL_COMMIT"
        and regression["spec"]["expected"]["invariant_name"],
        f"{regression['spec']['expected']['invariant_name']} · seed {regression['spec']['seed']}",
    )
    reviewed = client.patch(
        f"/api/v1/regression-tests/{regression['id']}",
        json={"notes": "Created by the end-to-end acceptance run."},
    )
    activated = client.post(f"/api/v1/regression-tests/{regression['id']}/activate")
    replay = client.post(f"/api/v1/regression-tests/{regression['id']}/replay").json()
    results = client.get(f"/api/v1/regression-tests/{regression['id']}/results").json()
    report.check(
        "regression is reviewable, activatable and reproducible",
        reviewed.status_code == 200
        and activated.json()["status"] == "ACTIVE"
        and replay["last_result"] in {"PASSED", "FAILED"}
        and results
        and results[-1]["result"] == replay["last_result"],
        f"replay={replay['last_result']} · stored results={len(results)}",
    )

    # ------------------------------------------------------------- 9. audit
    audit_runs = [
        run["id"] for run in client.get("/api/v1/runs", params={"workflow_id": wf["id"]}).json()
    ]
    chains = [client.get(f"/api/v1/runs/{run_id}/events/verify").json() for run_id in audit_runs]
    report.check(
        "every run chain verifies",
        chains and all(item["valid"] for item in chains),
        f"{sum(1 for item in chains if item['valid'])}/{len(chains)} chains valid",
    )

    report.finish()
