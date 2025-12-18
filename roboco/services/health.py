"""
Health Check Service

Infrastructure health check functions.
"""

import redis.asyncio as redis
from sqlalchemy import text

from roboco.config import settings
from roboco.db.base import get_db_context


async def check_database() -> tuple[str, bool]:
    """Check database connectivity."""
    try:
        async with get_db_context() as session:
            await session.execute(text("SELECT 1"))
        return "ok", True
    except Exception as e:
        return str(e), False


async def check_redis() -> tuple[str, bool]:
    """Check Redis connectivity."""
    try:
        client = redis.from_url(settings.redis_url)
        await client.ping()
        await client.close()
        return "ok", True
    except Exception as e:
        return str(e), False
