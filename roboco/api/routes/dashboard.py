"""
Dashboard API Routes

Auditor dashboard and CEO overview endpoints.
Provides aggregated views, alerts, and reporting.
"""

from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select

from roboco.api.deps import DbSession
from roboco.db.tables import AgentTable, ChannelTable, MessageTable, TaskTable
from roboco.models.base import AgentStatus, TaskStatus, Team
from roboco.services.kanban import get_kanban_service
from roboco.services.metrics import get_metrics_service
from roboco.utils.converters import require_uuid

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# =============================================================================
# MODELS
# =============================================================================


class FlagSeverity(str, Enum):
    """Severity levels for auditor flags."""

    INFO = "info"
    WARNING = "warning"
    URGENT = "urgent"


class AuditorFlag(BaseModel):
    """A flag raised by the auditor."""

    id: UUID
    severity: FlagSeverity
    category: str  # quality, process, communication, blocked, documentation
    title: str
    description: str
    related_task_id: UUID | None = None
    related_agent_id: UUID | None = None
    created_at: datetime
    resolved_at: datetime | None = None
    notes: str | None = None


class AuditorReport(BaseModel):
    """An auditor report for the CEO."""

    id: UUID
    report_type: str  # daily, weekly, alert
    title: str
    summary: str
    sections: list[dict[str, Any]]
    created_at: datetime
    sent_at: datetime | None = None


class ChannelFeed(BaseModel):
    """Live feed status for a channel."""

    id: UUID
    name: str
    status: str  # streaming, idle, offline
    last_activity: datetime | None
    message_count_24h: int


class AuditorDashboard(BaseModel):
    """Complete auditor dashboard data."""

    live_feeds: list[ChannelFeed]
    flagged_items: list[AuditorFlag]
    metrics: dict[str, Any]
    audit_queue: list[dict[str, Any]]
    recent_reports: list[AuditorReport]


class TeamHealth(BaseModel):
    """Health status for a team."""

    team: str
    status: str  # ok, slow, critical
    active_tasks: int
    blocked_tasks: int
    blocked_ratio: float
    completed_this_week: int


class CEOOverview(BaseModel):
    """Complete CEO overview data."""

    health_status: list[TeamHealth]
    key_metrics: dict[str, Any]
    auditor_alerts: dict[str, Any]
    roadmap_progress: dict[str, Any]


class CreateFlagRequest(BaseModel):
    """Request to create an auditor flag."""

    severity: FlagSeverity
    category: str
    title: str
    description: str
    related_task_id: UUID | None = None
    related_agent_id: UUID | None = None


class CreateReportRequest(BaseModel):
    """Request to create an auditor report."""

    report_type: str
    title: str
    summary: str
    sections: list[dict[str, Any]] = Field(default_factory=list)


# =============================================================================
# IN-MEMORY STORAGE (Would be database in production)
# =============================================================================

# Temporary in-memory storage for flags and reports
# In production, these would be database tables
_flags: dict[UUID, dict[str, Any]] = {}
_reports: dict[UUID, dict[str, Any]] = {}


# =============================================================================
# AUDITOR DASHBOARD
# =============================================================================


@router.get("/auditor", response_model=AuditorDashboard)
async def get_auditor_dashboard(
    db: DbSession,
) -> AuditorDashboard:
    """
    Get the complete auditor dashboard.

    Includes:
    - Live channel feeds with status
    - Flagged items requiring attention
    - Key metrics
    - Audit queue
    - Recent reports
    """
    metrics_service = get_metrics_service(db)

    # Get live feeds (channel status)
    channel_result = await db.execute(select(ChannelTable))
    channels = channel_result.scalars().all()

    live_feeds = []
    for channel in channels:
        five_minutes = 5
        thirty_minutes = 30
        # Determine status based on last activity
        if channel.last_activity:
            minutes_ago = (
                datetime.now(UTC) - channel.last_activity
            ).total_seconds() / 60
            if minutes_ago < five_minutes:
                status = "streaming"
            elif minutes_ago < thirty_minutes:
                status = "idle"
            else:
                status = "offline"
        else:
            status = "offline"

        live_feeds.append(
            ChannelFeed(
                id=require_uuid(channel.id),
                name=channel.name,
                status=status,
                last_activity=channel.last_activity,
                message_count_24h=channel.message_count,  # Simplified
            )
        )

    # Get flagged items
    flagged_items = [
        AuditorFlag(
            id=UUID(int=i),
            **flag,
        )
        for i, flag in enumerate(_flags.values())
        if not flag.get("resolved_at")
    ]

    # Get metrics
    velocity = await metrics_service.get_velocity(7)
    blockers = await metrics_service.get_blocker_metrics()
    comm = await metrics_service.get_communication_volume(24)

    metrics = {
        "tasks_completed_24h": velocity.tasks_completed,
        "avg_completion_time": velocity.avg_completion_hours,
        "active_blockers": blockers.active_blockers,
        "communication_volume": comm["total_messages"],
    }

    # Build audit queue from tasks needing attention
    audit_queue = []

    # Tasks blocked > 24 hours
    blocked_result = await db.execute(
        select(TaskTable).where(TaskTable.status == TaskStatus.BLOCKED)
    )
    for task in blocked_result.scalars().all():
        audit_queue.append(
            {
                "type": "blocked_task",
                "title": f"Blocked: {task.title}",
                "task_id": str(task.id),
                "team": task.team.value,
            }
        )

    # Tasks awaiting QA > 24 hours
    qa_result = await db.execute(
        select(TaskTable).where(TaskTable.status == TaskStatus.AWAITING_QA)
    )
    for task in qa_result.scalars().all():
        audit_queue.append(
            {
                "type": "qa_review",
                "title": f"QA Review: {task.title}",
                "task_id": str(task.id),
                "team": task.team.value,
            }
        )

    # Recent reports
    recent_reports = [
        AuditorReport(
            id=UUID(int=i),
            **report,
        )
        for i, report in enumerate(_reports.values())
    ][-5:]  # Last 5

    return AuditorDashboard(
        live_feeds=live_feeds,
        flagged_items=flagged_items,
        metrics=metrics,
        audit_queue=audit_queue[:10],
        recent_reports=recent_reports,
    )


@router.get("/auditor/flags", response_model=list[AuditorFlag])
async def get_auditor_flags(
    _db: DbSession,
    severity: FlagSeverity | None = None,
    resolved: bool = False,
) -> list[AuditorFlag]:
    """Get auditor flags with optional filters."""
    flags = []
    for i, flag_data in enumerate(_flags.values()):
        if severity and flag_data.get("severity") != severity.value:
            continue
        if not resolved and flag_data.get("resolved_at"):
            continue
        if resolved and not flag_data.get("resolved_at"):
            continue

        flags.append(
            AuditorFlag(
                id=UUID(int=i),
                **flag_data,
            )
        )

    return flags


@router.post(
    "/auditor/flags", response_model=AuditorFlag, status_code=status.HTTP_201_CREATED
)
async def create_auditor_flag(
    data: CreateFlagRequest,
    _db: DbSession,
) -> AuditorFlag:
    """Create a new auditor flag."""
    flag_id = uuid4()
    created_at = datetime.now(UTC)
    flag_data = {
        "severity": data.severity.value,
        "category": data.category,
        "title": data.title,
        "description": data.description,
        "related_task_id": data.related_task_id,
        "related_agent_id": data.related_agent_id,
        "created_at": created_at,
        "resolved_at": None,
        "notes": None,
    }
    _flags[flag_id] = flag_data

    return AuditorFlag(
        id=flag_id,
        severity=data.severity,
        category=data.category,
        title=data.title,
        description=data.description,
        related_task_id=data.related_task_id,
        related_agent_id=data.related_agent_id,
        created_at=created_at,
        resolved_at=None,
        notes=None,
    )


@router.put("/auditor/flags/{flag_id}/resolve")
async def resolve_auditor_flag(
    flag_id: UUID,
    notes: str | None = None,
) -> dict[str, str]:
    """Resolve an auditor flag."""
    if flag_id not in _flags:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Flag not found"
        )

    _flags[flag_id]["resolved_at"] = datetime.now(UTC)
    if notes:
        _flags[flag_id]["notes"] = notes

    return {"status": "resolved", "flag_id": str(flag_id)}


@router.get("/auditor/reports", response_model=list[AuditorReport])
async def get_auditor_reports(
    report_type: str | None = None,
    limit: int = Query(default=10, ge=1, le=100),
) -> list[AuditorReport]:
    """Get auditor reports."""
    reports = []
    for i, report_data in enumerate(_reports.values()):
        if report_type and report_data.get("report_type") != report_type:
            continue

        reports.append(
            AuditorReport(
                id=UUID(int=i),
                **report_data,
            )
        )

    return reports[-limit:]


@router.post(
    "/auditor/reports",
    response_model=AuditorReport,
    status_code=status.HTTP_201_CREATED,
)
async def create_auditor_report(
    data: CreateReportRequest,
    _db: DbSession,
) -> AuditorReport:
    """Create a new auditor report."""
    report_id = uuid4()
    created_at = datetime.now(UTC)
    report_data = {
        "report_type": data.report_type,
        "title": data.title,
        "summary": data.summary,
        "sections": data.sections,
        "created_at": created_at,
        "sent_at": None,
    }
    _reports[report_id] = report_data

    return AuditorReport(
        id=report_id,
        report_type=data.report_type,
        title=data.title,
        summary=data.summary,
        sections=data.sections,
        created_at=created_at,
        sent_at=None,
    )


@router.post("/auditor/reports/{report_id}/send")
async def send_auditor_report(report_id: UUID) -> dict[str, str]:
    """Mark a report as sent to CEO."""
    if report_id not in _reports:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report not found"
        )

    _reports[report_id]["sent_at"] = datetime.now(UTC)

    return {"status": "sent", "report_id": str(report_id)}


# =============================================================================
# CEO OVERVIEW
# =============================================================================


async def _get_team_health_list(metrics_service: Any) -> list[TeamHealth]:
    """Get health status for all teams."""
    teams = [Team.BACKEND, Team.FRONTEND, Team.UX_UI, Team.BOARD]
    health_list = []
    for team in teams:
        health = await metrics_service.get_health_status(team)
        health_list.append(
            TeamHealth(
                team=team.value,
                status=health["status"],
                active_tasks=health["active_tasks"],
                blocked_tasks=health["blocked_tasks"],
                blocked_ratio=health["blocked_ratio"],
                completed_this_week=health["completed_this_week"],
            )
        )
    return health_list


async def _get_key_metrics(metrics_service: Any) -> dict[str, Any]:
    """Get key organization metrics."""
    velocity = await metrics_service.get_velocity(7)
    team_metrics = await metrics_service.get_all_team_metrics()
    blockers = await metrics_service.get_blocker_metrics()

    total_docs = sum(tm.documentation_coverage for tm in team_metrics)
    avg_doc_coverage = total_docs / len(team_metrics) if team_metrics else 0

    return {
        "velocity_weekly": velocity.tasks_completed,
        "completion_rate": velocity.completion_rate,
        "documentation_coverage": round(avg_doc_coverage, 2),
        "active_blockers": blockers.active_blockers,
    }


def _count_unresolved_flags(severity: str) -> int:
    """Count unresolved flags of a given severity."""
    return sum(
        1
        for f in _flags.values()
        if f.get("severity") == severity and not f.get("resolved_at")
    )


def _get_last_report_time() -> str | None:
    """Get the timestamp of the most recent report."""
    recent_reports = [r for r in _reports.values() if r.get("sent_at")]
    if not recent_reports:
        return None
    last_time = max(r["sent_at"] for r in recent_reports)
    return last_time.isoformat() if last_time else None


def _get_auditor_alerts() -> dict[str, Any]:
    """Get auditor alerts summary."""
    return {
        "urgent_count": _count_unresolved_flags("urgent"),
        "warning_count": _count_unresolved_flags("warning"),
        "last_report_at": _get_last_report_time(),
    }


async def _get_roadmap_progress(db: DbSession) -> dict[str, Any]:
    """Get roadmap progress from high-priority tasks."""
    total_result = await db.execute(
        select(func.count(TaskTable.id)).where(TaskTable.priority <= 1)
    )
    total_priority = total_result.scalar() or 0

    completed_result = await db.execute(
        select(func.count(TaskTable.id)).where(
            and_(
                TaskTable.priority <= 1,
                TaskTable.status == TaskStatus.COMPLETED,
            )
        )
    )
    completed_priority = completed_result.scalar() or 0
    progress = completed_priority / total_priority if total_priority > 0 else 0

    return {
        "current_quarter_progress": round(progress, 2),
        "high_priority_total": total_priority,
        "high_priority_completed": completed_priority,
    }


@router.get("/ceo", response_model=CEOOverview)
async def get_ceo_overview(
    db: DbSession,
) -> CEOOverview:
    """
    Get the CEO overview dashboard.

    Includes:
    - Health status for all teams
    - Key organization-wide metrics
    - Auditor alerts summary
    - Roadmap progress
    """
    metrics_service = get_metrics_service(db)

    return CEOOverview(
        health_status=await _get_team_health_list(metrics_service),
        key_metrics=await _get_key_metrics(metrics_service),
        auditor_alerts=_get_auditor_alerts(),
        roadmap_progress=await _get_roadmap_progress(db),
    )


@router.get("/ceo/teams")
async def get_ceo_team_details(
    db: DbSession,
) -> list[dict[str, Any]]:
    """Get detailed metrics for all teams."""
    metrics_service = get_metrics_service(db)
    team_metrics = await metrics_service.get_all_team_metrics()
    return [tm.to_dict() for tm in team_metrics]


# =============================================================================
# KANBAN ENDPOINTS
# =============================================================================


@router.get("/kanban/{team}")
async def get_team_kanban(
    team: Team,
    db: DbSession,
    swimlane_by: str | None = Query(
        None, description="Swimlane by: priority or assignee"
    ),
) -> dict[str, Any]:
    """Get kanban board for a specific team."""
    kanban_service = get_kanban_service(db)
    board = await kanban_service.get_dev_board(team, swimlane_by)
    return (
        board.to_dict()
        if hasattr(board, "to_dict")
        else {
            "id": board.id,
            "title": board.title,
            "board_type": board.board_type.value,
            "team": board.team.value if board.team else None,
            "columns": [
                {
                    "id": col.id,
                    "title": col.title,
                    "status": col.status.value if col.status else None,
                    "cards": [
                        {
                            "id": str(card.id),
                            "title": card.title,
                            "priority": card.priority,
                            "status": card.status.value,
                            "assignee_name": card.assignee_name,
                            "is_blocked": card.is_blocked,
                        }
                        for card in col.cards
                    ],
                    "card_count": col.card_count,
                }
                for col in board.columns
            ],
            "total_cards": board.total_cards,
            "blocked_count": board.blocked_count,
        }
    )


@router.get("/kanban/main-pm")
async def get_main_pm_kanban(
    db: DbSession,
) -> dict[str, Any]:
    """Get the Main PM cross-cell kanban board."""
    kanban_service = get_kanban_service(db)
    board = await kanban_service.get_main_pm_board_flat()
    return board.model_dump()


# =============================================================================
# AGENT STATUS ENDPOINTS
# =============================================================================


@router.get("/agents/status")
async def get_all_agent_status(
    db: DbSession,
    team: Team | None = None,
) -> dict[str, Any]:
    """
    Get status of all agents.

    Returns agent status summary including active, idle, and offline counts.
    """
    query = select(AgentTable)
    if team:
        query = query.where(AgentTable.team == team)

    result = await db.execute(query)
    agents = result.scalars().all()

    # Group by status
    status_counts = {status.value: 0 for status in AgentStatus}
    agent_list = []

    for agent in agents:
        status = agent.status or AgentStatus.IDLE
        status_counts[status.value] = status_counts.get(status.value, 0) + 1
        agent_list.append(
            {
                "id": str(agent.id),
                "name": agent.name,
                "role": agent.role.value,
                "team": agent.team.value if agent.team else None,
                "status": status.value,
                "current_task_id": str(agent.current_task_id)
                if agent.current_task_id
                else None,
            }
        )

    return {
        "total": len(agents),
        "by_status": status_counts,
        "agents": agent_list,
    }


# =============================================================================
# RECENT ACTIVITY ENDPOINTS
# =============================================================================


@router.get("/activity/recent")
async def get_recent_activity(
    db: DbSession,
    hours: int = Query(default=24, ge=1, le=168),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """
    Get recent activity across channels.

    Returns recent messages and task updates for dashboard feed.
    """
    since = datetime.now(UTC) - timedelta(hours=hours)

    # Get recent messages
    message_result = await db.execute(
        select(MessageTable)
        .where(MessageTable.timestamp >= since)
        .order_by(MessageTable.timestamp.desc())
        .limit(limit)
    )
    messages = message_result.scalars().all()

    # Get recent task updates
    task_result = await db.execute(
        select(TaskTable)
        .where(TaskTable.updated_at >= since)
        .order_by(TaskTable.updated_at.desc())
        .limit(limit)
    )
    tasks = task_result.scalars().all()

    activity_feed = []

    # Add messages to feed
    for msg in messages:
        activity_feed.append(
            {
                "type": "message",
                "timestamp": msg.timestamp.isoformat(),
                "agent_id": str(msg.agent_id),
                "channel_id": str(msg.channel_id),
                "content_preview": msg.content[:100] if msg.content else "",
                "message_type": msg.type.value,
            }
        )

    # Add task updates to feed
    for task in tasks:
        activity_feed.append(
            {
                "type": "task_update",
                "timestamp": task.updated_at.isoformat()
                if task.updated_at
                else task.created_at.isoformat(),
                "task_id": str(task.id),
                "title": task.title,
                "status": task.status.value,
                "team": task.team.value if task.team else "",
            }
        )

    # Sort by timestamp
    activity_feed.sort(key=lambda x: x["timestamp"], reverse=True)

    return {
        "period_hours": hours,
        "activity": activity_feed[:limit],
    }


@router.get("/ceo/blockers")
async def get_ceo_blocker_details(
    db: DbSession,
) -> dict[str, Any]:
    """Get detailed blocker information for CEO."""
    metrics_service = get_metrics_service(db)
    blockers = await metrics_service.get_blocker_metrics()
    return blockers.to_dict()


@router.get("/ceo/velocity")
async def get_ceo_velocity(
    db: DbSession,
    days: int = Query(default=7, ge=1, le=90),
) -> dict[str, Any]:
    """Get velocity metrics for a time period."""
    metrics_service = get_metrics_service(db)
    velocity = await metrics_service.get_velocity(days)
    return velocity.to_dict()


# =============================================================================
# METRICS ENDPOINTS
# =============================================================================


@router.get("/metrics/velocity")
async def get_velocity_metrics(
    db: DbSession,
    days: int = Query(default=7, ge=1, le=90),
    team: Team | None = None,
) -> dict[str, Any]:
    """Get velocity metrics."""
    metrics_service = get_metrics_service(db)
    velocity = await metrics_service.get_velocity(days, team)
    return velocity.to_dict()


@router.get("/metrics/blockers")
async def get_blocker_metrics(
    db: DbSession,
) -> dict[str, Any]:
    """Get blocker metrics."""
    metrics_service = get_metrics_service(db)
    blockers = await metrics_service.get_blocker_metrics()
    return blockers.to_dict()


@router.get("/metrics/team/{team}")
async def get_team_metrics(
    team: Team,
    db: DbSession,
) -> dict[str, Any]:
    """Get metrics for a specific team."""
    metrics_service = get_metrics_service(db)
    metrics = await metrics_service.get_team_metrics(team)
    return metrics.to_dict()


@router.get("/metrics/communication")
async def get_communication_metrics(
    db: DbSession,
    hours: int = Query(default=24, ge=1, le=168),
) -> dict[str, Any]:
    """Get communication volume metrics."""
    metrics_service = get_metrics_service(db)
    return await metrics_service.get_communication_volume(hours)


@router.get("/metrics/health")
async def get_health_metrics(
    db: DbSession,
    team: Team | None = None,
) -> dict[str, Any]:
    """Get health status for a team or the whole organization."""
    metrics_service = get_metrics_service(db)
    return await metrics_service.get_health_status(team)


@router.get("/metrics/agent/{agent_id}")
async def get_agent_metrics(
    agent_id: UUID,
    db: DbSession,
) -> dict[str, Any]:
    """Get metrics for a specific agent."""
    metrics_service = get_metrics_service(db)
    metrics = await metrics_service.get_agent_metrics(agent_id)
    if not metrics:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found"
        )
    return metrics.to_dict()
