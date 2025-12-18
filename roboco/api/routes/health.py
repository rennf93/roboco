"""
Health Check Routes

Endpoints for monitoring application health and readiness.
"""

from fastapi import APIRouter, status

from roboco.api.schemas.health import HealthResponse, ReadinessResponse
from roboco.config import settings
from roboco.services.health import check_database, check_redis

router = APIRouter()


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
    """Check if all dependencies are ready."""
    db_status, db_ok = await check_database()
    redis_status, redis_ok = await check_redis()

    overall = "ok" if (db_ok and redis_ok) else "degraded"

    return ReadinessResponse(
        status=overall,
        database=db_status,
        redis=redis_status,
    )
