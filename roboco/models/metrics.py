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
