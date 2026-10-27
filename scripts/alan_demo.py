#!/usr/bin/env python3
"""End-to-end demo of the typed FLOWSTATE adapter.

Requires a running FLOWSTATE backend and FLOWSTATE_API_TOKEN when the backend
requires auth. The script never approves a human decision and never claims
success without a verified backend result.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from flowstate.alan.client import FlowstateClient
from flowstate.alan.service import AlanOperations


def show(title: str, value: Any) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(value, indent=2, default=str))


def confirmed(ops: AlanOperations, operation: str, **kwargs: Any) -> dict[str, Any]:
    preview = getattr(ops, operation)(**kwargs)
    if preview.get("ok"):
        return preview
    if preview.get("code") != "CONFIRMATION_REQUIRED":
        return preview
    token = (preview.get("details") or {}).get("confirmation_token")
    if not token:
        return preview
    print(f"\n[confirmation required] {preview.get('details', {}).get('summary')}")
    return getattr(ops, operation)(**kwargs, confirmation_token=token)


def main() -> int:
    client = FlowstateClient()
    ops = AlanOperations(client)
    try:
        listed = ops.list_workflows()
        show("workflows", listed.get("data", {}).get("workflows", []))

        inspected = ops.inspect_workflow("invoice-approval")
        if not inspected.get("ok"):
            show("inspection failed", inspected)
            return 1
        show(
            "inspect invoice-approval",
            {
                "driver": inspected["data"]["driver"],
                "execution_kind": inspected["data"]["execution_kind"],
                "required_inputs": inspected["data"]["version"]["required_inputs"],
                "actions": len(inspected["data"]["actions"]),
            },
        )

        run = confirmed(ops, "run_workflow", workflow="invoice-approval")
        if not run.get("ok"):
            show("run failed", run)
            return 1
        show(
            "requested execution",
            {
                "run_id": run["data"]["run_id"],
                "status": run["data"]["status"],
                "verdict": run["data"]["verdict"],
                "driver": run["data"]["driver"],
                "is_success": run["data"]["is_success"],
                "blockers": len(run["data"].get("blockers") or []),
            },
        )

        explanation = ops.explain_failure(run["data"]["run_id"])
        show(
            "why it is blocked",
            {
                "explanation": explanation["data"]["explanation"]
                if explanation.get("ok")
                else explanation.get("message"),
                "next_steps": explanation["data"].get("next_steps")
                if explanation.get("ok")
                else None,
            },
        )

        report = confirmed(
            ops,
            "run_chaos_test",
            workflow="invoice-approval",
            scenario="Connection drop before send",
        )
        if not report.get("ok"):
            show("chaos evaluation failed", report)
            return 1
        show(
            "sandboxed stress test",
            {
                "scenario": report["data"]["scenario"],
                "evaluation": report["data"]["evaluation"],
                "baseline": report["data"]["baseline"],
                "fault": report["data"]["fault"],
                "failed_safely": report["data"]["failed_safely"],
            },
        )

        fault_run = report["data"]["fault"]["run_id"]
        fault_explanation = ops.explain_failure(fault_run)
        show(
            "why the stress test stopped",
            {
                "explanation": fault_explanation["data"]["explanation"]
                if fault_explanation.get("ok")
                else fault_explanation.get("message"),
                "next_steps": fault_explanation["data"].get("next_steps")
                if fault_explanation.get("ok")
                else None,
            },
        )

        recovery = confirmed(
            ops, "request_recovery", run_id=fault_run, strategy="RESUME"
        )
        if not recovery.get("ok"):
            show("recovery failed", recovery)
            return 1
        show(
            "recovery",
            {
                "attempt_status": recovery["data"]["attempt"]["status"],
                "verified": recovery["data"]["verified"],
                "run_status": recovery["data"]["run"]["status"],
                "run_verdict": recovery["data"]["run"]["verdict"],
            },
        )
        if not recovery["data"]["verified"]:
            print("\nRecovery did not verify. Do not report success.")
            return 1

        tests = ops.list_regression_tests()
        show(
            "regression tests",
            [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "status": item["status"],
                    "last_result": item["last_result"],
                }
                for item in tests.get("data", {}).get("tests", [])
            ],
        )
        test_ids = [
            item["id"]
            for item in tests.get("data", {}).get("tests", [])
            if item["status"] not in {"DRAFT", "QUARANTINED"}
        ][:3]
        if test_ids:
            replayed = confirmed(ops, "run_regression_tests", test_ids=test_ids)
            show("regression replay", replayed.get("data", {}).get("results", replayed))

        print(
            "\nDemo complete. Success is only claimed where the backend returned VERIFIED."
        )
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
