"""TgCockpitService coverage: the /telegram/today aggregate composes
needs-you counts, fleet snapshot, spend, and ship state from seeded rows.

Assertions are DELTAS against a pre-seed baseline, never absolute counts —
CI runs the whole test tree in one process against one database, so other
suites' committed rows are visible here and an "empty company" cannot be
assumed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
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
    # priority 0 (critical) sorts seeded rows ahead of any leaked ones, so
    # they stay inside the brief's per-section item caps.
    return TaskTable(
        id=uuid4(),
        title=title,
        description="A description long enough to satisfy any length floor.",
        acceptance_criteria=["it is visible on the Today brief"],
        status=task_status,
        priority=0,
        task_type=TT.ADMINISTRATIVE,
        nature=TN.NON_TECHNICAL,
        estimated_complexity=Complexity.LOW,
        created_by=SYSTEM_UUID,
        team=team,
        source=source,
        confirmed_by_human=True,
    )


async def _seed_working_agent(session: AsyncSession, current_task_id: UUID) -> str:
    slug = f"be-dev-{uuid4().hex[:6]}"
    session.add(
        AgentTable(
            id=uuid4(),
            name=slug,
            slug=slug,
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
    return slug


@pytest.mark.asyncio
async def test_today_brief_shape(db_session: AsyncSession) -> None:
    """Structure + invariants that hold regardless of shared-DB residue."""
    brief = await get_tg_cockpit_service(db_session).today()

    assert set(brief) == {"needs_you", "fleet", "spend", "velocity", "ship"}
    assert len(brief["spend"]["series"]) == 7  # noqa: PLR2004
    assert len(brief["velocity"]["series"]) == 7  # noqa: PLR2004
    needs = brief["needs_you"]
    assert needs["total"] == (
        needs["awaiting_ceo_count"]
        + needs["blocked_count"]
        + sum(needs["held_drafts"].values())
    )
    assert isinstance(brief["spend"]["tokens_today"], int)
    assert isinstance(brief["spend"]["cost_today_usd"], float)
    assert brief["ship"]["version"] == settings.app_version


@pytest.mark.asyncio
async def test_today_composes_needs_you_fleet_and_ship(
    db_session: AsyncSession,
) -> None:
    await _seed_system_agent(db_session)
    baseline = await get_tg_cockpit_service(db_session).today()

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
    agent_slug = await _seed_working_agent(db_session, cast("UUID", awaiting.id))

    brief = await get_tg_cockpit_service(db_session).today()

    needs, base_needs = brief["needs_you"], baseline["needs_you"]
    assert needs["awaiting_ceo_count"] == base_needs["awaiting_ceo_count"] + 1
    seeded = next(
        item for item in needs["awaiting_ceo"] if item["title"] == "Root PR ready"
    )
    assert seeded["status"] == "awaiting_ceo_approval"
    assert needs["blocked_count"] == base_needs["blocked_count"] + 1
    for key in ("release_proposals", "x_posts", "video_posts", "roadmap_items"):
        assert needs["held_drafts"][key] == base_needs["held_drafts"][key] + 1
    assert needs["total"] == base_needs["total"] + EXPECTED_NEEDS_YOU_TOTAL

    workers = {
        agent["name"]: agent.get("task_title") for agent in brief["fleet"]["working"]
    }
    assert workers.get(agent_slug) == "Root PR ready"

    assert brief["ship"]["open_release_proposal"] is True
    assert brief["ship"]["ci_fix_tasks"] == baseline["ship"]["ci_fix_tasks"] + 1
