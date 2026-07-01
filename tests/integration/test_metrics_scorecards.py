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
    db_session.add(
        AgentSpawnSessionTable(
            id=uuid4(),
            agent_slug=dev.slug,
            team="backend",
            role="developer",
            model="claude",
            task_id=str(inflight.id),
            started_at=now - timedelta(seconds=200),
            ended_at=now,
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
