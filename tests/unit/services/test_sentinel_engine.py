"""Sentinel engine: originate ONE held exploration cycle, deduped, never
authors content itself, and — like PeriscopeEngine — needs no per-project
opt-in to RUN (org-scoped: it reads org-wide drift signals, not a repo).

Mirrors test_periscope_engine.py: like periscope, the exploration task's
``project_id`` still resolves against the RoboCo project (a hard
TaskService._require_target_or_umbrella invariant every non-coordination
task carries) even though the program itself needs no per-project opt-in.
Also covers ``evidence_context``'s four aggregate sections, mirroring
test_pest_control_engine.py's evidence-gathering coverage.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.config import settings as cfg
from roboco.db.tables import (
    AgentSpawnSessionTable,
    AgentTable,
    BoardProgramCycleTable,
    ProjectConventionFindingTable,
    ProjectTable,
    SystemSettingTable,
    TaskReviewFindingTable,
    TaskTable,
)
from roboco.foundation import identity as _foundation
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    Complexity,
    TaskNature,
    TaskType,
    Team,
)
from roboco.models.base import TaskStatus as TS
from roboco.services.sentinel_engine import SentinelEngine
from roboco.services.task import (
    PERISCOPE_SOURCE,
    PEST_CONTROL_SOURCE,
    ROADMAP_SOURCE,
    SENTINEL_SOURCE,
    X_FEATURE_EXPLORATION_SOURCE,
    TaskCreateRequest,
    get_task_service,
)
from sqlalchemy import delete, select, update

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
AUDITOR_UUID = _foundation.AGENTS["auditor"].uuid
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
                    SENTINEL_SOURCE,
                ]
            ),
            TaskTable.status.notin_([TS.COMPLETED, TS.CANCELLED]),
        )
        .values(status=TS.CANCELLED)
    )
    await db_session.commit()


async def _seed(session: AsyncSession) -> None:
    for uuid, slug, role, team in (
        (SYSTEM_UUID, "system", AgentRole.SYSTEM, None),
        (AUDITOR_UUID, "auditor", AgentRole.AUDITOR, None),
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
    session.add(
        ProjectTable(
            name="RoboCo",
            slug=SLUG,
            git_url="https://github.com/x/roboco.git",
            default_branch="master",
            protected_branches=["master"],
            assigned_cell=Team.BACKEND,
            created_by=SYSTEM_UUID,
            is_active=True,
        )
    )
    await session.flush()


def _arm(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    session.add(SystemSettingTable(key="board_program.sentinel.enabled", value="true"))
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)


# --------------------------------------------------------------------------- #
# run_cycle
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_disabled_creates_no_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    engine = SentinelEngine(db_session)
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_sentinel_cycles() == []


@pytest.mark.asyncio
async def test_no_legacy_flag_backdoor(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No settings-store row at all = disabled — there is no legacy env flag
    for sentinel to fall back to (unlike roadmap/x_feature)."""
    await _seed(db_session)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    engine = SentinelEngine(db_session)
    assert await engine.run_cycle() is None


@pytest.mark.asyncio
async def test_enabled_originates_held_exploration_task(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _arm(db_session, monkeypatch)
    await db_session.flush()
    engine = SentinelEngine(db_session)
    task = await engine.run_cycle()
    assert task is not None

    open_cycles = await get_task_service(db_session).list_open_sentinel_cycles()
    assert len(open_cycles) == ONE
    cycle = open_cycles[0]
    assert cycle.status == TS.PENDING
    assert cycle.confirmed_by_human is False  # HELD; board-dispatched only
    assert cycle.assigned_to == AUDITOR_UUID
    assert cycle.team == Team.BOARD
    assert cycle.source == SENTINEL_SOURCE
    assert "Sentinel" in cycle.title


@pytest.mark.asyncio
async def test_dedupe_one_open_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _arm(db_session, monkeypatch)
    await db_session.flush()
    await SentinelEngine(db_session).run_cycle()
    second = await SentinelEngine(db_session).run_cycle()
    assert second is None
    assert len(await get_task_service(db_session).list_open_sentinel_cycles()) == ONE


@pytest.mark.asyncio
async def test_settings_store_false_creates_no_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    db_session.add(
        SystemSettingTable(key="board_program.sentinel.enabled", value="false")
    )
    await db_session.flush()
    engine = SentinelEngine(db_session)
    assert await engine.run_cycle() is None


@pytest.mark.asyncio
async def test_unresolvable_project_no_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    db_session.add(
        SystemSettingTable(key="board_program.sentinel.enabled", value="true")
    )
    monkeypatch.setattr(cfg, "self_heal_project_slug", "no-such-project")
    await db_session.flush()
    engine = SentinelEngine(db_session)
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_sentinel_cycles() == []


@pytest.mark.asyncio
async def test_a_completed_cycle_unblocks_the_next_one(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _arm(db_session, monkeypatch)
    await db_session.flush()
    first = await SentinelEngine(db_session).run_cycle()
    assert first is not None
    first.status = TS.COMPLETED
    await db_session.flush()

    second = await SentinelEngine(db_session).run_cycle()
    assert second is not None
    assert second.id != first.id


# --------------------------------------------------------------------------- #
# evidence_context
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_evidence_context_empty_when_nothing_to_report(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(delete(TaskReviewFindingTable))
    await db_session.execute(delete(ProjectConventionFindingTable))
    await db_session.commit()
    context = await SentinelEngine(db_session).evidence_context()
    assert context == ""


async def _make_task(session: AsyncSession, *, title: str = "x") -> TaskTable:
    await _seed(session)
    project = (
        await session.execute(select(ProjectTable).where(ProjectTable.slug == SLUG))
    ).scalar_one()
    return await get_task_service(session).create(
        TaskCreateRequest(
            title=title,
            description="x",
            acceptance_criteria=["x"],
            team=Team.BACKEND,
            assigned_to=None,
            created_by=SYSTEM_UUID,
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.LOW,
            project_id=cast("UUID", project.id),
            status=TS.COMPLETED,
        )
    )


@pytest.mark.asyncio
async def test_evidence_context_renders_waived_trend(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(delete(TaskReviewFindingTable))
    task = await _make_task(db_session, title="waived-trend task")
    now = datetime.now(UTC)
    db_session.add(
        TaskReviewFindingTable(
            id=uuid4(),
            task_id=task.id,
            origin="qa",
            round=1,
            author_slug="qa-1",
            file="roboco/services/task.py",
            line=10,
            severity="minor",
            expected="x",
            actual="y",
            status="waived",
            created_at=now - timedelta(days=1),
        )
    )
    await db_session.flush()

    context = await SentinelEngine(db_session).evidence_context()
    assert "waived this week" in context
    assert "1 waived this week" in context


@pytest.mark.asyncio
async def test_evidence_context_renders_open_findings_by_severity(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(delete(TaskReviewFindingTable))
    task = await _make_task(db_session, title="open-findings task")
    db_session.add(
        TaskReviewFindingTable(
            id=uuid4(),
            task_id=task.id,
            origin="qa",
            round=1,
            author_slug="qa-1",
            file="roboco/services/task.py",
            line=10,
            severity="blocker",
            expected="x",
            actual="y",
            status="open",
        )
    )
    await db_session.flush()

    context = await SentinelEngine(db_session).evidence_context()
    assert "Open findings by severity" in context
    assert "blocker: 1 open" in context


@pytest.mark.asyncio
async def test_evidence_context_renders_conventions_hotspots(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(delete(ProjectConventionFindingTable))
    await _seed(db_session)
    project = (
        await db_session.execute(select(ProjectTable).where(ProjectTable.slug == SLUG))
    ).scalar_one()
    db_session.add(
        ProjectConventionFindingTable(
            id=uuid4(),
            project_id=project.id,
            task_id=None,
            file="roboco/api/routes/x.py",
            line=5,
            rule="thin_routes",
            level="block",
            message="model defined in router",
        )
    )
    await db_session.flush()

    context = await SentinelEngine(db_session).evidence_context()
    assert "Conventions-violation hotspots" in context
    assert "thin_routes: 1 violations" in context


@pytest.mark.asyncio
async def test_evidence_context_renders_top_task_and_project_spend(
    db_session: AsyncSession,
) -> None:
    task = await _make_task(db_session, title="expensive task")
    db_session.add(
        AgentSpawnSessionTable(
            id=uuid4(),
            agent_slug="be-dev-1",
            team="backend",
            role="developer",
            model="claude-opus-4-8",
            task_id=str(task.id),
            estimated_cost_usd=12.5,
        )
    )
    await db_session.flush()

    context = await SentinelEngine(db_session).evidence_context()
    assert "Top spend by task" in context
    assert "expensive task: $12.50" in context
    assert "Top spend by project" in context
    assert f"{SLUG}: $12.50" in context
