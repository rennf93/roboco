"""
Metrics Service

Collects and aggregates metrics for reporting and dashboards.
Tracks velocity, blockers, completion rates, and agent performance.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.tables import AgentTable, MessageTable, NotificationTable, TaskTable
from roboco.models.base import TaskStatus, Team
from roboco.models.metrics import (
    AgentMetrics,
    BlockerMetrics,
    TeamMetrics,
    VelocityMetrics,
)
from roboco.services.base import BaseService
from roboco.utils.converters import to_python_uuid

# Constants
DEFAULT_VELOCITY_DAYS = 7
DEFAULT_COMM_HOURS = 24
HOURS_PER_DAY = 24
SECONDS_PER_HOUR = 3600

# Active task statuses for queries
ACTIVE_STATUSES = frozenset(
    {
        TaskStatus.CLAIMED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.VERIFYING,
        TaskStatus.AWAITING_QA,
    }
)

# Health thresholds
CRITICAL_BLOCKED_RATIO = 0.3
SLOW_BLOCKED_RATIO = 0.15
STALE_TASK_THRESHOLD = 5


def _calculate_completion_rate(completed: int, created: int) -> float:
    """Calculate task completion rate."""
    return completed / created if created > 0 else 0.0


def _calculate_avg_blocked_hours(blocked_hours: list[float]) -> float | None:
    """Calculate average blocked hours from list."""
    return sum(blocked_hours) / len(blocked_hours) if blocked_hours else None


def _format_period(days: int) -> str:
    """Format days as period string."""
    return f"{days}d"


class MetricsService(BaseService):
    """
    Service for collecting and aggregating metrics.

    Provides:
    - Velocity metrics (completion rate, throughput)
    - Blocker tracking
    - Team and agent performance
    - Communication volume
    """

    service_name: ClassVar[str] = "metrics"

    # =========================================================================
    # VELOCITY METRICS
    # =========================================================================

    async def get_velocity(
        self,
        days: int = 7,
        team: Team | None = None,
    ) -> VelocityMetrics:
        """Get velocity metrics for a time period."""
        since = datetime.now(UTC) - timedelta(days=days)

        # Tasks completed in period
        completed_query = select(func.count(TaskTable.id)).where(
            and_(
                TaskTable.completed_at >= since,
                TaskTable.status == TaskStatus.COMPLETED,
            )
        )
        if team:
            completed_query = completed_query.where(TaskTable.team == team)

        completed_result = await self.session.execute(completed_query)
        tasks_completed = completed_result.scalar() or 0

        # Tasks created in period
        created_query = select(func.count(TaskTable.id)).where(
            TaskTable.created_at >= since
        )
        if team:
            created_query = created_query.where(TaskTable.team == team)

        created_result = await self.session.execute(created_query)
        tasks_created = created_result.scalar() or 0

        # Average completion time
        avg_query = select(
            func.avg(
                func.extract(
                    "epoch",
                    TaskTable.completed_at - TaskTable.started_at,
                )
                / 3600  # Convert to hours
            )
        ).where(
            and_(
                TaskTable.completed_at >= since,
                TaskTable.started_at.isnot(None),
                TaskTable.status == TaskStatus.COMPLETED,
            )
        )
        if team:
            avg_query = avg_query.where(TaskTable.team == team)

        avg_result = await self.session.execute(avg_query)
        avg_hours = avg_result.scalar()

        completion_rate = _calculate_completion_rate(tasks_completed, tasks_created)

        return VelocityMetrics(
            period=_format_period(days),
            tasks_completed=tasks_completed,
            tasks_created=tasks_created,
            avg_completion_hours=round(avg_hours, 2) if avg_hours else None,
            completion_rate=round(completion_rate, 2),
        )

    # =========================================================================
    # BLOCKER METRICS
    # =========================================================================

    async def get_blocker_metrics(self) -> BlockerMetrics:
        """Get metrics about blocked tasks."""
        # Count active blockers
        count_result = await self.session.execute(
            select(func.count(TaskTable.id)).where(
                TaskTable.status == TaskStatus.BLOCKED
            )
        )
        active_blockers = count_result.scalar() or 0

        # Get blocked tasks for analysis
        blocked_result = await self.session.execute(
            select(TaskTable).where(TaskTable.status == TaskStatus.BLOCKED)
        )
        blocked_tasks = blocked_result.scalars().all()

        # Calculate average blocked time
        now = datetime.now(UTC)
        blocked_hours = []
        longest_task_id = None
        longest_hours = 0.0

        for task in blocked_tasks:
            # Assume task got blocked around last update or creation
            blocked_since = task.updated_at or task.created_at
            hours = (now - blocked_since).total_seconds() / 3600
            blocked_hours.append(hours)

            if hours > longest_hours:
                longest_hours = hours
                longest_task_id = task.id

        avg_blocked = _calculate_avg_blocked_hours(blocked_hours)

        # Count blockers by team
        team_result = await self.session.execute(
            select(TaskTable.team, func.count(TaskTable.id))
            .where(TaskTable.status == TaskStatus.BLOCKED)
            .group_by(TaskTable.team)
        )
        blockers_by_team = {row[0].value: row[1] for row in team_result.all()}

        return BlockerMetrics(
            active_blockers=active_blockers,
            avg_blocked_hours=round(avg_blocked, 2) if avg_blocked else None,
            longest_blocked_task_id=to_python_uuid(longest_task_id),
            longest_blocked_hours=round(longest_hours, 2) if longest_hours else None,
            blockers_by_team=blockers_by_team,
        )

    # =========================================================================
    # TEAM METRICS
    # =========================================================================

    async def get_team_metrics(self, team: Team) -> TeamMetrics:
        """Get metrics for a specific team."""
        week_ago = datetime.now(UTC) - timedelta(days=7)

        # Active tasks
        active_result = await self.session.execute(
            select(func.count(TaskTable.id)).where(
                and_(
                    TaskTable.team == team,
                    TaskTable.status.in_(
                        [
                            TaskStatus.CLAIMED,
                            TaskStatus.IN_PROGRESS,
                            TaskStatus.VERIFYING,
                            TaskStatus.AWAITING_QA,
                        ]
                    ),
                )
            )
        )
        active_tasks = active_result.scalar() or 0

        # Completed this week
        completed_result = await self.session.execute(
            select(func.count(TaskTable.id)).where(
                and_(
                    TaskTable.team == team,
                    TaskTable.completed_at >= week_ago,
                    TaskTable.status == TaskStatus.COMPLETED,
                )
            )
        )
        completed_tasks_week = completed_result.scalar() or 0

        # Blocked tasks
        blocked_result = await self.session.execute(
            select(func.count(TaskTable.id)).where(
                and_(
                    TaskTable.team == team,
                    TaskTable.status == TaskStatus.BLOCKED,
                )
            )
        )
        blocked_tasks = blocked_result.scalar() or 0

        # Average completion time
        avg_result = await self.session.execute(
            select(
                func.avg(
                    func.extract(
                        "epoch",
                        TaskTable.completed_at - TaskTable.started_at,
                    )
                    / 3600
                )
            ).where(
                and_(
                    TaskTable.team == team,
                    TaskTable.completed_at >= week_ago,
                    TaskTable.started_at.isnot(None),
                    TaskTable.status == TaskStatus.COMPLETED,
                )
            )
        )
        avg_hours = avg_result.scalar()

        # Documentation coverage (tasks with QA pass that have docs)
        # Simplified: ratio of completed tasks with dev_notes
        total_result = await self.session.execute(
            select(func.count(TaskTable.id)).where(
                and_(
                    TaskTable.team == team,
                    TaskTable.status == TaskStatus.COMPLETED,
                )
            )
        )
        total_completed = total_result.scalar() or 0

        with_docs_result = await self.session.execute(
            select(func.count(TaskTable.id)).where(
                and_(
                    TaskTable.team == team,
                    TaskTable.status == TaskStatus.COMPLETED,
                    TaskTable.dev_notes.isnot(None),
                )
            )
        )
        with_docs = with_docs_result.scalar() or 0

        doc_coverage = with_docs / total_completed if total_completed > 0 else 0.0

        return TeamMetrics(
            team=team,
            active_tasks=active_tasks,
            completed_tasks_week=completed_tasks_week,
            blocked_tasks=blocked_tasks,
            avg_completion_hours=round(avg_hours, 2) if avg_hours else None,
            documentation_coverage=round(doc_coverage, 2),
        )

    async def get_all_team_metrics(self) -> list[TeamMetrics]:
        """Get metrics for all teams."""
        metrics = []
        for team in [Team.BACKEND, Team.FRONTEND, Team.UX_UI]:
            metrics.append(await self.get_team_metrics(team))
        return metrics

    # =========================================================================
    # AGENT METRICS
    # =========================================================================

    async def get_agent_metrics(self, agent_id: UUID) -> AgentMetrics | None:
        """Get metrics for a specific agent."""
        # Get agent
        agent_result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        if not agent:
            return None

        week_ago = datetime.now(UTC) - timedelta(days=7)

        # Tasks completed this week
        completed_result = await self.session.execute(
            select(func.count(TaskTable.id)).where(
                and_(
                    TaskTable.assigned_to == agent_id,
                    TaskTable.completed_at >= week_ago,
                    TaskTable.status == TaskStatus.COMPLETED,
                )
            )
        )
        tasks_completed = completed_result.scalar() or 0

        # Average completion time
        avg_result = await self.session.execute(
            select(
                func.avg(
                    func.extract(
                        "epoch",
                        TaskTable.completed_at - TaskTable.started_at,
                    )
                    / 3600
                )
            ).where(
                and_(
                    TaskTable.assigned_to == agent_id,
                    TaskTable.completed_at >= week_ago,
                    TaskTable.started_at.isnot(None),
                    TaskTable.status == TaskStatus.COMPLETED,
                )
            )
        )
        avg_hours = avg_result.scalar()

        # Messages sent this week
        messages_result = await self.session.execute(
            select(func.count(MessageTable.id)).where(
                and_(
                    MessageTable.agent_id == agent_id,
                    MessageTable.timestamp >= week_ago,
                )
            )
        )
        messages_sent = messages_result.scalar() or 0

        return AgentMetrics(
            agent_id=agent_id,
            agent_name=agent.name,
            tasks_completed_week=tasks_completed,
            current_task_id=to_python_uuid(agent.current_task_id),
            avg_completion_hours=round(avg_hours, 2) if avg_hours else None,
            messages_sent_week=messages_sent,
        )

    # =========================================================================
    # COMMUNICATION METRICS
    # =========================================================================

    async def get_communication_volume(
        self,
        hours: int = 24,
    ) -> dict[str, Any]:
        """Get communication volume metrics."""
        since = datetime.now(UTC) - timedelta(hours=hours)

        # Total messages
        total_result = await self.session.execute(
            select(func.count(MessageTable.id)).where(MessageTable.timestamp >= since)
        )
        total_messages = total_result.scalar() or 0

        # Messages by type
        type_result = await self.session.execute(
            select(MessageTable.type, func.count(MessageTable.id))
            .where(MessageTable.timestamp >= since)
            .group_by(MessageTable.type)
        )
        by_type = {row[0].value: row[1] for row in type_result.all()}

        # Active channels
        channel_result = await self.session.execute(
            select(func.count(func.distinct(MessageTable.channel_id))).where(
                MessageTable.timestamp >= since
            )
        )
        active_channels = channel_result.scalar() or 0

        # Notifications sent
        notif_result = await self.session.execute(
            select(func.count(NotificationTable.id)).where(
                NotificationTable.timestamp >= since
            )
        )
        notifications_sent = notif_result.scalar() or 0

        return {
            "period_hours": hours,
            "total_messages": total_messages,
            "messages_by_type": by_type,
            "active_channels": active_channels,
            "notifications_sent": notifications_sent,
        }

    # =========================================================================
    # HEALTH STATUS
    # =========================================================================

    def _determine_health_status(
        self,
        blocked_ratio: float,
        active_count: int,
        completed_count: int,
    ) -> str:
        """Determine health status from metrics."""
        if blocked_ratio > CRITICAL_BLOCKED_RATIO:
            return "critical"
        if blocked_ratio > SLOW_BLOCKED_RATIO:
            return "slow"
        if active_count > STALE_TASK_THRESHOLD and completed_count == 0:
            return "slow"
        return "ok"

    async def _get_task_count(
        self,
        status_filter: list[TaskStatus] | TaskStatus,
        team: Team | None,
        since: datetime | None = None,
    ) -> int:
        """Get count of tasks matching criteria."""
        conditions: list[Any] = []
        if isinstance(status_filter, list):
            conditions.append(TaskTable.status.in_(status_filter))
        else:
            conditions.append(TaskTable.status == status_filter)
        if team:
            conditions.append(TaskTable.team == team)
        if since:
            conditions.append(TaskTable.completed_at >= since)

        query = select(func.count(TaskTable.id)).where(and_(*conditions))
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def get_health_status(self, team: Team | None = None) -> dict[str, Any]:
        """
        Get health status for a team or the whole organization.

        Returns status: ok, slow, or critical based on:
        - Blocked task ratio
        - Completion rate
        - Average task age
        """
        week_ago = datetime.now(UTC) - timedelta(days=7)
        active_statuses = [
            TaskStatus.CLAIMED,
            TaskStatus.IN_PROGRESS,
            TaskStatus.VERIFYING,
            TaskStatus.AWAITING_QA,
            TaskStatus.BLOCKED,
        ]

        active_count = await self._get_task_count(active_statuses, team)
        blocked_count = await self._get_task_count(TaskStatus.BLOCKED, team)
        completed_count = await self._get_task_count(
            TaskStatus.COMPLETED, team, since=week_ago
        )

        blocked_ratio = blocked_count / active_count if active_count > 0 else 0
        status_str = self._determine_health_status(
            blocked_ratio, active_count, completed_count
        )

        return {
            "status": status_str,
            "team": team.value if team else "all",
            "active_tasks": active_count,
            "blocked_tasks": blocked_count,
            "blocked_ratio": round(blocked_ratio, 2),
            "completed_this_week": completed_count,
        }


# =============================================================================
# SERVICE FACTORY
# =============================================================================


def get_metrics_service(session: AsyncSession) -> MetricsService:
    """Get a MetricsService instance."""
    return MetricsService(session)
