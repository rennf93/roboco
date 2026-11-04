"""
Health Check API Schemas

Request/response models for health check endpoints.
"""

from datetime import datetime

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    environment: str
    failed_index_count: int = 0


class ReviewPathHealthResponse(BaseModel):
    """Review-path (evidence/git-readonly) outage state read by the panel."""

    outage_active: bool
    type: str
    window_start: datetime | None = None
    recovered: bool


class ReadinessResponse(BaseModel):
    """Readiness check response."""

    status: str
    database: str
    redis: str
