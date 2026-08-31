"""Docs provenance-flip trigger: fire on completion, guarded by the root chain.

A doc written mid-task via roboco_docs_write is stamped
``provenance="live_write"`` + the writing task's id. TaskService's completion
hook must flip those chunks back to ``repo_tree`` — but ONLY once the
writing task's ROOT ancestor is terminal-completed: a leaf or cell task
completing while the root is still in flight must NOT flip (the caveat
stays until the whole root ships to master).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from roboco.db.tables import ProjectTable, TaskTable
from roboco.models import Team
from roboco.models.base import TaskNature, TaskStatus, TaskType
from roboco.services.task import TaskService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_chain(
    db_session: AsyncSession,
    *,
    root_status: TaskStatus,
    child_status: TaskStatus,
) -> tuple[TaskTable, TaskTable]:
    """Seed a two-task chain (root ← child) with the given statuses."""
    project = ProjectTable(
        id=uuid4(),
        name=f"flip-proj-{uuid4().hex[:8]}",
        slug=f"flip-proj-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=uuid4(),
    )
    db_session.add(project)
    await db_session.flush()

    root = TaskTable(
        id=uuid4(),
        title="root",
        description="d",
        acceptance_criteria=["ac"],
        status=root_status,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=project.id,
        created_by=project.created_by,
        team=Team.BACKEND,
    )
    db_session.add(root)
    await db_session.flush()

    child = TaskTable(
        id=uuid4(),
        title="child",
        description="d",
        acceptance_criteria=["ac"],
        status=child_status,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=project.id,
        created_by=project.created_by,
        team=Team.BACKEND,
        parent_task_id=root.id,
    )
    db_session.add(child)
    await db_session.flush()
    return root, child


async def _drain_added_background_tasks(before: set) -> None:
    """Await the background tasks _trigger_completion_hooks just spawned, so
    the fire-and-forget flip is deterministically observable."""
    await asyncio.gather(*(TaskService._background_tasks - before))


@pytest.mark.asyncio
async def test_flip_fires_when_root_chain_terminal_completed(
    db_session: AsyncSession,
) -> None:
    """doc → root chain terminal-completed → provenance flips. The hook
    resolves the completed root inline and fires the flip with the WHOLE
    subtree's ids — no restart, no manual reindex."""
    root, child = await _seed_chain(
        db_session,
        root_status=TaskStatus.COMPLETED,
        child_status=TaskStatus.AWAITING_PM_REVIEW,
    )
    # The child's own completion is what now exhausts the root chain.
    child.status = TaskStatus.COMPLETED
    await db_session.flush()

    svc = TaskService(db_session)
    mock_optimal = AsyncMock()
    mock_optimal.flip_docs_task_provenance = AsyncMock(return_value=3)
    before = set(TaskService._background_tasks)
    with (
        patch(
            "roboco.services.optimal.get_optimal_service",
            AsyncMock(return_value=mock_optimal),
        ),
        patch.object(svc, "_extract_completion_learnings", AsyncMock()),
    ):
        await svc._trigger_completion_hooks(child, None)
    await _drain_added_background_tasks(before)

    mock_optimal.flip_docs_task_provenance.assert_awaited_once_with(
        sorted([str(root.id), str(child.id)])
    )


@pytest.mark.asyncio
async def test_no_flip_while_root_chain_non_terminal(
    db_session: AsyncSession,
) -> None:
    """The race guard: the completing leaf's root ancestor is still in
    flight — no flip, the caveat outlives the leaf completion."""
    _root, child = await _seed_chain(
        db_session,
        root_status=TaskStatus.AWAITING_PM_REVIEW,
        child_status=TaskStatus.AWAITING_PM_REVIEW,
    )
    child.status = TaskStatus.COMPLETED
    await db_session.flush()

    svc = TaskService(db_session)
    mock_optimal = AsyncMock()
    mock_optimal.flip_docs_task_provenance = AsyncMock(return_value=3)
    before = set(TaskService._background_tasks)
    with (
        patch(
            "roboco.services.optimal.get_optimal_service",
            AsyncMock(return_value=mock_optimal),
        ),
        patch.object(svc, "_extract_completion_learnings", AsyncMock()),
    ):
        await svc._trigger_completion_hooks(child, None)
    await _drain_added_background_tasks(before)

    mock_optimal.flip_docs_task_provenance.assert_not_awaited()
    assert await svc._completed_root_subtree_ids(child) is None


@pytest.mark.asyncio
async def test_parentless_task_flips_itself_on_completion(
    db_session: AsyncSession,
) -> None:
    """A parentless task IS its own root: completing it completes the chain."""
    project = ProjectTable(
        id=uuid4(),
        name=f"flip-proj-{uuid4().hex[:8]}",
        slug=f"flip-proj-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=uuid4(),
    )
    db_session.add(project)
    await db_session.flush()
    task = TaskTable(
        id=uuid4(),
        title="solo",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.COMPLETED,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=project.id,
        created_by=project.created_by,
        team=Team.BACKEND,
    )
    db_session.add(task)
    await db_session.flush()

    svc = TaskService(db_session)
    assert await svc._completed_root_subtree_ids(task) == [str(task.id)]


@pytest.mark.asyncio
async def test_flip_failure_is_swallowed_and_logged(
    db_session: AsyncSession,
) -> None:
    """Best-effort (mirrors DocsService._index_doc_in_rag): a KB/store
    failure during the flip is a logged warning, never an exception —
    the completing task's transition must never fail on the flip."""
    _root, child = await _seed_chain(
        db_session,
        root_status=TaskStatus.COMPLETED,
        child_status=TaskStatus.COMPLETED,
    )
    svc = TaskService(db_session)
    with patch(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(side_effect=RuntimeError("kb down")),
    ):
        # No exception raised.
        await svc._flip_docs_provenance_background([str(child.id)])


@pytest.mark.asyncio
async def test_subtree_guard_failure_never_breaks_completion_hooks(
    db_session: AsyncSession,
) -> None:
    """A guard-resolution failure (bad session, corrupt chain) is
    best-effort: the hook completes with the flip simply skipped."""
    _root, child = await _seed_chain(
        db_session,
        root_status=TaskStatus.COMPLETED,
        child_status=TaskStatus.COMPLETED,
    )
    svc = TaskService(db_session)
    with (
        patch.object(
            svc,
            "_completed_root_subtree_ids",
            AsyncMock(side_effect=RuntimeError("corrupt chain")),
        ),
        patch.object(svc, "_extract_completion_learnings", AsyncMock()),
    ):
        # No exception raised.
        await svc._trigger_completion_hooks(child, None)
