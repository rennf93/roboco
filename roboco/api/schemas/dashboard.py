"""
Dashboard API Schemas

Request/response models for auditor and CEO dashboards.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class FlagSeverity(StrEnum):
    """Severity levels for auditor flags."""

    INFO = "info"
    WARNING = "warning"
    URGENT = "urgent"


class AuditorFlag(BaseModel):
    """A flag raised by the auditor."""

    id: UUID
    severity: FlagSeverity
    category: str  # quality, process, communication, blocked, documentation
    title: str
    description: str
    related_task_id: UUID | None = None
    related_agent_id: UUID | None = None
    created_at: datetime
    resolved_at: datetime | None = None
    notes: str | None = None


class AuditorReport(BaseModel):
    """An auditor report for the CEO."""

    id: UUID
    report_type: str  # daily, weekly, alert
    title: str
    summary: str
    sections: list[dict[str, Any]]
    created_at: datetime
    sent_at: datetime | None = None


class AuditorDashboard(BaseModel):
    """Complete auditor dashboard data."""

    flagged_items: list[AuditorFlag]
    metrics: dict[str, Any]
    audit_queue: list[dict[str, Any]]
    recent_reports: list[AuditorReport]
    findings: list[dict[str, Any]] = Field(default_factory=list)


class TeamHealth(BaseModel):
    """Health status for a team."""

    team: str
    status: str  # ok, slow, critical
    active_tasks: int
    blocked_tasks: int
    blocked_ratio: float
    completed_this_week: int


class UsageSummary(BaseModel):
    """Today's token usage summary for the CEO dashboard."""

    tokens_today: int = Field(
        default=0, description="Total tokens (input + output + cache) used today"
    )
    cost_today_usd: float = Field(
        default=0.0, description="Estimated USD cost for today"
    )


class CEOOverview(BaseModel):
    """Complete CEO overview data."""

    health_status: list[TeamHealth]
    key_metrics: dict[str, Any]
    auditor_alerts: dict[str, Any]
    roadmap_progress: dict[str, Any]
    usage_summary: UsageSummary | None = Field(
        default=None,
        description="Today's token usage and cost from daily_usage_rollups",
    )


class CreateFlagRequest(BaseModel):
    """Request to create an auditor flag."""

    severity: FlagSeverity
    category: str
    title: str
    description: str
    related_task_id: UUID | None = None
    related_agent_id: UUID | None = None


class CreateReportRequest(BaseModel):
    """Request to create an auditor report."""

    report_type: str
    title: str
    summary: str
    sections: list[dict[str, Any]] = Field(default_factory=list)
