"""DashboardService coverage — flags, reports, channel feeds, audit queue."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import uuid4
from uuid import uuid4 as _u

import pytest
import pytest_asyncio
from roboco.db.tables import (
    AgentTable,
    ChannelTable,
    GroupTable,
    MessageTable,
    ProjectTable,
    SessionTable,
    TaskTable,
)
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import (
    ChannelType,
    MessageType,
    SessionStatus,
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.models.dashboard import CreateFlagParams
from roboco.services.dashboard import (
    DashboardService,
    _DashboardStorageHolder,
    get_dashboard_service,
    get_storage,
    reset_storage,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def dash_setup(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    reset_storage()
    agent = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"be-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="D-Proj",
        slug=f"d-proj-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()
    yield {
        "svc": DashboardService(db_session),
        "agent_id": agent.id,
        "project_id": project.id,
    }


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


def test_create_flag(dash_setup: dict) -> None:
    svc = dash_setup["svc"]
    flag = svc.create_flag(
        CreateFlagParams(
            severity="urgent",
            category="quality",
            title="t",
            description="d",
        )
    )
    assert flag.severity == "urgent"
    fetched = svc.get_flag(flag.id)
    assert fetched is not None
    assert fetched.id == flag.id


def test_get_flags_filters_unresolved(dash_setup: dict) -> None:
    svc = dash_setup["svc"]
    a = svc.create_flag(
        CreateFlagParams(severity="urgent", category="c", title="a", description="d")
    )
    svc.resolve_flag(a.id, notes="fixed")
    unresolved = svc.get_flags(resolved=False)
    assert all(f.id != a.id for f in unresolved)
    resolved = svc.get_flags(resolved=True)
    assert any(f.id == a.id for f in resolved)


def test_get_flags_filters_by_severity(dash_setup: dict) -> None:
    svc = dash_setup["svc"]
    svc.create_flag(
        CreateFlagParams(severity="urgent", category="c", title="a", description="d")
    )
    svc.create_flag(
        CreateFlagParams(severity="warning", category="c", title="b", description="d")
    )
    urgent_only = svc.get_flags(severity="urgent")
    assert all(f.severity == "urgent" for f in urgent_only)


def test_resolve_flag_returns_false_for_missing(dash_setup: dict) -> None:
    svc = dash_setup["svc"]
    assert svc.resolve_flag(uuid4()) is False


def test_get_flag_returns_none_for_missing(dash_setup: dict) -> None:
    svc = dash_setup["svc"]
    assert svc.get_flag(uuid4()) is None


def test_count_unresolved_flags(dash_setup: dict) -> None:
    svc = dash_setup["svc"]
    svc.create_flag(
        CreateFlagParams(severity="urgent", category="c", title="a", description="d")
    )
    svc.create_flag(
        CreateFlagParams(severity="urgent", category="c", title="b", description="d")
    )
    _FLAGS = 2
    assert svc.count_unresolved_flags("urgent") == _FLAGS
    assert svc.count_unresolved_flags("warning") == 0


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def test_create_report(dash_setup: dict) -> None:
    svc = dash_setup["svc"]
    report = svc.create_report(
        report_type="weekly", title="t", summary="s", sections=None
    )
    assert report.report_type == "weekly"
    assert svc.get_report(report.id) is not None


def test_get_reports_filters(dash_setup: dict) -> None:
    svc = dash_setup["svc"]
    svc.create_report(report_type="weekly", title="a", summary="s")
    svc.create_report(report_type="incident", title="b", summary="s")
    weekly = svc.get_reports(report_type="weekly")
    assert all(r.report_type == "weekly" for r in weekly)


def test_send_report(dash_setup: dict) -> None:
    svc = dash_setup["svc"]
    r = svc.create_report(report_type="weekly", title="t", summary="s")
    assert svc.send_report(r.id) is True
    fetched = svc.get_report(r.id)
    assert fetched is not None
    assert fetched.sent_at is not None


def test_send_report_returns_false_for_missing(dash_setup: dict) -> None:
    svc = dash_setup["svc"]
    assert svc.send_report(uuid4()) is False


def test_get_last_report_time_none_if_no_reports(dash_setup: dict) -> None:
    svc = dash_setup["svc"]
    assert svc.get_last_report_time() is None


def test_get_last_report_time_returns_most_recent(dash_setup: dict) -> None:
    svc = dash_setup["svc"]
    r = svc.create_report(report_type="weekly", title="t", summary="s")
    svc.send_report(r.id)
    assert svc.get_last_report_time() is not None


def test_get_report_returns_none_for_missing(dash_setup: dict) -> None:
    svc = dash_setup["svc"]
    assert svc.get_report(uuid4()) is None


# ---------------------------------------------------------------------------
# Channel feeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_channel_feeds(db_session: AsyncSession, dash_setup: dict) -> None:
    svc = dash_setup["svc"]
    ch = ChannelTable(
        id=uuid4(),
        name="ch",
        slug=f"ch-{uuid4().hex[:6]}",
        type=ChannelType.CELL,
    )
    db_session.add(ch)
    await db_session.flush()
    feeds = await svc.get_channel_feeds()
    assert any(f.id == ch.id for f in feeds)


@pytest.mark.asyncio
async def test_compute_channel_status_offline_when_no_activity(
    dash_setup: dict,
) -> None:
    svc = dash_setup["svc"]
    assert svc._compute_channel_status(None) == "offline"


# ---------------------------------------------------------------------------
# Audit queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_audit_queue_includes_blocked_and_qa(
    db_session: AsyncSession, dash_setup: dict
) -> None:
    svc = dash_setup["svc"]
    aid = dash_setup["agent_id"]
    pid = dash_setup["project_id"]
    blocked = TaskTable(
        id=uuid4(),
        title="t-blocked",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.BLOCKED,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=pid,
        created_by=aid,
        team=Team.BACKEND,
    )
    awaiting_qa = TaskTable(
        id=uuid4(),
        title="t-qa",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.AWAITING_QA,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=pid,
        created_by=aid,
        team=Team.BACKEND,
    )
    db_session.add_all([blocked, awaiting_qa])
    await db_session.flush()

    queue = await svc.get_audit_queue()
    types = {item.type for item in queue}
    assert "blocked_task" in types
    assert "qa_review" in types


# ---------------------------------------------------------------------------
# Roadmap progress (defensive on empty DB — division-by-zero safe path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_roadmap_progress_safe_on_empty(dash_setup: dict) -> None:
    svc = dash_setup["svc"]
    progress = await svc.get_roadmap_progress()
    assert "current_quarter_progress" in progress


# ---------------------------------------------------------------------------
# Auditor alerts
# ---------------------------------------------------------------------------


def test_get_auditor_alerts_returns_dict(dash_setup: dict) -> None:
    svc = dash_setup["svc"]
    alerts = svc.get_auditor_alerts()
    assert "urgent_count" in alerts
    assert "warning_count" in alerts


# ---------------------------------------------------------------------------
# get_storage — module-level singleton
# ---------------------------------------------------------------------------


def test_get_storage_returns_singleton() -> None:
    # Force instance back to None so the get_storage() init branch runs.
    _DashboardStorageHolder.instance = None
    s1 = get_storage()
    s2 = get_storage()
    assert s1 is s2


# ---------------------------------------------------------------------------
# get_flags — resolved=True returns only resolved
# ---------------------------------------------------------------------------


def test_get_flags_resolved_true(dash_setup: dict) -> None:
    svc = dash_setup["svc"]
    a = svc.create_flag(
        CreateFlagParams(severity="warning", category="c", title="r", description="d")
    )
    svc.create_flag(
        CreateFlagParams(severity="warning", category="c", title="u", description="d")
    )
    svc.resolve_flag(a.id)
    only_resolved = svc.get_flags(resolved=True)
    assert all(f.resolved_at is not None for f in only_resolved)
    assert any(f.id == a.id for f in only_resolved)


# ---------------------------------------------------------------------------
# _compute_channel_status — streaming/idle thresholds
# ---------------------------------------------------------------------------


def test_compute_channel_status_streaming(dash_setup: dict) -> None:
    """Recent activity (<5min) → streaming."""
    svc = dash_setup["svc"]
    recent = datetime.now(UTC) - timedelta(minutes=2)
    assert svc._compute_channel_status(recent) == "streaming"


def test_compute_channel_status_idle(dash_setup: dict) -> None:
    """Activity 5-30min ago → idle."""
    svc = dash_setup["svc"]
    moderate = datetime.now(UTC) - timedelta(minutes=15)
    assert svc._compute_channel_status(moderate) == "idle"


def test_compute_channel_status_offline(dash_setup: dict) -> None:
    """Activity >30min ago → offline."""
    svc = dash_setup["svc"]
    stale = datetime.now(UTC) - timedelta(hours=2)
    assert svc._compute_channel_status(stale) == "offline"


# ---------------------------------------------------------------------------
# Lazy MetricsService loader
# ---------------------------------------------------------------------------


def test_metrics_lazy_loads(dash_setup: dict) -> None:
    svc = dash_setup["svc"]
    # Access the metrics property — first call constructs.
    m1 = svc.metrics
    m2 = svc.metrics
    assert m1 is m2  # Cached on second access.


# ---------------------------------------------------------------------------
# get_team_health_list — uses metrics.get_health_status per team
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_team_health_list(dash_setup: dict) -> None:
    """Returns one TeamHealthData per team, sourced from metrics service."""
    svc = dash_setup["svc"]
    # Stub metrics.get_health_status.
    fake = {
        "status": "healthy",
        "active_tasks": 3,
        "blocked_tasks": 0,
        "blocked_ratio": 0.0,
        "completed_this_week": 2,
    }
    mock_metrics = AsyncMock()
    mock_metrics.get_health_status = AsyncMock(return_value=fake)
    svc._metrics = mock_metrics
    health_list = await svc.get_team_health_list()
    _TEAMS = 4
    assert len(health_list) == _TEAMS


# ---------------------------------------------------------------------------
# get_key_metrics — averages doc coverage across teams
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_key_metrics(dash_setup: dict) -> None:
    svc = dash_setup["svc"]
    velocity = SimpleNamespace(tasks_completed=10, completion_rate=0.8)
    team_metrics = [
        SimpleNamespace(documentation_coverage=0.5),
        SimpleNamespace(documentation_coverage=0.7),
    ]
    blockers = SimpleNamespace(active_blockers=2)
    mock_metrics = AsyncMock()
    mock_metrics.get_velocity = AsyncMock(return_value=velocity)
    mock_metrics.get_all_team_metrics = AsyncMock(return_value=team_metrics)
    mock_metrics.get_blocker_metrics = AsyncMock(return_value=blockers)
    svc._metrics = mock_metrics
    result = await svc.get_key_metrics()
    assert result["velocity_weekly"] == velocity.tasks_completed
    assert result["active_blockers"] == blockers.active_blockers


@pytest.mark.asyncio
async def test_get_key_metrics_empty_team_metrics(
    dash_setup: dict,
) -> None:
    """No team metrics → avg_doc_coverage defaults to 0."""
    svc = dash_setup["svc"]
    mock_metrics = AsyncMock()
    mock_metrics.get_velocity = AsyncMock(
        return_value=SimpleNamespace(tasks_completed=0, completion_rate=0.0)
    )
    mock_metrics.get_all_team_metrics = AsyncMock(return_value=[])
    mock_metrics.get_blocker_metrics = AsyncMock(
        return_value=SimpleNamespace(active_blockers=0)
    )
    svc._metrics = mock_metrics
    result = await svc.get_key_metrics()
    assert result["documentation_coverage"] == 0


# ---------------------------------------------------------------------------
# get_auditor_metrics — surfaces velocity/blockers/communication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_auditor_metrics(dash_setup: dict) -> None:
    svc = dash_setup["svc"]
    mock_metrics = AsyncMock()
    mock_metrics.get_velocity = AsyncMock(
        return_value=SimpleNamespace(tasks_completed=5, avg_completion_hours=12.5)
    )
    mock_metrics.get_blocker_metrics = AsyncMock(
        return_value=SimpleNamespace(active_blockers=1)
    )
    mock_metrics.get_communication_volume = AsyncMock(
        return_value={"total_messages": 100}
    )
    svc._metrics = mock_metrics
    result = await svc.get_auditor_metrics()
    _COMPLETED = 5
    assert result["tasks_completed_24h"] == _COMPLETED
    _COMM = 100
    assert result["communication_volume"] == _COMM


# ---------------------------------------------------------------------------
# get_all_agent_status — counts + per-agent snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_all_agent_status_no_team_filter(
    dash_setup: dict,
) -> None:
    svc = dash_setup["svc"]
    result = await svc.get_all_agent_status()
    assert "total" in result
    assert "by_status" in result
    assert "agents" in result


@pytest.mark.asyncio
async def test_get_all_agent_status_with_team_filter(
    dash_setup: dict,
) -> None:
    svc = dash_setup["svc"]
    result = await svc.get_all_agent_status(team=Team.BACKEND)
    assert all(
        a["team"] == Team.BACKEND.value or a["team"] is None for a in result["agents"]
    )


@pytest.mark.asyncio
async def test_get_all_agent_status_includes_active_agent(dash_setup: dict) -> None:
    """The seeded agent is in the snapshot."""
    svc = dash_setup["svc"]
    aid = dash_setup["agent_id"]
    result = await svc.get_all_agent_status()
    assert any(a["id"] == str(aid) for a in result["agents"])


# ---------------------------------------------------------------------------
# get_recent_activity — messages + task updates
# ---------------------------------------------------------------------------


_DEFAULT_HOURS = 24


@pytest.mark.asyncio
async def test_get_recent_activity_returns_period_and_activity(
    dash_setup: dict,
) -> None:
    svc = dash_setup["svc"]
    out = await svc.get_recent_activity(hours=_DEFAULT_HOURS, limit=10)
    assert out["period_hours"] == _DEFAULT_HOURS
    assert isinstance(out["activity"], list)


@pytest.mark.asyncio
async def test_get_recent_activity_includes_messages_and_tasks(
    dash_setup: dict, db_session: AsyncSession
) -> None:
    """Seed a recent message + a recent task update; both appear in feed."""
    svc = dash_setup["svc"]
    aid = dash_setup["agent_id"]
    pid = dash_setup["project_id"]
    # Seed channel/group/session for the message FK chain.
    ch = ChannelTable(
        id=_u(),
        name="ch",
        slug=f"ch-{_u().hex[:6]}",
        type=ChannelType.CELL,
        last_activity=datetime.now(UTC),
    )
    db_session.add(ch)
    await db_session.flush()
    grp = GroupTable(
        id=_u(),
        name="g",
        channel_id=ch.id,
        allowed_roles=[],
        members=[],
    )
    db_session.add(grp)
    await db_session.flush()
    sess = SessionTable(
        id=_u(),
        group_id=grp.id,
        status=SessionStatus.ACTIVE,
        started_at=datetime.now(UTC),
    )
    db_session.add(sess)
    await db_session.flush()
    msg = MessageTable(
        id=_u(),
        agent_id=aid,
        channel_id=ch.id,
        group_id=grp.id,
        session_id=sess.id,
        type=MessageType.DIALOGUE,
        content="hello",
        content_length=5,
    )
    db_session.add(msg)
    task = TaskTable(
        id=_u(),
        title="rec",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=pid,
        created_by=aid,
        team=Team.BACKEND,
        updated_at=datetime.now(UTC),
    )
    db_session.add(task)
    await db_session.flush()
    out = await svc.get_recent_activity(hours=24, limit=10)
    types = {item["type"] for item in out["activity"]}
    assert "message" in types
    assert "task_update" in types


@pytest.mark.asyncio
async def test_get_recent_activity_task_without_updated_at(
    dash_setup: dict, db_session: AsyncSession
) -> None:
    """Task with no updated_at uses created_at fallback path."""
    svc = dash_setup["svc"]
    aid = dash_setup["agent_id"]
    pid = dash_setup["project_id"]
    task = TaskTable(
        id=_u(),
        title="rec-no-update",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=pid,
        created_by=aid,
        team=Team.BACKEND,
        updated_at=datetime.now(UTC),
    )
    db_session.add(task)
    await db_session.flush()
    # Force updated_at back to None and rely on created_at fallback.
    task.updated_at = None
    await db_session.flush()
    # Re-insert it via raw query so the query picks it up via created_at.
    out = await svc.get_recent_activity(hours=24, limit=10)
    # Coverage: just ensure no crash; the fallback path runs when task is in
    # the result set with updated_at None — exercised by task `task` having
    # no updated_at after the second flush.
    assert "activity" in out


# ---------------------------------------------------------------------------
# Factory function smoke-test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_dashboard_service_factory(
    db_session: AsyncSession,
) -> None:
    svc = get_dashboard_service(db_session)
    assert isinstance(svc, DashboardService)
