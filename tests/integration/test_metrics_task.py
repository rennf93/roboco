"""get_task_metrics — granular per-task effort against a real Postgres.

Seeds a task's audit-log journey + agent spawn stints (with turns/tool_calls/
tokens/cost) + named qa/pr fail events, then asserts the composed metrics:
summed effort vs wall-clock, turns/tool_calls/tokens/cost, per-stage
active-vs-wait, and who-caused-rework.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, NamedTuple
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import (
    AgentSpawnSessionTable,
    AgentTable,
    AuditLogTable,
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

    from sqlalchemy.ext.asyncio import AsyncSession

_T0 = datetime(2026, 6, 20, 12, 0, 0, tzinfo=UTC)


def _sec(n: int) -> datetime:
    return _T0 + timedelta(seconds=n)


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


def _audit(
    task_id: Any,
    status: str,
    ts: datetime,
    *,
    agent_id: Any = None,
    event_type: str | None = None,
) -> AuditLogTable:
    return AuditLogTable(
        id=uuid4(),
        event_type=event_type or f"task.{status}",
        agent_id=agent_id,
        target_type="task",
        target_id=task_id,
        severity="info",
        details={"to_status": status, "from_status": "prev", "team": "backend"},
        timestamp=ts,
    )


class _Usage(NamedTuple):
    turns: int
    tool_calls: int
    tokens_in: int
    tokens_out: int
    cost: float


def _spawn(
    task_id: str,
    started: datetime,
    ended: datetime | None,
    usage: _Usage,
) -> AgentSpawnSessionTable:
    return AgentSpawnSessionTable(
        id=uuid4(),
        agent_slug="be-dev-1",
        team="backend",
        role="developer",
        model="claude",
        task_id=task_id,
        started_at=started,
        ended_at=ended,
        turns=usage.turns,
        tool_calls=usage.tool_calls,
        tokens_input=usage.tokens_in,
        tokens_output=usage.tokens_out,
        estimated_cost_usd=usage.cost,
    )


@pytest_asyncio.fixture
async def setup(db_session: AsyncSession) -> AsyncIterator[dict]:
    dev = _agent(AgentRole.DEVELOPER, f"be-dev-{uuid4().hex[:6]}")
    qa = _agent(AgentRole.QA, f"be-qa-{uuid4().hex[:6]}")
    db_session.add_all([dev, qa])
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
    yield {
        "svc": MetricsService(db_session),
        "db": db_session,
        "project_id": project.id,
        "dev_id": dev.id,
        "qa_id": qa.id,
    }


@pytest.mark.asyncio
async def test_returns_none_for_missing_task(setup: dict) -> None:
    assert await setup["svc"].get_task_metrics(uuid4()) is None


@pytest.mark.asyncio
async def test_composes_effort_turns_stages_and_rework(setup: dict) -> None:
    db = setup["db"]
    tid = uuid4()
    db.add(
        TaskTable(
            id=tid,
            title="t",
            description="d",
            acceptance_criteria=["ac"],
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            status=TaskStatus.COMPLETED,
            team=Team.BACKEND,
            project_id=setup["project_id"],
            created_by=setup["dev_id"],
            assigned_to=setup["dev_id"],
            revision_count=2,
            estimated_complexity=Complexity.MEDIUM,
            started_at=_T0,
            completed_at=_sec(7200),
        )
    )
    db.add_all(
        [
            _audit(tid, "claimed", _T0),
            _audit(tid, "in_progress", _sec(60)),
            _audit(tid, "awaiting_qa", _sec(3660)),
            _audit(tid, "completed", _sec(7200)),
            _audit(tid, "needs_revision", _sec(3660), event_type="task.qa_fail"),
            _audit(tid, "needs_revision", _sec(3000), event_type="task.pr_fail"),
        ]
    )
    db.add_all(
        [
            _spawn(str(tid), _T0, _sec(600), _Usage(5, 10, 100, 50, 1.0)),
            _spawn(str(tid), _sec(3600), _sec(3660), _Usage(3, 4, 20, 10, 0.5)),
        ]
    )
    await db.flush()

    m = await setup["svc"].get_task_metrics(tid)
    assert m is not None
    # summed effort (600 + 60) vs wall-clock (2h).
    expected_active_s = 660
    expected_wall_s = 7200
    assert m.active_runtime_seconds == expected_active_s
    assert m.wall_clock_seconds == expected_wall_s
    assert (m.turns, m.tool_calls, m.tokens) == (8, 14, 180)
    assert m.cost_usd == pytest.approx(1.5)
    assert (m.revision_count, m.qa_fails, m.pr_fails, m.stints) == (2, 1, 1, 2)

    stages = {s.status: s for s in m.stages}
    # claimed [0,60): stint1 covers it fully.
    assert (stages["claimed"].active_seconds, stages["claimed"].wait_seconds) == (60, 0)
    # in_progress [60,3660): stint1 60..600 (540) + stint2 3600..3660 (60) = 600 active.
    assert (
        stages["in_progress"].active_seconds,
        stages["in_progress"].wait_seconds,
    ) == (600, 3000)
    # awaiting_qa [3660,7200): no stint running -> all wait.
    assert (
        stages["awaiting_qa"].active_seconds,
        stages["awaiting_qa"].wait_seconds,
    ) == (0, 3540)


@pytest.mark.asyncio
async def test_in_flight_open_stint_and_open_window_decompose(setup: dict) -> None:
    db = setup["db"]
    tid = uuid4()
    db.add(
        TaskTable(
            id=tid,
            title="t",
            description="d",
            acceptance_criteria=["ac"],
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            status=TaskStatus.IN_PROGRESS,
            team=Team.BACKEND,
            project_id=setup["project_id"],
            created_by=setup["dev_id"],
            assigned_to=setup["dev_id"],
            estimated_complexity=Complexity.MEDIUM,
            started_at=_T0,
            completed_at=None,
        )
    )
    db.add_all([_audit(tid, "claimed", _T0), _audit(tid, "in_progress", _sec(60))])
    # An OPEN stint (ended_at=None) -> runs to now.
    db.add(_spawn(str(tid), _T0, None, _Usage(2, 3, 1, 1, 0.1)))
    await db.flush()

    m = await setup["svc"].get_task_metrics(tid)
    assert m is not None
    assert m.stints == 1
    assert m.active_runtime_seconds > 0  # open stint ran to now
    assert m.wall_clock_seconds > 0  # open task -> now
    # The open final window (in_progress) still decomposes.
    assert "in_progress" in {s.status for s in m.stages}
