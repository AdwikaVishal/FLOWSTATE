"""Repository ports and the SQLite unit of work.

Services depend on this module, never on SQLAlchemy directly in their
signatures. Swapping SQLite for PostgreSQL means providing another UnitOfWork.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any, Protocol, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from flowstate.db.models import (
    ActionDefinition,
    Base,
    Blocker,
    BlockerEvidence,
    CapabilityToken,
    CapturedAction,
    CaptureSession,
    Checkpoint,
    DecisionRecord,
    EvidenceRecord,
    ExecutionEvent,
    ExecutionJob,
    ExecutionNodeState,
    ExecutionRun,
    FaultScenario,
    HumanDecision,
    IdempotencyRecord,
    Invariant,
    InvariantVerdictRecord,
    RecoveryAttempt,
    RegressionResult,
    RegressionTest,
    Workflow,
    WorkflowVersion,
)

T = TypeVar("T", bound=Base)


class ReadRepository(Protocol[T]):
    def get(self, entity_id: str) -> T | None: ...
    def list(self, **filters: Any) -> list[T]: ...
    def count(self, **filters: Any) -> int: ...


class Repository(ReadRepository[T], Protocol[T]):
    def add(self, entity: T) -> T: ...
    def delete(self, entity: T) -> None: ...


class SqlRepository[T: Base]:
    """Generic CRUD over a mapped model.

    Deliberately boring: filters are equality-only keyword arguments applied to
    the model's columns. Anything more complex belongs in a service.
    """

    def __init__(self, session: Session, model: type[T]) -> None:
        self.session = session
        self.model = model

    def _apply(self, stmt: Select[Any], filters: dict[str, Any]) -> Select[Any]:
        for key, value in filters.items():
            if not hasattr(self.model, key):
                raise AttributeError(f"{self.model.__name__} has no column {key!r}")
            column = getattr(self.model, key)
            stmt = stmt.where(column.is_(None) if value is None else column == value)
        return stmt

    def get(self, entity_id: str) -> T | None:
        return self.session.get(self.model, entity_id)

    def list(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        order_by: str | None = None,
        descending: bool = False,
        **filters: Any,
    ) -> list[T]:
        stmt = self._apply(select(self.model), filters)
        column = getattr(self.model, order_by, None) if order_by else None
        if column is not None:
            stmt = stmt.order_by(column.desc() if descending else column.asc())
        if offset:
            stmt = stmt.offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.session.scalars(stmt).all())

    def count(self, **filters: Any) -> int:
        stmt = self._apply(select(func.count()).select_from(self.model), filters)
        return int(self.session.scalar(stmt) or 0)

    def add(self, entity: T) -> T:
        self.session.add(entity)
        self.session.flush()
        return entity

    def delete(self, entity: T) -> None:
        self.session.delete(entity)
        self.session.flush()

    def first(self, **filters: Any) -> T | None:
        rows = self.list(limit=1, **filters)
        return rows[0] if rows else None


class EventStore(Protocol):
    def append(self, events: Sequence[ExecutionEvent]) -> None: ...
    def read(
        self, run_id: str, *, after_seq: int = -1, limit: int = 1000
    ) -> Sequence[ExecutionEvent]: ...
    def last(self, run_id: str) -> ExecutionEvent | None: ...
    def count(self, run_id: str) -> int: ...


class SqlEventStore:
    """Append-only. No update or delete method exists on purpose."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def append(self, events: Sequence[ExecutionEvent]) -> None:
        for item in events:
            self.session.add(item)
        self.session.flush()

    def read(
        self, run_id: str, *, after_seq: int = -1, limit: int = 1000
    ) -> Sequence[ExecutionEvent]:
        stmt = (
            select(ExecutionEvent)
            .where(ExecutionEvent.run_id == run_id, ExecutionEvent.seq > after_seq)
            .order_by(ExecutionEvent.seq.asc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt).all())

    def last(self, run_id: str) -> ExecutionEvent | None:
        stmt = (
            select(ExecutionEvent)
            .where(ExecutionEvent.run_id == run_id)
            .order_by(ExecutionEvent.seq.desc())
            .limit(1)
        )
        return self.session.scalars(stmt).first()

    def count(self, run_id: str) -> int:
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(ExecutionEvent)
                .where(ExecutionEvent.run_id == run_id)
            )
            or 0
        )


class UnitOfWork:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.workflows = SqlRepository(session, Workflow)
        self.versions = SqlRepository(session, WorkflowVersion)
        self.actions = SqlRepository(session, ActionDefinition)
        self.invariants = SqlRepository(session, Invariant)
        self.runs = SqlRepository(session, ExecutionRun)
        self.execution_jobs = SqlRepository(session, ExecutionJob)
        self.events = SqlEventStore(session)
        self.node_states = SqlRepository(session, ExecutionNodeState)
        self.scenarios = SqlRepository(session, FaultScenario)
        self.decisions = SqlRepository(session, HumanDecision)
        self.decision_records = SqlRepository(session, DecisionRecord)
        self.blockers = SqlRepository(session, Blocker)
        self.blocker_evidence = SqlRepository(session, BlockerEvidence)
        self.tokens = SqlRepository(session, CapabilityToken)
        self.checkpoints = SqlRepository(session, Checkpoint)
        self.recoveries = SqlRepository(session, RecoveryAttempt)
        self.regressions = SqlRepository(session, RegressionTest)
        self.regression_results = SqlRepository(session, RegressionResult)
        self.verdicts = SqlRepository(session, InvariantVerdictRecord)
        self.evidence = SqlRepository(session, EvidenceRecord)
        self.captures = SqlRepository(session, CaptureSession)
        self.captured_actions = SqlRepository(session, CapturedAction)
        self.idempotency = SqlRepository(session, IdempotencyRecord)

    def __enter__(self) -> UnitOfWork:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is None:
            self.session.commit()
        else:
            self.session.rollback()
        self.session.close()

    def flush(self) -> None:
        self.session.flush()

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()


def unit_of_work(session_factory: Any) -> Iterator[UnitOfWork]:
    session = session_factory()
    try:
        yield UnitOfWork(session)
    finally:
        session.close()
