"""CLI boundary for Alan sessions that do not have the MCP connector.

Commands print a single JSON envelope to stdout. Consequential commands return a
confirmation token first and exit 2; repeat them with --confirm <token> after the
user explicitly approves. The token comes from an environment variable, never a
command-line argument.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from flowstate.alan.client import AlanConfig, FlowstateClient
from flowstate.alan.service import AlanOperations

CONFIRMATION_CODES = frozenset(
    {
        "CONFIRMATION_REQUIRED",
        "MISSING_INPUTS",
        "WORKFLOW_REQUIRED",
        "WORKFLOW_AMBIGUOUS",
        "CHOOSE_SCENARIO",
        "SCENARIO_AMBIGUOUS",
        "SCENARIO_NOT_FOUND",
    }
)


def _json_object(value: str, label: str) -> dict[str, Any]:
    if not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{label} is not valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f"{label} must be a JSON object.")
    return parsed


def _emit(result: dict[str, Any], *, compact: bool) -> None:
    if compact:
        print(json.dumps(result, separators=(",", ":"), default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


def _exit_code(result: dict[str, Any]) -> int:
    if result.get("ok"):
        return 0
    if result.get("code") in CONFIRMATION_CODES:
        return 2
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flowstate-alan",
        description="Typed FLOWSTATE operations for Alan agents.",
    )
    parser.add_argument("--base-url", default=None, help="Defaults to FLOWSTATE_API_URL.")
    parser.add_argument("--compact", action="store_true", help="Emit single-line JSON.")
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Refuse all mutation operations in this invocation.",
    )
    parser.add_argument(
        "--allowed-workflows",
        default=None,
        help="Comma-separated workflow ids/slugs/names this process may resolve.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    lw = sub.add_parser("list-workflows", help="List workflows.")
    lw.add_argument("--query", default="")
    lw.add_argument("--origin", default=None, choices=["DEMO", "LIVE"])

    iw = sub.add_parser("inspect-workflow", help="Inspect a workflow and its inputs.")
    iw.add_argument("workflow", nargs="?")
    iw.add_argument("--version-id", default=None)

    rw = sub.add_parser("run-workflow", help="Run a published workflow.")
    rw.add_argument("workflow", nargs="?")
    rw.add_argument("--inputs", default="{}")
    rw.add_argument("--version-id", default=None)
    rw.add_argument("--confirm", default=None, help="confirmation_token from the preview call")
    rw.add_argument("--idempotency-key", default=None)

    st = sub.add_parser("get-execution-status", aliases=["execution-status"], help="Read a run.")
    st.add_argument("run_id")

    rc = sub.add_parser("run-chaos-test", help="Evaluate a chaos scenario.")
    rc.add_argument("workflow", nargs="?")
    rc.add_argument("--scenario", default=None)
    rc.add_argument("--fault-type", default=None)
    rc.add_argument("--confirm", default=None)
    rc.add_argument("--idempotency-key", default=None)

    ef = sub.add_parser("explain-failure", help="Explain a non-success run.")
    ef.add_argument("run_id")

    lb = sub.add_parser("list-blockers", help="List blockers.")
    lb.add_argument("--run-id", default=None)
    lb.add_argument("--all", action="store_true", help="Include resolved blockers.")

    ie = sub.add_parser("inspect-evidence", help="List or verify evidence.")
    ie.add_argument("run_id")
    ie.add_argument("--evidence-id", default=None)

    rr = sub.add_parser("request-recovery", help="Request recovery for a run.")
    rr.add_argument("run_id")
    rr.add_argument("--strategy", default="RESUME", choices=["RESUME", "COMPENSATE", "MANUAL"])
    rr.add_argument("--confirm", default=None)
    rr.add_argument("--idempotency-key", default=None)

    lr = sub.add_parser("list-regression-tests", help="List regression tests.")
    lr.add_argument("--workflow", default=None)

    run_r = sub.add_parser("run-regression-tests", help="Replay regression tests.")
    run_r.add_argument("--test-id", action="append", default=None)
    run_r.add_argument("--confirm", default=None)
    run_r.add_argument("--idempotency-key", default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = AlanConfig()
    if args.base_url:
        config.base_url = args.base_url
    allowed = None
    if args.allowed_workflows is not None:
        allowed = {
            item.strip().casefold() for item in args.allowed_workflows.split(",") if item.strip()
        }
    client = FlowstateClient(config)
    ops = AlanOperations(
        client,
        read_only=args.read_only,
        allowed_workflows=allowed,
    )
    try:
        if args.command == "list-workflows":
            result = ops.list_workflows(query=args.query, origin=args.origin)
        elif args.command == "inspect-workflow":
            result = ops.inspect_workflow(workflow=args.workflow, version_id=args.version_id)
        elif args.command == "run-workflow":
            result = ops.run_workflow(
                workflow=args.workflow,
                inputs=_json_object(args.inputs, "--inputs"),
                version_id=args.version_id,
                confirmation_token=args.confirm,
                idempotency_key=args.idempotency_key,
            )
        elif args.command in {"get-execution-status", "execution-status"}:
            result = ops.get_execution_status(run_id=args.run_id)
        elif args.command == "run-chaos-test":
            result = ops.run_chaos_test(
                workflow=args.workflow,
                scenario=args.scenario,
                fault_type=args.fault_type,
                confirmation_token=args.confirm,
                idempotency_key=args.idempotency_key,
            )
        elif args.command == "explain-failure":
            result = ops.explain_failure(run_id=args.run_id)
        elif args.command == "list-blockers":
            result = ops.list_blockers(run_id=args.run_id, open_only=not args.all)
        elif args.command == "inspect-evidence":
            result = ops.inspect_evidence(run_id=args.run_id, evidence_id=args.evidence_id)
        elif args.command == "request-recovery":
            result = ops.request_recovery(
                run_id=args.run_id,
                strategy=args.strategy,
                confirmation_token=args.confirm,
                idempotency_key=args.idempotency_key,
            )
        elif args.command == "list-regression-tests":
            result = ops.list_regression_tests(workflow=args.workflow)
        elif args.command == "run-regression-tests":
            result = ops.run_regression_tests(
                test_ids=args.test_id,
                confirmation_token=args.confirm,
                idempotency_key=args.idempotency_key,
            )
        else:  # pragma: no cover - argparse enforces the choices
            result = {"ok": False, "code": "UNKNOWN_COMMAND", "message": args.command}
    finally:
        client.close()
    _emit(result, compact=args.compact)
    return _exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
