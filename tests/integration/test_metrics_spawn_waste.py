"""MetricsService.get_spawn_waste_metrics — zero-progress spawn pricing.

Seeds ended, task-scoped agent_spawn_sessions rows against a real Postgres
and asserts the zero-progress classification: a session is zero-progress
only when none of (audit_log task.* status-advance, commit, progress_update,
journal entry) falls inside its own [started_at, ended_at] window.
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
    JournalEntryTable,
    JournalTable,
    ProjectTable,
    TaskTable,
)
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    Complexity,
    JournalEntryType,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.services.metrics import MetricsService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

_NOW = datetime.now(UTC)


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


def _task(project_id: Any, created_by: Any) -> TaskTable:
    return TaskTable(
        id=uuid4(),
        title="t",
        description="d",
        acceptance_criteria=["ac"],
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        status=TaskStatus.IN_PROGRESS,
        team=Team.BACKEND,
        project_id=project_id,
        created_by=created_by,
        estimated_complexity=Complexity.MEDIUM,
    )


def _session(
    slug: str, task_id: Any, *, started: datetime, ended: datetime, cost: float
) -> AgentSpawnSessionTable:
    return AgentSpawnSessionTable(
        id=uuid4(),
        agent_slug=slug,
        team="backend",
        role="developer",
        model="claude",
        task_id=str(task_id),
        started_at=started,
        ended_at=ended,
        estimated_cost_usd=cost,
    )


@pytest_asyncio.fixture
async def waste_setup(db_session: AsyncSession) -> AsyncIterator[dict]:
    dev = _agent(AgentRole.DEVELOPER, Team.BACKEND, f"be-dev-{uuid4().hex[:6]}")
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
    yield {
        "svc": MetricsService(db_session),
        "db": db_session,
        "project_id": project.id,
        "dev_id": dev.id,
        "dev_slug": dev.slug,
    }


@pytest.mark.asyncio
async def test_session_with_a_commit_is_not_zero_progress(waste_setup: dict) -> None:
    db = waste_setup["db"]
    task = _task(waste_setup["project_id"], waste_setup["dev_id"])
    started = _NOW - timedelta(hours=1)
    ended = _NOW
    task.commits = [
        {
            "hash": "a" * 40,
            "message": "did the thing",
            "timestamp": (started + timedelta(minutes=10)).isoformat(),
            "author_agent_id": str(waste_setup["dev_id"]),
        }
    ]
    db.add(task)
    db.add(
        _session(
            waste_setup["dev_slug"], task.id, started=started, ended=ended, cost=1.0
        )
    )
    await db.flush()

    report = await waste_setup["svc"].get_spawn_waste_metrics(days=30)
    assert report.total_sessions == 1
    assert report.zero_progress_sessions == 0
    assert report.zero_progress_cost_usd == pytest.approx(0.0)
    assert report.total_cost_usd == pytest.approx(1.0)
    assert report.to_dict()["zero_progress_cost_share"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_session_with_no_signal_is_zero_progress(waste_setup: dict) -> None:
    db = waste_setup["db"]
    task = _task(waste_setup["project_id"], waste_setup["dev_id"])
    started = _NOW - timedelta(hours=1)
    ended = _NOW
    db.add(task)
    db.add(
        _session(
            waste_setup["dev_slug"], task.id, started=started, ended=ended, cost=2.5
        )
    )
    await db.flush()

    report = await waste_setup["svc"].get_spawn_waste_metrics(days=30)
    assert report.total_sessions == 1
    assert report.zero_progress_sessions == 1
    assert report.zero_progress_cost_usd == pytest.approx(2.5)
    assert report.total_cost_usd == pytest.approx(2.5)
    assert report.to_dict()["zero_progress_cost_share"] == pytest.approx(1.0)
    agent_row = next(
        a for a in report.by_agent if a.agent_slug == waste_setup["dev_slug"]
    )
    assert agent_row.sessions == 1
    assert agent_row.zero_progress_sessions == 1
    team_row = next(t for t in report.by_team if t.team == "backend")
    assert team_row.zero_progress_sessions == 1


@pytest.mark.asyncio
async def test_status_advance_outside_window_still_counts_as_zero_progress(
    waste_setup: dict,
) -> None:
    """A status change that happened well BEFORE this session's window (a
    prior spawn's own progress) must not exonerate a later no-op spawn."""
    db = waste_setup["db"]
    task = _task(waste_setup["project_id"], waste_setup["dev_id"])
    db.add(task)
    await db.flush()
    db.add(
        AuditLogTable(
            id=uuid4(),
            event_type="task.in_progress",
            agent_id=waste_setup["dev_id"],
            target_type="task",
            target_id=task.id,
            severity="info",
            details={"to_status": "in_progress"},
            timestamp=_NOW - timedelta(hours=5),
        )
    )
    started = _NOW - timedelta(hours=1)
    ended = _NOW
    db.add(
        _session(
            waste_setup["dev_slug"], task.id, started=started, ended=ended, cost=1.0
        )
    )
    await db.flush()

    report = await waste_setup["svc"].get_spawn_waste_metrics(days=30)
    assert report.zero_progress_sessions == 1


@pytest.mark.asyncio
async def test_journal_entry_in_window_counts_as_progress(waste_setup: dict) -> None:
    db = waste_setup["db"]
    task = _task(waste_setup["project_id"], waste_setup["dev_id"])
    db.add(task)
    journal = JournalTable(id=uuid4(), agent_id=waste_setup["dev_id"])
    db.add(journal)
    await db.flush()
    started = _NOW - timedelta(hours=1)
    ended = _NOW
    db.add(
        JournalEntryTable(
            id=uuid4(),
            journal_id=journal.id,
            type=JournalEntryType.GENERAL,
            title="progress",
            content="did stuff",
            task_id=task.id,
            timestamp=started + timedelta(minutes=5),
        )
    )
    db.add(
        _session(
            waste_setup["dev_slug"], task.id, started=started, ended=ended, cost=1.0
        )
    )
    await db.flush()

    report = await waste_setup["svc"].get_spawn_waste_metrics(days=30)
    assert report.zero_progress_sessions == 0


@pytest.mark.asyncio
async def test_empty_window_returns_all_zero_no_division_by_zero(
    waste_setup: dict,
) -> None:
    report = await waste_setup["svc"].get_spawn_waste_metrics(days=30)
    assert report.total_sessions == 0
    assert report.zero_progress_sessions == 0
    assert report.zero_progress_cost_usd == pytest.approx(0.0)
    assert report.total_cost_usd == pytest.approx(0.0)
    assert report.to_dict()["zero_progress_cost_share"] == pytest.approx(0.0)
    assert report.by_agent == []
    assert report.by_team == []
    assert report.by_task == []


@pytest.mark.asyncio
async def test_named_non_transition_event_does_not_count_as_progress(
    waste_setup: dict,
) -> None:
    """A named audit event (task.qa_fail) lands inside the session window but
    its event_type never equals 'task.' + its own details.to_status, so it
    must not be mistaken for a genuine status-advance signal."""
    db = waste_setup["db"]
    task = _task(waste_setup["project_id"], waste_setup["dev_id"])
    db.add(task)
    await db.flush()
    started = _NOW - timedelta(hours=1)
    ended = _NOW
    db.add(
        AuditLogTable(
            id=uuid4(),
            event_type="task.qa_fail",
            agent_id=waste_setup["dev_id"],
            target_type="task",
            target_id=task.id,
            severity="info",
            details={"to_status": "needs_revision"},
            timestamp=started + timedelta(minutes=10),
        )
    )
    db.add(
        _session(
            waste_setup["dev_slug"], task.id, started=started, ended=ended, cost=1.0
        )
    )
    await db.flush()

    report = await waste_setup["svc"].get_spawn_waste_metrics(days=30)
    assert report.zero_progress_sessions == 1


@pytest.mark.asyncio
async def test_genuine_transition_in_window_counts_as_progress(
    waste_setup: dict,
) -> None:
    """A generic task.<to_status> transition (event_type == 'task.' +
    details.to_status) inside the window IS a real progress signal."""
    db = waste_setup["db"]
    task = _task(waste_setup["project_id"], waste_setup["dev_id"])
    db.add(task)
    await db.flush()
    started = _NOW - timedelta(hours=1)
    ended = _NOW
    db.add(
        AuditLogTable(
            id=uuid4(),
            event_type="task.needs_revision",
            agent_id=waste_setup["dev_id"],
            target_type="task",
            target_id=task.id,
            severity="info",
            details={"to_status": "needs_revision"},
            timestamp=started + timedelta(minutes=10),
        )
    )
    db.add(
        _session(
            waste_setup["dev_slug"], task.id, started=started, ended=ended, cost=1.0
        )
    )
    await db.flush()

    report = await waste_setup["svc"].get_spawn_waste_metrics(days=30)
    assert report.zero_progress_sessions == 0


@pytest.mark.asyncio
async def test_malformed_and_naive_timestamp_entries_are_skipped_not_fatal(
    waste_setup: dict,
) -> None:
    """A malformed ISO string (ValueError) and a naive datetime (would 500 on
    the tz-aware window comparison) must both be tolerated, not crash the
    endpoint — the malformed one is dropped; the naive one is normalised to
    UTC and still counts as a real progress signal."""
    db = waste_setup["db"]
    task = _task(waste_setup["project_id"], waste_setup["dev_id"])
    started = _NOW - timedelta(hours=1)
    ended = _NOW
    task.commits = [
        {
            "hash": "a" * 40,
            "message": "malformed",
            "timestamp": "not-a-real-timestamp",
            "author_agent_id": str(waste_setup["dev_id"]),
        },
        {
            "hash": "b" * 40,
            "message": "naive",
            # No UTC offset — naive datetime, must not crash the comparison.
            "timestamp": (started + timedelta(minutes=10))
            .replace(tzinfo=None)
            .isoformat(),
            "author_agent_id": str(waste_setup["dev_id"]),
        },
    ]
    db.add(task)
    db.add(
        _session(
            waste_setup["dev_slug"], task.id, started=started, ended=ended, cost=1.0
        )
    )
    await db.flush()

    report = await waste_setup["svc"].get_spawn_waste_metrics(days=30)
    assert report.total_sessions == 1
    assert report.zero_progress_sessions == 0


@pytest.mark.asyncio
async def test_by_task_breakdown_reports_task_id_and_cost(waste_setup: dict) -> None:
    db = waste_setup["db"]
    task = _task(waste_setup["project_id"], waste_setup["dev_id"])
    started = _NOW - timedelta(hours=1)
    ended = _NOW
    db.add(task)
    db.add(
        _session(
            waste_setup["dev_slug"], task.id, started=started, ended=ended, cost=3.0
        )
    )
    await db.flush()

    report = await waste_setup["svc"].get_spawn_waste_metrics(days=30)
    task_row = next(t for t in report.by_task if t.task_id == str(task.id))
    assert task_row.sessions == 1
    assert task_row.zero_progress_sessions == 1
    assert task_row.zero_progress_cost_usd == pytest.approx(3.0)
    assert report.to_dict()["by_task"][0]["task_id"] == str(task.id)


@pytest.mark.asyncio
async def test_in_flight_session_excluded_from_denominator(waste_setup: dict) -> None:
    """An open (ended_at=None) session hasn't finished yet and must not be
    judged — it shouldn't inflate either the total or zero-progress count."""
    db = waste_setup["db"]
    task = _task(waste_setup["project_id"], waste_setup["dev_id"])
    db.add(task)
    db.add(
        AgentSpawnSessionTable(
            id=uuid4(),
            agent_slug=waste_setup["dev_slug"],
            team="backend",
            role="developer",
            model="claude",
            task_id=str(task.id),
            started_at=_NOW - timedelta(minutes=30),
            ended_at=None,
            estimated_cost_usd=9.0,
        )
    )
    await db.flush()

    report = await waste_setup["svc"].get_spawn_waste_metrics(days=30)
    assert report.total_sessions == 0
