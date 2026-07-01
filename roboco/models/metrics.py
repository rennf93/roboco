"""
Metrics Models

Data classes for metrics and analytics.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from roboco.foundation.policy.stage_effort import StageEffort
from roboco.models.base import Team

# CEO decision transitions used by the CEO scorecard (audit-log to_status
# values). Approval = any CEO exit from awaiting_ceo_approval (incl. the
# coordination-root reject that lands in `pending`); unblock = a CEO revive
# out of `blocked`.
CEO_APPROVAL_DECISIONS = ("completed", "needs_revision", "cancelled", "pending")
CEO_UNBLOCK_DECISIONS = ("in_progress", "pending")


class VelocityMetrics:
    """Velocity metrics over a time period."""

    def __init__(
        self,
        period: str,
        tasks_completed: int,
        tasks_created: int,
        avg_completion_hours: float | None,
        completion_rate: float,
    ):
        self.period = period
        self.tasks_completed = tasks_completed
        self.tasks_created = tasks_created
        self.avg_completion_hours = avg_completion_hours
        self.completion_rate = completion_rate

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "tasks_completed": self.tasks_completed,
            "tasks_created": self.tasks_created,
            "avg_completion_hours": self.avg_completion_hours,
            "completion_rate": self.completion_rate,
        }


class BlockerMetrics:
    """Blocker metrics."""

    def __init__(
        self,
        active_blockers: int,
        avg_blocked_hours: float | None,
        longest_blocked_task_id: UUID | None,
        longest_blocked_hours: float | None,
        blockers_by_team: dict[str, int],
    ):
        self.active_blockers = active_blockers
        self.avg_blocked_hours = avg_blocked_hours
        self.longest_blocked_task_id = longest_blocked_task_id
        self.longest_blocked_hours = longest_blocked_hours
        self.blockers_by_team = blockers_by_team

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_blockers": self.active_blockers,
            "avg_blocked_hours": self.avg_blocked_hours,
            "longest_blocked_task_id": str(self.longest_blocked_task_id)
            if self.longest_blocked_task_id
            else None,
            "longest_blocked_hours": self.longest_blocked_hours,
            "blockers_by_team": self.blockers_by_team,
        }


@dataclass
class TeamMetrics:
    """Metrics for a specific team."""

    team: Team
    active_tasks: int
    completed_tasks_week: int
    blocked_tasks: int
    avg_completion_hours: float | None
    documentation_coverage: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "team": self.team.value,
            "active_tasks": self.active_tasks,
            "completed_tasks_week": self.completed_tasks_week,
            "blocked_tasks": self.blocked_tasks,
            "avg_completion_hours": self.avg_completion_hours,
            "documentation_coverage": self.documentation_coverage,
        }


@dataclass
class AgentMetrics:
    """Metrics for a specific agent."""

    agent_id: UUID
    agent_name: str
    tasks_completed_week: int
    current_task_id: UUID | None
    avg_completion_hours: float | None
    messages_sent_week: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": str(self.agent_id),
            "agent_name": self.agent_name,
            "tasks_completed_week": self.tasks_completed_week,
            "current_task_id": str(self.current_task_id)
            if self.current_task_id
            else None,
            "avg_completion_hours": self.avg_completion_hours,
            "messages_sent_week": self.messages_sent_week,
        }


# =============================================================================
# OBSERVABILITY (0.10.0): cycle-time, bottlenecks, rework, scorecard
# =============================================================================


@dataclass
class StageTiming:
    """Time tasks spend in one lifecycle status, reconstructed from audit_log."""

    status: str
    avg_seconds: float
    median_seconds: float
    p90_seconds: float
    sample_size: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "avg_seconds": round(self.avg_seconds, 1),
            "median_seconds": round(self.median_seconds, 1),
            "p90_seconds": round(self.p90_seconds, 1),
            "sample_size": self.sample_size,
        }


@dataclass
class StageBottleneck:
    """Cumulative dwell + current parked count for one lifecycle status."""

    status: str
    cumulative_seconds: float
    parked_now: int
    pct_of_total: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "cumulative_seconds": round(self.cumulative_seconds, 1),
            "parked_now": self.parked_now,
            "pct_of_total": round(self.pct_of_total, 4),
        }


@dataclass
class BottleneckReport:
    """Where the work piles up: cumulative dwell per stage + live parked counts."""

    by_stage: list[StageBottleneck]
    worst_stage: str | None
    active_blockers: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "by_stage": [s.to_dict() for s in self.by_stage],
            "worst_stage": self.worst_stage,
            "active_blockers": self.active_blockers,
        }


@dataclass
class AgentReworkRate:
    """Per-agent rework: bounce rate + fails attributed to this agent's reviews."""

    agent_slug: str
    rate: float
    qa_fails: int
    pr_fails: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_slug": self.agent_slug,
            "rate": round(self.rate, 4),
            "qa_fails": self.qa_fails,
            "pr_fails": self.pr_fails,
        }


@dataclass
class TeamReworkRate:
    """Per-cell rework rate."""

    team: str
    rate: float

    def to_dict(self) -> dict[str, Any]:
        return {"team": self.team, "rate": round(self.rate, 4)}


@dataclass
class ReworkReport:
    """How often work bounces to needs_revision, by team and by agent."""

    rate: float
    total_completed: int
    total_reworked: int
    by_team: list[TeamReworkRate]
    by_agent: list[AgentReworkRate]
    rework_cost_usd: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "rate": round(self.rate, 4),
            "total_completed": self.total_completed,
            "total_reworked": self.total_reworked,
            "by_team": [t.to_dict() for t in self.by_team],
            "by_agent": [a.to_dict() for a in self.by_agent],
            "rework_cost_usd": round(self.rework_cost_usd, 4),
        }


@dataclass
class Scorecard:
    """Fused per-agent or per-cell delivery scorecard."""

    scope: str  # "agent" | "cell"
    id: str
    name: str
    tasks_completed: int
    avg_cycle_hours: float | None
    rework_rate: float
    tokens: int
    cost_usd: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "id": self.id,
            "name": self.name,
            "tasks_completed": self.tasks_completed,
            "avg_cycle_hours": self.avg_cycle_hours,
            "rework_rate": round(self.rework_rate, 4),
            "tokens": self.tokens,
            "cost_usd": round(self.cost_usd, 4),
        }


@dataclass
class MemberScorecard:
    """Per-member rollup scorecard (agent) with derived rates.

    Served from member_performance_daily (terminal work) optionally enriched
    with a live in-flight overlay (the member's non-terminal tasks' effort so
    far). Completion counts stay rollup-only — the two sets are disjoint by
    status — so ``includes_live_inflight`` only enriches effort/turns/cost.
    """

    scope: str  # "member"
    id: str
    name: str
    tasks_completed: int
    first_pass_yield: float | None
    effort_throughput_per_hour: float | None
    active_runtime_hours: float
    turns: int
    tool_calls: int
    tokens: int
    cost_usd: float
    turns_per_task: float | None
    tool_calls_per_task: float | None
    revisions_caused: int
    revisions_received: int
    qa_pass_rate: float | None
    escalations: int
    blocked_others: int
    idle_hours: float
    utilization: float | None
    includes_live_inflight: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "id": self.id,
            "name": self.name,
            "member_kind": "agent",
            "tasks_completed": self.tasks_completed,
            "first_pass_yield": self.first_pass_yield,
            "effort_throughput_per_hour": self.effort_throughput_per_hour,
            "active_runtime_hours": round(self.active_runtime_hours, 2),
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "tokens": self.tokens,
            "cost_usd": round(self.cost_usd, 4),
            "turns_per_task": self.turns_per_task,
            "tool_calls_per_task": self.tool_calls_per_task,
            "revisions_caused": self.revisions_caused,
            "revisions_received": self.revisions_received,
            "qa_pass_rate": self.qa_pass_rate,
            "escalations": self.escalations,
            "blocked_others": self.blocked_others,
            "idle_hours": round(self.idle_hours, 2),
            "utilization": self.utilization,
            "includes_live_inflight": self.includes_live_inflight,
        }


@dataclass
class OrgScorecard:
    """Team or whole-org rollup aggregate (rollup-only, no live overlay)."""

    scope: str  # "org" | "team"
    team: str | None
    member_count: int
    tasks_completed: int
    first_pass_yield: float | None
    effort_throughput_per_hour: float | None
    active_runtime_hours: float
    turns: int
    tool_calls: int
    tokens: int
    cost_usd: float
    revisions_caused: int
    revisions_received: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "team": self.team,
            "member_count": self.member_count,
            "tasks_completed": self.tasks_completed,
            "first_pass_yield": self.first_pass_yield,
            "effort_throughput_per_hour": self.effort_throughput_per_hour,
            "active_runtime_hours": round(self.active_runtime_hours, 2),
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "tokens": self.tokens,
            "cost_usd": round(self.cost_usd, 4),
            "revisions_caused": self.revisions_caused,
            "revisions_received": self.revisions_received,
        }


@dataclass
class CeoScorecard:
    """The human CEO as a measured member — read purely from the audit_log.

    The CEO never runs an LLM, so token/cost/turns are n/a. Its metrics are
    approval dwell (awaiting_ceo_approval -> a CEO decision), unblock dwell
    (blocked -> a CEO revive), and god-mode action count (every ceo-attributed
    transition in the window).
    """

    approval_p50_seconds: float
    approval_p90_seconds: float
    approval_count: int
    unblock_p50_seconds: float
    unblock_count: int
    godmode_actions: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "member_kind": "ceo",
            "approval_p50_seconds": round(self.approval_p50_seconds, 2),
            "approval_p90_seconds": round(self.approval_p90_seconds, 2),
            "approval_count": self.approval_count,
            "unblock_p50_seconds": round(self.unblock_p50_seconds, 2),
            "unblock_count": self.unblock_count,
            "godmode_actions": self.godmode_actions,
        }


@dataclass
class TaskMetrics:
    """Granular per-task effort: real runtime vs wall-clock, turns, cost, rework.

    ``active_runtime_seconds`` is summed spawn-stint effort (can exceed
    wall-clock when stints run concurrently); ``stages`` is the wall-clock
    active-vs-wait decomposition per status window (active clamped to the
    window). Computed live from ``agent_spawn_sessions`` (by task_id) joined
    with ``audit_log`` (by target_id).
    """

    task_id: str
    active_runtime_seconds: int
    wall_clock_seconds: int
    turns: int
    tool_calls: int
    tokens: int
    cost_usd: float
    revision_count: int
    qa_fails: int
    pr_fails: int
    stints: int
    stages: list[StageEffort]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "active_runtime_seconds": self.active_runtime_seconds,
            "wall_clock_seconds": self.wall_clock_seconds,
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "tokens": self.tokens,
            "cost_usd": round(self.cost_usd, 4),
            "revision_count": self.revision_count,
            "qa_fails": self.qa_fails,
            "pr_fails": self.pr_fails,
            "stints": self.stints,
            "stages": [s.to_dict() for s in self.stages],
        }
