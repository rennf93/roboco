"""
Health Check Routes

Endpoints for monitoring application health and readiness.
"""

from fastapi import APIRouter, status
from pydantic import BaseModel

from roboco.config import settings

router = APIRouter()


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    """Readiness check response."""

    status: str
    database: str
    redis: str


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Health check",
    description="Basic health check endpoint. Returns OK if the service is running.",
)
async def health_check() -> HealthResponse:
    """Check if the service is healthy."""
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        environment=settings.environment,
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    status_code=status.HTTP_200_OK,
    summary="Readiness check",
    description="Check if the service is ready to handle requests.",
)
async def readiness_check() -> ReadinessResponse:
    """
    Check if all dependencies are ready.

    TODO: Actually check database and Redis connections.
    """
    # TODO: Add actual health checks for database and Redis
    return ReadinessResponse(
        status="ok",
        database="ok",
        redis="ok",
    )
