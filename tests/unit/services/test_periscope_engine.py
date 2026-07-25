"""Periscope engine: originate ONE held exploration cycle, deduped, never
authors content itself, and — unlike PestControlEngine — needs no
per-project opt-in to RUN (org-scoped: it reads the market, not a repo).

Mirrors test_roadmap_engine.py: like roadmap, the exploration task's
``project_id`` still resolves against the RoboCo project (a hard
TaskService._require_target_or_umbrella invariant every non-coordination
task carries) even though the program itself needs no per-project opt-in.
"""

from __future__ import annotations

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
from roboco.foundation.policy.content import markers
from roboco.models.base import AgentRole, AgentStatus, Team
from roboco.models.base import TaskStatus as TS
from roboco.services.periscope_engine import PeriscopeEngine
from roboco.services.task import (
    PERISCOPE_SOURCE,
    PEST_CONTROL_SOURCE,
    ROADMAP_SOURCE,
    X_FEATURE_EXPLORATION_SOURCE,
    get_task_service,
)
from sqlalchemy import delete, update

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
HOM_UUID = _foundation.AGENTS["head-marketing"].uuid
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
    session.add(SystemSettingTable(key="board_program.periscope.enabled", value="true"))
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)


@pytest.mark.asyncio
async def test_disabled_creates_no_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    engine = PeriscopeEngine(db_session)
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_periscope_cycles() == []


@pytest.mark.asyncio
async def test_no_legacy_flag_backdoor(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No settings-store row at all = disabled — there is no legacy env flag
    for periscope to fall back to (unlike roadmap/x_feature)."""
    await _seed(db_session)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    engine = PeriscopeEngine(db_session)
    assert await engine.run_cycle() is None


@pytest.mark.asyncio
async def test_enabled_originates_held_exploration_task(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _arm(db_session, monkeypatch)
    await db_session.flush()
    engine = PeriscopeEngine(db_session)
    task = await engine.run_cycle()
    assert task is not None

    open_cycles = await get_task_service(db_session).list_open_periscope_cycles()
    assert len(open_cycles) == ONE
    cycle = open_cycles[0]
    assert cycle.status == TS.PENDING
    assert cycle.confirmed_by_human is False  # HELD; board-dispatched only
    assert cycle.assigned_to == HOM_UUID
    assert cycle.team == Team.BOARD
    assert cycle.source == PERISCOPE_SOURCE
    assert "Periscope" in cycle.title


@pytest.mark.asyncio
async def test_dedupe_one_open_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _arm(db_session, monkeypatch)
    await db_session.flush()
    await PeriscopeEngine(db_session).run_cycle()
    second = await PeriscopeEngine(db_session).run_cycle()
    assert second is None
    assert len(await get_task_service(db_session).list_open_periscope_cycles()) == ONE


@pytest.mark.asyncio
async def test_settings_store_false_creates_no_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    db_session.add(
        SystemSettingTable(key="board_program.periscope.enabled", value="false")
    )
    await db_session.flush()
    engine = PeriscopeEngine(db_session)
    assert await engine.run_cycle() is None


@pytest.mark.asyncio
async def test_unresolvable_project_no_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    db_session.add(
        SystemSettingTable(key="board_program.periscope.enabled", value="true")
    )
    monkeypatch.setattr(cfg, "self_heal_project_slug", "no-such-project")
    await db_session.flush()
    engine = PeriscopeEngine(db_session)
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_periscope_cycles() == []


@pytest.mark.asyncio
async def test_a_completed_cycle_unblocks_the_next_one(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _arm(db_session, monkeypatch)
    await db_session.flush()
    first = await PeriscopeEngine(db_session).run_cycle()
    assert first is not None
    first.status = TS.COMPLETED
    await db_session.flush()

    second = await PeriscopeEngine(db_session).run_cycle()
    assert second is not None
    assert second.id != first.id


@pytest.mark.asyncio
async def test_latest_brief_context_empty_when_nothing_filed(
    db_session: AsyncSession,
) -> None:
    context = await PeriscopeEngine(db_session).latest_brief_context()
    assert context == ""


@pytest.mark.asyncio
async def test_latest_brief_context_renders_headline_and_findings(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _arm(db_session, monkeypatch)
    await db_session.flush()
    task = await PeriscopeEngine(db_session).run_cycle()
    assert task is not None
    markers.set_market_brief(
        task,
        {
            "headline": "A rival tool shipped agentic PR review",
            "findings": [
                {
                    "id": "finding-0",
                    "claim": "Competitor X launched an autonomous reviewer",
                    "source_url": "https://example.com/x-launch",
                    "relevance": "Overlaps our pr_reviewer role",
                }
            ],
            "threats": [],
            "opportunities": [],
            "positioning_note": "",
            "injection_hits": [],
        },
    )
    task.status = TS.COMPLETED
    await db_session.flush()

    context = await PeriscopeEngine(db_session).latest_brief_context()
    assert "A rival tool shipped agentic PR review" in context
    assert "Competitor X launched an autonomous reviewer" in context
    assert "https://example.com/x-launch" in context


@pytest.mark.asyncio
async def test_latest_brief_context_picks_the_most_recent_completed_brief(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two completed cycles: the LATEST brief's content wins, not the first."""
    await _seed(db_session)
    _arm(db_session, monkeypatch)
    await db_session.flush()

    first = await PeriscopeEngine(db_session).run_cycle()
    assert first is not None
    markers.set_market_brief(
        first,
        {
            "headline": "Older signal",
            "findings": [
                {
                    "id": "finding-0",
                    "claim": "Stale claim",
                    "source_url": "https://example.com/old",
                    "relevance": "old",
                }
            ],
            "threats": [],
            "opportunities": [],
            "positioning_note": "",
            "injection_hits": [],
        },
    )
    first.status = TS.COMPLETED
    await db_session.flush()

    second = await PeriscopeEngine(db_session).run_cycle()
    assert second is not None
    markers.set_market_brief(
        second,
        {
            "headline": "Fresh signal",
            "findings": [
                {
                    "id": "finding-0",
                    "claim": "Fresh claim",
                    "source_url": "https://example.com/new",
                    "relevance": "new",
                }
            ],
            "threats": [],
            "opportunities": [],
            "positioning_note": "",
            "injection_hits": [],
        },
    )
    second.status = TS.COMPLETED
    await db_session.flush()

    context = await PeriscopeEngine(db_session).latest_brief_context()
    assert "Fresh signal" in context
    assert "Older signal" not in context
