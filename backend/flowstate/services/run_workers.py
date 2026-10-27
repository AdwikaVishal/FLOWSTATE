"""Bounded, durable background execution for interactive runs."""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from threading import RLock
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import sessionmaker

from flowstate.clock import Clock
from flowstate.config import Settings
from flowstate.db.models import ExecutionJob
from flowstate.domain.enums import (
    ActorKind,
    ActorRole,
    EventType,
    NodeStatus,
    Origin,
    RunStatus,
    RunTrigger,
    RunVerdict,
)
from flowstate.ids import IdFactory
from flowstate.repositories import UnitOfWork
from flowstate.services.audit import AuditWriter
from flowstate.services.cancellation import CancellationRegistry
from flowstate.services.evidence import EvidenceStore
from flowstate.services.execution import ExecuteOptions, ExecutionEngine

logger = logging.getLogger(__name__)
MAX_WORKERS = 4
MAX_QUEUED_OR_RUNNING = 32


class RunWorkerPool:
    """Runs persisted jobs on a bounded pool and resumes queued work at startup."""

    def __init__(
        self,
        session_factory: sessionmaker[Any],
        clock: Clock,
        ids: IdFactory,
        settings: Settings,
        evidence: EvidenceStore,
        cancellations: CancellationRegistry,
        browser_factory: Any,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._ids = ids
        self._settings = settings
        self._evidence = evidence
        self._cancellations = cancellations
        self._browser_factory = browser_factory
        self._executor = ThreadPoolExecutor(
            max_workers=MAX_WORKERS, thread_name_prefix="flowstate-run"
        )
        self._lock = RLock()
        self._reserved = 0
        self._futures: set[Future[None]] = set()
        self._closed = False

    def reserve(self) -> bool:
        with self._lock:
            if self._closed or len(self._futures) + self._reserved >= MAX_QUEUED_OR_RUNNING:
                return False
            self._reserved += 1
            return True

    def release_reservation(self) -> None:
        with self._lock:
            self._reserved = max(self._reserved - 1, 0)

    def submit_reserved(self, run_id: str) -> None:
        with self._lock:
            self._reserved = max(self._reserved - 1, 0)
            if self._closed:
                return
            future = self._executor.submit(self._execute, run_id)
            self._futures.add(future)
            future.add_done_callback(self._discard_future)

    def start(self) -> None:
        self._mark_interrupted_jobs()
        session = self._session_factory()
        try:
            uow = UnitOfWork(session)
            queued = uow.execution_jobs.list(status="QUEUED", order_by="created_at")
            for job in queued[:MAX_QUEUED_OR_RUNNING]:
                with self._lock:
                    if self._closed or len(self._futures) >= MAX_QUEUED_OR_RUNNING:
                        break
                    future = self._executor.submit(self._execute, job.run_id)
                    self._futures.add(future)
                    future.add_done_callback(self._discard_future)
        finally:
            session.close()

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _discard_future(self, future: Future[None]) -> None:
        with self._lock:
            self._futures.discard(future)
        try:
            future.result()
        except Exception:
            logger.exception("background execution worker terminated unexpectedly")

    def _execute(self, run_id: str) -> None:
        session = self._session_factory()
        try:
            claimed = session.execute(
                update(ExecutionJob)
                .where(ExecutionJob.run_id == run_id, ExecutionJob.status == "QUEUED")
                .values(status="RUNNING", started_at=self._clock.now())
            )
            if claimed.rowcount != 1:
                session.rollback()
                return
            session.commit()

            uow = UnitOfWork(session)
            job = uow.execution_jobs.get(run_id)
            run = uow.runs.get(run_id)
            if job is None or run is None:
                raise RuntimeError(f"Persisted execution job {run_id} has no job or run record.")
            payload = dict(job.payload or {})
            workflow = uow.workflows.get(run.workflow_id)
            version = uow.versions.get(run.workflow_version_id)
            if workflow is None or version is None:
                raise RuntimeError(f"Execution job {run_id} references missing workflow data.")
            actions = uow.actions.list(workflow_version_id=version.id, order_by="ordinal")
            invariants = uow.invariants.list(workflow_version_id=version.id)
            scenario_id = payload.get("scenario_id")
            scenario = uow.scenarios.get(str(scenario_id)) if scenario_id else None
            options = ExecuteOptions(
                trigger=RunTrigger(str(payload["trigger"])),
                origin=Origin(run.origin),
                seed=run.seed,
                scenario=scenario,
                actor=run.actor,
                actor_kind=ActorKind(str(payload["actor_kind"])),
                actor_role=ActorRole(str(payload["actor_role"])),
                capture_session_id=run.capture_session_id,
                inputs=dict(payload.get("inputs") or {}),
                publish_progress=True,
            )
            engine = ExecutionEngine(
                uow,
                self._clock,
                self._ids,
                self._settings,
                self._evidence,
                cancellations=self._cancellations,
                browser_factory=self._browser_factory,
            )
            engine.start_run(
                workflow=workflow,
                version=version,
                actions=actions,
                invariants=invariants,
                options=options,
                existing_run=run,
            )
            job.status = "COMPLETED"
            job.ended_at = self._clock.now()
            uow.commit()
        except Exception:
            logger.exception("background execution failed", extra={"run_id": run_id})
            session.rollback()
            session.close()
            self._record_failure(run_id)
            return
        finally:
            if session.is_active:
                session.close()

    def _record_failure(self, run_id: str) -> None:
        session = self._session_factory()
        try:
            uow = UnitOfWork(session)
            job = uow.execution_jobs.get(run_id)
            run = uow.runs.get(run_id)
            if job is None or run is None:
                logger.error("failed execution job cannot be recorded", extra={"run_id": run_id})
                return
            job.status = "FAILED"
            job.failure_code = "BACKGROUND_EXECUTION_FAILED"
            job.ended_at = self._clock.now()
            if run.status in {RunStatus.PENDING, RunStatus.RUNNING}:
                run.status = str(RunStatus.AWAITING_RECOVERY)
                run.verdict = str(RunVerdict.PENDING)
                run.failure_code = "BACKGROUND_EXECUTION_FAILED"
                run.failure_message = (
                    "The background worker failed. Review the latest checkpoint before recovery."
                )
                run.failure_ordinal = run.current_ordinal
                run.ended_at = self._clock.now()
                for node_state in uow.node_states.list(run_id=run_id):
                    if node_state.status == NodeStatus.RUNNING:
                        node_state.status = str(NodeStatus.BLOCKED)
                        node_state.ended_at = self._clock.now()
                        node_state.error_code = "BACKGROUND_EXECUTION_FAILED"
                        node_state.error_message = run.failure_message
                AuditWriter(uow, self._clock, self._ids).append(
                    run_id,
                    EventType.RUN_FAILED,
                    payload={
                        "code": run.failure_code,
                        "detail": run.failure_message,
                        "recoverable": True,
                    },
                )
            uow.commit()
        except Exception:
            session.rollback()
            logger.exception(
                "could not persist background execution failure", extra={"run_id": run_id}
            )
        finally:
            session.close()

    def _mark_interrupted_jobs(self) -> None:
        session = self._session_factory()
        try:
            uow = UnitOfWork(session)
            interrupted = uow.execution_jobs.list(status="RUNNING")
            for job in interrupted:
                run = uow.runs.get(job.run_id)
                if run is not None and run.status not in {RunStatus.PENDING, RunStatus.RUNNING}:
                    job.status = "COMPLETED"
                    job.ended_at = self._clock.now()
                    continue
                job.status = "INTERRUPTED"
                job.failure_code = "WORKER_INTERRUPTED"
                job.ended_at = self._clock.now()
                if run is None:
                    continue
                run.status = str(RunStatus.AWAITING_RECOVERY)
                run.verdict = str(RunVerdict.PENDING)
                run.failure_code = "WORKER_INTERRUPTED"
                run.failure_message = (
                    "The process restarted during execution. Inspect the last committed "
                    "checkpoint; the interrupted action outcome is not assumed."
                )
                run.failure_ordinal = run.current_ordinal
                run.ended_at = self._clock.now()
                for node_state in uow.node_states.list(run_id=run.id):
                    if node_state.status == NodeStatus.RUNNING:
                        node_state.status = str(NodeStatus.BLOCKED)
                        node_state.ended_at = self._clock.now()
                        node_state.error_code = "WORKER_INTERRUPTED"
                        node_state.error_message = run.failure_message
                AuditWriter(uow, self._clock, self._ids).append(
                    run.id,
                    EventType.RUN_FAILED,
                    payload={
                        "code": "WORKER_INTERRUPTED",
                        "detail": run.failure_message,
                        "outcome": "UNKNOWN",
                        "recoverable": True,
                    },
                )
            uow.commit()
        except Exception:
            session.rollback()
            logger.exception("could not recover interrupted execution jobs")
        finally:
            session.close()
