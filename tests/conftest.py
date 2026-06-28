"""Top-level test fixtures.

Provides a Postgres-backed `db_session` and a synthetic `smoke_test_batch`
fixture that seeds the minimum DB state needed by the Phase 4 tracing-
completeness property test.

Why Postgres and not SQLite:
    The production schema uses Postgres-only types — `UUID(as_uuid=True)`,
    `ARRAY(String)`, `ARRAY(UUID(as_uuid=True))` — across many tables. SQLite
    cannot compile those via `Base.metadata.create_all`, and migration
    006_gateway_columns adds `acceptance_criteria_status` /
    `qa_evidence_inspected` as JSON+Boolean columns that the current ORM
    model in `roboco/db/tables.py` does NOT yet map (a pre-existing layer
    drift). The honest path is to spin up a real Postgres test database,
    build the schema via `Base.metadata.create_all` (sidestepping pre-existing
    alembic drift in 001's enum casing and 008's nonexistent `agents.skills`
    column), then apply the additive columns from migration 006 manually so
    the test reads the same DB-level contract that the live services do.

Behaviour:
    Tests that request `db_session` or `smoke_test_batch` are skipped at
    collection time when no Postgres is reachable on `localhost:5432`. Set
    `ROBOCO_TEST_DB_HOST`, `ROBOCO_TEST_DB_PORT`, `ROBOCO_TEST_DB_USER`, or
    `ROBOCO_TEST_DB_PASSWORD` to override.
"""

from __future__ import annotations

import json
import os
import socket
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio
from roboco.db import tables as roboco_tables
from roboco.db.base import Base
from roboco.db.tables import (
    AgentTable,
    AuditLogTable,
    JournalEntryTable,
    JournalTable,
    ProjectTable,
    TaskTable,
)
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    Complexity,
    JournalEntryType,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


# ---------------------------------------------------------------------------
# Test DB endpoint discovery — env-overridable.
#
# Defaults match the project's own running postgres (`roboco-postgres` in
# `docker-compose.yml`): superuser `roboco`/`roboco`, host-exposed on
# `localhost:15432` (the container's 5432). `roboco` has CREATEDB, which the
# session fixture needs to provision/drop an ephemeral per-run test DB.
#
# Previously these defaulted to the OS `$USER` with an empty password on
# `localhost:5432`, which hit a bare system postgres that has no such role —
# every `db_session` test failed with `InvalidPasswordError: password
# authentication failed for user "renzof"` instead of running. Defaulting to
# the project's actual DB makes the integration suite run out of the box; any
# of these can still be overridden with `ROBOCO_TEST_DB_*`.
# ---------------------------------------------------------------------------
_TEST_DB_HOST = os.environ.get("ROBOCO_TEST_DB_HOST", "localhost")
_TEST_DB_PORT = int(os.environ.get("ROBOCO_TEST_DB_PORT", "15432"))
_TEST_DB_USER = os.environ.get("ROBOCO_TEST_DB_USER", "roboco")
_TEST_DB_PASSWORD = os.environ.get("ROBOCO_TEST_DB_PASSWORD", "roboco")
_TEST_DB_ADMIN_DB = os.environ.get("ROBOCO_TEST_DB_ADMIN_DB", "postgres")


def _postgres_reachable() -> bool:
    """Check that we can open a TCP connection to the configured Postgres."""
    try:
        with socket.create_connection((_TEST_DB_HOST, _TEST_DB_PORT), timeout=1.0):
            return True
    except OSError:
        return False


_PG_AVAILABLE = _postgres_reachable()

# Sanity-check imported tables registered themselves on Base.metadata. Tied to
# `roboco_tables` so static analysis treats the import as load-bearing.
if not hasattr(roboco_tables, "TaskTable"):
    raise RuntimeError("roboco.db.tables failed to register TaskTable on Base")


def _build_url(database: str) -> str:
    auth = _TEST_DB_USER
    if _TEST_DB_PASSWORD:
        auth = f"{_TEST_DB_USER}:{_TEST_DB_PASSWORD}"
    return f"postgresql+asyncpg://{auth}@{_TEST_DB_HOST}:{_TEST_DB_PORT}/{database}"


# ---------------------------------------------------------------------------
# Session-scoped: provision an ephemeral test DB and build the schema.
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _test_database_url() -> AsyncIterator[str]:
    """Create a fresh `roboco_test_<pid>_<rand>` DB, build the schema, yield URL.

    Drops the database on teardown. Skips the entire test session if Postgres
    is unreachable.
    """
    if not _PG_AVAILABLE:
        pytest.skip(
            f"Postgres unreachable at {_TEST_DB_HOST}:{_TEST_DB_PORT} — "
            "set ROBOCO_TEST_DB_HOST/PORT/USER/PASSWORD or start Postgres",
            allow_module_level=False,
        )

    db_name = f"roboco_test_{os.getpid()}_{uuid4().hex[:8]}"

    # 1. CREATE DATABASE on the admin connection.
    admin = await asyncpg.connect(
        host=_TEST_DB_HOST,
        port=_TEST_DB_PORT,
        user=_TEST_DB_USER,
        password=_TEST_DB_PASSWORD or None,
        database=_TEST_DB_ADMIN_DB,
    )
    try:
        await admin.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await admin.close()

    test_url_async = _build_url(db_name)

    # 2. Enable pgvector extension (some tables created via create_all reference
    #    it; harmless if already enabled in the cluster).
    pgvector_engine = create_async_engine(test_url_async, future=True)
    try:
        async with pgvector_engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    finally:
        await pgvector_engine.dispose()

    # 3. Build the schema. We use `Base.metadata.create_all` (NOT alembic
    #    upgrade head) for two reasons:
    #
    #    a. Migration 001 declares the `agentrole` Postgres enum with
    #       lowercase string values (`"qa"`, `"developer"`, ...), but the
    #       SQLAlchemy ORM binds `Enum(AgentRole)` to the StrEnum's member
    #       NAMES (uppercase: `"QA"`, `"DEVELOPER"`, ...) — they're
    #       mismatched. Production DBs were bootstrapped via `create_all`
    #       (which generates uppercase enums to match the ORM) and then
    #       stamped at 001, masking this drift.
    #
    #    b. Migration 008 runs `UPDATE agents SET skills = ... WHERE id = ...`
    #       against an `agents.skills` column that no migration in this chain
    #       ever creates — another pre-existing drift item.
    #
    #    Neither bug is caused by this PR; both block any "fresh alembic
    #    upgrade head" run today. `create_all` sidesteps both. After
    #    `create_all` we manually apply the *additive* columns from migration
    #    006 (`acceptance_criteria_status`, `qa_evidence_inspected`) since
    #    those are the columns the tracing-completeness contract reads and
    #    they are NOT yet on the ORM TaskTable.
    schema_engine = create_async_engine(test_url_async, future=True)
    try:
        async with schema_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text(
                    "ALTER TABLE tasks "
                    "ADD COLUMN IF NOT EXISTS acceptance_criteria_status "
                    "JSON NOT NULL DEFAULT '[]'::json"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE tasks "
                    "ADD COLUMN IF NOT EXISTS qa_evidence_inspected "
                    "BOOLEAN NOT NULL DEFAULT FALSE"
                )
            )
    finally:
        await schema_engine.dispose()

    try:
        yield test_url_async
    finally:
        # 4. Drop the test database on teardown.
        teardown_admin = await asyncpg.connect(
            host=_TEST_DB_HOST,
            port=_TEST_DB_PORT,
            user=_TEST_DB_USER,
            password=_TEST_DB_PASSWORD or None,
            database=_TEST_DB_ADMIN_DB,
        )
        try:
            # Force-disconnect any straggler sessions before DROP.
            await teardown_admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{db_name}' AND pid <> pg_backend_pid()"
            )
            await teardown_admin.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        finally:
            await teardown_admin.close()


@pytest_asyncio.fixture
async def db_session(_test_database_url: str) -> AsyncIterator[AsyncSession]:
    """Yield an `AsyncSession` bound to the migrated test DB.

    Each test gets its own session and connection; teardown rolls back any
    uncommitted state and disposes the engine to keep connection counts low.
    """
    # No pool_pre_ping: a fresh per-test engine can't have stale connections, and
    # pre-ping on asyncpg leaves an un-awaited Connection._cancel coroutine that
    # surfaces as a RuntimeWarning during GC.
    engine = create_async_engine(_test_database_url, future=True)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


# ---------------------------------------------------------------------------
# Synthetic seed: 1 project + 3 agents + 1 completed task with full tracing.
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def smoke_test_batch(db_session: AsyncSession) -> AsyncIterator[list[UUID]]:
    """Seed a single completed task that satisfies all 6 tracing-contract bullets.

    Returns the list of completed task IDs (length 1). The test iterates this
    list and asserts the contract on each — the same shape Phase 4 Task 12's
    real smoke flow will produce on the NAS.

    Seeded rows:
        * 1 ProjectTable (assigned_cell=BACKEND)
        * 1 system AgentTable (creator-of-record)
        * 3 AgentTables: dev (DEVELOPER), qa (QA), pm (CELL_PM) — all BACKEND
        * 1 TaskTable: status=COMPLETED, with 2 acceptance_criteria
        * 3 AuditLogTable rows: dev claim, qa pass, pm complete (target_id=task.id)
        * 3 JournalTables (one per dev/qa/pm) + 3 JournalEntryTable rows:
            - dev: TASK_REFLECTION
            - qa:  LEARNING
            - pm:  DECISION_LOG
        * Raw-SQL UPDATE on `tasks.acceptance_criteria_status` and
          `tasks.qa_evidence_inspected` (these columns exist in the migrated DB
          but are NOT mapped on the ORM TaskTable — pre-existing drift).
    """
    # 1. Creator-of-record system agent (FK target for project.created_by etc.).
    system_agent = AgentTable(
        id=uuid4(),
        name="System",
        slug=f"system-{uuid4().hex[:8]}",
        role=AgentRole.SYSTEM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="system",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(system_agent)
    await db_session.flush()

    # 2. Project.
    project = ProjectTable(
        id=uuid4(),
        name="Smoke Test Project",
        slug=f"smoke-{uuid4().hex[:8]}",
        git_url="https://github.com/example/smoke.git",
        default_branch="main",
        protected_branches=["main"],
        assigned_cell=Team.BACKEND,
        created_by=system_agent.id,
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()

    # 3. Three workforce agents — dev, qa, cell_pm — all on the backend cell.
    dev_agent = AgentTable(
        id=uuid4(),
        name="Backend Dev 1",
        slug=f"be-dev-1-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=["python"],
        permissions={},
        metrics={},
    )
    qa_agent = AgentTable(
        id=uuid4(),
        name="Backend QA",
        slug=f"be-qa-{uuid4().hex[:8]}",
        role=AgentRole.QA,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="qa",
        capabilities=["review"],
        permissions={},
        metrics={},
    )
    pm_agent = AgentTable(
        id=uuid4(),
        name="Backend Cell PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=["review"],
        permissions={},
        metrics={},
    )
    db_session.add_all([dev_agent, qa_agent, pm_agent])
    await db_session.flush()

    # 4. Task — status=COMPLETED, with two acceptance criteria.
    criteria = [
        "API endpoint returns 200 for the happy path",
        "Failure mode returns structured error envelope",
    ]
    task = TaskTable(
        id=uuid4(),
        title="Add /api/v1/example endpoint",
        description="Smoke task — synthetic happy path.",
        acceptance_criteria=criteria,
        status=TaskStatus.COMPLETED,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=project.id,
        branch_name="feature/backend/SMOKE001",
        pr_number=1,
        pr_url="https://github.com/example/smoke/pull/1",
        docs_complete=True,
        pr_created=True,
        created_by=system_agent.id,
        assigned_to=dev_agent.id,
        team=Team.BACKEND,
        dependency_ids=[],
        blocker_ids=[],
        sequence=0,
        plan={"steps": ["draft", "implement", "test"]},
        estimated_complexity=Complexity.MEDIUM,
        checkpoints=[],
        progress_updates=[{"at": "t0", "note": "started"}],
        commits=[{"sha": "abc123", "message": "[SMOKE001] initial"}],
        documents=[],
        dev_notes="Built as planned.",
        qa_notes=(
            "Reviewed PR #1 carefully. Branch convention correct. Commit "
            "message includes task ID. Diff matches spec. No security issues."
        ),
        self_verified=True,
        qa_verified=True,
    )
    db_session.add(task)
    await db_session.flush()

    # 4b. Migration-only columns the ORM doesn't map: acceptance_criteria_status
    #     + qa_evidence_inspected. Set them via raw SQL so the test asserts the
    #     real DB-level contract.
    artifact_id = str(uuid4())
    status_payload: list[dict[str, Any]] = [
        {"criterion": criteria[0], "referencing_artifact_id": artifact_id},
        {"criterion": criteria[1], "referencing_artifact_id": artifact_id},
    ]
    await db_session.execute(
        text(
            "UPDATE tasks "
            "SET acceptance_criteria_status = CAST(:status AS json), "
            "    qa_evidence_inspected = TRUE "
            "WHERE id = :tid"
        ),
        {"status": json.dumps(status_payload), "tid": task.id},
    )

    # 5. Three audit-log rows — one per state transition, ALL with non-null
    #    agent_id targeting this task's id.
    audit_rows = [
        AuditLogTable(
            id=uuid4(),
            event_type="task.claimed",
            agent_id=dev_agent.id,
            target_type="task",
            target_id=task.id,
            severity="info",
            details={"from": "pending", "to": "claimed"},
        ),
        AuditLogTable(
            id=uuid4(),
            event_type="task.qa_pass",
            agent_id=qa_agent.id,
            target_type="task",
            target_id=task.id,
            severity="info",
            details={"from": "awaiting_qa", "to": "awaiting_documentation"},
        ),
        AuditLogTable(
            id=uuid4(),
            event_type="task.completed",
            agent_id=pm_agent.id,
            target_type="task",
            target_id=task.id,
            severity="info",
            details={"from": "awaiting_pm_review", "to": "completed"},
        ),
    ]
    db_session.add_all(audit_rows)
    await db_session.flush()

    # 6. Journals + entries: dev:TASK_REFLECTION, qa:LEARNING, pm:DECISION_LOG.
    dev_journal = JournalTable(id=uuid4(), agent_id=dev_agent.id)
    qa_journal = JournalTable(id=uuid4(), agent_id=qa_agent.id)
    pm_journal = JournalTable(id=uuid4(), agent_id=pm_agent.id)
    db_session.add_all([dev_journal, qa_journal, pm_journal])
    await db_session.flush()

    db_session.add_all(
        [
            JournalEntryTable(
                id=uuid4(),
                journal_id=dev_journal.id,
                type=JournalEntryType.TASK_REFLECTION,
                title="Reflection on SMOKE001",
                content="Implemented as planned; happy-path commits clean.",
                task_id=task.id,
                tags=["reflect"],
            ),
            JournalEntryTable(
                id=uuid4(),
                journal_id=qa_journal.id,
                type=JournalEntryType.LEARNING,
                title="Learning from SMOKE001 review",
                content="QA: confirmed acceptance criteria addressed by artifact.",
                task_id=task.id,
                tags=["learning"],
            ),
            JournalEntryTable(
                id=uuid4(),
                journal_id=pm_journal.id,
                type=JournalEntryType.DECISION_LOG,
                title="Decision: merge SMOKE001",
                content="PM approval recorded; PR merged into target branch.",
                task_id=task.id,
                tags=["decision"],
            ),
        ]
    )
    await db_session.commit()

    # `TaskTable.id` is annotated `Mapped[UUID]` where `UUID` is the SA dialect
    # UUID type (not `uuid.UUID`) — a pre-existing typing oversight in the ORM.
    # The runtime value IS a `uuid.UUID`; convert through `str()` so callers
    # (and mypy) see the right type without smuggling in a type-ignore comment.
    seeded_task_ids: list[UUID] = [UUID(str(task.id))]
    yield seeded_task_ids


# ---------------------------------------------------------------------------
# Skip Postgres-dependent tests when the test DB endpoint is unreachable.
# ---------------------------------------------------------------------------
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Mark Postgres-dependent tests as skipped if Postgres is unreachable.

    Tests that do NOT request `db_session` or `smoke_test_batch` are unaffected.
    """
    _ = config
    if _PG_AVAILABLE:
        return
    skip = pytest.mark.skip(
        reason=(
            f"Postgres unreachable at {_TEST_DB_HOST}:{_TEST_DB_PORT} — "
            "set ROBOCO_TEST_DB_HOST/PORT/USER/PASSWORD or start Postgres"
        )
    )
    fixtures_requiring_db = {"db_session", "smoke_test_batch"}
    for item in items:
        fixturenames = set(getattr(item, "fixturenames", ()))
        if fixturenames & fixtures_requiring_db:
            item.add_marker(skip)
