"""``approve()``/``reject()`` terminal-status guards.

The confirmed HIGH-severity bug: ``reject()`` sets a proposal CANCELLED, but
(pre-fix) ``approve()`` checked nothing — a stale Approve (e.g. a Telegram
button clicked after a reject) re-ran the fail-closed executor over a
rejected proposal. This mirrors the analogous DB-backed regression tests in
``test_x_post_service.py`` / ``test_video_post_service.py``, patching only
the executor seam (``get_release_executor``) exactly like the other
release-proposal hook test files (``test_release_proposal_x_hook.py`` et al.)
do for their own seams.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import AgentRole, AgentStatus, TaskNature, TaskStatus, TaskType
from roboco.models.base import Team as T
from roboco.services.release_proposal import (
    ReleaseProposalService,
    TaskAlreadyCompletedError,
)
from roboco.services.release_readiness import ReleaseReadinessReport, report_to_dict
from roboco.services.task import RELEASE_MANAGER_SOURCE, TaskService
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

if TYPE_CHECKING:
    from uuid import UUID

_VERSION = "0.18.0"


def _report() -> ReleaseReadinessReport:
    return ReleaseReadinessReport(
        proposed_version=_VERSION,
        bump_kind="minor",
        change_summary=["feat: a thing"],
        drafted_changelog=f"## [{_VERSION}]\n\n### Added\n- a thing\n",
        version_bump_plan=["pyproject.toml"],
        gaps=[],
        migration_notes=[],
        gate_state="green",
    )


async def _seed_proposal(session: AsyncSession) -> TaskTable:
    system_uuid = _foundation.AGENTS["system"].uuid
    secretary_uuid = _foundation.AGENTS["secretary-1"].uuid
    for uuid_, slug, role in (
        (system_uuid, "system", AgentRole.SYSTEM),
        (secretary_uuid, "secretary-1", AgentRole.SECRETARY),
    ):
        if await session.get(AgentTable, uuid_) is None:
            session.add(
                AgentTable(
                    id=uuid_,
                    name=slug,
                    slug=slug,
                    role=role,
                    team=None,
                    status=AgentStatus.ACTIVE,
                    model_config={},
                    system_prompt="x",
                    capabilities=[],
                    permissions={},
                    metrics={},
                )
            )
    await session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="RoboCo",
        slug=f"roboco-{uuid4().hex[:6]}",
        git_url="https://example.com/roboco.git",
        assigned_cell=T.BACKEND,
        created_by=system_uuid,
    )
    session.add(project)
    await session.flush()
    task = TaskTable(
        id=uuid4(),
        title=f"Release proposal: v{_VERSION}",
        description="proposal body",
        acceptance_criteria=["CEO approves"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.ADMINISTRATIVE,
        nature=TaskNature.NON_TECHNICAL,
        project_id=project.id,
        created_by=system_uuid,
        assigned_to=secretary_uuid,
        team=T.MAIN_PM,
        source=RELEASE_MANAGER_SOURCE,
        confirmed_by_human=False,
        orchestration_markers={"release_report": report_to_dict(_report())},
    )
    session.add(task)
    await session.flush()
    return task


@pytest.mark.asyncio
async def test_approve_refuses_already_rejected_proposal(
    db_session: AsyncSession,
) -> None:
    """The chokepoint guard: approving a CANCELLED (already-rejected)
    proposal refuses and never invokes the executor — the reproduced bug."""
    task = await _seed_proposal(db_session)
    task.status = TaskStatus.CANCELLED
    await db_session.flush()
    fake_executor = AsyncMock()
    fake_executor.execute = AsyncMock()

    with patch(
        "roboco.services.release_proposal.get_release_executor",
        AsyncMock(return_value=fake_executor),
    ):
        result = await ReleaseProposalService(db_session).approve(cast("UUID", task.id))

    assert result is not None
    assert result.status == "already_rejected"
    fake_executor.execute.assert_not_awaited()
    await db_session.refresh(task)
    assert task.status == TaskStatus.CANCELLED  # untouched, never re-run


@pytest.mark.asyncio
async def test_approve_refuses_already_published_proposal(
    db_session: AsyncSession,
) -> None:
    """The residual: a stale Approve on an already-COMPLETED (published)
    proposal must refuse without re-entering the executor (which could
    re-fire the post-publish draft hooks)."""
    task = await _seed_proposal(db_session)
    task.status = TaskStatus.COMPLETED
    await db_session.flush()
    fake_executor = AsyncMock()
    fake_executor.execute = AsyncMock()

    with patch(
        "roboco.services.release_proposal.get_release_executor",
        AsyncMock(return_value=fake_executor),
    ):
        result = await ReleaseProposalService(db_session).approve(cast("UUID", task.id))

    assert result is not None
    assert result.status == "already_published"
    fake_executor.execute.assert_not_awaited()
    await db_session.refresh(task)
    assert task.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_reject_raises_when_already_published(db_session: AsyncSession) -> None:
    """The mirror hole: rejecting an already-COMPLETED (published) proposal
    must refuse — cancelling it would lie about an already-public release."""
    task = await _seed_proposal(db_session)
    task.status = TaskStatus.COMPLETED
    await db_session.flush()

    with pytest.raises(TaskAlreadyCompletedError):
        await ReleaseProposalService(db_session).reject(
            cast("UUID", task.id), "needs another migration check"
        )
    await db_session.refresh(task)
    assert task.status == TaskStatus.COMPLETED  # untouched, never cancelled


async def _fresh_session(url: str) -> tuple[AsyncSession, AsyncEngine]:
    """A session on a brand-new engine/connection (caller disposes)."""
    engine = create_async_engine(url, future=True)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    return factory(), engine


async def _dispose(session: AsyncSession, engine: AsyncEngine) -> None:
    with contextlib.suppress(Exception):
        await session.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_reject_concurrent_approve_completes_during_lock_wait(
    db_session: AsyncSession, _test_database_url: str
) -> None:
    """Redis mutex pre-lock write audit regression for ``reject()``: a
    genuinely concurrent approve (a real second session/connection) publishes
    + commits COMPLETED in the window between reject's pre-lock read and its
    lock acquisition. The in-lock re-read must see that committed state and
    refuse — the CANCELLED status write and required-changes marker must
    never land on the just-published row, proving the fix holds across
    sessions, not merely within one. Mirrors
    ``test_x_post_service.test_reject_concurrent_approve_completes_during_lock_wait``.
    """
    task = await _seed_proposal(db_session)
    task_id = cast("UUID", task.id)
    await db_session.commit()

    real_get = TaskService.get
    injected = False

    async def _get_then_inject_concurrent_publish(
        self: TaskService, tid: UUID
    ) -> TaskTable | None:
        """Fires once, right after reject's pre-lock read — the exact window
        between that read and reject's own (would-be) pre-lock write."""
        nonlocal injected
        result = await real_get(self, tid)
        if not injected:
            injected = True
            other, other_engine = await _fresh_session(_test_database_url)
            try:
                other_task = await other.get(TaskTable, tid)
                assert other_task is not None
                other_task.status = TaskStatus.COMPLETED
                await other.commit()
            finally:
                await _dispose(other, other_engine)
        return result

    with (
        patch.object(TaskService, "get", _get_then_inject_concurrent_publish),
        patch.object(
            ReleaseProposalService,
            "_acquire_release_lock",
            AsyncMock(return_value="tok"),
        ),
        patch.object(
            ReleaseProposalService,
            "_release_release_lock",
            AsyncMock(return_value=None),
        ),
        patch.object(
            ReleaseProposalService, "_close_redis", AsyncMock(return_value=None)
        ),
        pytest.raises(TaskAlreadyCompletedError),
    ):
        await ReleaseProposalService(db_session).reject(
            task_id, "needs another migration check"
        )

    fresh, fresh_engine = await _fresh_session(_test_database_url)
    try:
        final = await fresh.get(TaskTable, task_id)
        assert final is not None
        assert final.status == TaskStatus.COMPLETED
        # The reject must never have landed on the just-published row.
        assert markers.get_release_required_changes(final) is None
    finally:
        await _dispose(fresh, fresh_engine)
