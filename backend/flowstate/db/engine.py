"""Engine creation and reliable schema initialisation.

There is no ORM-level auto-migration in this MVP, so the schema carries an
explicit version. ``init_schema`` creates the tables on a fresh database and
stamps ``PRAGMA user_version``; a later migration step can branch on it. This is
intentionally explicit rather than ``create_all`` on every boot, which would
hide drift.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from flowstate.db.models import Base

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 8

# Additive migrations applied to a SQLite database created by an earlier build.
# ``Base.metadata.create_all`` does not alter existing tables, so these run after
# it and are guarded by an explicit column check.
_SQLITE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("workflow_versions", "input_schema", "JSON NOT NULL DEFAULT '{}'"),
    ("workflow_versions", "output_schema", "JSON NOT NULL DEFAULT '{}'"),
    ("workflow_versions", "redact_paths", "JSON NOT NULL DEFAULT '[]'"),
    ("action_definitions", "node_key", "VARCHAR(80) NOT NULL DEFAULT ''"),
    ("action_definitions", "node_type", "VARCHAR(32) NOT NULL DEFAULT 'ACTION'"),
    ("action_definitions", "depends_on", "JSON NOT NULL DEFAULT '[]'"),
    ("action_definitions", "channel", "VARCHAR(16) NOT NULL DEFAULT 'SIMULATED'"),
    ("action_definitions", "retry_policy", "JSON NOT NULL DEFAULT '{}'"),
    ("action_definitions", "failure_handler", "JSON NOT NULL DEFAULT '{}'"),
    ("action_definitions", "input_schema", "JSON NOT NULL DEFAULT '{}'"),
    ("action_definitions", "output_schema", "JSON NOT NULL DEFAULT '{}'"),
    ("action_definitions", "redact_paths", "JSON NOT NULL DEFAULT '[]'"),
    ("execution_runs", "inputs", "JSON NOT NULL DEFAULT '{}'"),
    ("execution_runs", "outputs", "JSON NOT NULL DEFAULT '{}'"),
    ("execution_events", "node_key", "VARCHAR(80)"),
    ("execution_runs", "capture_session_id", "VARCHAR(36)"),
    ("human_decisions", "node_key", "VARCHAR(80)"),
    ("human_decisions", "blocker_id", "VARCHAR(36)"),
    ("human_decisions", "kind", "VARCHAR(40)"),
    ("human_decisions", "required_role", "VARCHAR(32)"),
    ("human_decisions", "resume_conditions", "JSON NOT NULL DEFAULT '[]'"),
    ("human_decisions", "evidence_hash", "VARCHAR(64)"),
    ("human_decisions", "provided_input", "JSON NOT NULL DEFAULT '{}'"),
    ("human_decisions", "deferred_until", "DATETIME"),
    ("human_decisions", "decided_role", "VARCHAR(32)"),
    ("run_blockers", "evidence_spec", "JSON NOT NULL DEFAULT '[]'"),
    ("fault_scenarios", "input_fixtures", "JSON NOT NULL DEFAULT '{}'"),
    ("fault_scenarios", "baseline_run_id", "VARCHAR(36)"),
    ("fault_scenarios", "last_run_id", "VARCHAR(36)"),
)


def _sqlite_pragmas(dbapi_connection: Any, _: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def build_engine(database_url: str) -> Engine:
    kwargs: dict[str, Any] = {"future": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in database_url:
            kwargs["poolclass"] = StaticPool
        path = database_url.removeprefix("sqlite:///")
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url, **kwargs)
    if database_url.startswith("sqlite"):
        event.listen(engine, "connect", _sqlite_pragmas)
    return engine


def read_schema_version(engine: Engine) -> int:
    if engine.dialect.name != "sqlite":
        return SCHEMA_VERSION
    with engine.connect() as conn:
        return int(conn.exec_driver_sql("PRAGMA user_version").scalar() or 0)


def _sqlite_column_names(conn: Any, table: str) -> set[str]:
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def migrate_sqlite(engine: Engine) -> None:
    """Add columns introduced by later schema versions, in place.

    The migration is idempotent and safe to run on a fresh database (where the
    columns already exist because ``create_all`` built the current model).
    """
    with engine.begin() as conn:
        for table, column, ddl in _SQLITE_COLUMNS:
            existing = _sqlite_column_names(conn, table)
            if not existing:
                continue  # table does not exist yet; create_all will build it
            if column in existing:
                continue
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            logger.info("schema column added", extra={"path": f"{table}.{column}"})

        # Backfill stable node keys for versions created by the pre-graph build.
        conn.exec_driver_sql(
            "UPDATE action_definitions SET node_key = 'n' || ordinal "
            "WHERE node_key IS NULL OR node_key = ''"
        )
        conn.exec_driver_sql(
            "UPDATE action_definitions SET node_type = 'ACTION' "
            "WHERE node_type IS NULL OR node_type = ''"
        )
        conn.exec_driver_sql(
            "UPDATE workflow_versions SET redact_paths = '[]' WHERE redact_paths IS NULL"
        )
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_action_node_key_index "
            "ON action_definitions (workflow_version_id, node_key)"
        )


def init_schema(engine: Engine) -> int:
    """Create tables if needed, migrate additively, and return the schema version."""
    Base.metadata.create_all(engine)
    current = read_schema_version(engine)
    if current < SCHEMA_VERSION:
        if engine.dialect.name == "sqlite":
            migrate_sqlite(engine)
            with engine.begin() as conn:
                conn.exec_driver_sql(f"PRAGMA user_version = {SCHEMA_VERSION}")
        logger.info("schema initialized", extra={"status_code": SCHEMA_VERSION})
        return SCHEMA_VERSION
    return current


class Database:
    """Small holder so the app can expose one engine and one session factory."""

    def __init__(self, database_url: str) -> None:
        self.url = database_url
        self.engine = build_engine(database_url)
        self.session_factory: sessionmaker[Session] = sessionmaker(
            bind=self.engine, expire_on_commit=False, autoflush=False
        )

    def init(self) -> int:
        return init_schema(self.engine)

    def healthy(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            logger.exception("database health check failed")
            return False
