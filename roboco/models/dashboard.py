"""
Dashboard Models

Data classes for auditor and CEO dashboards.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass
class FlagData:
    """Internal flag storage."""

    id: UUID
    severity: str
    category: str
    title: str
    description: str
    created_at: datetime
    related_task_id: UUID | None = None
    related_agent_id: UUID | None = None
    resolved_at: datetime | None = None
    notes: str | None = None


@dataclass
class ReportData:
    """Internal report storage."""

    id: UUID
    report_type: str
    title: str
    summary: str
    sections: list[dict[str, Any]]
    created_at: datetime
    sent_at: datetime | None = None


@dataclass
class ChannelFeedData:
    """Channel feed status."""

    id: UUID
    name: str
    status: str
    last_activity: datetime | None
    message_count_24h: int


@dataclass
class TeamHealthData:
    """Team health status."""

    team: str
    status: str
    active_tasks: int
    blocked_tasks: int
    blocked_ratio: float
    completed_this_week: int


@dataclass
class AuditQueueItem:
    """Item in the audit queue."""

    type: str
    title: str
    task_id: str
    team: str


@dataclass
class CreateFlagParams:
    """Parameters for creating an auditor flag."""

    severity: str
    category: str
    title: str
    description: str
    related_task_id: UUID | None = None
    related_agent_id: UUID | None = None


@dataclass
class DashboardStorage:
    """In-memory storage for flags and reports.

    In production, these would be database tables.
    Using a class instead of module globals for testability.
    """

    flags: dict[UUID, FlagData] = field(default_factory=dict)
    reports: dict[UUID, ReportData] = field(default_factory=dict)
