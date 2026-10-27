"""Regression test endpoints: generate, review, replay, compare."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from flowstate.api.deps import ActorDep, ClockDep, EngineDep, IdsDep, UowDep
from flowstate.domain.schemas import (
    RegressionCompareOut,
    RegressionCreate,
    RegressionOut,
    RegressionResultOut,
    RegressionUpdate,
)
from flowstate.services import regression as service

router = APIRouter(prefix="/regression-tests", tags=["regression"])


@router.get("", response_model=list[RegressionOut])
def list_tests(
    uow: UowDep,
    workflow_id: Annotated[str | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[RegressionOut]:
    filters: dict[str, object] = {}
    if status_filter:
        filters["status"] = status_filter
    rows = uow.regressions.list(
        order_by="created_at",
        descending=True,
        limit=limit,
        **filters,  # type: ignore[arg-type]
    )
    if workflow_id:
        rows = [item for item in rows if (item.spec or {}).get("workflow_id") == workflow_id]
    return [RegressionOut.model_validate(item) for item in rows]


@router.post("", response_model=RegressionOut, status_code=status.HTTP_201_CREATED)
def create_test(
    payload: RegressionCreate, uow: UowDep, clock: ClockDep, ids: IdsDep
) -> RegressionOut:
    test = service.create_from_run(uow, ids, clock, run_id=payload.run_id, name=payload.name)
    return RegressionOut.model_validate(test)


@router.get("/{test_id}", response_model=RegressionOut)
def get_test(test_id: str, uow: UowDep) -> RegressionOut:
    return RegressionOut.model_validate(service.get_test(uow, test_id))


@router.patch("/{test_id}", response_model=RegressionOut)
def review_test(
    test_id: str,
    payload: RegressionUpdate,
    uow: UowDep,
    clock: ClockDep,
    actor: ActorDep,
) -> RegressionOut:
    test = service.get_test(uow, test_id)
    test = service.review(
        uow,
        clock,
        test,
        actor=actor.id,
        name=payload.name,
        notes=payload.notes,
        expected_description=payload.expected_description,
    )
    return RegressionOut.model_validate(test)


@router.post("/{test_id}/activate", response_model=RegressionOut)
def activate_test(test_id: str, uow: UowDep) -> RegressionOut:
    test = service.get_test(uow, test_id)
    service.activate(uow, test)
    return RegressionOut.model_validate(test)


@router.post("/{test_id}/quarantine", response_model=RegressionOut)
def quarantine_test(test_id: str, payload: RegressionUpdate, uow: UowDep) -> RegressionOut:
    test = service.get_test(uow, test_id)
    service.quarantine(uow, test, payload.notes or "quarantined")
    return RegressionOut.model_validate(test)


@router.post("/{test_id}/replay", response_model=RegressionOut)
def replay_test(
    test_id: str,
    uow: UowDep,
    clock: ClockDep,
    ids: IdsDep,
    engine: EngineDep,
    actor: ActorDep,
    version_id: Annotated[str | None, Query()] = None,
) -> RegressionOut:
    test, _run_id = service.replay(
        uow, clock, ids, engine, test_id=test_id, version_id=version_id, actor=actor.id
    )
    return RegressionOut.model_validate(test)


@router.post("/{test_id}/compare", response_model=RegressionCompareOut)
def compare_test(
    test_id: str,
    uow: UowDep,
    clock: ClockDep,
    ids: IdsDep,
    engine: EngineDep,
    actor: ActorDep,
) -> RegressionCompareOut:
    test = service.get_test(uow, test_id)
    results = service.compare(uow, clock, ids, engine, test_id=test_id, actor=actor.id)
    return RegressionCompareOut(
        test_id=test_id,
        test=RegressionOut.model_validate(test),
        results=[RegressionResultOut.model_validate(item) for item in results],
        resolved_in=[item.version for item in results if item.result == "PASSED"],
        reproduces_in=[item.version for item in results if item.result == "FAILED"],
    )


@router.get("/{test_id}/results", response_model=list[RegressionResultOut])
def test_results(test_id: str, uow: UowDep) -> list[RegressionResultOut]:
    return [RegressionResultOut.model_validate(item) for item in service.results_for(uow, test_id)]
