"""
Metrics Service

Collects and aggregates metrics for reporting and dashboards.
Tracks velocity, blockers, completion rates, and agent performance.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import and_, bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.tables import (
    AgentSpawnSessionTable,
    AgentTable,
    AuditLogTable,
    MemberPerformanceDailyTable,
    TaskTable,
)
from roboco.foundation.policy.stage_effort import compute_stage_effort
from roboco.models.base import TaskStatus, Team
from roboco.models.metrics import (
    CEO_APPROVAL_DECISIONS,
    CEO_UNBLOCK_DECISIONS,
    AgentMetrics,
    AgentReworkRate,
    BlockerMetrics,
    BottleneckReport,
    CeoScorecard,
    MemberScorecard,
    OrgScorecard,
    ReworkReport,
    Scorecard,
    StageBottleneck,
    StageTiming,
    TaskMetrics,
    TeamMetrics,
    TeamReworkRate,
    VelocityMetrics,
)
from roboco.services.base import BaseService
from roboco.utils.converters import to_python_uuid

# Constants
DEFAULT_VELOCITY_DAYS = 7
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

    async def _blocked_since_map(
        self, blocked_tasks: Sequence[TaskTable]
    ) -> dict[str, datetime]:
        """Per-task ``blocked since`` timestamp from the ``task.blocked`` audit row.

        The real blockage start is the audit transition (#67), indexed on
        (target_id, event_type, timestamp); the old ``updated_at`` heuristic
        over-counted when a blocked task was later touched for a non-blocking
        reason. Returns ``{str(task_id): blocked_at}``; callers fall back to
        ``updated_at or created_at`` for tasks with no audit row.
        """
        blocked_ids = [t.id for t in blocked_tasks]
        if not blocked_ids:
            return {}
        audit_result = await self.session.execute(
            select(
                AuditLogTable.target_id,
                func.max(AuditLogTable.timestamp).label("ts"),
            )
            .where(
                AuditLogTable.event_type == "task.blocked",
                AuditLogTable.target_type == "task",
                AuditLogTable.target_id.in_(blocked_ids),
            )
            .group_by(AuditLogTable.target_id)
        )
        return {
            str(row.target_id): row.ts
            for row in audit_result.all()
            if row.ts is not None
        }

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

        # ``blocked since`` from the ``task.blocked`` audit row, falling back to
        # ``updated_at or created_at`` when no audit row exists (#67).
        blocked_at = await self._blocked_since_map(blocked_tasks)

        # Calculate average blocked time
        now = datetime.now(UTC)
        blocked_hours = []
        longest_task_id = None
        longest_hours = 0.0

        for task in blocked_tasks:
            blocked_since = (
                blocked_at.get(str(task.id)) or task.updated_at or task.created_at
            )
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

        return AgentMetrics(
            agent_id=agent_id,
            agent_name=agent.name,
            tasks_completed_week=tasks_completed,
            current_task_id=to_python_uuid(agent.current_task_id),
            avg_completion_hours=_as_hours(avg_hours),
        )

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
        # A NULL :team disables the team filter — the query is a static string
        # (no interpolation), so the team value is only ever a bound parameter.
        sql = text(
            """
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
                  AND (CAST(:team AS text) IS NULL OR a.details->>'team' = :team)
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
        params: dict[str, Any] = {"since": since, "team": team.value if team else None}
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

    async def _spawn_rollup_for_task(
        self, task_id: UUID, close_at: datetime
    ) -> dict[str, Any]:
        """Per-task spawn aggregates: stints, effort, turns, tool_calls, tokens, cost.

        ``active_runtime`` is summed stint duration (an open stint runs to
        ``close_at`` — completed_at for a terminal task, else now); it can exceed
        wall-clock when stints overlap. ``stints`` is the list of
        ``(started, ended)`` intervals for the stage decomposition. ``task_id``
        is bound as ``str`` — the column is ``String(36)``.
        """
        rows = (
            await self.session.execute(
                select(
                    AgentSpawnSessionTable.started_at,
                    AgentSpawnSessionTable.ended_at,
                    AgentSpawnSessionTable.turns,
                    AgentSpawnSessionTable.tool_calls,
                    AgentSpawnSessionTable.tokens_input,
                    AgentSpawnSessionTable.tokens_output,
                    AgentSpawnSessionTable.tokens_cache_read,
                    AgentSpawnSessionTable.tokens_cache_write,
                    AgentSpawnSessionTable.estimated_cost_usd,
                ).where(AgentSpawnSessionTable.task_id == str(task_id))
            )
        ).all()
        stints: list[tuple[datetime, datetime]] = []
        active = turns = tool_calls = tokens = 0.0
        cost = 0.0
        for r in rows:
            ended = r.ended_at or close_at
            stints.append((r.started_at, ended))
            active += max(0.0, (ended - r.started_at).total_seconds())
            turns += r.turns or 0
            tool_calls += r.tool_calls or 0
            tokens += (
                (r.tokens_input or 0)
                + (r.tokens_output or 0)
                + (r.tokens_cache_read or 0)
                + (r.tokens_cache_write or 0)
            )
            cost += r.estimated_cost_usd or 0.0
        return {
            "stints": stints,
            "active_runtime_seconds": round(active),
            "turns": int(turns),
            "tool_calls": int(tool_calls),
            "tokens": int(tokens),
            "cost_usd": float(cost),
        }

    async def _stage_windows_for_task(
        self, task_id: UUID, close_at: datetime
    ) -> list[tuple[str, datetime, datetime]]:
        """Ordered status windows for one task from the audit_log journey.

        Single-task variant of ``get_cycle_time_by_stage``: LEAD gives each
        generic ``task.<status>`` row's exit as the next row's timestamp; the
        open final window (``exited_at IS NULL``) is closed at ``close_at``
        (completed_at for a terminal task, else now) so an in-flight stage still
        decomposes and a terminal task's final stage doesn't grow forever.
        """
        sql = text(
            """
            WITH ordered AS (
                SELECT
                    (a.details->>'to_status') AS status,
                    a.timestamp AS entered_at,
                    LEAD(a.timestamp) OVER (ORDER BY a.timestamp) AS exited_at
                FROM audit_log a
                WHERE a.target_id = CAST(:tid AS uuid)
                  AND a.event_type LIKE 'task.%'
                  AND a.event_type = 'task.' || (a.details->>'to_status')
            )
            SELECT status, entered_at, exited_at FROM ordered ORDER BY entered_at
            """
        )
        rows = (await self.session.execute(sql, {"tid": str(task_id)})).all()
        return [(r.status, r.entered_at, r.exited_at or close_at) for r in rows]

    async def _task_fail_counts(self, task_id: UUID) -> tuple[int, int]:
        """(qa_fails, pr_fails) attributed to this task from named audit events."""
        rows = (
            await self.session.execute(
                select(AuditLogTable.event_type, func.count())
                .where(
                    AuditLogTable.target_id == task_id,
                    AuditLogTable.event_type.in_(["task.qa_fail", "task.pr_fail"]),
                )
                .group_by(AuditLogTable.event_type)
            )
        ).all()
        counts: dict[str, int] = {row[0]: row[1] for row in rows}
        return counts.get("task.qa_fail", 0), counts.get("task.pr_fail", 0)

    async def get_task_metrics(self, task_id: UUID) -> TaskMetrics | None:
        """Live granular metrics for one task, or None if the task doesn't exist.

        Composes summed effort + turns/tool_calls/tokens/cost (spawn sessions),
        the per-stage active-vs-wait decomposition (audit windows x stints), and
        who-caused-rework (revision_count + named qa/pr fail events).
        """
        task_row = (
            await self.session.execute(
                select(
                    TaskTable.started_at,
                    TaskTable.completed_at,
                    TaskTable.revision_count,
                ).where(TaskTable.id == task_id)
            )
        ).one_or_none()
        if task_row is None:
            return None
        started_at, completed_at, revision_count = task_row
        # Close open stints / the open final stage window at completed_at for a
        # terminal task (so stages don't grow past completion), else at now.
        wall_end = completed_at or datetime.now(UTC)
        wall_clock = (wall_end - started_at).total_seconds() if started_at else 0.0

        spawn = await self._spawn_rollup_for_task(task_id, wall_end)
        windows = await self._stage_windows_for_task(task_id, wall_end)
        qa_fails, pr_fails = await self._task_fail_counts(task_id)
        stages = compute_stage_effort(windows, spawn["stints"])

        return TaskMetrics(
            task_id=str(task_id),
            active_runtime_seconds=spawn["active_runtime_seconds"],
            wall_clock_seconds=round(max(0.0, wall_clock)),
            turns=spawn["turns"],
            tool_calls=spawn["tool_calls"],
            tokens=spawn["tokens"],
            cost_usd=spawn["cost_usd"],
            revision_count=revision_count or 0,
            qa_fails=qa_fails,
            pr_fails=pr_fails,
            stints=len(spawn["stints"]),
            stages=stages,
        )

    async def _ceo_latency(
        self, since: datetime, from_status: str, to_statuses: Sequence[str]
    ) -> tuple[float, float, int]:
        """(p50, p90, count) seconds from a ``from_status`` entry to the next
        CEO-attributed decision (``to_statuses``) on the same task.

        Reads only ``audit_log`` (agent_role='ceo' serializes from the CEO
        StrEnum). The decision is the earliest ceo row after the from-event.
        """
        sql = text(
            """
            WITH events AS (
                SELECT target_id, timestamp,
                       (details->>'to_status') AS to_status,
                       (details->>'agent_role') AS role
                FROM audit_log
                WHERE event_type LIKE 'task.%' AND timestamp >= :since
            ),
            paired AS (
                SELECT (
                    SELECT MIN(d.timestamp) FROM events d
                    WHERE d.target_id = e.target_id
                      AND d.timestamp > e.timestamp
                      AND d.role = 'ceo'
                      AND d.to_status IN :to_statuses
                ) - e.timestamp AS latency
                FROM events e
                WHERE e.to_status = :from_status
            )
            SELECT
                PERCENTILE_CONT(0.5) WITHIN GROUP (
                    ORDER BY EXTRACT(epoch FROM latency))::float AS p50,
                PERCENTILE_CONT(0.9) WITHIN GROUP (
                    ORDER BY EXTRACT(epoch FROM latency))::float AS p90,
                COUNT(latency) AS n
            FROM paired WHERE latency IS NOT NULL
            """
        ).bindparams(bindparam("to_statuses", expanding=True))
        row = (
            await self.session.execute(
                sql,
                {
                    "since": since,
                    "from_status": from_status,
                    "to_statuses": list(to_statuses),
                },
            )
        ).one()
        return float(row.p50 or 0.0), float(row.p90 or 0.0), int(row.n or 0)

    async def _ceo_godmode_count(self, since: datetime) -> int:
        """Count every CEO-attributed task transition in the window."""
        sql = text(
            """
            SELECT COUNT(*) AS n FROM audit_log
            WHERE event_type LIKE 'task.%'
              AND timestamp >= :since
              AND (details->>'agent_role') = 'ceo'
            """
        )
        return int((await self.session.execute(sql, {"since": since})).scalar() or 0)

    async def get_ceo_scorecard(self, days: int = 30) -> CeoScorecard:
        """The human CEO's scorecard — approval/unblock dwell + god-mode count."""
        since = datetime.now(UTC) - timedelta(days=days)
        approval_p50, approval_p90, approval_n = await self._ceo_latency(
            since, "awaiting_ceo_approval", CEO_APPROVAL_DECISIONS
        )
        unblock_p50, _unblock_p90, unblock_n = await self._ceo_latency(
            since, "blocked", CEO_UNBLOCK_DECISIONS
        )
        godmode = await self._ceo_godmode_count(since)
        return CeoScorecard(
            approval_p50_seconds=approval_p50,
            approval_p90_seconds=approval_p90,
            approval_count=approval_n,
            unblock_p50_seconds=unblock_p50,
            unblock_count=unblock_n,
            godmode_actions=godmode,
        )

    _ROLLUP_SUM_COLUMNS: ClassVar[tuple[str, ...]] = (
        "tasks_completed",
        "tasks_first_pass",
        "revisions_caused",
        "revisions_received",
        "active_runtime_seconds",
        "turns",
        "tool_calls",
        "tokens",
        "cost_usd",
        "qa_reviews_total",
        "qa_reviews_passed",
        "escalations",
        "blocked_others",
        "idle_seconds",
    )

    async def _rollup_sums(
        self, since_date: Any, *, agent_slug: str | None, team: Team | None
    ) -> tuple[dict[str, float], int]:
        """SUM the rollup columns over member_performance_daily agent rows.

        Filters to ``member_kind='agent'`` in the window, optionally scoped to
        one member (``agent_slug``) or one cell (``team``). Returns
        ``(sums, member_count)`` where member_count is the distinct slugs.
        """
        cols = [
            func.coalesce(
                func.sum(getattr(MemberPerformanceDailyTable, name)), 0
            ).label(name)
            for name in self._ROLLUP_SUM_COLUMNS
        ]
        conds: list[Any] = [
            MemberPerformanceDailyTable.member_kind == "agent",
            MemberPerformanceDailyTable.date >= since_date,
        ]
        if agent_slug is not None:
            conds.append(MemberPerformanceDailyTable.agent_slug == agent_slug)
        if team is not None:
            conds.append(MemberPerformanceDailyTable.team == team.value)
        row = (
            await self.session.execute(
                select(
                    *cols,
                    func.count(func.distinct(MemberPerformanceDailyTable.agent_slug)),
                ).where(and_(*conds))
            )
        ).one()
        sums = {
            name: float(getattr(row, name) or 0) for name in self._ROLLUP_SUM_COLUMNS
        }
        member_count = int(row[-1] or 0)
        return sums, member_count

    async def _live_inflight_overlay(self, agent_id: UUID) -> dict[str, float]:
        """Effort of the member's currently-OPEN (running) spawn sessions on
        non-terminal tasks — the live delta the terminal-day rollup cannot hold
        yet.

        Disjoint from the rollup by ``ended_at``: ``_msweep_spawn`` rolls up only
        CLOSED sessions (``ended_at IS NOT NULL``), so this counts only OPEN ones
        (``ended_at IS NULL``). A just-closed session lands in the rollup on the
        next ~60s sweep, so there is no double-count. (Summing *all* of a task's
        sessions here — as an earlier version did via ``get_task_metrics`` —
        re-counted the closed sessions the rollup already holds, inflating the
        member's effort on the common reap/respawn path.)
        """
        overlay = {
            "active_runtime_seconds": 0.0,
            "turns": 0.0,
            "tool_calls": 0.0,
            "tokens": 0.0,
            "cost_usd": 0.0,
        }
        ids = (
            (
                await self.session.execute(
                    select(TaskTable.id).where(
                        TaskTable.assigned_to == agent_id,
                        TaskTable.status.notin_(
                            [TaskStatus.COMPLETED, TaskStatus.CANCELLED]
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not ids:
            return overlay
        # Aggregate in SQL (mirrors _msweep_spawn); active_runtime for an open
        # session runs to now(). One row back — no per-row Python branching.
        s = AgentSpawnSessionTable
        row = (
            await self.session.execute(
                select(
                    func.coalesce(
                        func.sum(func.extract("epoch", func.now() - s.started_at)),
                        0,
                    ),
                    func.coalesce(func.sum(s.turns), 0),
                    func.coalesce(func.sum(s.tool_calls), 0),
                    func.coalesce(
                        func.sum(
                            s.tokens_input
                            + s.tokens_output
                            + s.tokens_cache_read
                            + s.tokens_cache_write
                        ),
                        0,
                    ),
                    func.coalesce(func.sum(s.estimated_cost_usd), 0),
                ).where(
                    s.task_id.in_([str(i) for i in ids]),
                    s.ended_at.is_(None),
                )
            )
        ).one()
        overlay["active_runtime_seconds"] = float(row[0] or 0)
        overlay["turns"] = float(row[1] or 0)
        overlay["tool_calls"] = float(row[2] or 0)
        overlay["tokens"] = float(row[3] or 0)
        overlay["cost_usd"] = float(row[4] or 0)
        return overlay

    @staticmethod
    def _ratio(numerator: float, denominator: float) -> float | None:
        """Guarded ratio → None when the denominator is 0."""
        return round(numerator / denominator, 4) if denominator else None

    async def get_member_scorecard(
        self, agent_id: UUID, days: int = 30
    ) -> MemberScorecard | None:
        """Per-member rollup scorecard + live in-flight overlay, or None if no
        such agent."""
        agent = (
            await self.session.execute(
                select(AgentTable.slug, AgentTable.name).where(
                    AgentTable.id == agent_id
                )
            )
        ).one_or_none()
        if agent is None:
            return None
        slug, name = agent
        since_date = (datetime.now(UTC) - timedelta(days=days)).date()
        sums, _ = await self._rollup_sums(since_date, agent_slug=slug, team=None)

        overlay = await self._live_inflight_overlay(agent_id)
        includes_live = any(v for v in overlay.values())
        active_runtime = (
            sums["active_runtime_seconds"] + overlay["active_runtime_seconds"]
        )
        turns = int(sums["turns"] + overlay["turns"])
        tool_calls = int(sums["tool_calls"] + overlay["tool_calls"])
        tokens = int(sums["tokens"] + overlay["tokens"])
        cost = sums["cost_usd"] + overlay["cost_usd"]
        completed = sums["tasks_completed"]

        return MemberScorecard(
            scope="member",
            id=str(agent_id),
            name=name,
            tasks_completed=int(completed),
            first_pass_yield=self._ratio(sums["tasks_first_pass"], completed),
            effort_throughput_per_hour=self._ratio(completed, active_runtime / 3600),
            active_runtime_hours=active_runtime / 3600,
            turns=turns,
            tool_calls=tool_calls,
            tokens=tokens,
            cost_usd=cost,
            turns_per_task=self._ratio(turns, completed),
            tool_calls_per_task=self._ratio(tool_calls, completed),
            revisions_caused=int(sums["revisions_caused"]),
            revisions_received=int(sums["revisions_received"]),
            qa_pass_rate=self._ratio(
                sums["qa_reviews_passed"], sums["qa_reviews_total"]
            ),
            escalations=int(sums["escalations"]),
            blocked_others=int(sums["blocked_others"]),
            idle_hours=sums["idle_seconds"] / 3600,
            utilization=self._ratio(
                sums["active_runtime_seconds"],
                sums["active_runtime_seconds"] + sums["idle_seconds"],
            ),
            includes_live_inflight=includes_live,
        )

    async def get_org_scorecard(
        self, team: Team | None = None, days: int = 30
    ) -> OrgScorecard:
        """Team (or whole-org when team is None) rollup aggregate."""
        since_date = (datetime.now(UTC) - timedelta(days=days)).date()
        sums, member_count = await self._rollup_sums(
            since_date, agent_slug=None, team=team
        )
        completed = sums["tasks_completed"]
        active_runtime = sums["active_runtime_seconds"]
        return OrgScorecard(
            scope="team" if team else "org",
            team=team.value if team else None,
            member_count=member_count,
            tasks_completed=int(completed),
            first_pass_yield=self._ratio(sums["tasks_first_pass"], completed),
            effort_throughput_per_hour=self._ratio(completed, active_runtime / 3600),
            active_runtime_hours=active_runtime / 3600,
            turns=int(sums["turns"]),
            tool_calls=int(sums["tool_calls"]),
            tokens=int(sums["tokens"]),
            cost_usd=sums["cost_usd"],
            revisions_caused=int(sums["revisions_caused"]),
            revisions_received=int(sums["revisions_received"]),
        )

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
