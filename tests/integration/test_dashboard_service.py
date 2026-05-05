"""DashboardService coverage — flags, reports, channel feeds, audit queue."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ChannelTable, ProjectTable, TaskTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import (
    ChannelType,
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.models.dashboard import CreateFlagParams
from roboco.services.dashboard import DashboardService, reset_storage

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
    assert svc.count_unresolved_flags("urgent") == 2
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
