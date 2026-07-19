"""Member / org rollup scorecards + the live in-flight overlay (real PG).

Seeds member_performance_daily rows (the rollup source) and asserts the derived
rates (FPY, effort-throughput, turns/task, qa pass-rate, utilization), the live
in-flight overlay (enriches effort but not completion counts — disjoint by
status), and the division guards.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import (
    AgentSpawnSessionTable,
    AgentTable,
    MemberPerformanceDailyTable,
    ProjectTable,
    TaskTable,
)
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    Complexity,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.services.metrics import MetricsService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

_TODAY = datetime.now(UTC).date()
_TOTAL_COMPLETED = 3
_ROLLUP_COMPLETED = 2
_OVERLAY_TURNS = 3
_ROLLUP_ONLY_TURNS = 5  # closed session already in the rollup, not re-added
_ORG_MEMBERS = 2
_ORG_COMPLETED = 3


def _daily(slug: str, **over: Any) -> MemberPerformanceDailyTable:
    base: dict[str, Any] = {
        "id": uuid4(),
        "date": _TODAY,
        "member_kind": "agent",
        "agent_slug": slug,
        "team": Team.BACKEND.value,
        "role": "developer",
    }
    base.update(over)
    return MemberPerformanceDailyTable(**base)


def _agent(role: AgentRole, slug: str) -> AgentTable:
    return AgentTable(
        id=uuid4(),
        name=slug,
        slug=slug,
        role=role,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )


@pytest_asyncio.fixture
async def svc(db_session: AsyncSession) -> AsyncIterator[MetricsService]:
    yield MetricsService(db_session)


@pytest.mark.asyncio
async def test_member_scorecard_rollup_and_derived(
    svc: MetricsService, db_session: AsyncSession
) -> None:
    dev = _agent(AgentRole.DEVELOPER, f"be-dev-{uuid4().hex[:6]}")
    db_session.add(dev)
    await db_session.flush()
    db_session.add_all(
        [
            _daily(
                dev.slug,
                tasks_completed=2,
                tasks_first_pass=1,
                active_runtime_seconds=1800,
                turns=6,
                tool_calls=12,
                tokens=100,
                cost_usd=1.0,
                qa_reviews_total=3,
                qa_reviews_passed=2,
                escalations=1,
                blocked_others=1,
                idle_seconds=600,
                revisions_caused=1,
                revisions_received=1,
            ),
            _daily(
                dev.slug,
                date=_TODAY - timedelta(days=1),
                tasks_completed=1,
                tasks_first_pass=1,
                active_runtime_seconds=1800,
                turns=4,
                tool_calls=8,
                tokens=50,
                cost_usd=0.5,
                qa_reviews_total=2,
                qa_reviews_passed=2,
                idle_seconds=1200,
            ),
        ]
    )
    await db_session.flush()

    card = await svc.get_member_scorecard(cast("UUID", dev.id), days=30)
    assert card is not None
    assert card.tasks_completed == _TOTAL_COMPLETED
    assert card.first_pass_yield == pytest.approx(2 / 3, abs=1e-4)  # 2 of 3
    # 3 tasks over 3600s = 1h -> 3.0/hr.
    assert card.effort_throughput_per_hour == pytest.approx(3.0)
    assert (card.turns, card.tool_calls) == (10, 20)
    assert card.turns_per_task == pytest.approx(10 / 3, abs=1e-4)
    assert card.qa_pass_rate == pytest.approx(4 / 5, abs=1e-4)  # 4 of 5
    assert card.escalations == 1
    assert card.blocked_others == 1
    # util = 3600 active / (3600 + 1800 idle) = 0.6667.
    assert card.utilization == pytest.approx(3600 / 5400, abs=1e-4)
    assert card.includes_live_inflight is False


@pytest.mark.asyncio
async def test_live_overlay_enriches_effort_not_completion(
    svc: MetricsService, db_session: AsyncSession
) -> None:
    dev = _agent(AgentRole.DEVELOPER, f"be-dev-{uuid4().hex[:6]}")
    db_session.add(dev)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="P",
        slug=f"p-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=dev.id,
    )
    db_session.add(project)
    await db_session.flush()
    db_session.add(_daily(dev.slug, tasks_completed=2, active_runtime_seconds=100))
    # A non-terminal (in-flight) task with a spawn stint -> overlay effort.
    inflight = TaskTable(
        id=uuid4(),
        title="t",
        description="d",
        acceptance_criteria=["ac"],
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        status=TaskStatus.IN_PROGRESS,
        team=Team.BACKEND,
        project_id=project.id,
        created_by=dev.id,
        assigned_to=dev.id,
        estimated_complexity=Complexity.MEDIUM,
        started_at=datetime.now(UTC) - timedelta(hours=1),
    )
    db_session.add(inflight)
    await db_session.flush()
    now = datetime.now(UTC)
    # OPEN (still-running) session — the live delta the rollup cannot hold yet.
    db_session.add(
        AgentSpawnSessionTable(
            id=uuid4(),
            agent_slug=dev.slug,
            team="backend",
            role="developer",
            model="claude",
            task_id=str(inflight.id),
            started_at=now - timedelta(seconds=200),
            ended_at=None,
            turns=3,
            tool_calls=4,
            tokens_input=10,
            tokens_output=5,
            estimated_cost_usd=0.2,
        )
    )
    await db_session.flush()

    card = await svc.get_member_scorecard(cast("UUID", dev.id), days=30)
    assert card is not None
    assert card.tasks_completed == _ROLLUP_COMPLETED  # in-flight NOT completed
    assert card.includes_live_inflight is True
    assert card.active_runtime_hours > 100 / 3600  # rollup + overlay effort
    assert card.turns == _OVERLAY_TURNS  # from the overlay (rollup row had 0)


@pytest.mark.asyncio
async def test_live_overlay_excludes_closed_session_no_double_count(
    svc: MetricsService, db_session: AsyncSession
) -> None:
    """A CLOSED session on a non-terminal task is already in the daily rollup
    (via _msweep_spawn, which counts ended_at IS NOT NULL). The overlay must NOT
    re-add it, or the member's effort/turns double-count on the common
    reap/respawn path."""
    dev = _agent(AgentRole.DEVELOPER, f"be-dev-{uuid4().hex[:6]}")
    db_session.add(dev)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="P",
        slug=f"p-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=dev.id,
    )
    db_session.add(project)
    await db_session.flush()
    # Rollup row already reflects the closed session (turns=5, active=300s).
    db_session.add(
        _daily(dev.slug, tasks_completed=0, active_runtime_seconds=300, turns=5)
    )
    inflight = TaskTable(
        id=uuid4(),
        title="t",
        description="d",
        acceptance_criteria=["ac"],
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        status=TaskStatus.IN_PROGRESS,
        team=Team.BACKEND,
        project_id=project.id,
        created_by=dev.id,
        assigned_to=dev.id,
        estimated_complexity=Complexity.MEDIUM,
        started_at=datetime.now(UTC) - timedelta(hours=1),
    )
    db_session.add(inflight)
    await db_session.flush()
    now = datetime.now(UTC)
    db_session.add(
        AgentSpawnSessionTable(
            id=uuid4(),
            agent_slug=dev.slug,
            team="backend",
            role="developer",
            model="claude",
            task_id=str(inflight.id),
            started_at=now - timedelta(seconds=300),
            ended_at=now,  # CLOSED — already counted by the rollup
            turns=5,
            tool_calls=4,
            tokens_input=10,
            tokens_output=5,
            estimated_cost_usd=0.2,
        )
    )
    await db_session.flush()

    card = await svc.get_member_scorecard(cast("UUID", dev.id), days=30)
    assert card is not None
    assert card.turns == _ROLLUP_ONLY_TURNS  # closed session is NOT re-added
    assert card.active_runtime_hours == pytest.approx(300 / 3600, abs=1e-4)
    assert card.includes_live_inflight is False  # no OPEN session


@pytest.mark.asyncio
async def test_member_scorecard_404_and_guards(
    svc: MetricsService, db_session: AsyncSession
) -> None:
    assert await svc.get_member_scorecard(uuid4()) is None
    # An agent with no rollup rows: division guards -> None, no crash.
    dev = _agent(AgentRole.DEVELOPER, f"be-dev-{uuid4().hex[:6]}")
    db_session.add(dev)
    await db_session.flush()
    card = await svc.get_member_scorecard(cast("UUID", dev.id), days=30)
    assert card is not None
    assert card.tasks_completed == 0
    assert card.first_pass_yield is None
    assert card.effort_throughput_per_hour is None
    assert card.qa_pass_rate is None
    assert card.utilization is None


_ALL_MEMBERS_EXPECTED = 3
_DEV2_OVERLAY_TURNS = 7


@pytest.mark.asyncio
async def test_all_member_scorecards_matches_single_agent_and_excludes_ceo_system(
    svc: MetricsService, db_session: AsyncSession
) -> None:
    """get_all_member_scorecards (the N+1 batch fix) must return the exact
    same derived numbers get_member_scorecard would for the same agent, plus:
    CEO/SYSTEM excluded, and a rollup-less agent still appears zeroed instead
    of silently dropped."""
    dev1 = _agent(AgentRole.DEVELOPER, f"be-dev-{uuid4().hex[:6]}")
    dev2 = _agent(AgentRole.QA, f"be-qa-{uuid4().hex[:6]}")
    dev3 = _agent(AgentRole.DEVELOPER, f"be-dev-{uuid4().hex[:6]}")  # no rollup rows
    ceo = _agent(AgentRole.CEO, "ceo")
    system = _agent(AgentRole.SYSTEM, "system")
    db_session.add_all([dev1, dev2, dev3, ceo, system])
    await db_session.flush()
    db_session.add_all(
        [
            _daily(
                dev1.slug,
                tasks_completed=2,
                tasks_first_pass=1,
                active_runtime_seconds=1800,
                turns=6,
                qa_reviews_total=3,
                qa_reviews_passed=2,
                idle_seconds=600,
            ),
            _daily(dev2.slug, tasks_completed=1, tasks_first_pass=1),
        ]
    )
    await db_session.flush()

    cards = await svc.get_all_member_scorecards(team=Team.BACKEND, days=30)
    by_id = {c.id: c for c in cards}
    assert len(cards) == _ALL_MEMBERS_EXPECTED
    assert str(ceo.id) not in by_id
    assert str(system.id) not in by_id

    single = await svc.get_member_scorecard(cast("UUID", dev1.id), days=30)
    assert single is not None
    batch_dev1 = by_id[str(dev1.id)]
    assert batch_dev1.tasks_completed == single.tasks_completed
    assert batch_dev1.turns == single.turns
    assert batch_dev1.first_pass_yield == single.first_pass_yield
    assert batch_dev1.qa_pass_rate == single.qa_pass_rate
    assert batch_dev1.utilization == single.utilization

    # A roster member with no rollup rows still appears (zeroed), not dropped.
    zeroed = by_id[str(dev3.id)]
    assert zeroed.tasks_completed == 0
    assert zeroed.first_pass_yield is None
    assert zeroed.utilization is None


@pytest.mark.asyncio
async def test_all_member_scorecards_attributes_live_overlay_per_agent(
    svc: MetricsService, db_session: AsyncSession
) -> None:
    """The batched overlay query must attribute each agent's OPEN spawn
    session to that agent alone — not merge every in-flight agent's effort
    into one bucket."""
    dev1 = _agent(AgentRole.DEVELOPER, f"be-dev-{uuid4().hex[:6]}")
    dev2 = _agent(AgentRole.DEVELOPER, f"be-dev-{uuid4().hex[:6]}")
    db_session.add_all([dev1, dev2])
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="P",
        slug=f"p-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=dev1.id,
    )
    db_session.add(project)
    await db_session.flush()
    db_session.add_all(
        [
            _daily(dev1.slug, tasks_completed=1, active_runtime_seconds=100),
            _daily(dev2.slug, tasks_completed=1, active_runtime_seconds=200),
        ]
    )

    def _inflight(owner_id: UUID) -> TaskTable:
        return TaskTable(
            id=uuid4(),
            title="t",
            description="d",
            acceptance_criteria=["ac"],
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            status=TaskStatus.IN_PROGRESS,
            team=Team.BACKEND,
            project_id=project.id,
            created_by=owner_id,
            assigned_to=owner_id,
            estimated_complexity=Complexity.MEDIUM,
            started_at=datetime.now(UTC) - timedelta(hours=1),
        )

    t1 = _inflight(cast("UUID", dev1.id))
    t2 = _inflight(cast("UUID", dev2.id))
    db_session.add_all([t1, t2])
    await db_session.flush()
    now = datetime.now(UTC)
    db_session.add_all(
        [
            AgentSpawnSessionTable(
                id=uuid4(),
                agent_slug=dev1.slug,
                team="backend",
                role="developer",
                model="claude",
                task_id=str(t1.id),
                started_at=now - timedelta(seconds=200),
                ended_at=None,
                turns=_OVERLAY_TURNS,
                tool_calls=4,
                tokens_input=10,
                tokens_output=5,
                estimated_cost_usd=0.2,
            ),
            AgentSpawnSessionTable(
                id=uuid4(),
                agent_slug=dev2.slug,
                team="backend",
                role="developer",
                model="claude",
                task_id=str(t2.id),
                started_at=now - timedelta(seconds=500),
                ended_at=None,
                turns=_DEV2_OVERLAY_TURNS,
                tool_calls=9,
                tokens_input=20,
                tokens_output=8,
                estimated_cost_usd=0.5,
            ),
        ]
    )
    await db_session.flush()

    cards = {c.id: c for c in await svc.get_all_member_scorecards(days=30)}
    c1, c2 = cards[str(dev1.id)], cards[str(dev2.id)]
    assert c1.includes_live_inflight is True
    assert c2.includes_live_inflight is True
    assert c1.turns == _OVERLAY_TURNS
    assert c2.turns == _DEV2_OVERLAY_TURNS


@pytest.mark.asyncio
async def test_org_scorecard_aggregates_members(
    svc: MetricsService, db_session: AsyncSession
) -> None:
    s1, s2 = f"be-dev-{uuid4().hex[:6]}", f"be-dev-{uuid4().hex[:6]}"
    db_session.add_all(
        [
            _daily(
                s1, tasks_completed=2, tasks_first_pass=2, active_runtime_seconds=3600
            ),
            _daily(
                s2, tasks_completed=1, tasks_first_pass=0, active_runtime_seconds=3600
            ),
        ]
    )
    await db_session.flush()
    org = await svc.get_org_scorecard(team=Team.BACKEND, days=30)
    assert org.scope == "team"
    assert org.member_count == _ORG_MEMBERS
    assert org.tasks_completed == _ORG_COMPLETED
    assert org.first_pass_yield == pytest.approx(2 / 3, abs=1e-4)
