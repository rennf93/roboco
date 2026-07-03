"""Roadmap engine: originate ONE held exploration cycle, deduped, never authors
content itself.

Mirrors the release-manager engine tests. The engine only opens a HELD
exploration task (confirmed_by_human=False, owned by the Product Owner,
source=board_roadmap) — it never authors the cycle payload or starts
anything; that is entirely the Product Owner's ``propose_roadmap`` plus the
CEO's per-item approve.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from roboco.config import settings as cfg
from roboco.db.tables import AgentTable, ProjectTable
from roboco.foundation import identity as _foundation
from roboco.models.base import AgentRole, AgentStatus, Team
from roboco.models.base import TaskStatus as TS
from roboco.services.roadmap_engine import RoadmapEngine
from roboco.services.task import ROADMAP_SOURCE, get_task_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
PO_UUID = _foundation.AGENTS["product-owner"].uuid
SLUG = "roboco"
ONE = 1


async def _seed(session: AsyncSession) -> None:
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


def _enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "roadmap_engine_enabled", True)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)


@pytest.mark.asyncio
async def test_disabled_creates_no_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "roadmap_engine_enabled", False)
    engine = RoadmapEngine(db_session)
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_roadmap_cycles() == []


@pytest.mark.asyncio
async def test_enabled_originates_held_exploration_task(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    engine = RoadmapEngine(db_session)
    task = await engine.run_cycle()
    assert task is not None

    open_cycles = await get_task_service(db_session).list_open_roadmap_cycles()
    assert len(open_cycles) == ONE
    cycle = open_cycles[0]
    assert cycle.status == TS.PENDING
    assert cycle.confirmed_by_human is False  # HELD; board-dispatched only
    assert cycle.assigned_to == PO_UUID
    assert cycle.team == Team.BOARD
    assert cycle.source == ROADMAP_SOURCE
    assert "Roadmap" in cycle.title


@pytest.mark.asyncio
async def test_dedupe_one_open_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    await RoadmapEngine(db_session).run_cycle()
    second = await RoadmapEngine(db_session).run_cycle()
    assert second is None
    assert len(await get_task_service(db_session).list_open_roadmap_cycles()) == ONE


@pytest.mark.asyncio
async def test_unresolvable_project_no_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _enable(monkeypatch)
    monkeypatch.setattr(cfg, "self_heal_project_slug", "no-such-project")
    engine = RoadmapEngine(db_session)
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_roadmap_cycles() == []
