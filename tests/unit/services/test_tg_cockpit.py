"""TgCockpitService coverage: the /telegram/today aggregate composes
needs-you counts, fleet snapshot, spend, and ship state from seeded rows.

Assertions are DELTAS against a pre-seed baseline, never absolute counts —
CI runs the whole test tree in one process against one database, so other
suites' committed rows are visible here and an "empty company" cannot be
assumed.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.db.tables import AgentSpawnSessionTable, AgentTable, TaskTable
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

_COST_TOL = 0.005
_TOKENS_FLOOR = 1


def _spawn_session(
    *, started_at: datetime, model: str, cost: float, tokens_input: int = 1000
) -> AgentSpawnSessionTable:
    return AgentSpawnSessionTable(
        id=uuid4(),
        agent_slug=f"be-dev-{uuid4().hex[:6]}",
        team="backend",
        role="developer",
        model=model,
        task_id=None,
        started_at=started_at,
        ended_at=started_at + timedelta(minutes=5),
        tokens_input=tokens_input,
        tokens_output=0,
        estimated_cost_usd=cost,
    )


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


# ---------------------------------------------------------------------------
# Display-timezone bucketing (Issue 2) — "today" is display_timezone-aware,
# not always the server's UTC day. `_session_metrics_by_day` is called
# directly with an explicit historical `days` window so the test is fully
# deterministic (disconnected from the real "now").
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_metrics_by_day_buckets_by_display_timezone(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """23:30 UTC on the 15th is already 00:30 on the 16th in Europe/Berlin
    (winter, CET = UTC+1) — the exact 'evening activity lands on the wrong
    display day' bug this fix targets."""
    started = datetime(2026, 1, 15, 23, 30, tzinfo=UTC)
    session_row = _spawn_session(started_at=started, model="claude-sonnet-5", cost=1.23)
    db_session.add(session_row)
    await db_session.flush()

    svc = get_tg_cockpit_service(db_session)

    monkeypatch.setattr(settings, "display_timezone", "UTC")
    cost_utc, tokens_utc, _ = await svc._session_metrics_by_day([date(2026, 1, 15)])
    assert cost_utc.get(date(2026, 1, 15), 0.0) >= _COST_TOL
    assert tokens_utc.get(date(2026, 1, 15), 0) >= _TOKENS_FLOOR

    monkeypatch.setattr(settings, "display_timezone", "Europe/Berlin")
    cost_berlin, tokens_berlin, _ = await svc._session_metrics_by_day(
        [date(2026, 1, 16)]
    )
    assert cost_berlin.get(date(2026, 1, 16), 0.0) >= _COST_TOL
    assert tokens_berlin.get(date(2026, 1, 16), 0) >= _TOKENS_FLOOR
    # And the SAME row must NOT double-count into the UTC calendar day under
    # the Berlin bucketing — the 15th should now come up empty for this row.
    cost_berlin_15, _, _ = await svc._session_metrics_by_day([date(2026, 1, 15)])
    assert cost_berlin_15.get(date(2026, 1, 15), 0.0) < _COST_TOL


@pytest.mark.asyncio
async def test_window_dates_shifts_with_display_timezone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_window_dates` (real 'now') must reflect the configured display
    timezone, not always UTC."""
    svc = get_tg_cockpit_service(cast("AsyncSession", None))
    monkeypatch.setattr(settings, "display_timezone", "UTC")
    utc_dates = svc._window_dates()
    monkeypatch.setattr(settings, "display_timezone", "Pacific/Kiritimati")
    # UTC+14 — the furthest-ahead real timezone; "today" there is never
    # earlier, and is later whenever UTC hasn't crossed its own midnight yet.
    kiritimati_dates = svc._window_dates()
    assert kiritimati_dates[-1] >= utc_dates[-1]


# ---------------------------------------------------------------------------
# Ollama Cloud honesty-labeling (Issue 1) — an ungrounded ':cloud' model's $0
# is flagged subscription_billed, never rendered as a bare misleading "$0".
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_today_spend_flags_ungrounded_ollama_cloud_as_subscription_billed(
    db_session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    db_session.add(
        _spawn_session(started_at=now, model="some-future-model:cloud", cost=0.0)
    )
    await db_session.flush()

    summary = await get_tg_cockpit_service(db_session).today_spend()

    assert summary["cost_today_usd"] == pytest.approx(0.0)
    assert summary["subscription_billed"] is True


@pytest.mark.asyncio
async def test_today_spend_not_subscription_billed_when_priced(
    db_session: AsyncSession,
) -> None:
    """A real per-token cost (even from a priced Ollama Cloud model like
    GLM-5.2) is never mislabeled as an untracked subscription figure."""
    now = datetime.now(UTC)
    db_session.add(_spawn_session(started_at=now, model="glm-5.2:cloud", cost=2.5))
    await db_session.flush()

    summary = await get_tg_cockpit_service(db_session).today_spend()

    assert summary["subscription_billed"] is False


@pytest.mark.asyncio
async def test_today_spend_not_subscription_billed_for_local_ollama(
    db_session: AsyncSession,
) -> None:
    """A genuinely-free self-hosted model (no ':cloud' tag) at $0 is just
    free, not an untracked subscription."""
    now = datetime.now(UTC)
    db_session.add(_spawn_session(started_at=now, model="ollama/llama3", cost=0.0))
    await db_session.flush()

    summary = await get_tg_cockpit_service(db_session).today_spend()

    assert summary["subscription_billed"] is False
