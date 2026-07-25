"""Scales engine: originate ONE held exploration cycle, deduped, never
authors content itself, and — like PeriscopeEngine — needs no per-project
opt-in to RUN (org-scoped: it reviews the live portfolio across every
project, not one repo).

Mirrors test_periscope_engine.py: like periscope/roadmap, the exploration
task's ``project_id`` still resolves against the RoboCo project (a hard
TaskService._require_target_or_umbrella invariant every non-coordination
task carries) even though the program itself needs no per-project opt-in.
The stale-backlog snapshot tests mirror PestControlEngine's
evidence_context suite.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from roboco.config import settings as cfg
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
from roboco.services.scales_engine import ScalesEngine
from roboco.services.task import (
    PERISCOPE_SOURCE,
    PEST_CONTROL_SOURCE,
    ROADMAP_SOURCE,
    SCALES_SOURCE,
    X_FEATURE_EXPLORATION_SOURCE,
    get_task_service,
)
from sqlalchemy import delete, update

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
PO_UUID = _foundation.AGENTS["product-owner"].uuid
SLUG = "roboco"
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
                    PERISCOPE_SOURCE,
                    SCALES_SOURCE,
                ]
            ),
            TaskTable.status.notin_([TS.COMPLETED, TS.CANCELLED]),
        )
        .values(status=TS.CANCELLED)
    )
    await db_session.commit()


async def _seed(session: AsyncSession) -> ProjectTable:
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
        name="RoboCo",
        slug=SLUG,
        git_url="https://github.com/x/roboco.git",
        default_branch="master",
        protected_branches=["master"],
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
        is_active=True,
    )
    session.add(project)
    await session.flush()
    return project


def _arm(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    session.add(SystemSettingTable(key="board_program.scales.enabled", value="true"))
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)


@pytest.mark.asyncio
async def test_disabled_creates_no_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    engine = ScalesEngine(db_session)
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_scales_cycles() == []


@pytest.mark.asyncio
async def test_no_legacy_flag_backdoor(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No settings-store row at all = disabled — there is no legacy env flag
    for scales to fall back to."""
    await _seed(db_session)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    engine = ScalesEngine(db_session)
    assert await engine.run_cycle() is None


@pytest.mark.asyncio
async def test_enabled_originates_held_exploration_task(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await _seed(db_session)
    _arm(db_session, monkeypatch)
    await db_session.flush()
    engine = ScalesEngine(db_session)
    task = await engine.run_cycle()
    assert task is not None

    open_cycles = await get_task_service(db_session).list_open_scales_cycles()
    assert len(open_cycles) == ONE
    cycle = open_cycles[0]
    assert cycle.status == TS.PENDING
    assert cycle.confirmed_by_human is False  # HELD; board-dispatched only
    assert cycle.assigned_to == PO_UUID
    assert cycle.team == Team.BOARD
    assert cycle.source == SCALES_SOURCE
    assert cycle.project_id == project.id
    assert "Scales" in cycle.title


@pytest.mark.asyncio
async def test_dedupe_one_open_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _arm(db_session, monkeypatch)
    await db_session.flush()
    await ScalesEngine(db_session).run_cycle()
    second = await ScalesEngine(db_session).run_cycle()
    assert second is None
    assert len(await get_task_service(db_session).list_open_scales_cycles()) == ONE


@pytest.mark.asyncio
async def test_settings_store_false_creates_no_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    db_session.add(
        SystemSettingTable(key="board_program.scales.enabled", value="false")
    )
    await db_session.flush()
    engine = ScalesEngine(db_session)
    assert await engine.run_cycle() is None


@pytest.mark.asyncio
async def test_unresolvable_project_no_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    db_session.add(SystemSettingTable(key="board_program.scales.enabled", value="true"))
    monkeypatch.setattr(cfg, "self_heal_project_slug", "no-such-project")
    await db_session.flush()
    engine = ScalesEngine(db_session)
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_scales_cycles() == []


@pytest.mark.asyncio
async def test_a_completed_cycle_unblocks_the_next_one(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _arm(db_session, monkeypatch)
    await db_session.flush()
    first = await ScalesEngine(db_session).run_cycle()
    assert first is not None
    first.status = TS.COMPLETED
    await db_session.flush()

    second = await ScalesEngine(db_session).run_cycle()
    assert second is not None
    assert second.id != first.id


_STALE_DAYS = 30


@pytest.mark.asyncio
async def test_evidence_context_reports_stale_backlog_task(
    db_session: AsyncSession,
) -> None:
    project = await _seed(db_session)
    old = datetime.now(UTC) - timedelta(days=_STALE_DAYS + 5)
    stale_task = TaskTable(
        title="Ancient onboarding polish",
        description="x",
        acceptance_criteria=["x"],
        status=TS.BACKLOG,
        priority=2,
        task_type=TT.CODE,
        nature=TN.TECHNICAL,
        estimated_complexity=Complexity.MEDIUM,
        created_by=SYSTEM_UUID,
        project_id=project.id,
        team=Team.BACKEND,
        created_at=old,
    )
    db_session.add(stale_task)
    await db_session.flush()

    context = await ScalesEngine(db_session).evidence_context()
    assert "Ancient onboarding polish" in context
    assert "P2" in context


@pytest.mark.asyncio
async def test_evidence_context_empty_when_nothing_stale(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    context = await ScalesEngine(db_session).evidence_context()
    assert context == ""


@pytest.mark.asyncio
async def test_evidence_context_excludes_fresh_backlog(
    db_session: AsyncSession,
) -> None:
    """A BACKLOG/PENDING task younger than the stale-days floor never
    appears — the snapshot is stale-backlog only, not the whole backlog."""
    project = await _seed(db_session)
    fresh = TaskTable(
        title="Freshly filed",
        description="x",
        acceptance_criteria=["x"],
        status=TS.BACKLOG,
        priority=2,
        task_type=TT.CODE,
        nature=TN.TECHNICAL,
        estimated_complexity=Complexity.MEDIUM,
        created_by=SYSTEM_UUID,
        project_id=project.id,
        team=Team.BACKEND,
    )
    db_session.add(fresh)
    await db_session.flush()

    context = await ScalesEngine(db_session).evidence_context()
    assert context == ""


@pytest.mark.asyncio
async def test_evidence_context_excludes_non_backlog_pending(
    db_session: AsyncSession,
) -> None:
    """A stale task that has already left BACKLOG/PENDING (e.g. claimed,
    completed) never appears — the snapshot only ever names live, unclaimed
    backlog."""
    project = await _seed(db_session)
    old = datetime.now(UTC) - timedelta(days=_STALE_DAYS + 5)
    claimed = TaskTable(
        title="Already in flight",
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
        created_at=old,
    )
    db_session.add(claimed)
    await db_session.flush()

    context = await ScalesEngine(db_session).evidence_context()
    assert context == ""


_OVER_CAP = 16
_CAP = 15


@pytest.mark.asyncio
async def test_evidence_context_caps_at_fifteen(db_session: AsyncSession) -> None:
    """The stale-backlog snapshot never renders more than 15 lines even when
    the backlog carries more stale candidates."""
    project = await _seed(db_session)
    old = datetime.now(UTC) - timedelta(days=_STALE_DAYS + 5)
    for i in range(_OVER_CAP):
        db_session.add(
            TaskTable(
                title=f"Stale task {i}",
                description="x",
                acceptance_criteria=["x"],
                status=TS.BACKLOG,
                priority=2,
                task_type=TT.CODE,
                nature=TN.TECHNICAL,
                estimated_complexity=Complexity.MEDIUM,
                created_by=SYSTEM_UUID,
                project_id=project.id,
                team=Team.BACKEND,
                created_at=old,
            )
        )
    await db_session.flush()

    context = await ScalesEngine(db_session).evidence_context()
    rendered_lines = [ln for ln in context.splitlines() if ln.startswith("- ")]
    assert len(rendered_lines) == _CAP
