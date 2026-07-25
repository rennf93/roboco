"""Mirror engine: originate ONE held exploration cycle, deduped, never
authors content itself, and only against an opted-in project.

Mirrors test_spackle_engine.py, minus the evidence_context tests (spec §4:
Mirror carries NO heavy server-side inventory engine — the messaging audit
is the Head of Marketing's own read-tool work, ordered by the spawn prompt,
not server-assembled). Also exercises the shared rotation helper
(``roboco.services.board_programs.pick_rotation_target``) through this
engine, proving it stays independent of the other project-scoped programs'
rotations.
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
    TaskTable,
)
from roboco.foundation import identity as _foundation
from roboco.models.base import AgentRole, AgentStatus, Complexity, Team
from roboco.models.base import TaskNature as TN
from roboco.models.base import TaskStatus as TS
from roboco.models.base import TaskType as TT
from roboco.services.mirror_engine import MirrorEngine
from roboco.services.task import (
    MIRROR_SOURCE,
    PEST_CONTROL_SOURCE,
    ROADMAP_SOURCE,
    SPACKLE_SOURCE,
    X_FEATURE_EXPLORATION_SOURCE,
    get_task_service,
)
from sqlalchemy import delete, update

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
HOM_UUID = _foundation.AGENTS["head-marketing"].uuid
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
                    SPACKLE_SOURCE,
                    MIRROR_SOURCE,
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
        (HOM_UUID, "head-marketing", AgentRole.HEAD_MARKETING, Team.BOARD),
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
        board_programs=["mirror"] if opted_in else None,
    )
    session.add(project)
    await session.flush()
    return project


def _arm(session: AsyncSession) -> None:
    session.add(SystemSettingTable(key="board_program.mirror.enabled", value="true"))


@pytest.mark.asyncio
async def test_disabled_creates_no_cycle(db_session: AsyncSession) -> None:
    await _seed(db_session)
    engine = MirrorEngine(db_session)
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_mirror_cycles() == []


@pytest.mark.asyncio
async def test_enabled_originates_held_exploration_task(
    db_session: AsyncSession,
) -> None:
    project = await _seed(db_session)
    _arm(db_session)
    await db_session.flush()
    engine = MirrorEngine(db_session)
    task = await engine.run_cycle()
    assert task is not None

    open_cycles = await get_task_service(db_session).list_open_mirror_cycles()
    assert len(open_cycles) == ONE
    cycle = open_cycles[0]
    assert cycle.status == TS.PENDING
    assert cycle.confirmed_by_human is False  # HELD; board-dispatched only
    assert cycle.assigned_to == HOM_UUID
    assert cycle.team == Team.BOARD
    assert cycle.source == MIRROR_SOURCE
    assert cycle.project_id == project.id
    assert "Mirror" in cycle.title


@pytest.mark.asyncio
async def test_dedupe_one_open_cycle(db_session: AsyncSession) -> None:
    await _seed(db_session)
    _arm(db_session)
    await db_session.flush()
    await MirrorEngine(db_session).run_cycle()
    second = await MirrorEngine(db_session).run_cycle()
    assert second is None
    assert len(await get_task_service(db_session).list_open_mirror_cycles()) == ONE


@pytest.mark.asyncio
async def test_no_opted_in_project_creates_no_cycle(db_session: AsyncSession) -> None:
    """A project-scoped program's cardinal guarantee: no cycle without an
    opt-in — no legacy flag exists that could ever bypass this."""
    await _seed(db_session, opted_in=False)
    _arm(db_session)
    await db_session.flush()
    engine = MirrorEngine(db_session)
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_mirror_cycles() == []


@pytest.mark.asyncio
async def test_settings_store_false_creates_no_cycle(db_session: AsyncSession) -> None:
    await _seed(db_session)
    db_session.add(
        SystemSettingTable(key="board_program.mirror.enabled", value="false")
    )
    await db_session.flush()
    engine = MirrorEngine(db_session)
    assert await engine.run_cycle() is None


@pytest.mark.asyncio
async def test_no_legacy_flag_backdoor(db_session: AsyncSession) -> None:
    """No settings-store row at all = disabled — there is no legacy env flag
    for mirror to fall back to (unlike roadmap/x_feature)."""
    await _seed(db_session)
    await db_session.flush()
    engine = MirrorEngine(db_session)
    assert await engine.run_cycle() is None


@pytest.mark.asyncio
async def test_multi_opted_projects_names_all_in_description(
    db_session: AsyncSession,
) -> None:
    """Two opted-in projects: the first cycle deterministically targets
    ``SLUG`` (seeded, so created first) and names the OTHER project as
    queued rather than claiming both are covered. Completing that cycle and
    running a second one rotates the target to the other project — the
    shared ``pick_rotation_target`` helper behaves identically to Spackle's
    rotation."""
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
        board_programs=["mirror"],
    )
    db_session.add(second)
    _arm(db_session)
    await db_session.flush()

    first_task = await MirrorEngine(db_session).run_cycle()
    assert first_task is not None
    assert first_task.project_id == project.id  # deterministic: created first
    assert f"target: {SLUG}" in first_task.description
    assert "Queued for subsequent cycles: frontend-app" in first_task.description

    first_task.status = TS.COMPLETED
    await db_session.flush()

    second_task = await MirrorEngine(db_session).run_cycle()
    assert second_task is not None
    assert second_task.project_id == second.id  # rotated to the other project
    assert "target: frontend-app" in second_task.description
    assert f"Queued for subsequent cycles: {SLUG}" in second_task.description


@pytest.mark.asyncio
async def test_rotation_keyed_by_own_source_not_spackles(
    db_session: AsyncSession,
) -> None:
    """The shared ``pick_rotation_target`` helper keys "last explored" by the
    caller's OWN source tag — a project spackle explored recently must not
    be treated as recently mirror-explored too (they are independent
    rotations sharing only the pure ranking function)."""
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
        board_programs=["mirror"],
    )
    db_session.add(second)
    await db_session.flush()

    # A completed SPACKLE_SOURCE exploration against `project` — must be
    # invisible to mirror's own rotation.
    spackle_task = TaskTable(
        title="Spackle exploration cycle",
        description="x",
        acceptance_criteria=["x"],
        status=TS.COMPLETED,
        priority=2,
        task_type=TT.ADMINISTRATIVE,
        nature=TN.NON_TECHNICAL,
        estimated_complexity=Complexity.LOW,
        created_by=SYSTEM_UUID,
        assigned_to=HOM_UUID,
        team=Team.BOARD,
        source=SPACKLE_SOURCE,
        confirmed_by_human=False,
        project_id=project.id,
    )
    db_session.add(spackle_task)
    _arm(db_session)
    await db_session.flush()

    # Both projects are never-explored under MIRROR_SOURCE, so the tie
    # breaks by deterministic order (created-first == `project`), exactly as
    # if the spackle row didn't exist.
    task = await MirrorEngine(db_session).run_cycle()
    assert task is not None
    assert task.project_id == project.id
