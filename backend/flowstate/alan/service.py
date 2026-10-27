"""Typed FLOWSTATE operations exposed to Alan.

The adapter resolves human references, validates declared inputs, requires an
explicit confirmation token before mutating a run, and derives every success
claim from persisted backend status and verdicts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from functools import wraps
from typing import Any

from flowstate.alan.client import AdapterError, FlowstateClient

logger = logging.getLogger(__name__)

MUTATING_OPERATIONS = frozenset(
    {"run_workflow", "run_chaos_test", "request_recovery", "run_regression_tests"}
)
RECOVERABLE = frozenset({"AWAITING_RECOVERY", "FAILED", "TIMED_OUT"})


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def confirmation_token(action: str, payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(f"{action}|{_canonical(payload)}".encode()).hexdigest()[:32]


def _auto_key(action: str, payload: Mapping[str, Any], token: str) -> str:
    digest = hashlib.sha256(f"alan|{action}|{_canonical(payload)}|{token}".encode()).hexdigest()
    return f"alan-{digest[:56]}"


def _ok(message: str, data: Mapping[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": True, "code": "OK", "message": message}
    if data is not None:
        result["data"] = dict(data)
    result.update(extra)
    return result


def _fail(
    code: str,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
    status: int = 0,
    request_id: str | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    if status:
        result["backend_status"] = status
    if request_id:
        result["request_id"] = request_id
    if details:
        result["details"] = dict(details)
    return result


def _error(exc: AdapterError) -> dict[str, Any]:
    return _fail(
        exc.code,
        exc.detail,
        details=exc.details,
        status=exc.status,
        request_id=exc.request_id,
        retryable=exc.retryable,
    )


def _guarded(func: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    @wraps(func)
    def wrapper(self: AlanOperations, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return func(self, *args, **kwargs)
        except AdapterError as exc:
            return _error(exc)
        except Exception as exc:
            logger.error("alan adapter operation failed: %s", type(exc).__name__)
            return _fail(
                "INTERNAL_ERROR",
                "The adapter could not complete this operation. No success is claimed.",
            )

    return wrapper


class AlanOperations:
    def __init__(
        self,
        client: FlowstateClient,
        *,
        read_only: bool | None = None,
        allowed_workflows: set[str] | None = None,
    ) -> None:
        self.client = client
        self.read_only = _env_flag("FLOWSTATE_ALAN_READ_ONLY") if read_only is None else read_only
        raw_allowed = os.getenv("FLOWSTATE_ALAN_ALLOWED_WORKFLOWS", "")
        self.allowed_workflows = (
            {item.strip().casefold() for item in raw_allowed.split(",") if item.strip()}
            if allowed_workflows is None
            else {item.casefold() for item in allowed_workflows}
        )

    # ---------------------------------------------------------------- helpers

    def _all_workflows(self) -> list[dict[str, Any]]:
        rows = self.client.get("/workflows", params={"limit": 200})
        if not isinstance(rows, list):
            raise AdapterError("MALFORMED_BACKEND_RESPONSE", "The workflow list was not an array.")
        return [item for item in rows if isinstance(item, dict)]

    def _ensure_allowed(self, workflow: Mapping[str, Any]) -> None:
        if not self.allowed_workflows:
            return
        names = {
            str(workflow.get("id", "")).casefold(),
            str(workflow.get("slug", "")).casefold(),
            str(workflow.get("name", "")).casefold(),
        }
        if not (names & self.allowed_workflows):
            raise AdapterError(
                "WORKFLOW_NOT_ALLOWED",
                "This workflow is not in FLOWSTATE_ALAN_ALLOWED_WORKFLOWS.",
                details={"workflow": workflow.get("name"), "slug": workflow.get("slug")},
            )

    def _resolve_workflow(self, reference: str | None) -> dict[str, Any]:
        rows = self._all_workflows()
        ref = (reference or "").strip()
        if not ref:
            if len(rows) == 1:
                self._ensure_allowed(rows[0])
                return rows[0]
            raise AdapterError(
                "WORKFLOW_REQUIRED",
                "Which workflow do you mean?",
                details={"candidates": _workflow_candidates(rows)},
            )

        lowered = ref.casefold()
        exact = [
            item
            for item in rows
            if lowered
            in {
                str(item.get("id", "")).casefold(),
                str(item.get("slug", "")).casefold(),
                str(item.get("name", "")).casefold(),
            }
        ]
        matches = exact or [
            item
            for item in rows
            if lowered
            in (
                f"{item.get('name', '')} {item.get('slug', '')} {item.get('description', '')}"
            ).casefold()
        ]
        if not matches:
            raise AdapterError(
                "WORKFLOW_NOT_FOUND",
                f"No workflow matches {ref!r}.",
                details={"available": _workflow_candidates(rows)},
            )
        if len(matches) > 1:
            raise AdapterError(
                "WORKFLOW_AMBIGUOUS",
                f"{len(matches)} workflows match {ref!r}; choose one.",
                details={"candidates": _workflow_candidates(matches)},
            )
        workflow = matches[0]
        self._ensure_allowed(workflow)
        return workflow

    def _version(
        self, workflow: Mapping[str, Any], version_id: str | None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        detail = self.client.get(f"/workflows/{workflow['id']}")
        if not isinstance(detail, dict):
            raise AdapterError("MALFORMED_BACKEND_RESPONSE", "Workflow detail was not an object.")
        versions = detail.get("versions") or []
        chosen_id = version_id or detail.get("current_version_id")
        if not chosen_id and versions:
            chosen_id = versions[0].get("id")
        if not chosen_id:
            raise AdapterError("NO_PUBLISHED_VERSION", "This workflow has no version to execute.")
        if not any(str(item.get("id")) == str(chosen_id) for item in versions):
            raise AdapterError(
                "VERSION_NOT_FOUND",
                "That version does not belong to this workflow.",
                details={"version_id": chosen_id},
            )
        version = self.client.get(f"/workflows/{workflow['id']}/versions/{chosen_id}")
        if not isinstance(version, dict):
            raise AdapterError("MALFORMED_BACKEND_RESPONSE", "Version detail was not an object.")
        return detail, version

    def _run_summary(self, run: Mapping[str, Any]) -> dict[str, Any]:
        driver = str(run.get("driver") or "")
        is_success = run.get("status") == "SUCCEEDED" and run.get("verdict") == "VERIFIED"
        return {
            "run_id": run.get("id"),
            "workflow_id": run.get("workflow_id"),
            "workflow_name": run.get("workflow_name"),
            "version": run.get("version"),
            "status": run.get("status"),
            "verdict": run.get("verdict"),
            "driver": driver,
            "execution_kind": _execution_kind(driver),
            "failure_code": run.get("failure_code"),
            "failure_message": run.get("failure_message"),
            "failure_ordinal": run.get("failure_ordinal"),
            "current_ordinal": run.get("current_ordinal"),
            "started_at": run.get("started_at"),
            "ended_at": run.get("ended_at"),
            "is_success": is_success,
            "simulated": driver == "simulated-v1",
            "inputs": run.get("inputs") or {},
            "outputs": run.get("outputs") or {},
        }

    def _require_mutation(self, action: str) -> dict[str, Any] | None:
        if self.read_only:
            return _fail(
                "READ_ONLY_MODE",
                f"{action} is disabled by FLOWSTATE_ALAN_READ_ONLY.",
            )
        return None

    def _require_confirmation(
        self,
        action: str,
        payload: Mapping[str, Any],
        token: str | None,
        summary: str,
    ) -> dict[str, Any] | None:
        expected = confirmation_token(action, payload)
        if token and token.strip() == expected:
            return None
        return _fail(
            "CONFIRMATION_REQUIRED",
            f"Confirm this consequential action before it runs: {summary}",
            details={
                "action": action,
                "summary": summary,
                "confirmation_token": expected,
                "instruction": (
                    "Show the user the summary. Only after explicit approval, call the same "
                    "operation again with this exact confirmation_token."
                ),
            },
        )

    def _blockers(self, run_id: str) -> list[dict[str, Any]]:
        try:
            rows = self.client.get(f"/runs/{run_id}/blockers")
        except AdapterError as exc:
            if exc.status == 404:
                return []
            raise
        return [item for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []

    # -------------------------------------------------------------- read-only

    @_guarded
    def list_workflows(self, query: str = "", origin: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": 200}
        if origin:
            params["origin"] = origin.upper()
        rows = self.client.get("/workflows", params=params)
        if not isinstance(rows, list):
            raise AdapterError("MALFORMED_BACKEND_RESPONSE", "Workflow list was not an array.")
        if query.strip():
            term = query.strip().casefold()
            rows = [
                item
                for item in rows
                if term
                in (
                    f"{item.get('name', '')} {item.get('slug', '')} {item.get('description', '')}"
                ).casefold()
            ]
        return _ok(
            f"{len(rows)} workflow(s).",
            {"workflows": [_workflow_candidates([item])[0] for item in rows]},
        )

    @_guarded
    def inspect_workflow(
        self, workflow: str | None = None, version_id: str | None = None
    ) -> dict[str, Any]:
        resolved = self._resolve_workflow(workflow)
        detail, version = self._version(resolved, version_id)
        schema = version.get("input_schema") or {}
        required = list(schema.get("required") or [])
        actions = [
            {
                "ordinal": item.get("ordinal"),
                "node_key": item.get("node_key"),
                "node_type": item.get("node_type"),
                "kind": item.get("kind"),
                "label": item.get("label"),
                "channel": item.get("channel"),
                "side_effect_class": item.get("side_effect_class"),
                "requires_authorization": item.get("requires_authorization"),
                "timeout_ms": item.get("timeout_ms"),
            }
            for item in version.get("actions") or []
        ]
        browser = any(item.get("channel") == "BROWSER" for item in actions)
        runs = self.client.get("/runs", params={"workflow_id": resolved["id"], "limit": 5})
        return _ok(
            f"Inspected {resolved.get('name')} v{version.get('version')}.",
            {
                "workflow": _workflow_candidates([resolved])[0],
                "version": {
                    "id": version.get("id"),
                    "version": version.get("version"),
                    "status": version.get("status"),
                    "label": version.get("label"),
                    "input_schema": schema,
                    "output_schema": version.get("output_schema") or {},
                    "required_inputs": required,
                },
                "executable": version.get("status") == "PUBLISHED",
                "driver": "playwright-v1" if browser else "simulated-v1",
                "execution_kind": "browser" if browser else "simulated",
                "actions": actions,
                "invariants": [
                    {
                        "name": item.get("name"),
                        "kind": item.get("kind"),
                        "severity": item.get("severity"),
                        "expression": item.get("expression"),
                    }
                    for item in version.get("invariants") or []
                ],
                "recent_runs": [
                    {
                        "run_id": item.get("id"),
                        "status": item.get("status"),
                        "verdict": item.get("verdict"),
                        "driver": item.get("driver"),
                    }
                    for item in (runs if isinstance(runs, list) else [])
                ],
                "versions": detail.get("versions") or [],
            },
        )

    @_guarded
    def get_execution_status(self, run_id: str) -> dict[str, Any]:
        run = self.client.get(f"/runs/{run_id}")
        if not isinstance(run, dict):
            raise AdapterError("MALFORMED_BACKEND_RESPONSE", "Run detail was not an object.")
        summary = self._run_summary(run)
        blockers = self._blockers(run_id)
        try:
            recoveries = self.client.get(f"/runs/{run_id}/recoveries")
        except AdapterError as exc:
            if exc.status != 404:
                raise
            recoveries = []
        try:
            recovery_plan = self.client.get(f"/runs/{run_id}/recovery-plan")
        except AdapterError as exc:
            if exc.status != 404:
                raise
            recovery_plan = None
        verdicts = [
            {
                "name": item.get("invariant_name"),
                "verdict": item.get("verdict"),
                "severity": item.get("severity"),
                "detail": item.get("detail"),
            }
            for item in run.get("verdicts") or []
        ]
        nodes = [
            {
                "node_key": item.get("node_key"),
                "ordinal": item.get("ordinal"),
                "label": item.get("label"),
                "status": item.get("status"),
                "attempt": item.get("attempt"),
                "error_class": item.get("error_class"),
                "error_code": item.get("error_code"),
                "error_message": item.get("error_message"),
                "duration_ms": item.get("duration_ms"),
            }
            for item in run.get("node_states") or []
            if item.get("status") not in {"PENDING", "PASSED", "SKIPPED"}
        ]
        return _ok(
            _status_sentence(summary),
            {
                **summary,
                "chain_valid": run.get("chain_valid"),
                "event_count": len(run.get("events") or []),
                "blockers": blockers,
                "recoveries": recoveries,
                "recovery_plan": recovery_plan,
                "verdicts": verdicts,
                "failed_nodes": nodes,
                "evidence": run.get("evidence") or [],
                "never_claim_success": True,
            },
        )

    @_guarded
    def explain_failure(self, run_id: str) -> dict[str, Any]:
        status = self.get_execution_status(run_id)
        if not status.get("ok"):
            return status
        data = status["data"]
        if data["is_success"]:
            return _ok(
                "The backend reports SUCCEEDED and VERIFIED. This is not a failure.",
                {"status": data, "explanation": "Backend verified the terminal state."},
            )
        blockers = data.get("blockers") or []
        violated = [item for item in data.get("verdicts") or [] if item["verdict"] == "VIOLATED"]
        inconclusive = [
            item for item in data.get("verdicts") or [] if item["verdict"] == "INCONCLUSIVE"
        ]
        failed_nodes = data.get("failed_nodes") or []
        reasons: list[str] = []
        if data.get("failure_code"):
            reasons.append(f"{data['failure_code']}: {data.get('failure_message') or ''}".strip())
        if violated:
            reasons.append(
                "First violated invariant: " + f"{violated[0]['name']} — {violated[0]['detail']}"
            )
        if inconclusive:
            reasons.append(
                "Inconclusive invariant (never a pass): "
                + f"{inconclusive[0]['name']} — {inconclusive[0]['detail']}"
            )
        if blockers:
            reasons.append(
                "Blocked on a human decision: "
                + f"{blockers[0].get('title')} (required role {blockers[0].get('required_role')})"
            )
        if not reasons:
            reasons.append(f"Run is {data['status']} with verdict {data['verdict']}.")

        next_steps: list[str] = []
        if data["status"] == "AWAITING_DECISION":
            next_steps.append(
                "A human with the required role must decide in the Decision Inbox. "
                "This adapter cannot approve decisions."
            )
        elif data["status"] == "AWAITING_RECOVERY":
            plan = data.get("recovery_plan") or {}
            if plan.get("supported"):
                next_steps.append(
                    "Request recovery with strategy "
                    + str(plan.get("strategy") or "RESUME")
                    + " after explicit confirmation."
                )
            else:
                next_steps.append(
                    "Manual intervention is required: "
                    + str(plan.get("reason") or "no supported recovery plan")
                )
        elif data["status"] in {"CANCELLED", "TIMED_OUT"}:
            next_steps.append("Start a new run only after reviewing the failure.")
        else:
            next_steps.append(
                "Do not rerun blindly. Inspect the failed action and save or replay a "
                "regression test once the cause is understood."
            )

        return _ok(
            " | ".join(reasons),
            {
                "status": data,
                "explanation": reasons,
                "first_violated_invariant": violated[0] if violated else None,
                "failed_nodes": failed_nodes,
                "blockers": blockers,
                "recovery": {
                    "attempts": data.get("recoveries") or [],
                    "plan": data.get("recovery_plan"),
                },
                "evidence": data.get("evidence") or [],
                "next_steps": next_steps,
                "never_claim_success": True,
            },
        )

    @_guarded
    def list_blockers(self, run_id: str | None = None, open_only: bool = True) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": 200}
        if run_id:
            params["run_id"] = run_id
        if open_only:
            params["status"] = "OPEN"
        rows = self.client.get("/blockers", params=params)
        if not isinstance(rows, list):
            raise AdapterError("MALFORMED_BACKEND_RESPONSE", "Blocker list was not an array.")
        blockers = [
            {
                "id": item.get("id"),
                "run_id": item.get("run_id"),
                "action_ordinal": item.get("action_ordinal"),
                "node_key": item.get("node_key"),
                "kind": item.get("kind"),
                "code": item.get("code"),
                "title": item.get("title"),
                "detail": item.get("detail"),
                "status": item.get("status"),
                "required_role": item.get("required_role"),
                "decision_id": item.get("decision_id"),
                "input_spec": item.get("input_spec") or [],
                "resume_conditions": item.get("resume_conditions") or [],
                "evidence_hash": item.get("evidence_hash"),
                "evidence_count": len(item.get("evidence") or []),
            }
            for item in rows
        ]
        return _ok(f"{len(blockers)} blocker(s).", {"blockers": blockers})

    @_guarded
    def inspect_evidence(self, run_id: str, evidence_id: str | None = None) -> dict[str, Any]:
        run = self.client.get(f"/runs/{run_id}")
        if not isinstance(run, dict):
            raise AdapterError("MALFORMED_BACKEND_RESPONSE", "Run detail was not an object.")
        evidence = [item for item in run.get("evidence") or [] if isinstance(item, dict)]
        if evidence_id:
            match = next((item for item in evidence if item.get("id") == evidence_id), None)
            if match is None:
                raise AdapterError(
                    "EVIDENCE_NOT_FOUND",
                    "That evidence record does not belong to this run.",
                    status=404,
                )
            content = self.client.get_bytes(f"/runs/{run_id}/evidence/{evidence_id}")
            digest = hashlib.sha256(content).hexdigest()
            result: dict[str, Any] = {
                "run_id": run_id,
                "evidence": {
                    "id": match.get("id"),
                    "kind": match.get("kind"),
                    "mime_type": match.get("mime_type"),
                    "sha256": match.get("sha256"),
                    "captured_at": match.get("captured_at"),
                    "bytes": len(content),
                    "hash_matches": digest == match.get("sha256"),
                },
                "content_excerpt": None,
            }
            if (
                str(match.get("mime_type", "")).startswith("application/json")
                and len(content) <= 131_072
            ):
                try:
                    result["content_excerpt"] = json.loads(content.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    result["content_excerpt"] = None
            return _ok(
                "Evidence hash verified against the persisted record."
                if result["evidence"]["hash_matches"]
                else "Evidence hash did not match; treat this evidence as tampered.",
                result,
            )
        if not evidence:
            raise AdapterError(
                "NO_EVIDENCE", "This run has no persisted evidence records.", status=404
            )
        return _ok(
            f"{len(evidence)} evidence record(s).",
            {
                "run_id": run_id,
                "evidence": [
                    {
                        "id": item.get("id"),
                        "kind": item.get("kind"),
                        "mime_type": item.get("mime_type"),
                        "sha256": item.get("sha256"),
                        "captured_at": item.get("captured_at"),
                        "verified": item.get("verified"),
                    }
                    for item in evidence
                ],
            },
        )

    @_guarded
    def list_regression_tests(self, workflow: str | None = None) -> dict[str, Any]:
        rows = self.client.get("/regression-tests", params={"limit": 200})
        if not isinstance(rows, list):
            raise AdapterError("MALFORMED_BACKEND_RESPONSE", "Regression list was not an array.")
        if workflow:
            resolved = self._resolve_workflow(workflow)
            rows = [
                item
                for item in rows
                if (item.get("spec") or {}).get("workflow_id") == resolved.get("id")
            ]
        tests = [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "status": item.get("status"),
                "source_run_id": item.get("source_run_id"),
                "last_result": item.get("last_result"),
                "last_run_at": item.get("last_run_at"),
                "workflow_id": (item.get("spec") or {}).get("workflow_id"),
                "workflow_name": (item.get("spec") or {}).get("workflow_name"),
                "expected": (item.get("spec") or {}).get("expected"),
                "scenario": (item.get("spec") or {}).get("scenario"),
            }
            for item in rows
        ]
        return _ok(f"{len(tests)} regression test(s).", {"tests": tests})

    # ------------------------------------------------------------- mutations

    @_guarded
    def run_workflow(
        self,
        workflow: str | None = None,
        inputs: Mapping[str, Any] | None = None,
        version_id: str | None = None,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        disabled = self._require_mutation("run_workflow")
        if disabled:
            return disabled
        resolved = self._resolve_workflow(workflow)
        _detail, version = self._version(resolved, version_id)
        if version.get("status") != "PUBLISHED":
            raise AdapterError(
                "VERSION_NOT_PUBLISHED",
                f"Version {version.get('version')} is {version.get('status')}; "
                "only a PUBLISHED version can execute.",
                status=409,
            )
        supplied = dict(inputs or {})
        schema = version.get("input_schema") or {}
        missing = _missing_inputs(supplied, schema)
        if missing:
            return _fail(
                "MISSING_INPUTS",
                "This workflow needs input values before it can run.",
                details={
                    "missing": missing,
                    "input_schema": schema,
                    "required_inputs": list(schema.get("required") or []),
                },
            )
        browser = any(item.get("channel") == "BROWSER" for item in version.get("actions") or [])
        payload = {
            "workflow_id": resolved["id"],
            "version_id": version.get("id"),
            "inputs": supplied,
        }
        summary = (
            f"Run {resolved.get('name')} v{version.get('version')} with inputs "
            f"{_canonical(supplied)} using "
            + ("Chromium against the controlled demo." if browser else "the simulated sandbox.")
        )
        confirmation = self._require_confirmation(
            "run_workflow", payload, confirmation_token, summary
        )
        if confirmation:
            return confirmation
        key = idempotency_key or _auto_key("run_workflow", payload, confirmation_token or "")
        run = self.client.post(
            "/runs",
            body={
                "workflow_id": resolved["id"],
                "version_id": version.get("id"),
                "inputs": supplied,
            },
            idempotency_key=key,
        )
        if not isinstance(run, dict):
            raise AdapterError("MALFORMED_BACKEND_RESPONSE", "Run response was not an object.")
        result = self._run_summary(run)
        result["blockers"] = self._blockers(str(run.get("id")))
        result["next_step"] = _run_next_step(result)
        return _ok(_status_sentence(result), result, idempotency_key=key)

    @_guarded
    def run_chaos_test(
        self,
        workflow: str | None = None,
        scenario: str | None = None,
        fault_type: str | None = None,
        show_choices_if_unspecified: bool = True,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        disabled = self._require_mutation("run_chaos_test")
        if disabled:
            return disabled
        resolved = self._resolve_workflow(workflow)
        rows = self.client.get("/scenarios", params={"workflow_id": resolved["id"], "limit": 200})
        if not isinstance(rows, list):
            raise AdapterError("MALFORMED_BACKEND_RESPONSE", "Scenario list was not an array.")
        choices = [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "fault_type": item.get("fault_type"),
                "target_ordinal": item.get("target_ordinal"),
                "seed": item.get("seed"),
                "status": item.get("status"),
                "description": item.get("description"),
            }
            for item in rows
        ]
        ref = (scenario or "").strip().casefold()
        if ref:
            matches = [
                item
                for item in rows
                if ref
                in {
                    str(item.get("id", "")).casefold(),
                    str(item.get("name", "")).casefold(),
                }
            ] or [
                item
                for item in rows
                if ref in f"{item.get('name', '')} {item.get('description', '')}".casefold()
            ]
        elif fault_type:
            matches = [
                item
                for item in rows
                if str(item.get("fault_type", "")).casefold() == fault_type.casefold()
            ]
        else:
            matches = []
        if len(matches) > 1:
            return _fail(
                "SCENARIO_AMBIGUOUS",
                "More than one scenario matches; choose one.",
                details={"candidates": choices},
            )
        if not matches:
            if show_choices_if_unspecified and not ref and not fault_type:
                return _fail(
                    "CHOOSE_SCENARIO",
                    "Choose a chaos scenario for this workflow.",
                    details={"candidates": choices},
                )
            return _fail(
                "SCENARIO_NOT_FOUND",
                "No scenario matches that description.",
                details={"available": choices, "workflow": resolved.get("name")},
            )
        chosen = matches[0]
        payload = {
            "workflow_id": resolved["id"],
            "scenario_id": chosen.get("id"),
            "seed": chosen.get("seed"),
        }
        summary = (
            f"Evaluate {chosen.get('name')} against {resolved.get('name')}: "
            f"{chosen.get('fault_type')} at ordinal {chosen.get('target_ordinal')}, "
            f"seed {chosen.get('seed')}. This creates a baseline and a fault run."
        )
        confirmation = self._require_confirmation(
            "run_chaos_test", payload, confirmation_token, summary
        )
        if confirmation:
            return confirmation
        key = idempotency_key or _auto_key("run_chaos_test", payload, confirmation_token or "")
        report = self.client.post(f"/scenarios/{chosen['id']}/evaluate", idempotency_key=key)
        if not isinstance(report, dict):
            raise AdapterError("MALFORMED_BACKEND_RESPONSE", "Chaos report was not an object.")
        fault = report.get("fault") or {}
        baseline = report.get("baseline") or {}
        data = {
            "scenario": chosen.get("name"),
            "scenario_id": chosen.get("id"),
            "fault_type": chosen.get("fault_type"),
            "seed": report.get("seed"),
            "input_fixtures": report.get("input_fixtures") or {},
            "baseline": {
                "run_id": baseline.get("run_id"),
                "status": baseline.get("status"),
                "verdict": baseline.get("verdict"),
                "driver": baseline.get("driver"),
            },
            "fault": {
                "run_id": fault.get("run_id"),
                "status": fault.get("status"),
                "verdict": fault.get("verdict"),
                "driver": fault.get("driver"),
                "failure_code": fault.get("failure_code"),
                "failure_message": fault.get("failure_message"),
                "reconciled_actions": fault.get("reconciled_actions"),
            },
            "first_violated_invariant": report.get("first_violated_invariant"),
            "failure_action": report.get("failure_action"),
            "failed_safely": report.get("failed_safely"),
            "recovery": report.get("recovery"),
            "evaluation": report.get("evaluation"),
            "regression_test_id": report.get("regression_test_id"),
            "baseline_verified": report.get("baseline_verified"),
            "fault_verified": fault.get("verdict") == "VERIFIED",
        }
        return _ok(
            f"Chaos evaluation {report.get('evaluation')}; fault run is "
            f"{fault.get('status')}/{fault.get('verdict')}.",
            data,
            idempotency_key=key,
        )

    @_guarded
    def request_recovery(
        self,
        run_id: str,
        strategy: str = "RESUME",
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        disabled = self._require_mutation("request_recovery")
        if disabled:
            return disabled
        run = self.client.get(f"/runs/{run_id}")
        if not isinstance(run, dict):
            raise AdapterError("MALFORMED_BACKEND_RESPONSE", "Run detail was not an object.")
        if run.get("status") not in RECOVERABLE:
            return _fail(
                "RUN_NOT_RECOVERABLE",
                f"Run is {run.get('status')}; only failed, timed-out or "
                "awaiting-recovery runs can be recovered.",
                details={"status": run.get("status"), "verdict": run.get("verdict")},
            )
        try:
            plan = self.client.get(f"/runs/{run_id}/recovery-plan")
        except AdapterError as exc:
            if exc.status != 404:
                raise
            plan = None
        payload = {"run_id": run_id, "strategy": strategy.upper()}
        summary = (
            f"Request {strategy.upper()} recovery for run {run_id}. This mutates the run "
            "and may resume workflow actions after re-checking preconditions."
        )
        confirmation = self._require_confirmation(
            "request_recovery", payload, confirmation_token, summary
        )
        if confirmation:
            return confirmation
        key = idempotency_key or _auto_key("request_recovery", payload, confirmation_token or "")
        attempt = self.client.post(
            f"/runs/{run_id}/recover",
            body={"strategy": strategy.upper()},
            idempotency_key=key,
        )
        refreshed = self.client.get(f"/runs/{run_id}")
        if not isinstance(attempt, dict) or not isinstance(refreshed, dict):
            raise AdapterError("MALFORMED_BACKEND_RESPONSE", "Recovery response was not an object.")
        verified = attempt.get("status") == "VERIFIED" and refreshed.get("verdict") == "VERIFIED"
        return _ok(
            f"Recovery attempt finished as {attempt.get('status')}. "
            + (
                "The backend verified the run."
                if verified
                else "The run is not verified; do not claim success."
            ),
            {
                "run_id": run_id,
                "attempt": attempt,
                "recovery_plan": plan,
                "run": self._run_summary(refreshed),
                "verified": verified,
            },
            idempotency_key=key,
        )

    @_guarded
    def run_regression_tests(
        self,
        test_ids: list[str] | None = None,
        confirmation_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        disabled = self._require_mutation("run_regression_tests")
        if disabled:
            return disabled
        rows = self.client.get("/regression-tests", params={"limit": 200})
        if not isinstance(rows, list):
            raise AdapterError("MALFORMED_BACKEND_RESPONSE", "Regression list was not an array.")
        by_id = {str(item.get("id")): item for item in rows}
        selected_ids = (
            test_ids
            or [
                str(item.get("id"))
                for item in rows
                if item.get("status") not in {"QUARANTINED", "DRAFT"}
            ][:20]
        )
        if not selected_ids:
            return _fail("NO_REGRESSION_TESTS", "There are no active regression tests to run.")
        unknown = [item for item in selected_ids if item not in by_id]
        if unknown:
            return _fail(
                "REGRESSION_TEST_NOT_FOUND",
                "One or more regression tests do not exist.",
                details={"unknown": unknown},
            )
        payload = {"test_ids": selected_ids}
        summary = f"Replay {len(selected_ids)} regression test(s): {', '.join(selected_ids[:5])}."
        confirmation = self._require_confirmation(
            "run_regression_tests", payload, confirmation_token, summary
        )
        if confirmation:
            return confirmation
        results: list[dict[str, Any]] = []
        for test_id in selected_ids:
            key = idempotency_key or _auto_key(
                f"run_regression_tests:{test_id}", payload, confirmation_token or ""
            )
            try:
                updated = self.client.post(
                    f"/regression-tests/{test_id}/replay", idempotency_key=key
                )
            except AdapterError as exc:
                results.append(
                    {
                        "test_id": test_id,
                        "ok": False,
                        "code": exc.code,
                        "detail": exc.detail,
                    }
                )
                continue
            results.append(
                {
                    "test_id": test_id,
                    "ok": True,
                    "status": updated.get("status") if isinstance(updated, dict) else None,
                    "last_result": updated.get("last_result")
                    if isinstance(updated, dict)
                    else None,
                    "last_run_at": updated.get("last_run_at")
                    if isinstance(updated, dict)
                    else None,
                }
            )
        return _ok(f"Replayed {len(results)} regression test(s).", {"results": results})


def _workflow_candidates(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id"),
            "slug": item.get("slug"),
            "name": item.get("name"),
            "origin": item.get("origin"),
            "latest_version": item.get("latest_version"),
            "latest_status": item.get("latest_status"),
        }
        for item in rows
    ]


def _missing_inputs(inputs: Mapping[str, Any], schema: Mapping[str, Any]) -> list[str]:
    missing: list[str] = []
    for key in schema.get("required") or []:
        value = inputs.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(str(key))
    return missing


def _execution_kind(driver: str) -> str:
    if driver == "playwright-v1":
        return "browser"
    if driver == "simulated-v1":
        return "simulated"
    return "unknown"


def _status_sentence(run: Mapping[str, Any]) -> str:
    status = run.get("status")
    verdict = run.get("verdict")
    driver = run.get("driver") or ""
    kind = _execution_kind(str(driver))
    if run.get("is_success"):
        return f"Backend verified SUCCEEDED/VERIFIED ({kind} driver: {driver})."
    return f"Run is {status}/{verdict} on the {kind} driver ({driver}). Success is not claimed."


def _run_next_step(run: Mapping[str, Any]) -> str:
    status = run.get("status")
    if status == "AWAITING_DECISION":
        return "A human must decide in the Decision Inbox; this adapter cannot approve."
    if status == "AWAITING_RECOVERY":
        return "Inspect the failure and request recovery only after explicit confirmation."
    if status == "SUCCEEDED" and run.get("verdict") == "VERIFIED":
        return "The backend verified the run. You may report success."
    return "Do not report success. Inspect the run and explain the persisted failure."


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}
