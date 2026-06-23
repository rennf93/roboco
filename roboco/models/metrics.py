"""
Metrics Models

Data classes for metrics and analytics.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from roboco.models.base import Team


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
