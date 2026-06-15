"""Per-agent daily quota for the web-research capability.

A simple Redis counter keyed by ``agent_id`` + UTC day, with a 24h expiry on
first use. Cost control, not security — so it **fails open**: if Redis is
unreachable the call is allowed (research must not break because the cache is
down). Mirrors the Redis access pattern in ``rate_limit_tracker``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import redis.asyncio as redis

from roboco.config import settings

logger = logging.getLogger(__name__)

_DAY_SECONDS = 86400


@dataclass(frozen=True)
class QuotaStatus:
    """Outcome of a quota check."""

    allowed: bool
    used: int
    limit: int
    day: str


class ResearchQuotaTracker:
    """Track per-agent/day research call counts in Redis (fail-open)."""

    _KEY_PREFIX: str = "roboco:research_quota:"

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url or settings.redis_url
        self._redis: redis.Redis | None = None

    async def _conn(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.from_url(self._redis_url)
        return self._redis

    def _key(self, agent_id: str, day: str) -> str:
        return f"{self._KEY_PREFIX}{agent_id}:{day}"

    async def check_and_consume(
        self, agent_id: str, limit: int, *, now: datetime | None = None
    ) -> QuotaStatus:
        """Atomically increment today's counter and report whether it's allowed.

        The increment happens before the limit comparison (a single atomic
        ``INCR``), so an over-limit call still bumps the counter — fine for a
        ceiling. On the first call of the day a 24h expiry is set so counters
        self-clean. Any Redis error fails open (``allowed=True``).
        """
        day = (now or datetime.now(UTC)).strftime("%Y-%m-%d")
        key = self._key(agent_id, day)
        try:
            conn = await self._conn()
            used = int(await conn.incr(key))
            if used == 1:
                await conn.expire(key, _DAY_SECONDS)
        except Exception as exc:
            # Fail-open: quota is cost control, not security — a Redis outage
            # must not break research for Board/PM agents.
            logger.warning("research quota check failed open (redis): %s", exc)
            return QuotaStatus(allowed=True, used=0, limit=limit, day=day)
        return QuotaStatus(allowed=used <= limit, used=used, limit=limit, day=day)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
