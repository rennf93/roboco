"""
Dashboard API Routes

Auditor dashboard and CEO overview endpoints.
Provides aggregated views, alerts, and reporting.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.dashboard import (
    AuditorDashboard,
    AuditorFlag,
    AuditorReport,
    CEOOverview,
    ChannelFeed,
    CreateFlagRequest,
    CreateReportRequest,
    FlagSeverity,
    TeamHealth,
    UsageSummary,
)
from roboco.models import AgentRole
from roboco.models.base import Team
from roboco.models.dashboard import CreateFlagParams
from roboco.services.dashboard import get_dashboard_service
from roboco.services.kanban import get_kanban_service
from roboco.services.metrics import get_metrics_service
from roboco.services.usage import get_usage_service

router = APIRouter()

# The auditor flag/report mutating routes are gated to the Auditor and the
# CEO. The Auditor is the silent-observer role whose flags/reports feed the
# CEO; the CEO overrides. Mirrors ``_require_curator`` in playbooks.py and
# ``_require_ceo`` in release.py. Read-only auditor views (``GET
# /auditor/flags``, ``GET /auditor/reports``, ``GET /auditor``) stay open —
# the dashboard is observable by any authenticated operator.
_AUDITOR_OR_CEO_ROLES = frozenset({AgentRole.AUDITOR, AgentRole.CEO})


def _require_auditor_or_ceo(agent: CurrentAgentContext) -> None:
    if agent.role not in _AUDITOR_OR_CEO_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the Auditor or CEO may mutate auditor flags or reports",
        )


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
    service = get_dashboard_service(db)

    # Get live feeds
    feeds = await service.get_channel_feeds()
    live_feeds = [
        ChannelFeed(
            id=f.id,
            name=f.name,
            status=f.status,
            last_activity=f.last_activity,
            message_count_24h=f.message_count_24h,
        )
        for f in feeds
    ]

    # Get flagged items
    flags = service.get_flags(resolved=False)
    flagged_items = [
        AuditorFlag(
            id=f.id,
            severity=FlagSeverity(f.severity),
            category=f.category,
            title=f.title,
            description=f.description,
            related_task_id=f.related_task_id,
            related_agent_id=f.related_agent_id,
            created_at=f.created_at,
            resolved_at=f.resolved_at,
            notes=f.notes,
        )
        for f in flags
    ]

    # Get metrics
    metrics = await service.get_auditor_metrics()

    # Get audit queue
    queue = await service.get_audit_queue(limit=10)
    audit_queue = [
        {"type": q.type, "title": q.title, "task_id": q.task_id, "team": q.team}
        for q in queue
    ]

    # Get recent reports
    reports = service.get_reports(limit=5)
    recent_reports = [
        AuditorReport(
            id=r.id,
            report_type=r.report_type,
            title=r.title,
            summary=r.summary,
            sections=r.sections,
            created_at=r.created_at,
            sent_at=r.sent_at,
        )
        for r in reports
    ]

    return AuditorDashboard(
        live_feeds=live_feeds,
        flagged_items=flagged_items,
        metrics=metrics,
        audit_queue=audit_queue,
        recent_reports=recent_reports,
    )


@router.get("/auditor/flags", response_model=list[AuditorFlag])
async def get_auditor_flags(
    db: DbSession,
    severity: FlagSeverity | None = None,
    resolved: bool = False,
) -> list[AuditorFlag]:
    """Get auditor flags with optional filters."""
    service = get_dashboard_service(db)
    flags = service.get_flags(
        severity=severity.value if severity else None,
        resolved=resolved,
    )
    return [
        AuditorFlag(
            id=f.id,
            severity=FlagSeverity(f.severity),
            category=f.category,
            title=f.title,
            description=f.description,
            related_task_id=f.related_task_id,
            related_agent_id=f.related_agent_id,
            created_at=f.created_at,
            resolved_at=f.resolved_at,
            notes=f.notes,
        )
        for f in flags
    ]


@router.post(
    "/auditor/flags", response_model=AuditorFlag, status_code=status.HTTP_201_CREATED
)
async def create_auditor_flag(
    data: CreateFlagRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> AuditorFlag:
    """Create a new auditor flag."""
    _require_auditor_or_ceo(agent)
    service = get_dashboard_service(db)
    params = CreateFlagParams(
        severity=data.severity.value,
        category=data.category,
        title=data.title,
        description=data.description,
        related_task_id=data.related_task_id,
        related_agent_id=data.related_agent_id,
    )
    flag = service.create_flag(params)
    return AuditorFlag(
        id=flag.id,
        severity=data.severity,
        category=flag.category,
        title=flag.title,
        description=flag.description,
        related_task_id=flag.related_task_id,
        related_agent_id=flag.related_agent_id,
        created_at=flag.created_at,
        resolved_at=flag.resolved_at,
        notes=flag.notes,
    )


@router.put("/auditor/flags/{flag_id}/resolve")
async def resolve_auditor_flag(
    flag_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
    notes: str | None = None,
) -> dict[str, str]:
    """Resolve an auditor flag."""
    _require_auditor_or_ceo(agent)
    service = get_dashboard_service(db)
    if not service.resolve_flag(flag_id, notes):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Flag not found"
        )
    return {"status": "resolved", "flag_id": str(flag_id)}


@router.get("/auditor/reports", response_model=list[AuditorReport])
async def get_auditor_reports(
    db: DbSession,
    report_type: str | None = None,
    limit: int = Query(default=10, ge=1, le=100),
) -> list[AuditorReport]:
    """Get auditor reports."""
    service = get_dashboard_service(db)
    reports = service.get_reports(report_type=report_type, limit=limit)
    return [
        AuditorReport(
            id=r.id,
            report_type=r.report_type,
            title=r.title,
            summary=r.summary,
            sections=r.sections,
            created_at=r.created_at,
            sent_at=r.sent_at,
        )
        for r in reports
    ]


@router.post(
    "/auditor/reports",
    response_model=AuditorReport,
    status_code=status.HTTP_201_CREATED,
)
async def create_auditor_report(
    data: CreateReportRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> AuditorReport:
    """Create a new auditor report."""
    _require_auditor_or_ceo(agent)
    service = get_dashboard_service(db)
    report = service.create_report(
        report_type=data.report_type,
        title=data.title,
        summary=data.summary,
        sections=data.sections,
    )
    return AuditorReport(
        id=report.id,
        report_type=report.report_type,
        title=report.title,
        summary=report.summary,
        sections=report.sections,
        created_at=report.created_at,
        sent_at=report.sent_at,
    )


@router.post("/auditor/reports/{report_id}/send")
async def send_auditor_report(
    report_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
) -> dict[str, str]:
    """Mark a report as sent to CEO."""
    _require_auditor_or_ceo(agent)
    service = get_dashboard_service(db)
    if not service.send_report(report_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report not found"
        )
    return {"status": "sent", "report_id": str(report_id)}


# =============================================================================
# CEO OVERVIEW
# =============================================================================


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
    service = get_dashboard_service(db)
    usage_svc = get_usage_service(db)

    health_list = await service.get_team_health_list()
    health_status = [
        TeamHealth(
            team=h.team,
            status=h.status,
            active_tasks=h.active_tasks,
            blocked_tasks=h.blocked_tasks,
            blocked_ratio=h.blocked_ratio,
            completed_this_week=h.completed_this_week,
        )
        for h in health_list
    ]

    # Populate usage_summary from daily_usage_rollups for today
    try:
        today_usage = await usage_svc.get_today_summary()
        usage_summary = UsageSummary(
            tokens_today=today_usage["tokens_today"],
            cost_today_usd=today_usage["cost_today_usd"],
        )
    except Exception:
        usage_summary = UsageSummary(tokens_today=0, cost_today_usd=0.0)

    return CEOOverview(
        health_status=health_status,
        key_metrics=await service.get_key_metrics(),
        auditor_alerts=service.get_auditor_alerts(),
        roadmap_progress=await service.get_roadmap_progress(),
        usage_summary=usage_summary,
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


@router.get("/kanban/main-pm")
async def get_main_pm_kanban(
    db: DbSession,
) -> dict[str, Any]:
    """Get the Main PM cross-cell kanban board.

    Declared BEFORE `/kanban/{team}` so FastAPI matches the literal
    `main-pm` segment instead of treating it as a `Team` enum value
    (which would 422 since "main-pm" isn't a Team member).
    """
    kanban_service = get_kanban_service(db)
    board = await kanban_service.get_main_pm_board_flat()
    return board.model_dump()


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


# =============================================================================
# AGENT STATUS ENDPOINTS
# =============================================================================


@router.get("/agents/status")
async def get_all_agent_status(
    db: DbSession,
    team: Team | None = None,
) -> dict[str, Any]:
    """Return agent-status summary (counts + per-agent snapshot)."""
    dashboard = get_dashboard_service(db)
    return await dashboard.get_all_agent_status(team)


# =============================================================================
# RECENT ACTIVITY ENDPOINTS
# =============================================================================


@router.get("/activity/recent")
async def get_recent_activity(
    db: DbSession,
    hours: int = Query(default=24, ge=1, le=168),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Return recent messages + task updates for the dashboard feed."""
    dashboard = get_dashboard_service(db)
    return await dashboard.get_recent_activity(hours=hours, limit=limit)


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


# =============================================================================
# OBSERVABILITY ENDPOINTS (0.10.0): cycle-time / bottlenecks / rework / scorecard
# =============================================================================


@router.get("/metrics/cycle-time")
async def get_cycle_time(
    db: DbSession,
    days: int = Query(default=30, ge=1, le=90),
    team: Team | None = None,
) -> list[dict[str, Any]]:
    """Per-stage cycle time (dwell per lifecycle status) over the window."""
    metrics_service = get_metrics_service(db)
    stages = await metrics_service.get_cycle_time_by_stage(team=team, days=days)
    return [s.to_dict() for s in stages]


@router.get("/metrics/bottlenecks")
async def get_bottlenecks(
    db: DbSession,
    days: int = Query(default=30, ge=1, le=90),
) -> dict[str, Any]:
    """Where work piles up: cumulative dwell per stage + live parked counts."""
    metrics_service = get_metrics_service(db)
    report = await metrics_service.get_bottleneck_distribution(days=days)
    return report.to_dict()


@router.get("/metrics/rework")
async def get_rework(
    db: DbSession,
    days: int = Query(default=30, ge=1, le=90),
    team: Team | None = None,
) -> dict[str, Any]:
    """Rework rate (bounced/completed) overall, by team, and by agent + cost."""
    metrics_service = get_metrics_service(db)
    report = await metrics_service.get_rework_metrics(team=team, days=days)
    return report.to_dict()


@router.get("/metrics/scorecard/agent/{agent_id}")
async def get_agent_scorecard(
    agent_id: UUID,
    db: DbSession,
    days: int = Query(default=7, ge=1, le=90),
) -> dict[str, Any]:
    """Fused per-agent delivery scorecard."""
    metrics_service = get_metrics_service(db)
    card = await metrics_service.get_scorecard(agent_id=agent_id, days=days)
    if card is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found"
        )
    return card.to_dict()


@router.get("/metrics/scorecard/team/{team}")
async def get_team_scorecard(
    team: Team,
    db: DbSession,
    days: int = Query(default=7, ge=1, le=90),
) -> dict[str, Any]:
    """Fused per-cell delivery scorecard."""
    metrics_service = get_metrics_service(db)
    card = await metrics_service.get_scorecard(team=team, days=days)
    if card is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Team scorecard unavailable"
        )
    return card.to_dict()


@router.get("/metrics/task/{task_id}")
async def get_task_metrics(
    task_id: UUID,
    db: DbSession,
) -> dict[str, Any]:
    """Granular per-task metrics: real effort vs wall-clock, turns/tool-calls,
    per-stage active-vs-wait, who-caused-rework, tokens + cost (live)."""
    metrics_service = get_metrics_service(db)
    metrics = await metrics_service.get_task_metrics(task_id)
    if metrics is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )
    return metrics.to_dict()
