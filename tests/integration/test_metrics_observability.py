"""0.10.0 observability metric layer: cycle-time, bottleneck, rework, scorecard.

Seeds an audit_log journey + reworked tasks + rejector-attributed fail events +
spawn-session costs against a real Postgres and asserts the reconstructed
metrics. The named task.qa_fail / task.pr_fail events must NOT pollute the
cycle-time reconstruction (they share a timestamp with the needs_revision row).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
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

# Relative, not hardcoded: the service windows (30d metrics, 7d scorecards)
# filter on now(), so a fixed date silently ages out of the window and the
# suite detonates weeks later. Two days back sits inside every window.
_T0 = datetime.now(UTC) - timedelta(days=2)
_EXPECTED_TASKS = 2  # completed tasks seeded per rework / scorecard test
_EXPECTED_TOKENS = 1500  # 1000 input + 500 output in the scorecard spawn session


def _agent(role: AgentRole, team: Team, slug: str) -> AgentTable:
    return AgentTable(
        id=uuid4(),
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


def _task(
    project_id: Any,
    created_by: Any,
    *,
    assigned_to: Any = None,
    revision_count: int = 0,
    started_hours_ago: int | None = None,
) -> TaskTable:
    """A COMPLETED backend task completed `now` (in-window), optionally started."""
    started = (
        datetime.now(UTC) - timedelta(hours=started_hours_ago)
        if started_hours_ago is not None
        else None
    )
    return TaskTable(
        id=uuid4(),
        title="t",
        description="d",
        acceptance_criteria=["ac"],
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        status=TaskStatus.COMPLETED,
        team=Team.BACKEND,
        project_id=project_id,
        created_by=created_by,
        assigned_to=assigned_to,
        revision_count=revision_count,
        estimated_complexity=Complexity.MEDIUM,
        completed_at=datetime.now(UTC),
        started_at=started,
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


def _spawn(
    slug: str, *, task_id: str | None, cost: float, tokens_in: int, tokens_out: int
) -> AgentSpawnSessionTable:
    return AgentSpawnSessionTable(
        id=uuid4(),
        agent_slug=slug,
        team="backend",
        role="developer",
        model="claude",
        task_id=task_id,
        started_at=datetime.now(UTC) - timedelta(hours=1),
        tokens_input=tokens_in,
        tokens_output=tokens_out,
        estimated_cost_usd=cost,
    )


@pytest_asyncio.fixture
async def obs_setup(db_session: AsyncSession) -> AsyncIterator[dict]:
    dev = _agent(AgentRole.DEVELOPER, Team.BACKEND, f"be-dev-{uuid4().hex[:6]}")
    qa = _agent(AgentRole.QA, Team.BACKEND, f"be-qa-{uuid4().hex[:6]}")
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
        "dev_slug": dev.slug,
    }


@pytest.mark.asyncio
async def test_cycle_time_reconstructs_per_stage_dwell(obs_setup: dict) -> None:
    db = obs_setup["db"]
    tid = uuid4()
    # claimed (60s) -> in_progress (3600s) -> awaiting_qa (120s) -> completed
    db.add_all(
        [
            _audit(tid, "claimed", _T0),
            _audit(tid, "in_progress", _T0 + timedelta(seconds=60)),
            _audit(tid, "awaiting_qa", _T0 + timedelta(seconds=60 + 3600)),
            _audit(tid, "completed", _T0 + timedelta(seconds=60 + 3600 + 120)),
            # A named fail event sharing the awaiting_qa timestamp must be ignored.
            _audit(
                tid,
                "needs_revision",
                _T0 + timedelta(seconds=60 + 3600),
                event_type="task.qa_fail",
            ),
        ]
    )
    await db.flush()
    stages = {s.status: s for s in await obs_setup["svc"].get_cycle_time_by_stage()}
    assert stages["claimed"].avg_seconds == pytest.approx(60.0)
    assert stages["in_progress"].avg_seconds == pytest.approx(3600.0)
    assert stages["awaiting_qa"].avg_seconds == pytest.approx(120.0)
    # The named qa_fail event did not create a zero-length needs_revision stage.
    assert "needs_revision" not in stages


@pytest.mark.asyncio
async def test_bottleneck_ranks_longest_cumulative_stage(obs_setup: dict) -> None:
    db = obs_setup["db"]
    tid = uuid4()
    db.add_all(
        [
            _audit(tid, "claimed", _T0),
            _audit(tid, "in_progress", _T0 + timedelta(seconds=60)),
            _audit(tid, "awaiting_qa", _T0 + timedelta(seconds=60 + 7200)),
            _audit(tid, "completed", _T0 + timedelta(seconds=60 + 7200 + 30)),
        ]
    )
    await db.flush()
    report = await obs_setup["svc"].get_bottleneck_distribution()
    assert report.worst_stage == "in_progress"
    assert report.by_stage[0].status == "in_progress"


@pytest.mark.asyncio
async def test_rework_rate_and_attribution(obs_setup: dict) -> None:
    db = obs_setup["db"]
    pid, dev_id, qa_id = (
        obs_setup["project_id"],
        obs_setup["dev_id"],
        obs_setup["qa_id"],
    )
    clean = _task(pid, dev_id, assigned_to=dev_id)
    reworked = _task(pid, dev_id, assigned_to=dev_id, revision_count=2)
    db.add_all([clean, reworked])
    await db.flush()
    # The QA agent bounced the reworked task once (rejector attribution).
    db.add(
        _audit(
            reworked.id,
            "needs_revision",
            datetime.now(UTC) - timedelta(hours=1),
            agent_id=qa_id,
            event_type="task.qa_fail",
        )
    )
    # A spawn session attributes the rework's cost.
    db.add(
        _spawn(
            obs_setup["dev_slug"],
            task_id=str(reworked.id),
            cost=0.42,
            tokens_in=100,
            tokens_out=50,
        )
    )
    await db.flush()

    report = await obs_setup["svc"].get_rework_metrics(days=30)
    assert report.total_completed == _EXPECTED_TASKS
    assert report.total_reworked == 1
    assert report.rate == pytest.approx(0.5)
    assert report.rework_cost_usd == pytest.approx(0.42)
    qa_row = next(a for a in report.by_agent if a.qa_fails > 0)
    assert qa_row.qa_fails == 1
    assert qa_row.pm_rejects == 0
    assert qa_row.ceo_rejects == 0


@pytest.mark.asyncio
async def test_rework_rate_attributes_pm_and_ceo_rejects(obs_setup: dict) -> None:
    """task.request_changes / task.ceo_reject attribute to their rejector
    exactly like task.qa_fail / task.pr_fail — the PM-merge-review reject and
    the CEO reject are rework causes too, not just QA/PR-gate."""
    db = obs_setup["db"]
    pid, dev_id, qa_id = (
        obs_setup["project_id"],
        obs_setup["dev_id"],
        obs_setup["qa_id"],
    )
    pm_reworked = _task(pid, dev_id, assigned_to=dev_id, revision_count=1)
    ceo_reworked = _task(pid, dev_id, assigned_to=dev_id, revision_count=1)
    db.add_all([pm_reworked, ceo_reworked])
    await db.flush()
    db.add_all(
        [
            _audit(
                pm_reworked.id,
                "needs_revision",
                datetime.now(UTC) - timedelta(hours=1),
                agent_id=qa_id,
                event_type="task.request_changes",
            ),
            _audit(
                ceo_reworked.id,
                "needs_revision",
                datetime.now(UTC) - timedelta(hours=1),
                agent_id=qa_id,
                event_type="task.ceo_reject",
            ),
        ]
    )
    await db.flush()

    report = await obs_setup["svc"].get_rework_metrics(days=30)
    # Both named events landed on the same rejector (qa_id) — one aggregate row.
    combined = next(a for a in report.by_agent if a.pm_rejects or a.ceo_rejects)
    assert combined.pm_rejects == 1
    assert combined.ceo_rejects == 1


@pytest.mark.asyncio
async def test_scorecard_agent_and_cell(obs_setup: dict) -> None:
    db = obs_setup["db"]
    pid, dev_id = obs_setup["project_id"], obs_setup["dev_id"]
    db.add_all(
        [
            _task(pid, dev_id, assigned_to=dev_id, started_hours_ago=2),
            _task(
                pid, dev_id, assigned_to=dev_id, revision_count=1, started_hours_ago=4
            ),
        ]
    )
    db.add(
        _spawn(
            obs_setup["dev_slug"],
            task_id=None,
            cost=1.25,
            tokens_in=1000,
            tokens_out=500,
        )
    )
    await db.flush()

    card = await obs_setup["svc"].get_scorecard(agent_id=dev_id, days=7)
    assert card is not None
    assert card.scope == "agent"
    assert card.tasks_completed == _EXPECTED_TASKS
    assert card.rework_rate == pytest.approx(0.5)
    assert card.tokens == _EXPECTED_TOKENS
    assert card.cost_usd == pytest.approx(1.25)

    cell = await obs_setup["svc"].get_scorecard(team=Team.BACKEND, days=7)
    assert cell is not None
    assert cell.scope == "cell"
    assert cell.tasks_completed == _EXPECTED_TASKS

    assert await obs_setup["svc"].get_scorecard(agent_id=uuid4()) is None
