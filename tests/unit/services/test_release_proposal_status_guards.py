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

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.foundation import identity as _foundation
from roboco.models.base import AgentRole, AgentStatus, TaskNature, TaskStatus, TaskType
from roboco.models.base import Team as T
from roboco.services.release_proposal import (
    ReleaseProposalService,
    TaskAlreadyCompletedError,
)
from roboco.services.release_readiness import ReleaseReadinessReport, report_to_dict
from roboco.services.task import RELEASE_MANAGER_SOURCE

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

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
