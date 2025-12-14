"""
Dashboard Service

Business logic for auditor and CEO dashboards.
Manages flags, reports, and aggregated metrics.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.tables import ChannelTable, TaskTable
from roboco.models.base import TaskStatus, Team
from roboco.models.dashboard import (
    AuditQueueItem,
    ChannelFeedData,
    CreateFlagParams,
    DashboardStorage,
    FlagData,
    ReportData,
    TeamHealthData,
)
from roboco.services.metrics import MetricsService, get_metrics_service
from roboco.utils.converters import require_uuid

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


class DashboardService:
    """
    Service for dashboard business logic.

    Manages:
    - Auditor flags (create, list, resolve)
    - Auditor reports (create, list, send)
    - CEO overview aggregations
    - Channel status computations
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._storage = get_storage()
        self._metrics: MetricsService | None = None

    @property
    def metrics(self) -> MetricsService:
        """Lazy-load metrics service."""
        if self._metrics is None:
            self._metrics = get_metrics_service(self.db)
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
    # CHANNEL STATUS
    # =========================================================================

    async def get_channel_feeds(self) -> list[ChannelFeedData]:
        """Get live feed status for all channels."""
        result = await self.db.execute(select(ChannelTable))
        channels = result.scalars().all()

        feeds = []
        for channel in channels:
            status = self._compute_channel_status(channel.last_activity)
            feeds.append(
                ChannelFeedData(
                    id=require_uuid(channel.id),
                    name=channel.name,
                    status=status,
                    last_activity=channel.last_activity,
                    message_count_24h=channel.message_count,
                )
            )
        return feeds

    def _compute_channel_status(self, last_activity: datetime | None) -> str:
        """Compute channel status based on last activity."""
        if not last_activity:
            return "offline"

        minutes_ago = (datetime.now(UTC) - last_activity).total_seconds() / 60
        streaming_threshold = 5
        idle_threshold = 30

        if minutes_ago < streaming_threshold:
            return "streaming"
        if minutes_ago < idle_threshold:
            return "idle"
        return "offline"

    # =========================================================================
    # AUDIT QUEUE
    # =========================================================================

    async def get_audit_queue(self, limit: int = 10) -> list[AuditQueueItem]:
        """Build audit queue from tasks needing attention."""
        queue: list[AuditQueueItem] = []

        # Tasks blocked
        blocked_result = await self.db.execute(
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
        qa_result = await self.db.execute(
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
        total_result = await self.db.execute(
            select(func.count(TaskTable.id)).where(TaskTable.priority <= 1)
        )
        total_priority = total_result.scalar() or 0

        completed_result = await self.db.execute(
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
        comm = await self.metrics.get_communication_volume(24)

        return {
            "tasks_completed_24h": velocity.tasks_completed,
            "avg_completion_time": velocity.avg_completion_hours,
            "active_blockers": blockers.active_blockers,
            "communication_volume": comm["total_messages"],
        }


# =============================================================================
# SERVICE FACTORY
# =============================================================================


def get_dashboard_service(db: AsyncSession) -> DashboardService:
    """Get a DashboardService instance."""
    return DashboardService(db)
