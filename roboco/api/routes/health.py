"""
Health Check Routes

Endpoints for monitoring application health and readiness.
"""

import redis.asyncio as redis
from fastapi import APIRouter, status
from pydantic import BaseModel
from sqlalchemy import text

from roboco.config import settings
from roboco.db.base import get_db_context

router = APIRouter()


async def _check_database() -> tuple[str, bool]:
    """Check database connectivity."""
    try:
        async with get_db_context() as session:
            await session.execute(text("SELECT 1"))
        return "ok", True
    except Exception as e:
        return str(e), False


async def _check_redis() -> tuple[str, bool]:
    """Check Redis connectivity."""
    try:
        client = redis.from_url(settings.redis_url)
        await client.ping()
        await client.close()
        return "ok", True
    except Exception as e:
        return str(e), False


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
    """Check if all dependencies are ready."""
    db_status, db_ok = await _check_database()
    redis_status, redis_ok = await _check_redis()

    overall = "ok" if (db_ok and redis_ok) else "degraded"

    return ReadinessResponse(
        status=overall,
        database=db_status,
        redis=redis_status,
    )
