"""System overview and safe configuration reporting."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from flowstate import __version__
from flowstate.clock import Clock
from flowstate.config import Settings
from flowstate.domain.enums import (
    TERMINAL_RUN_STATUSES,
    DecisionStatus,
    Origin,
    VersionStatus,
)
from flowstate.repositories import UnitOfWork
from flowstate.services.audit import verify_chain


def overview(
    uow: UnitOfWork,
    clock: Clock,
    settings: Settings,
    *,
    started_at: datetime,
    db_healthy: bool,
) -> dict[str, Any]:
    workflows = list(uow.workflows.list(order_by="created_at", descending=True))
    published = list(uow.versions.list(status=VersionStatus.PUBLISHED))
    runs = list(uow.runs.list(order_by="created_at", descending=True, limit=200))
    open_decisions = [
        item
        for item in uow.decisions.list()
        if item.status
        in {
            DecisionStatus.REQUESTED,
            DecisionStatus.NOTIFIED,
            DecisionStatus.DEFERRED,
            DecisionStatus.INFO_PROVIDED,
        }
    ]
    scenarios = list(uow.scenarios.list(order_by="created_at", descending=True))

    runs_by_status: dict[str, int] = {}
    for run in runs:
        runs_by_status[run.status] = runs_by_status.get(run.status, 0) + 1

    violations = sum(1 for verdict in uow.verdicts.list() if verdict.verdict == "VIOLATED")

    demo_runs = sum(1 for run in runs if run.origin == Origin.DEMO)
    live_runs = sum(1 for run in runs if run.origin == Origin.LIVE)

    recent = []
    for run in runs[:8]:
        workflow = uow.workflows.get(run.workflow_id)
        version = uow.versions.get(run.workflow_version_id)
        recent.append(
            {
                "id": run.id,
                "workflow_id": run.workflow_id,
                "workflow_name": workflow.name if workflow else "Unknown workflow",
                "version": version.version if version else None,
                "status": run.status,
                "verdict": run.verdict,
                "trigger": run.trigger,
                "origin": run.origin,
                "driver": run.driver,
                "created_at": run.created_at,
                "ended_at": run.ended_at,
                "failure_code": run.failure_code,
            }
        )

    chain = {"checked": 0, "valid": 0, "broken": 0}
    for run in runs[:20]:
        result = verify_chain(list(uow.events.read(run.id)))
        chain["checked"] += 1
        if result.valid:
            chain["valid"] += 1
        else:
            chain["broken"] += 1

    terminal = [run for run in runs if run.status in TERMINAL_RUN_STATUSES]
    verified = sum(1 for run in terminal if run.verdict == "VERIFIED")

    return {
        "generated_at": clock.now(),
        "uptime_seconds": max((clock.now() - started_at).total_seconds(), 0.0),
        "database_healthy": db_healthy,
        "counts": {
            "workflows": len(workflows),
            "demo_workflows": sum(1 for item in workflows if item.origin == Origin.DEMO),
            "published_versions": len(published),
            "runs": len(runs),
            "demo_runs": demo_runs,
            "live_runs": live_runs,
            "open_decisions": len(open_decisions),
            "scenarios": len(scenarios),
            "invariant_violations": violations,
        },
        "runs_by_status": runs_by_status,
        "terminal": {
            "total": len(terminal),
            "verified": verified,
            "success_rate": (verified / len(terminal)) if terminal else None,
        },
        "audit": chain,
        "recent_runs": recent,
        "driver": {
            "name": "simulated-v1",
            "kind": "engine + learning drivers",
            "browser": True,
            "note": (
                "Engine workflows run in the in-process sandbox. Learned workflows "
                "replay through playwright-v1 against the controlled /demo-app only."
            ),
        },
    }


def system_info(
    settings: Settings,
    clock: Clock,
    *,
    started_at: datetime,
    db_url: str,
    schema_version: int,
    db_healthy: bool,
) -> dict[str, Any]:
    return {
        "app": settings.app_name,
        "version": __version__,
        "environment": settings.environment,
        "api_prefix": settings.api_prefix,
        "server_time": clock.now(),
        "uptime_seconds": max((clock.now() - started_at).total_seconds(), 0.0),
        "database": {
            "url": _redact_db_url(db_url),
            "schema_version": schema_version,
            "healthy": db_healthy,
        },
        "checks": {
            "health": "pass",
            "database": "pass" if db_healthy else "fail",
            "audit_chain": "available",
        },
        "settings": {
            "auth_mode": settings.auth_mode,
            "cors_origins": settings.cors_origins,
            "demo_seed_enabled": settings.demo_seed_enabled,
            "decision_ttl_seconds": settings.decision_ttl_seconds,
            "run_action_budget": settings.run_action_budget,
            "log_level": settings.log_level,
            "log_json": settings.log_json,
        },
        "driver": {
            "name": "simulated-v1",
            "kind": "engine + learning drivers",
            "browser": True,
            "browser_capture": True,
            "browser_driver": "playwright-v1",
            "note": (
                "Browser replay is limited to the controlled demo. "
                "Engine workflows still use the in-process sandbox."
            ),
            "limitations": [
                "Browser replay is limited to the controlled /demo-app site",
                "Learned workflows can be replayed but are not resumable mid-page",
                "Process-level sandbox only; no container isolation",
            ],
        },
    }


def _redact_db_url(url: str) -> str:
    if "@" in url and "://" in url:
        scheme, _, rest = url.partition("://")
        _creds, _, host = rest.rpartition("@")
        return f"{scheme}://***@{host}"
    return url
