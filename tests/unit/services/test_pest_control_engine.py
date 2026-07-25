"""Pest control engine: originate ONE held exploration cycle, deduped, never
authors content itself, and only against an opted-in project.

Mirrors test_roadmap_engine.py, minus the RoboCo-project-slug resolution (this
program is project-scoped: it targets whichever project has opted in via
``projects.board_programs``, never a hardcoded slug).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from roboco.db.tables import (
    AgentTable,
    BoardProgramCycleTable,
    ProjectTable,
    SystemSettingTable,
    TaskReviewFindingTable,
    TaskTable,
)
from roboco.foundation import identity as _foundation
from roboco.models.base import AgentRole, AgentStatus, Complexity, Team
from roboco.models.base import TaskNature as TN
from roboco.models.base import TaskStatus as TS
from roboco.models.base import TaskType as TT
from roboco.services.pest_control_engine import PestControlEngine
from roboco.services.task import (
    CORONER_SOURCE,
    PEST_CONTROL_SOURCE,
    ROADMAP_SOURCE,
    X_FEATURE_EXPLORATION_SOURCE,
    get_task_service,
)
from sqlalchemy import delete, update

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
PO_UUID = _foundation.AGENTS["product-owner"].uuid
SLUG = "backend-svc"
ONE = 1


@pytest_asyncio.fixture(autouse=True)
async def _purge_board_program_pollution(db_session: AsyncSession) -> None:
    """See test_board_program_engine.py's identical fixture: Board Program
    settings-store rows / ledger rows / open exploration tasks are shared,
    cross-test-persistent DB state. Purge before every test in this file."""
    await db_session.execute(
        delete(SystemSettingTable).where(SystemSettingTable.key.like("board_program.%"))
    )
    await db_session.execute(delete(BoardProgramCycleTable))
    await db_session.execute(
        update(TaskTable)
        .where(
            TaskTable.source.in_(
                [
                    ROADMAP_SOURCE,
                    X_FEATURE_EXPLORATION_SOURCE,
                    PEST_CONTROL_SOURCE,
                    CORONER_SOURCE,
                ]
            ),
            TaskTable.status.notin_([TS.COMPLETED, TS.CANCELLED]),
        )
        .values(status=TS.CANCELLED)
    )
    await db_session.commit()


async def _seed(session: AsyncSession, *, opted_in: bool = True) -> ProjectTable:
    for uuid, slug, role, team in (
        (SYSTEM_UUID, "system", AgentRole.SYSTEM, None),
        (PO_UUID, "product-owner", AgentRole.PRODUCT_OWNER, Team.BOARD),
    ):
        if await session.get(AgentTable, uuid) is None:
            session.add(
                AgentTable(
                    id=uuid,
                    name=slug,
                    slug=slug,
                    role=role,
                    team=team,
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
        name="Backend Service",
        slug=SLUG,
        git_url="https://github.com/x/backend-svc.git",
        default_branch="master",
        protected_branches=["master"],
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
        is_active=True,
        board_programs=["pest_control"] if opted_in else None,
    )
    session.add(project)
    await session.flush()
    return project


def _arm(session: AsyncSession) -> None:
    session.add(
        SystemSettingTable(key="board_program.pest_control.enabled", value="true")
    )


@pytest.mark.asyncio
async def test_disabled_creates_no_cycle(db_session: AsyncSession) -> None:
    await _seed(db_session)
    engine = PestControlEngine(db_session)
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_pest_control_cycles() == []


@pytest.mark.asyncio
async def test_enabled_originates_held_exploration_task(
    db_session: AsyncSession,
) -> None:
    project = await _seed(db_session)
    _arm(db_session)
    await db_session.flush()
    engine = PestControlEngine(db_session)
    task = await engine.run_cycle()
    assert task is not None

    open_cycles = await get_task_service(db_session).list_open_pest_control_cycles()
    assert len(open_cycles) == ONE
    cycle = open_cycles[0]
    assert cycle.status == TS.PENDING
    assert cycle.confirmed_by_human is False  # HELD; board-dispatched only
    assert cycle.assigned_to == PO_UUID
    assert cycle.team == Team.BOARD
    assert cycle.source == PEST_CONTROL_SOURCE
    assert cycle.project_id == project.id
    assert "Pest Control" in cycle.title


@pytest.mark.asyncio
async def test_dedupe_one_open_cycle(db_session: AsyncSession) -> None:
    await _seed(db_session)
    _arm(db_session)
    await db_session.flush()
    await PestControlEngine(db_session).run_cycle()
    second = await PestControlEngine(db_session).run_cycle()
    assert second is None
    assert (
        len(await get_task_service(db_session).list_open_pest_control_cycles()) == ONE
    )


@pytest.mark.asyncio
async def test_no_opted_in_project_creates_no_cycle(db_session: AsyncSession) -> None:
    """A project-scoped program's cardinal guarantee: no cycle without an
    opt-in — no legacy flag exists that could ever bypass this."""
    await _seed(db_session, opted_in=False)
    _arm(db_session)
    await db_session.flush()
    engine = PestControlEngine(db_session)
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_pest_control_cycles() == []


@pytest.mark.asyncio
async def test_settings_store_false_creates_no_cycle(db_session: AsyncSession) -> None:
    await _seed(db_session)
    db_session.add(
        SystemSettingTable(key="board_program.pest_control.enabled", value="false")
    )
    await db_session.flush()
    engine = PestControlEngine(db_session)
    assert await engine.run_cycle() is None


@pytest.mark.asyncio
async def test_no_legacy_flag_backdoor(db_session: AsyncSession) -> None:
    """No settings-store row at all = disabled — there is no legacy env flag
    for pest_control to fall back to (unlike roadmap/x_feature)."""
    await _seed(db_session)
    await db_session.flush()
    engine = PestControlEngine(db_session)
    assert await engine.run_cycle() is None


@pytest.mark.asyncio
async def test_multi_opted_projects_names_all_in_description(
    db_session: AsyncSession,
) -> None:
    """Two opted-in projects: the first cycle deterministically targets
    ``SLUG`` (seeded, so created first) and names the OTHER project as
    queued rather than claiming both are covered. Completing that cycle and
    running a second one rotates the target to the other project — a
    round-robin, not a repeat pick of the same repo."""
    project = await _seed(db_session)
    second = ProjectTable(
        name="Frontend App",
        slug="frontend-app",
        git_url="https://github.com/x/frontend-app.git",
        default_branch="master",
        protected_branches=["master"],
        assigned_cell=Team.FRONTEND,
        created_by=SYSTEM_UUID,
        is_active=True,
        board_programs=["pest_control"],
    )
    db_session.add(second)
    _arm(db_session)
    await db_session.flush()

    first_task = await PestControlEngine(db_session).run_cycle()
    assert first_task is not None
    assert first_task.project_id == project.id  # deterministic: created first
    assert f"target: {SLUG}" in first_task.description
    assert "Queued for subsequent cycles: frontend-app" in first_task.description

    first_task.status = TS.COMPLETED
    await db_session.flush()

    second_task = await PestControlEngine(db_session).run_cycle()
    assert second_task is not None
    assert second_task.project_id == second.id  # rotated to the other project
    assert "target: frontend-app" in second_task.description
    assert f"Queued for subsequent cycles: {SLUG}" in second_task.description


@pytest.mark.asyncio
async def test_evidence_context_reports_rework_hotspot(
    db_session: AsyncSession,
) -> None:
    project = await _seed(db_session)
    hot_task = TaskTable(
        title="Chronically bounced task",
        description="x",
        acceptance_criteria=["x"],
        status=TS.NEEDS_REVISION,
        priority=2,
        task_type=TT.CODE,
        nature=TN.TECHNICAL,
        estimated_complexity=Complexity.MEDIUM,
        created_by=SYSTEM_UUID,
        project_id=project.id,
        team=Team.BACKEND,
        revision_count=3,
    )
    db_session.add(hot_task)
    await db_session.flush()

    context = await PestControlEngine(db_session).evidence_context()
    assert "Chronically bounced task" in context
    assert "bounced 3x" in context


@pytest.mark.asyncio
async def test_evidence_context_empty_when_nothing_to_report(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    context = await PestControlEngine(db_session).evidence_context()
    assert context == ""


@pytest.mark.asyncio
async def test_evidence_context_reports_recurring_findings(
    db_session: AsyncSession,
) -> None:
    project = await _seed(db_session)
    task = TaskTable(
        title="Some task",
        description="x",
        acceptance_criteria=["x"],
        status=TS.IN_PROGRESS,
        priority=2,
        task_type=TT.CODE,
        nature=TN.TECHNICAL,
        estimated_complexity=Complexity.MEDIUM,
        created_by=SYSTEM_UUID,
        project_id=project.id,
        team=Team.BACKEND,
    )
    db_session.add(task)
    await db_session.flush()
    for round_n in (1, 2):
        db_session.add(
            TaskReviewFindingTable(
                task_id=task.id,
                origin="qa",
                round=round_n,
                author_slug="qa-1",
                file="roboco/services/task.py",
                line=42,
                severity="major",
                expected="a fix",
                actual="still broken",
            )
        )
    await db_session.flush()

    context = await PestControlEngine(db_session).evidence_context()
    assert "roboco/services/task.py" in context
    assert "2 findings" in context
