"""FastAPI application factory.

Run with::

    uvicorn flowstate.main:app --reload --port 8000

from the ``backend/`` directory.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from flowstate import __version__
from flowstate.api.deps import require_auth
from flowstate.api.routers import (
    audit,
    captures,
    chaos,
    decisions,
    demo,
    feedback,
    health,
    recovery,
    regression,
    runs,
    system,
    workflows,
)
from flowstate.capture.demo_app.router import router as demo_app_router
from flowstate.clock import Clock, SystemClock
from flowstate.config import Settings, get_settings
from flowstate.db.engine import Database
from flowstate.ids import IdFactory, UuidFactory
from flowstate.logging import configure_logging
from flowstate.repositories import UnitOfWork
from flowstate.services.cancellation import CancellationRegistry
from flowstate.services.evidence import EvidenceStore
from flowstate.services.run_workers import RunWorkerPool

logger = logging.getLogger(__name__)


def _build_lifespan(
    settings: Settings,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level, json_output=settings.log_json)

        settings.data_dir.mkdir(parents=True, exist_ok=True)
        evidence_root = settings.data_dir / "evidence"
        evidence_root.mkdir(parents=True, exist_ok=True)

        clock: Clock = app.state.clock
        ids: IdFactory = app.state.ids

        db = Database(settings.resolved_database_url)
        schema_version = db.init()
        app.state.db = db
        app.state.schema_version = schema_version
        app.state.evidence_root = evidence_root
        app.state.evidence = EvidenceStore(evidence_root, clock, ids)
        app.state.started_at = clock.now()
        app.state.run_workers = RunWorkerPool(
            db.session_factory,
            clock,
            ids,
            settings,
            app.state.evidence,
            app.state.cancellations,
            app.state.browser_factory,
        )

        if settings.auto_seed_demo and settings.demo_seed_enabled:
            from flowstate.demo.seed import seed_demo

            session = db.session_factory()
            uow = UnitOfWork(session)
            try:
                result = seed_demo(uow, clock, ids, settings, app.state.evidence)
                session.commit()
                logger.info("demo workspace ready", extra={"status_code": 200})
                app.state.demo_seeded = bool(result.get("created"))
            except Exception:
                session.rollback()
                logger.exception("demo seeding failed; continuing without fixtures")
                app.state.demo_seeded = False
            finally:
                session.close()
        else:
            app.state.demo_seeded = False

        app.state.run_workers.start()
        logger.info(
            "flowstate api ready",
            extra={"path": settings.resolved_database_url, "status_code": 200},
        )
        try:
            yield
        finally:
            app.state.run_workers.shutdown()
            db.engine.dispose()
            logger.info("flowstate api stopped")

    return lifespan


def _browser_factory(settings: Settings) -> object:
    """Return a lazy Playwright session factory, or ``None`` when unavailable."""
    if not settings.capture_enabled:
        return None
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        logger.warning("playwright is not installed; browser workflows will be rejected")
        return None

    from flowstate.capture.browser import PlaywrightBrowserSession
    from flowstate.domain.policy import ActionPolicy

    policy = ActionPolicy()

    def factory() -> PlaywrightBrowserSession:
        return PlaywrightBrowserSession(policy=policy, headless=True)

    return factory


def create_app(
    settings: Settings | None = None,
    *,
    clock: Clock | None = None,
    ids: IdFactory | None = None,
) -> FastAPI:
    resolved = settings or get_settings()

    app = FastAPI(
        title=resolved.app_name,
        version=__version__,
        description=(
            "Workflow reliability platform. Execution, verification and audit are "
            "backend-only; the console is a client."
        ),
        lifespan=_build_lifespan(resolved),
    )
    app.state.settings = resolved
    app.state.clock = clock or SystemClock()
    app.state.ids = ids or UuidFactory()
    app.state.cancellations = CancellationRegistry()
    app.state.browser_factory = _browser_factory(resolved)
    app.state.logger = logger

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration = (time.perf_counter() - started) * 1000
            logger.exception(
                "request failed",
                extra={
                    "request_id": request_id,
                    "path": request.url.path,
                    "method": request.method,
                    "duration_ms": round(duration, 2),
                },
            )
            raise
        duration = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request",
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method,
                "status_code": response.status_code,
                "duration_ms": round(duration, 2),
            },
        )
        return response

    from flowstate.errors import register_exception_handlers

    register_exception_handlers(app)

    prefix = resolved.api_prefix

    if resolved.environment.strip().lower() in {"development", "dev", "test", "local"}:
        # The controlled demo is intentionally public only in local modes so
        # the learning iframe can load without an API bearer token.
        app.include_router(demo_app_router)

    # Health is intentionally reachable without a token: it is the probe.
    # The router is mounted exactly once so OpenAPI has one operation per path.
    app.include_router(health.router, prefix=prefix)

    protected = [
        workflows.router,
        captures.router,
        runs.router,
        chaos.router,
        decisions.router,
        decisions.blocker_router,
        decisions.run_blocker_router,
        recovery.router,
        recovery.run_router,
        regression.router,
        feedback.router,
        audit.router,
        demo.router,
        system.router,
    ]
    for router in protected:
        app.include_router(router, prefix=prefix, dependencies=[Depends(require_auth)])

    @app.get("/", include_in_schema=False)
    def root() -> JSONResponse:
        return JSONResponse(
            {
                "service": resolved.app_name,
                "version": __version__,
                "api": prefix,
                "docs": "/docs",
                "health": f"{prefix}/health",
            }
        )

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    app.state.evidence_root = Path(resolved.data_dir) / "evidence"
    return app


app = create_app()
