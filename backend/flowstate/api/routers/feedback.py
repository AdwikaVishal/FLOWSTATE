"""The learning feedback loop.

Read-only: it reports which previously detected failure modes later versions
resolve. Nothing here rewrites a workflow.
"""

from __future__ import annotations

from fastapi import APIRouter

from flowstate.api.deps import UowDep
from flowstate.domain.schemas import FeedbackFailureModeOut
from flowstate.services import regression

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.get("/failure-modes", response_model=list[FeedbackFailureModeOut])
def failure_modes(uow: UowDep) -> list[FeedbackFailureModeOut]:
    return [FeedbackFailureModeOut.model_validate(item) for item in regression.failure_modes(uow)]
