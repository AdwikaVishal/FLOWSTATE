"""FastAPI dependencies: unit of work, auth and actor identity."""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request

from flowstate.clock import Clock
from flowstate.config import DEVELOPMENT_ENVIRONMENTS, Settings
from flowstate.domain.enums import ActorKind, ActorRole
from flowstate.errors import ForbiddenError, UnauthorizedError
from flowstate.ids import IdFactory
from flowstate.repositories import UnitOfWork
from flowstate.services.cancellation import CancellationRegistry
from flowstate.services.evidence import EvidenceStore
from flowstate.services.execution import ExecutionEngine


@dataclass(frozen=True)
class Actor:
    id: str
    kind: ActorKind
    role: ActorRole = ActorRole.ADMIN


def get_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str | None:
    from flowstate.services.idempotency import validate_key

    return validate_key(idempotency_key)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_clock(request: Request) -> Clock:
    return request.app.state.clock


def get_ids(request: Request) -> IdFactory:
    return request.app.state.ids


def get_evidence(request: Request) -> EvidenceStore:
    return request.app.state.evidence


def get_cancellations(request: Request) -> CancellationRegistry:
    return request.app.state.cancellations


def get_uow(request: Request) -> Iterator[UnitOfWork]:
    factory = request.app.state.db.session_factory
    session = factory()
    uow = UnitOfWork(session)
    try:
        yield uow
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def require_auth(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    settings: Settings = request.app.state.settings
    if settings.api_token is None:
        return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("A bearer token is required.", code="UNAUTHORIZED")
    presented = authorization.split(" ", 1)[1].strip()
    if not secrets.compare_digest(presented, settings.api_token):
        raise UnauthorizedError("The bearer token is not valid.", code="INVALID_TOKEN")


def get_actor(
    request: Request,
    actor_id: Annotated[str | None, Header(alias="X-Flowstate-Actor")] = None,
    actor_kind: Annotated[str | None, Header(alias="X-Flowstate-Actor-Kind")] = None,
    actor_role: Annotated[str | None, Header(alias="X-Flowstate-Role")] = None,
) -> Actor:
    settings: Settings = request.app.state.settings
    if settings.environment.strip().lower() not in DEVELOPMENT_ENVIRONMENTS:
        return Actor(
            id="authenticated-api-client",
            kind=settings.api_actor_kind,
            role=settings.api_actor_role,
        )

    kind = ActorKind.HUMAN
    if actor_kind:
        try:
            kind = ActorKind(actor_kind.upper())
        except ValueError:
            kind = ActorKind.AGENT
    role = ActorRole.ADMIN
    if actor_role:
        try:
            role = ActorRole(actor_role.upper())
        except ValueError:
            role = ActorRole.OPERATOR
    return Actor(id=actor_id or "local-operator", kind=kind, role=role)


def require_human(actor: Annotated[Actor, Depends(get_actor)]) -> Actor:
    if actor.kind != ActorKind.HUMAN:
        raise ForbiddenError("This action requires a human actor.", code="HUMAN_ACTOR_REQUIRED")
    return actor


def get_engine(
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_uow, scope="function")],
    clock: Annotated[Clock, Depends(get_clock)],
    ids: Annotated[IdFactory, Depends(get_ids)],
    settings: Annotated[Settings, Depends(get_settings)],
    evidence: Annotated[EvidenceStore, Depends(get_evidence)],
    cancellations: Annotated[CancellationRegistry, Depends(get_cancellations)],
) -> ExecutionEngine:
    return ExecutionEngine(
        uow,
        clock,
        ids,
        settings,
        evidence,
        cancellations=cancellations,
        browser_factory=getattr(request.app.state, "browser_factory", None),
    )


UowDep = Annotated[UnitOfWork, Depends(get_uow, scope="function")]
SettingsDep = Annotated[Settings, Depends(get_settings)]
ClockDep = Annotated[Clock, Depends(get_clock)]
IdsDep = Annotated[IdFactory, Depends(get_ids)]
EvidenceDep = Annotated[EvidenceStore, Depends(get_evidence)]
ActorDep = Annotated[Actor, Depends(get_actor)]
HumanDep = Annotated[Actor, Depends(require_human)]
EngineDep = Annotated[ExecutionEngine, Depends(get_engine)]
CancellationDep = Annotated[CancellationRegistry, Depends(get_cancellations)]
IdempotencyDep = Annotated[str | None, Depends(get_idempotency_key)]
