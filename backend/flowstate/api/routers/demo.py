"""Demonstration workspace endpoints.

Seeding is an explicit, labelled action. No route here can fabricate a run that
is labelled LIVE.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from flowstate.api.deps import ClockDep, EvidenceDep, IdsDep, SettingsDep, UowDep
from flowstate.demo import fixtures
from flowstate.demo.seed import reset_demo, seed_demo
from flowstate.domain.enums import Origin
from flowstate.domain.schemas import DeleteResponse, DemoInfo, DemoSeedResponse
from flowstate.errors import DemoSeedDisabledError

router = APIRouter(prefix="/demo", tags=["demo"])


@router.get("", response_model=DemoInfo)
def demo_info(uow: UowDep, settings: SettingsDep) -> DemoInfo:
    workflow = uow.workflows.first(slug=fixtures.DEMO_SLUG)
    seeded = workflow is not None and workflow.origin == Origin.DEMO
    return DemoInfo(
        batch=fixtures.DEMO_BATCH,
        seeded=seeded,
        workflow_id=workflow.id if seeded and workflow else None,
        enabled=settings.demo_seed_enabled,
        origin=Origin.DEMO,
    )


@router.post("/seed", response_model=DemoSeedResponse, status_code=status.HTTP_201_CREATED)
def seed(
    uow: UowDep,
    clock: ClockDep,
    ids: IdsDep,
    settings: SettingsDep,
    evidence: EvidenceDep,
) -> DemoSeedResponse:
    if not settings.demo_seed_enabled:
        raise DemoSeedDisabledError(
            "Demo seeding is disabled by configuration.", code="DEMO_SEED_DISABLED"
        )
    result = seed_demo(uow, clock, ids, settings, evidence)
    return DemoSeedResponse(**result)


@router.delete("/reset", response_model=DeleteResponse)
def reset(uow: UowDep, settings: SettingsDep) -> DeleteResponse:
    if not settings.demo_seed_enabled:
        raise DemoSeedDisabledError(
            "Demo reset is disabled by configuration.", code="DEMO_SEED_DISABLED"
        )
    result = reset_demo(uow)
    return DeleteResponse(**result)
