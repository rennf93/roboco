"""The release-proposal publish hook drafts an X post (best-effort, never
raises into approve()). Layering: release_proposal calls only the small typed
seam ``XEngine.draft_release_post`` — this test patches at that seam, not the
engine's internals."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.foundation import identity as _foundation
from roboco.models.base import AgentRole, AgentStatus, TaskNature, TaskStatus, TaskType
from roboco.models.base import Team as T
from roboco.services.release_executor import ReleaseResult
from roboco.services.release_proposal import ReleaseProposalService
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
        change_summary=["feat: a thing", "fix: another thing"],
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
async def test_publish_success_calls_x_engine_draft_seam(
    db_session: AsyncSession,
) -> None:
    task = await _seed_proposal(db_session)
    published = ReleaseResult(
        status="published",
        version=_VERSION,
        files_changed=["pyproject.toml"],
        commit_sha="abc123",
        release_url=f"https://github.com/x/roboco/releases/tag/v{_VERSION}",
        detail="ok",
    )
    fake_executor = AsyncMock()
    fake_executor.execute = AsyncMock(return_value=published)
    fake_engine = AsyncMock()
    fake_engine.draft_release_post = AsyncMock(return_value=None)

    with (
        patch(
            "roboco.services.release_proposal.get_release_executor",
            AsyncMock(return_value=fake_executor),
        ),
        patch("roboco.services.x_engine.get_x_engine", return_value=fake_engine),
        patch.object(
            ReleaseProposalService, "_acquire_release_lock", AsyncMock(return_value="t")
        ),
        patch.object(
            ReleaseProposalService,
            "_release_release_lock",
            AsyncMock(return_value=None),
        ),
        patch.object(
            ReleaseProposalService,
            "_heartbeat_release_lock",
            AsyncMock(return_value=True),
        ),
    ):
        result = await ReleaseProposalService(db_session).approve(cast("UUID", task.id))

    assert result is not None
    assert result.status == "published"
    fake_engine.draft_release_post.assert_awaited_once_with(
        version=_VERSION,
        highlights=["feat: a thing", "fix: another thing"],
        project_id=task.project_id,
    )


@pytest.mark.asyncio
async def test_x_draft_failure_never_fails_the_approve(
    db_session: AsyncSession,
) -> None:
    """A drafting exception is swallowed — the release already published."""
    task = await _seed_proposal(db_session)
    published = ReleaseResult(
        status="published",
        version=_VERSION,
        files_changed=["pyproject.toml"],
        commit_sha="abc123",
        release_url=None,
        detail="ok",
    )
    fake_executor = AsyncMock()
    fake_executor.execute = AsyncMock(return_value=published)

    with (
        patch(
            "roboco.services.release_proposal.get_release_executor",
            AsyncMock(return_value=fake_executor),
        ),
        patch(
            "roboco.services.x_engine.get_x_engine",
            side_effect=RuntimeError("x-engine boom"),
        ),
        patch.object(
            ReleaseProposalService, "_acquire_release_lock", AsyncMock(return_value="t")
        ),
        patch.object(
            ReleaseProposalService,
            "_release_release_lock",
            AsyncMock(return_value=None),
        ),
        patch.object(
            ReleaseProposalService,
            "_heartbeat_release_lock",
            AsyncMock(return_value=True),
        ),
    ):
        result = await ReleaseProposalService(db_session).approve(cast("UUID", task.id))

    assert result is not None
    assert result.status == "published"
    await db_session.refresh(task)
    assert task.status == TaskStatus.COMPLETED  # the release itself still completed
