"""TgCockpitService coverage: the /telegram/today aggregate composes
needs-you counts, fleet snapshot, spend, and ship state from seeded rows —
and degrades to an all-zeros brief on an empty company.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.db.tables import AgentTable, TaskTable
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    Complexity,
    Team,
)
from roboco.models.base import TaskNature as TN
from roboco.models.base import TaskStatus as TS
from roboco.models.base import TaskType as TT
from roboco.services.task import (
    RELEASE_MANAGER_SOURCE,
    ROADMAP_SOURCE,
    VIDEO_POST_SOURCE,
    X_POST_SOURCE,
)
from roboco.services.tg_cockpit import get_tg_cockpit_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid

CI_WATCH_SOURCE = "ci_watch"

# 1 awaiting + 1 blocked + 4 held drafts (release/x/video/roadmap-item).
EXPECTED_NEEDS_YOU_TOTAL = 6


async def _seed_system_agent(session: AsyncSession) -> None:
    if await session.get(AgentTable, SYSTEM_UUID) is None:
        session.add(
            AgentTable(
                id=SYSTEM_UUID,
                name="system",
                slug="system",
                role=AgentRole.SYSTEM,
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


def _task(
    title: str,
    task_status: TS,
    *,
    source: str | None = None,
    team: Team = Team.BACKEND,
) -> TaskTable:
    return TaskTable(
        id=uuid4(),
        title=title,
        description="A description long enough to satisfy any length floor.",
        acceptance_criteria=["it is visible on the Today brief"],
        status=task_status,
        priority=2,
        task_type=TT.ADMINISTRATIVE,
        nature=TN.NON_TECHNICAL,
        estimated_complexity=Complexity.LOW,
        created_by=SYSTEM_UUID,
        team=team,
        source=source,
        confirmed_by_human=True,
    )


async def _seed_working_agent(session: AsyncSession, current_task_id: UUID) -> None:
    session.add(
        AgentTable(
            id=uuid4(),
            name="be-dev-1",
            slug="be-dev-1",
            role=AgentRole.DEVELOPER,
            team=Team.BACKEND,
            status=AgentStatus.ACTIVE,
            model_config={},
            system_prompt="x",
            capabilities=[],
            permissions={},
            metrics={},
            current_task_id=current_task_id,
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_today_is_all_zeros_on_an_empty_company(
    db_session: AsyncSession,
) -> None:
    brief = await get_tg_cockpit_service(db_session).today()

    assert brief["needs_you"]["total"] == 0
    assert brief["needs_you"]["awaiting_ceo"] == []
    assert brief["needs_you"]["blocked"] == []
    assert brief["fleet"]["working"] == []
    assert brief["spend"] == {"tokens_today": 0, "cost_today_usd": 0.0}
    assert brief["ship"]["version"] == settings.app_version
    assert brief["ship"]["open_release_proposal"] is False
    assert brief["ship"]["ci_fix_tasks"] == 0


@pytest.mark.asyncio
async def test_today_composes_needs_you_fleet_and_ship(
    db_session: AsyncSession,
) -> None:
    await _seed_system_agent(db_session)

    awaiting = _task("Root PR ready", TS.AWAITING_CEO_APPROVAL)
    blocked = _task("Stuck on infra", TS.BLOCKED)
    x_draft = _task("X draft", TS.PENDING, source=X_POST_SOURCE, team=Team.BOARD)
    video_draft = _task(
        "Video draft", TS.PENDING, source=VIDEO_POST_SOURCE, team=Team.BOARD
    )
    release_prop = _task(
        "Release 0.26.0", TS.PENDING, source=RELEASE_MANAGER_SOURCE, team=Team.BOARD
    )
    ci_fix = _task("Fix red CI", TS.PENDING, source=CI_WATCH_SOURCE)
    cycle = _task("Roadmap cycle", TS.PENDING, source=ROADMAP_SOURCE, team=Team.BOARD)
    for row in (
        awaiting,
        blocked,
        x_draft,
        video_draft,
        release_prop,
        ci_fix,
        cycle,
    ):
        db_session.add(row)
    await db_session.flush()
    markers.set_roadmap_cycle(
        cycle,
        {
            "goal": "g",
            "items": [
                {"id": "item-0", "status": "proposed"},
                {"id": "item-1", "status": "approved"},
            ],
        },
    )
    await _seed_working_agent(db_session, awaiting.id)

    brief = await get_tg_cockpit_service(db_session).today()

    needs = brief["needs_you"]
    assert needs["awaiting_ceo_count"] == 1
    assert needs["awaiting_ceo"][0]["title"] == "Root PR ready"
    assert needs["awaiting_ceo"][0]["status"] == "awaiting_ceo_approval"
    assert needs["blocked_count"] == 1
    assert needs["held_drafts"] == {
        "release_proposals": 1,
        "x_posts": 1,
        "video_posts": 1,
        "roadmap_items": 1,
    }
    assert needs["total"] == EXPECTED_NEEDS_YOU_TOTAL

    working = brief["fleet"]["working"]
    assert len(working) == 1
    assert working[0]["name"] == "be-dev-1"
    assert working[0]["task_title"] == "Root PR ready"

    assert brief["ship"]["open_release_proposal"] is True
    assert brief["ship"]["ci_fix_tasks"] == 1
