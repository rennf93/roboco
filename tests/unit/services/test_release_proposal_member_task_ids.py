"""``member_task_ids_for_proposal`` — the delivery-task-set derivation backing
``ReleaseProposalResponse.member_task_ids`` (round-1 pr_gate finding
F-41ebb0a6). Mirrors ``test_release_proposal_status_guards.py``'s
``_seed_proposal`` pattern.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.foundation import identity as _foundation
from roboco.models.base import AgentRole, AgentStatus, TaskNature, TaskStatus, TaskType
from roboco.models.base import Team as T
from roboco.services.release_proposal import member_task_ids_for_proposal
from roboco.services.release_readiness import ReleaseReadinessReport, report_to_dict
from roboco.services.task import RELEASE_MANAGER_SOURCE

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

_VERSION = "0.19.0"
_T0 = datetime(2026, 6, 20, 12, 0, 0, tzinfo=UTC)


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


async def _seed_agents(session: AsyncSession) -> tuple[UUID, UUID]:
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
    return system_uuid, secretary_uuid


async def _seed_project(session: AsyncSession, system_uuid: UUID) -> ProjectTable:
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
    return project


async def _seed_proposal(
    session: AsyncSession,
    project: ProjectTable,
    system_uuid: UUID,
    secretary_uuid: UUID,
    *,
    completed_at: datetime | None = None,
) -> TaskTable:
    task = TaskTable(
        id=uuid4(),
        title=f"Release proposal: v{_VERSION}",
        description="proposal body",
        acceptance_criteria=["CEO approves"],
        status=TaskStatus.COMPLETED if completed_at else TaskStatus.PENDING,
        completed_at=completed_at,
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


async def _seed_delivery_task(
    session: AsyncSession,
    project: ProjectTable,
    system_uuid: UUID,
    completed_at: datetime,
    **spec: Any,
) -> TaskTable:
    """A COMPLETED delivery task; optional spec keys ``pr_number`` / ``source``."""
    task = TaskTable(
        id=uuid4(),
        title="Delivery work",
        description="body",
        acceptance_criteria=[],
        status=TaskStatus.COMPLETED,
        completed_at=completed_at,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=project.id,
        created_by=system_uuid,
        team=T.BACKEND,
        source=spec.get("source", "manual"),
        confirmed_by_human=True,
        pr_number=spec.get("pr_number"),
    )
    session.add(task)
    await session.flush()
    return task


@pytest.mark.asyncio
async def test_member_task_ids_scoped_to_window_and_project(
    db_session: AsyncSession,
) -> None:
    """Only COMPLETED delivery tasks in the SAME project, after the previous
    same-project release's completion, are members — held/coordination
    artifacts and PR-review tasks are excluded, mirroring the release
    task-set deny-list used elsewhere."""
    system_uuid, secretary_uuid = await _seed_agents(db_session)
    project = await _seed_project(db_session, system_uuid)
    previous = await _seed_proposal(
        db_session,
        project,
        system_uuid,
        secretary_uuid,
        completed_at=_T0 - timedelta(hours=1),
    )
    target = await _seed_proposal(
        db_session, project, system_uuid, secretary_uuid, completed_at=None
    )
    in_window = await _seed_delivery_task(
        db_session, project, system_uuid, _T0 + timedelta(minutes=30), pr_number=42
    )
    await _seed_delivery_task(
        db_session,
        project,
        system_uuid,
        _T0 - timedelta(hours=2),
        pr_number=1,
    )  # before the previous release -- excluded
    await _seed_delivery_task(
        db_session,
        project,
        system_uuid,
        _T0 + timedelta(minutes=45),
        source="external_pr",
    )  # PR-review task -- excluded
    other_project = await _seed_project(db_session, system_uuid)
    await _seed_delivery_task(
        db_session,
        other_project,
        system_uuid,
        _T0 + timedelta(minutes=30),
    )  # different project -- excluded

    result = await member_task_ids_for_proposal(db_session, target)

    assert result == [{"task_id": str(in_window.id), "pr_number": 42}]
    assert previous.id != target.id  # sanity: two distinct proposals seeded


@pytest.mark.asyncio
async def test_member_task_ids_empty_for_project_less_proposal(
    db_session: AsyncSession,
) -> None:
    system_uuid, secretary_uuid = await _seed_agents(db_session)
    project = await _seed_project(db_session, system_uuid)
    target = await _seed_proposal(
        db_session, project, system_uuid, secretary_uuid, completed_at=None
    )
    target.project_id = None
    await db_session.flush()

    assert await member_task_ids_for_proposal(db_session, target) == []
