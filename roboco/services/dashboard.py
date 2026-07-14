"""
Dashboard Service

Business logic for auditor and CEO dashboards.
Manages flags, reports, and aggregated metrics.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.tables import AgentTable, TaskTable
from roboco.models.base import AgentStatus, TaskStatus, Team
from roboco.models.dashboard import (
    AuditQueueItem,
    CreateFlagParams,
    DashboardStorage,
    FlagData,
    ReportData,
    TeamHealthData,
)
from roboco.services.base import BaseService
from roboco.services.metrics import MetricsService, get_metrics_service

# =============================================================================
# STORAGE MANAGEMENT
# =============================================================================


class _DashboardStorageHolder:
    """Holder for singleton DashboardStorage instance."""

    instance: DashboardStorage | None = None


def get_storage() -> DashboardStorage:
    """Get the storage instance (for testing/injection)."""
    if _DashboardStorageHolder.instance is None:
        _DashboardStorageHolder.instance = DashboardStorage()
    return _DashboardStorageHolder.instance


def reset_storage() -> None:
    """Reset storage (for testing)."""
    _DashboardStorageHolder.instance = DashboardStorage()


# =============================================================================
# DASHBOARD SERVICE
# =============================================================================


class DashboardService(BaseService):
    """
    Service for dashboard business logic.

    Manages:
    - Auditor flags (create, list, resolve)
    - Auditor reports (create, list, send)
    - CEO overview aggregations
    """

    service_name: ClassVar[str] = "dashboard"

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(db)
        self._storage = get_storage()
        self._metrics: MetricsService | None = None

    @property
    def metrics(self) -> MetricsService:
        """Lazy-load metrics service."""
        if self._metrics is None:
            self._metrics = get_metrics_service(self.session)
        return self._metrics

    # =========================================================================
    # FLAG MANAGEMENT
    # =========================================================================

    def create_flag(self, params: CreateFlagParams) -> FlagData:
        """Create a new auditor flag."""
        flag = FlagData(
            id=uuid4(),
            severity=params.severity,
            category=params.category,
            title=params.title,
            description=params.description,
            related_task_id=params.related_task_id,
            related_agent_id=params.related_agent_id,
            created_at=datetime.now(UTC),
        )
        self._storage.flags[flag.id] = flag
        return flag

    def get_flags(
        self,
        *,
        severity: str | None = None,
        resolved: bool = False,
    ) -> list[FlagData]:
        """Get flags with optional filters."""
        result = []
        for flag in self._storage.flags.values():
            if severity and flag.severity != severity:
                continue
            if not resolved and flag.resolved_at:
                continue
            if resolved and not flag.resolved_at:
                continue
            result.append(flag)
        return result

    def get_flag(self, flag_id: UUID) -> FlagData | None:
        """Get a single flag by ID."""
        return self._storage.flags.get(flag_id)

    def resolve_flag(self, flag_id: UUID, notes: str | None = None) -> bool:
        """Resolve a flag. Returns True if found and resolved."""
        flag = self._storage.flags.get(flag_id)
        if not flag:
            return False
        flag.resolved_at = datetime.now(UTC)
        if notes:
            flag.notes = notes
        return True

    def count_unresolved_flags(self, severity: str) -> int:
        """Count unresolved flags of a given severity."""
        return sum(
            1
            for f in self._storage.flags.values()
            if f.severity == severity and not f.resolved_at
        )

    # =========================================================================
    # REPORT MANAGEMENT
    # =========================================================================

    def create_report(
        self,
        *,
        report_type: str,
        title: str,
        summary: str,
        sections: list[dict[str, Any]] | None = None,
    ) -> ReportData:
        """Create a new auditor report."""
        report = ReportData(
            id=uuid4(),
            report_type=report_type,
            title=title,
            summary=summary,
            sections=sections or [],
            created_at=datetime.now(UTC),
        )
        self._storage.reports[report.id] = report
        return report

    def get_reports(
        self,
        *,
        report_type: str | None = None,
        limit: int = 10,
    ) -> list[ReportData]:
        """Get reports with optional filters."""
        result = []
        for report in self._storage.reports.values():
            if report_type and report.report_type != report_type:
                continue
            result.append(report)
        return result[-limit:]

    def get_report(self, report_id: UUID) -> ReportData | None:
        """Get a single report by ID."""
        return self._storage.reports.get(report_id)

    def send_report(self, report_id: UUID) -> bool:
        """Mark a report as sent. Returns True if found and marked."""
        report = self._storage.reports.get(report_id)
        if not report:
            return False
        report.sent_at = datetime.now(UTC)
        return True

    def get_last_report_time(self) -> datetime | None:
        """Get the timestamp of the most recent sent report."""
        sent_reports = [r for r in self._storage.reports.values() if r.sent_at]
        if not sent_reports:
            return None
        return max(r.sent_at for r in sent_reports if r.sent_at)

    # =========================================================================
    # AUDIT QUEUE
    # =========================================================================

    async def get_audit_queue(self, limit: int = 10) -> list[AuditQueueItem]:
        """Build audit queue from tasks needing attention."""
        queue: list[AuditQueueItem] = []

        # Tasks blocked
        blocked_result = await self.session.execute(
            select(TaskTable).where(TaskTable.status == TaskStatus.BLOCKED)
        )
        for task in blocked_result.scalars().all():
            queue.append(
                AuditQueueItem(
                    type="blocked_task",
                    title=f"Blocked: {task.title}",
                    task_id=str(task.id),
                    team=task.team.value,
                )
            )

        # Tasks awaiting QA
        qa_result = await self.session.execute(
            select(TaskTable).where(TaskTable.status == TaskStatus.AWAITING_QA)
        )
        for task in qa_result.scalars().all():
            queue.append(
                AuditQueueItem(
                    type="qa_review",
                    title=f"QA Review: {task.title}",
                    task_id=str(task.id),
                    team=task.team.value,
                )
            )

        return queue[:limit]

    # =========================================================================
    # CEO OVERVIEW HELPERS
    # =========================================================================

    async def get_team_health_list(self) -> list[TeamHealthData]:
        """Get health status for all teams."""
        teams = [Team.BACKEND, Team.FRONTEND, Team.UX_UI, Team.BOARD]
        health_list = []

        for team in teams:
            health = await self.metrics.get_health_status(team)
            health_list.append(
                TeamHealthData(
                    team=team.value,
                    status=health["status"],
                    active_tasks=health["active_tasks"],
                    blocked_tasks=health["blocked_tasks"],
                    blocked_ratio=health["blocked_ratio"],
                    completed_this_week=health["completed_this_week"],
                )
            )

        return health_list

    async def get_key_metrics(self) -> dict[str, Any]:
        """Get key organization metrics."""
        velocity = await self.metrics.get_velocity(7)
        team_metrics = await self.metrics.get_all_team_metrics()
        blockers = await self.metrics.get_blocker_metrics()

        total_docs = sum(tm.documentation_coverage for tm in team_metrics)
        avg_doc_coverage = total_docs / len(team_metrics) if team_metrics else 0

        return {
            "velocity_weekly": velocity.tasks_completed,
            "completion_rate": velocity.completion_rate,
            "documentation_coverage": round(avg_doc_coverage, 2),
            "active_blockers": blockers.active_blockers,
        }

    def get_auditor_alerts(self) -> dict[str, Any]:
        """Get auditor alerts summary."""
        last_time = self.get_last_report_time()
        return {
            "urgent_count": self.count_unresolved_flags("urgent"),
            "warning_count": self.count_unresolved_flags("warning"),
            "last_report_at": last_time.isoformat() if last_time else None,
        }

    async def get_roadmap_progress(self) -> dict[str, Any]:
        """Get roadmap progress from high-priority tasks."""
        total_result = await self.session.execute(
            select(func.count(TaskTable.id)).where(TaskTable.priority <= 1)
        )
        total_priority = total_result.scalar() or 0

        completed_result = await self.session.execute(
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

    # =========================================================================
    # AUDITOR DASHBOARD METRICS
    # =========================================================================

    async def get_auditor_metrics(self) -> dict[str, Any]:
        """Get metrics for auditor dashboard."""
        velocity = await self.metrics.get_velocity(7)
        blockers = await self.metrics.get_blocker_metrics()

        return {
            "tasks_completed_24h": velocity.tasks_completed,
            "avg_completion_time": velocity.avg_completion_hours,
            "active_blockers": blockers.active_blockers,
        }

    async def get_open_findings(self, limit: int = 20) -> list[dict[str, Any]]:
        """Open review findings across all tasks for the auditor's findings queue.

        Blocking severity first then newest. Fails open (empty list) — the
        dashboard must never break on a findings-query error.
        """
        from roboco.services.repositories.review_findings import (
            ReviewFindingsRepository,
        )

        try:
            repo = ReviewFindingsRepository(self.session)
            rows = await repo.list_open_findings(limit=limit)
        except Exception:
            return []
        return [
            {
                "id": str(r.id),
                "task_id": str(r.task_id),
                "origin": r.origin,
                "severity": r.severity,
                "file": r.file,
                "line": r.line,
                "criterion": r.criterion,
                "expected": r.expected,
                "actual": r.actual,
                "round": r.round,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]

    async def get_all_agent_status(self, team: Team | None = None) -> dict[str, Any]:
        """Return agent-status summary (counts + per-agent snapshot)."""
        query = select(AgentTable)
        if team:
            query = query.where(AgentTable.team == team)
        result = await self.session.execute(query)
        agents = result.scalars().all()

        status_counts = {s.value: 0 for s in AgentStatus}
        agent_list: list[dict[str, Any]] = []
        for agent in agents:
            agent_status = agent.status or AgentStatus.IDLE
            status_counts[agent_status.value] = (
                status_counts.get(agent_status.value, 0) + 1
            )
            agent_list.append(
                {
                    "id": str(agent.id),
                    "name": agent.name,
                    "role": agent.role.value,
                    "team": agent.team.value if agent.team else None,
                    "status": agent_status.value,
                    "current_task_id": (
                        str(agent.current_task_id) if agent.current_task_id else None
                    ),
                }
            )
        return {
            "total": len(agents),
            "by_status": status_counts,
            "agents": agent_list,
        }

    async def get_recent_activity(self, *, hours: int, limit: int) -> dict[str, Any]:
        """Return recent task updates for the dashboard feed."""
        since = datetime.now(UTC) - timedelta(hours=hours)

        task_result = await self.session.execute(
            select(TaskTable)
            .where(TaskTable.updated_at >= since)
            .order_by(TaskTable.updated_at.desc())
            .limit(limit)
        )
        tasks = task_result.scalars().all()

        feed: list[dict[str, Any]] = []
        for task in tasks:
            feed.append(
                {
                    "type": "task_update",
                    "timestamp": (
                        task.updated_at.isoformat()
                        if task.updated_at
                        else task.created_at.isoformat()
                    ),
                    "task_id": str(task.id),
                    "title": task.title,
                    "status": task.status.value,
                    "team": task.team.value if task.team else "",
                }
            )
        feed.sort(key=lambda x: x["timestamp"], reverse=True)
        return {"period_hours": hours, "activity": feed[:limit]}


# =============================================================================
# SERVICE FACTORY
# =============================================================================


def get_dashboard_service(db: AsyncSession) -> DashboardService:
    """Get a DashboardService instance."""
    return DashboardService(db)
