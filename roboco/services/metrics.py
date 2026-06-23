"""
Metrics Service

Collects and aggregates metrics for reporting and dashboards.
Tracks velocity, blockers, completion rates, and agent performance.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.tables import (
    AgentSpawnSessionTable,
    AgentTable,
    AuditLogTable,
    MessageTable,
    NotificationTable,
    TaskTable,
)
from roboco.models.base import TaskStatus, Team
from roboco.models.metrics import (
    AgentMetrics,
    AgentReworkRate,
    BlockerMetrics,
    BottleneckReport,
    ReworkReport,
    Scorecard,
    StageBottleneck,
    StageTiming,
    TeamMetrics,
    TeamReworkRate,
    VelocityMetrics,
)
from roboco.services.base import BaseService
from roboco.utils.converters import to_python_uuid

# Constants
DEFAULT_VELOCITY_DAYS = 7
DEFAULT_COMM_HOURS = 24
HOURS_PER_DAY = 24
SECONDS_PER_HOUR = 3600


def _as_hours(value: Any) -> float | None:
    """Coerce a SQL avg/extract aggregate to a rounded float, or None.

    ``EXTRACT(epoch ...)`` returns ``numeric`` on PostgreSQL 14+, which asyncpg
    surfaces as a ``Decimal`` — and a ``Decimal`` serializes to a JSON *string*,
    crashing the panel's numeric formatting (``value.toFixed(...)``). Rounding to
    a real ``float`` here keeps every "hours" field a JSON number.
    """
    return round(float(value), 2) if value else None


# Active task statuses for get_team_metrics and related queries.
# Note: BLOCKED is intentionally excluded here; get_health_status() uses its
# own local list that includes BLOCKED to compute the blocked-task ratio.
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
            avg_completion_hours=_as_hours(avg_hours),
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
            avg_blocked_hours=_as_hours(avg_blocked),
            longest_blocked_task_id=to_python_uuid(longest_task_id),
            longest_blocked_hours=_as_hours(longest_hours),
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
                    TaskTable.status.in_(ACTIVE_STATUSES),
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
            avg_completion_hours=_as_hours(avg_hours),
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
            avg_completion_hours=_as_hours(avg_hours),
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

    # =========================================================================
    # OBSERVABILITY (cycle-time / bottleneck / rework / scorecard)
    # =========================================================================

    async def get_cycle_time_by_stage(
        self, team: Team | None = None, days: int = 30
    ) -> list[StageTiming]:
        """Per-stage dwell time reconstructed from the audit_log journey.

        Each generic ``task.<status>`` event marks entry into a status; the
        dwell in that status is the gap to the next event for the same task.
        The named ``task.qa_fail`` / ``task.pr_fail`` events are excluded —
        ``event_type = 'task.' || to_status`` keeps only generic transitions, so
        the same-timestamp named events can't inject a zero-length stage.
        """
        since = datetime.now(UTC) - timedelta(days=days)
        team_clause = "AND a.details->>'team' = :team" if team else ""
        sql = text(
            f"""
            WITH ordered AS (
                SELECT
                    (a.details->>'to_status') AS status,
                    a.timestamp AS entered_at,
                    LEAD(a.timestamp) OVER (
                        PARTITION BY a.target_id ORDER BY a.timestamp
                    ) AS exited_at
                FROM audit_log a
                WHERE a.event_type LIKE 'task.%'
                  AND a.event_type = 'task.' || (a.details->>'to_status')
                  AND a.timestamp >= :since
                  {team_clause}
            )
            SELECT
                status,
                AVG(EXTRACT(epoch FROM (exited_at - entered_at)))::float AS avg_s,
                PERCENTILE_CONT(0.5) WITHIN GROUP (
                    ORDER BY EXTRACT(epoch FROM (exited_at - entered_at))
                )::float AS median_s,
                PERCENTILE_CONT(0.9) WITHIN GROUP (
                    ORDER BY EXTRACT(epoch FROM (exited_at - entered_at))
                )::float AS p90_s,
                COUNT(*) AS n
            FROM ordered
            WHERE exited_at IS NOT NULL
            GROUP BY status
            ORDER BY avg_s DESC
            """
        )
        params: dict[str, Any] = {"since": since}
        if team:
            params["team"] = team.value
        rows = (await self.session.execute(sql, params)).all()
        return [
            StageTiming(
                status=r.status,
                avg_seconds=float(r.avg_s or 0.0),
                median_seconds=float(r.median_s or 0.0),
                p90_seconds=float(r.p90_s or 0.0),
                sample_size=int(r.n),
            )
            for r in rows
        ]

    async def get_bottleneck_distribution(self, days: int = 30) -> BottleneckReport:
        """Where work piles up: cumulative dwell per stage + live parked counts."""
        stages = await self.get_cycle_time_by_stage(days=days)
        cumulative = {s.status: s.avg_seconds * s.sample_size for s in stages}
        total = sum(cumulative.values())

        parked_rows = (
            await self.session.execute(
                select(TaskTable.status, func.count(TaskTable.id)).group_by(
                    TaskTable.status
                )
            )
        ).all()
        parked = {
            (st.value if hasattr(st, "value") else str(st)): cnt
            for st, cnt in parked_rows
        }

        by_stage = [
            StageBottleneck(
                status=status,
                cumulative_seconds=cum,
                parked_now=parked.get(status, 0),
                pct_of_total=(cum / total) if total else 0.0,
            )
            for status, cum in cumulative.items()
        ]
        by_stage.sort(key=lambda s: s.cumulative_seconds, reverse=True)
        blockers = await self.get_blocker_metrics()
        return BottleneckReport(
            by_stage=by_stage,
            worst_stage=by_stage[0].status if by_stage else None,
            active_blockers=blockers.active_blockers,
        )

    async def _completed_reworked_counts(
        self, since: datetime, team: Team | None
    ) -> tuple[int, int]:
        """(#completed, #completed-with-a-rework) in the window, optional team."""
        base: list[Any] = [
            TaskTable.status == TaskStatus.COMPLETED,
            TaskTable.completed_at >= since,
        ]
        if team:
            base.append(TaskTable.team == team)
        completed = (
            await self.session.execute(
                select(func.count(TaskTable.id)).where(and_(*base))
            )
        ).scalar() or 0
        reworked = (
            await self.session.execute(
                select(func.count(TaskTable.id)).where(
                    and_(*base, TaskTable.revision_count > 0)
                )
            )
        ).scalar() or 0
        return completed, reworked

    async def _rework_by_agent(self, since: datetime) -> list[AgentReworkRate]:
        """Per-agent rework: owner bounce-rate + reviewer-attributed fails."""
        fail_rows = (
            await self.session.execute(
                select(
                    AuditLogTable.agent_id,
                    AuditLogTable.event_type,
                    func.count(AuditLogTable.id),
                )
                .where(
                    AuditLogTable.event_type.in_(["task.qa_fail", "task.pr_fail"]),
                    AuditLogTable.timestamp >= since,
                    AuditLogTable.agent_id.isnot(None),
                )
                .group_by(AuditLogTable.agent_id, AuditLogTable.event_type)
            )
        ).all()
        completed_by = await self._count_by_assignee(since, reworked_only=False)
        reworked_by = await self._count_by_assignee(since, reworked_only=True)

        qa_by: dict[str, int] = {}
        pr_by: dict[str, int] = {}
        for agent_id, event_type, cnt in fail_rows:
            key = str(agent_id)
            (qa_by if event_type == "task.qa_fail" else pr_by)[key] = cnt

        agent_ids = set(completed_by) | set(qa_by) | set(pr_by)
        if not agent_ids:
            return []
        slug_rows = (
            await self.session.execute(
                select(AgentTable.id, AgentTable.slug).where(
                    AgentTable.id.in_([UUID(a) for a in agent_ids])
                )
            )
        ).all()
        slug_by = {str(i): s for i, s in slug_rows}

        out = [
            AgentReworkRate(
                agent_slug=slug_by.get(aid, aid),
                rate=(
                    reworked_by.get(aid, 0) / completed_by[aid]
                    if completed_by.get(aid)
                    else 0.0
                ),
                qa_fails=qa_by.get(aid, 0),
                pr_fails=pr_by.get(aid, 0),
            )
            for aid in agent_ids
        ]
        out.sort(key=lambda a: (a.qa_fails + a.pr_fails, a.rate), reverse=True)
        return out

    async def _count_by_assignee(
        self, since: datetime, *, reworked_only: bool
    ) -> dict[str, int]:
        """#completed tasks per assignee in the window (optionally rework-only)."""
        conds: list[Any] = [
            TaskTable.status == TaskStatus.COMPLETED,
            TaskTable.completed_at >= since,
            TaskTable.assigned_to.isnot(None),
        ]
        if reworked_only:
            conds.append(TaskTable.revision_count > 0)
        rows = (
            await self.session.execute(
                select(TaskTable.assigned_to, func.count(TaskTable.id))
                .where(and_(*conds))
                .group_by(TaskTable.assigned_to)
            )
        ).all()
        return {str(a): c for a, c in rows}

    async def _rework_cost(self, since: datetime, team: Team | None) -> float:
        """Total spawn-session cost of the reworked tasks in the window."""
        base: list[Any] = [
            TaskTable.status == TaskStatus.COMPLETED,
            TaskTable.completed_at >= since,
            TaskTable.revision_count > 0,
        ]
        if team:
            base.append(TaskTable.team == team)
        ids = (
            (await self.session.execute(select(TaskTable.id).where(and_(*base))))
            .scalars()
            .all()
        )
        if not ids:
            return 0.0
        cost = (
            await self.session.execute(
                select(
                    func.coalesce(
                        func.sum(AgentSpawnSessionTable.estimated_cost_usd), 0.0
                    )
                ).where(AgentSpawnSessionTable.task_id.in_([str(i) for i in ids]))
            )
        ).scalar() or 0.0
        return float(cost)

    async def get_rework_metrics(
        self, team: Team | None = None, days: int = 30
    ) -> ReworkReport:
        """Rework rate (bounced/completed) overall, by team, and by agent + cost."""
        since = datetime.now(UTC) - timedelta(days=days)
        completed, reworked = await self._completed_reworked_counts(since, team)
        by_team = []
        for t in [Team.BACKEND, Team.FRONTEND, Team.UX_UI]:
            c, r = await self._completed_reworked_counts(since, t)
            by_team.append(TeamReworkRate(team=t.value, rate=(r / c if c else 0.0)))
        by_agent = await self._rework_by_agent(since)
        cost = await self._rework_cost(since, team)
        return ReworkReport(
            rate=(reworked / completed if completed else 0.0),
            total_completed=completed,
            total_reworked=reworked,
            by_team=by_team,
            by_agent=by_agent,
            rework_cost_usd=cost,
        )

    async def _tokens_cost_for(
        self, *, agent_slug: str | None, team: Team | None, since: datetime
    ) -> tuple[int, float]:
        """Sum (tokens, cost) from spawn sessions for an agent or a team."""
        tok = (
            AgentSpawnSessionTable.tokens_input
            + AgentSpawnSessionTable.tokens_output
            + AgentSpawnSessionTable.tokens_cache_read
            + AgentSpawnSessionTable.tokens_cache_write
        )
        conds: list[Any] = [AgentSpawnSessionTable.started_at >= since]
        if agent_slug is not None:
            conds.append(AgentSpawnSessionTable.agent_slug == agent_slug)
        if team is not None:
            conds.append(AgentSpawnSessionTable.team == team.value)
        row = (
            await self.session.execute(
                select(
                    func.coalesce(func.sum(tok), 0),
                    func.coalesce(
                        func.sum(AgentSpawnSessionTable.estimated_cost_usd), 0.0
                    ),
                ).where(and_(*conds))
            )
        ).first()
        return (int(row[0]) if row else 0, float(row[1]) if row else 0.0)

    async def get_scorecard(
        self,
        agent_id: UUID | None = None,
        team: Team | None = None,
        days: int = 7,
    ) -> Scorecard | None:
        """Fused per-agent or per-cell delivery scorecard (None if agent absent)."""
        since = datetime.now(UTC) - timedelta(days=days)
        if agent_id is not None:
            agent = (
                await self.session.execute(
                    select(AgentTable).where(AgentTable.id == agent_id)
                )
            ).scalar_one_or_none()
            if agent is None:
                return None
            completed, reworked = await self._completed_for_owner(agent_id, since)
            avg_hours = await self._avg_cycle_hours(
                owner=agent_id, team=None, since=since
            )
            tokens, cost = await self._tokens_cost_for(
                agent_slug=agent.slug, team=None, since=since
            )
            return Scorecard(
                scope="agent",
                id=str(agent_id),
                name=agent.name,
                tasks_completed=completed,
                avg_cycle_hours=avg_hours,
                rework_rate=(reworked / completed if completed else 0.0),
                tokens=tokens,
                cost_usd=cost,
            )
        if team is not None:
            completed, reworked = await self._completed_reworked_counts(since, team)
            avg_hours = await self._avg_cycle_hours(owner=None, team=team, since=since)
            tokens, cost = await self._tokens_cost_for(
                agent_slug=None, team=team, since=since
            )
            return Scorecard(
                scope="cell",
                id=team.value,
                name=team.value,
                tasks_completed=completed,
                avg_cycle_hours=avg_hours,
                rework_rate=(reworked / completed if completed else 0.0),
                tokens=tokens,
                cost_usd=cost,
            )
        return None

    async def _completed_for_owner(
        self, agent_id: UUID, since: datetime
    ) -> tuple[int, int]:
        """(#completed, #reworked) tasks owned by an agent in the window."""
        base: list[Any] = [
            TaskTable.assigned_to == agent_id,
            TaskTable.status == TaskStatus.COMPLETED,
            TaskTable.completed_at >= since,
        ]
        completed = (
            await self.session.execute(
                select(func.count(TaskTable.id)).where(and_(*base))
            )
        ).scalar() or 0
        reworked = (
            await self.session.execute(
                select(func.count(TaskTable.id)).where(
                    and_(*base, TaskTable.revision_count > 0)
                )
            )
        ).scalar() or 0
        return completed, reworked

    async def _avg_cycle_hours(
        self, *, owner: UUID | None, team: Team | None, since: datetime
    ) -> float | None:
        """Average completed-task cycle time (started→completed) in hours."""
        conds: list[Any] = [
            TaskTable.completed_at >= since,
            TaskTable.started_at.isnot(None),
            TaskTable.status == TaskStatus.COMPLETED,
        ]
        if owner is not None:
            conds.append(TaskTable.assigned_to == owner)
        if team is not None:
            conds.append(TaskTable.team == team)
        avg_hours = (
            await self.session.execute(
                select(
                    func.avg(
                        func.extract(
                            "epoch", TaskTable.completed_at - TaskTable.started_at
                        )
                        / 3600
                    )
                ).where(and_(*conds))
            )
        ).scalar()
        return _as_hours(avg_hours)


# =============================================================================
# SERVICE FACTORY
# =============================================================================


def get_metrics_service(session: AsyncSession) -> MetricsService:
    """Get a MetricsService instance."""
    return MetricsService(session)
