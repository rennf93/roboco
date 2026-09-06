"""Branch creation atomicity.

When ``_ensure_branch_for_task`` raises (git checkout fails, push fails,
no token, etc.), ``_provision_claim`` must roll back the claim fields
``_apply_claim_fields`` durably committed: otherwise the task is left
CLAIMED with branch_name=NULL and the next claim attempt collides on a
non-idempotent ``git checkout -b``.

This test exercises the rollback path against a real Postgres session
by patching ``_ensure_branch_for_task`` to raise.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import (
    AgentTable,
    AuditLogTable,
    ProjectTable,
    TaskTable,
    WorkSessionTable,
)
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.services.task import TaskService
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def claim_setup(db_session: AsyncSession) -> AsyncIterator[dict[str, Any]]:
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

    project = ProjectTable(
        id=uuid4(),
        name="Atom Test",
        slug=f"atom-{uuid4().hex[:8]}",
        git_url="https://github.com/example/atom.git",
        default_branch="main",
        protected_branches=["main"],
        assigned_cell=Team.BACKEND,
        created_by=system_agent.id,
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()

    dev = AgentTable(
        id=uuid4(),
        name="BE Dev",
        slug=f"be-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=["python"],
        permissions={},
        metrics={},
    )
    db_session.add(dev)
    await db_session.flush()

    task = TaskTable(
        id=uuid4(),
        title="Task that will fail at branch creation",
        description="…",
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        team=Team.BACKEND,
        project_id=project.id,
        created_by=system_agent.id,
        assigned_to=dev.id,
        acceptance_criteria=["does the thing"],
        # No branch_name — claim path will try to create one.
    )
    db_session.add(task)
    await db_session.flush()
    yield {"task": task, "dev": dev, "project": project}


@pytest.mark.asyncio
async def test_finalize_claim_rolls_back_on_branch_failure(
    db_session: AsyncSession, claim_setup: dict[str, Any]
) -> None:
    """git failure during _ensure_branch_for_task must revert claim fields.

    Without rollback the task is left CLAIMED with branch_name=NULL and
    `git checkout -b` is non-idempotent on retry.
    """
    task = claim_setup["task"]
    dev = claim_setup["dev"]
    svc = TaskService(db_session)

    # Snapshot pre-claim state so we can assert exact rollback.
    pre_status = task.status
    pre_assigned = task.assigned_to
    pre_claimed_by = task.claimed_by
    pre_claimed_at = task.claimed_at
    pre_heartbeat = task.last_heartbeat_at
    pre_claimant = task.active_claimant_id

    async def boom(_self: Any, _task: Any, _agent_id: Any) -> str:
        raise RuntimeError("simulated: git checkout -b failed")

    with (
        patch.object(TaskService, "_ensure_branch_for_task", boom),
        pytest.raises(RuntimeError, match="git checkout -b failed"),
    ):
        await svc.claim(task.id, dev.id)

    # Re-read the task from a clean state via a fresh fetch.
    refreshed = await svc.get(task.id)
    assert refreshed is not None
    assert refreshed.status == pre_status, "status must roll back"
    assert refreshed.assigned_to == pre_assigned, "assigned_to must roll back"
    assert refreshed.claimed_by == pre_claimed_by, "claimed_by must roll back"
    assert refreshed.claimed_at == pre_claimed_at, "claimed_at must roll back"
    assert refreshed.last_heartbeat_at == pre_heartbeat, "heartbeat must roll back"
    assert refreshed.active_claimant_id == pre_claimant, (
        "active_claimant_id must roll back too"
    )

    # This test's own claim + revert did not leave the task itself in a
    # different state (asserted above), but the durability boundary makes
    # claim() commit twice on this failure path anyway: once for the
    # forward claim (_apply_claim_fields), once for the revert
    # (_provision_claim). Both are real commits, so the audit rows they
    # write (task.claimed, task.pending) survive db_session's
    # rollback-based teardown, same leak as
    # test_claim_commits_before_branch_creation below. Clean them up on the
    # same live session (no fresh connection needed here; this test never
    # claims durability across one).
    await db_session.execute(
        delete(AuditLogTable).where(AuditLogTable.target_id == task.id)
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_claim_commits_before_branch_creation(
    db_session: AsyncSession, claim_setup: dict[str, Any], _test_database_url: str
) -> None:
    """The claim write is durable before _ensure_branch_for_task's git I/O runs.

    Reproduces the 2026-09-05 NAS incident shape: a gateway-style timeout
    (CancelledError) mid-branch-creation must not undo the claim, since the
    durability boundary already committed it, releasing the row lock, so a
    re-claim attempt from another request no longer wedges on lock_timeout.
    Verified via a FRESH session/connection, proving the write reached
    Postgres and isn't just visible inside this test's own session.
    """
    task = claim_setup["task"]
    dev = claim_setup["dev"]
    svc = TaskService(db_session)

    async def cancelled(_self: Any, _task: Any, _agent_id: Any) -> str:
        raise asyncio.CancelledError()

    with (
        patch.object(TaskService, "_ensure_branch_for_task", cancelled),
        pytest.raises(asyncio.CancelledError),
    ):
        await svc.claim(task.id, dev.id)

    engine = create_async_engine(_test_database_url, future=True)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as fresh:
            refreshed = await fresh.get(TaskTable, task.id)
            assert refreshed is not None
            assert refreshed.status == TaskStatus.CLAIMED, (
                "claim must be durable across a fresh connection"
            )
            assert refreshed.claimed_by == dev.id
    finally:
        # The durability boundary commits the claim for real, so
        # db_session's rollback-based teardown never reaches these rows,
        # unlike claim_setup's other fixtures, which stay uncommitted and
        # roll back normally. Delete them explicitly (children before
        # parents) so the shared per-run test DB is left exactly as this
        # test found it: test_metrics_observability.py and
        # test_metrics_service.py reconstruct cycle time / team counts from
        # every row in this DB, and a leaked task or audit row skews both.
        async with factory() as cleanup:
            await cleanup.execute(
                delete(AuditLogTable).where(AuditLogTable.target_id == task.id)
            )
            await cleanup.execute(
                delete(WorkSessionTable).where(WorkSessionTable.task_id == task.id)
            )
            await cleanup.execute(delete(TaskTable).where(TaskTable.id == task.id))
            await cleanup.execute(
                delete(ProjectTable).where(ProjectTable.id == claim_setup["project"].id)
            )
            await cleanup.execute(
                delete(AgentTable).where(AgentTable.id.in_([dev.id, task.created_by]))
            )
            await cleanup.commit()
        await engine.dispose()
        await db_session.rollback()


async def _delete_durable_claim_rows(
    database_url: str, claim_setup: dict[str, Any]
) -> None:
    """Delete a task's durably-committed claim rows (children before
    parents), used by tests below whose claim landed for real and so
    outlives ``db_session``'s rollback-based teardown."""
    task = claim_setup["task"]
    dev = claim_setup["dev"]
    engine = create_async_engine(database_url, future=True)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as cleanup:
            await cleanup.execute(
                delete(AuditLogTable).where(AuditLogTable.target_id == task.id)
            )
            await cleanup.execute(
                delete(WorkSessionTable).where(WorkSessionTable.task_id == task.id)
            )
            await cleanup.execute(delete(TaskTable).where(TaskTable.id == task.id))
            await cleanup.execute(
                delete(ProjectTable).where(ProjectTable.id == claim_setup["project"].id)
            )
            await cleanup.execute(
                delete(AgentTable).where(AgentTable.id.in_([dev.id, task.created_by]))
            )
            await cleanup.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_branch_commit_survives_a_later_provisioning_failure(
    db_session: AsyncSession, claim_setup: dict[str, Any], _test_database_url: str
) -> None:
    """Finding A: a failure AFTER a successful branch create must not lose
    the branch commit.

    ``_ensure_branch_for_task`` succeeds and sets branch_name; a LATER step
    (``_create_work_session_if_needed``) then raises, and ``get_db()``'s
    exception handler (simulated here by rolling back this session) rolls
    back whatever it left pending. Before the Finding A fix, the branch's
    own flush was never committed on its own, so this rollback discarded
    branch_name while the earlier CLAIMED commit stood, landing
    ``status=CLAIMED, branch_name=NULL`` with the branch already pushed to
    origin. Verified on a FRESH connection, proving the branch commit
    reached Postgres and is not just visible inside this test's own
    session; the row must also not still be locked, since the claim
    already committed twice by this point.
    """
    task = claim_setup["task"]
    dev = claim_setup["dev"]
    svc = TaskService(db_session)

    async def set_branch(_self: Any, _task: Any, _agent_id: Any) -> str:
        branch_name = "feature/backend/finding-a"
        task.branch_name = branch_name
        await db_session.flush()
        return branch_name

    async def boom_work_session(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("simulated: work session creation failed")

    with (
        patch.object(TaskService, "_ensure_branch_for_task", set_branch),
        patch.object(TaskService, "_create_work_session_if_needed", boom_work_session),
        pytest.raises(RuntimeError, match="work session creation failed"),
    ):
        await svc.claim(task.id, dev.id)

    # Mirrors get_db()'s exception handler: an uncaught raise past the
    # route rolls this session back.
    await db_session.rollback()

    engine = create_async_engine(_test_database_url, future=True)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as fresh:
            refreshed = await fresh.get(TaskTable, task.id)
            assert refreshed is not None
            assert refreshed.status == TaskStatus.CLAIMED, (
                "claim must stay durable across the later failure"
            )
            assert refreshed.branch_name == "feature/backend/finding-a", (
                "the branch commit must survive a later provisioning failure"
            )
        async with factory() as locker:
            # FOR UPDATE NOWAIT only succeeds if no other transaction still
            # holds the row lock: proves both commits released it and this
            # rolled-back session isn't still holding it open.
            await locker.execute(
                text("SELECT 1 FROM tasks WHERE id = :tid FOR UPDATE NOWAIT"),
                {"tid": str(task.id)},
            )
            await locker.rollback()
    finally:
        await engine.dispose()

    await _delete_durable_claim_rows(_test_database_url, claim_setup)
