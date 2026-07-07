"""
Health Check API Schemas

Request/response models for health check endpoints.
"""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    environment: str
    failed_index_count: int = 0


class ReadinessResponse(BaseModel):
    """Readiness check response."""

    status: str
    database: str
    redis: str
