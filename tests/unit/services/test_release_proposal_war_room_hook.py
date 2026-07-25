"""The release-proposal publish hook originates a War Room campaign-planning
cycle (best-effort, never raises into approve()). Layering: release_proposal
calls only the small typed seam ``WarRoomEngine.open_for_release`` — this
test patches at that seam, not the engine's internals. Mirrors
test_release_proposal_docs_sync_hook.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock, patch
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

_VERSION = "0.30.0"


def _report() -> ReleaseReadinessReport:
    return ReleaseReadinessReport(
        proposed_version=_VERSION,
        bump_kind="minor",
        change_summary=["feat: war room engine", "fix: typos"],
        drafted_changelog=f"## [{_VERSION}]\n\n### Added\n- war room engine\n",
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
async def test_publish_success_calls_war_room_seam(db_session: AsyncSession) -> None:
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
    fake_engine.open_for_release = AsyncMock(return_value=None)

    with (
        patch(
            "roboco.services.release_proposal.get_release_executor",
            AsyncMock(return_value=fake_executor),
        ),
        patch(
            "roboco.services.war_room_engine.get_war_room_engine",
            return_value=fake_engine,
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
    fake_engine.open_for_release.assert_awaited_once_with(
        version=_VERSION,
        highlights=["feat: war room engine", "fix: typos"],
        project_id=task.project_id,
    )


@pytest.mark.asyncio
async def test_draft_war_room_calls_engine_seam() -> None:
    """``_draft_war_room`` is the best-effort seam; cover it directly so the
    publish-success path is exercised even when the full ``approve()`` DB
    fixture is unavailable."""
    report = _report()
    fake_engine = AsyncMock()
    fake_engine.open_for_release = AsyncMock(return_value=None)
    project_id = uuid4()

    with patch(
        "roboco.services.war_room_engine.get_war_room_engine",
        return_value=fake_engine,
    ):
        await ReleaseProposalService(MagicMock())._draft_war_room(report, project_id)

    fake_engine.open_for_release.assert_awaited_once_with(
        version=_VERSION,
        highlights=["feat: war room engine", "fix: typos"],
        project_id=project_id,
    )


@pytest.mark.asyncio
async def test_draft_war_room_swallows_engine_exception() -> None:
    """An engine exception must never propagate out of the best-effort seam —
    the release already published; a campaign origination failure can't
    un-publish it."""
    report = _report()

    with patch(
        "roboco.services.war_room_engine.get_war_room_engine",
        side_effect=RuntimeError("war-room boom"),
    ):
        await ReleaseProposalService(MagicMock())._draft_war_room(report, uuid4())
