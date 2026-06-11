"""Redis-backed rate-limit state tracker for the agent gateway.

State is persisted in Redis as a JSON blob keyed by provider name.
Because it is backed by Redis rather than process memory, state survives
a process restart and a *new* ``RateLimitStateTracker`` instance pointing
at the same Redis URL will read the same values — satisfying the
cross-reconnection persistence requirement.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis

from roboco.config import settings


class RateLimitStateTracker:
    """Track rate-limit state for a single AI provider in Redis.

    Usage
    -----
    tracker = RateLimitStateTracker("anthropic")
    await tracker.activate(retry_after=60.0, affected_agents=["be-dev-1"])
    assert await tracker.is_rate_limited()

    A second instance that uses the same Redis URL and provider name
    will observe the same state — no in-process singleton required.
    """

    _KEY_PREFIX: str = "roboco:rate_limit:"

    def __init__(self, provider: str, redis_url: str | None = None) -> None:
        """Construct a tracker for *provider*.

        Args:
            provider:  Logical provider name, e.g. ``"anthropic"`` or
                       ``"ollama_cloud"``.  Used as part of the Redis key.
            redis_url: Override the Redis URL (defaults to
                       ``settings.redis_url``).
        """
        self._provider = provider
        self._redis_url = redis_url or settings.redis_url
        self._redis: redis.Redis | None = None  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _conn(self) -> redis.Redis:  # type: ignore[type-arg]
        """Return a (lazy-connected) redis.asyncio.Redis client."""
        if self._redis is None:
            self._redis = redis.from_url(self._redis_url)
        return self._redis

    def _key(self) -> str:
        """Redis key for this provider's state blob."""
        return f"{self._KEY_PREFIX}{self._provider}:state"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def activate(
        self,
        retry_after: float | None = None,
        affected_agents: list[str] | None = None,
    ) -> None:
        """Mark the provider as rate-limited.

        Args:
            retry_after:      Seconds until the provider should accept new
                              requests, or ``None`` if unknown.
            affected_agents:  Agent slugs that were active when the limit
                              was hit (informational; stored in state).
        """
        r = await self._conn()
        state: dict[str, Any] = {
            "rate_limited": True,
            "activated_at": datetime.now(UTC).isoformat(),
            "retry_after": retry_after,
            "affected_agents": affected_agents or [],
            "probe_failures": 0,
        }
        await r.set(self._key(), json.dumps(state))

    async def clear(self) -> None:
        """Remove rate-limit state for this provider."""
        r = await self._conn()
        await r.delete(self._key())

    async def is_rate_limited(self) -> bool:
        """Return ``True`` if the provider is currently rate-limited."""
        state = await self.get_state()
        return bool(state.get("rate_limited", False))

    async def get_state(self) -> dict[str, Any]:
        """Return the stored state dict, or ``{}`` if none exists."""
        r = await self._conn()
        raw = await r.get(self._key())
        if raw is None:
            return {}
        decoded: str = raw.decode() if isinstance(raw, bytes) else str(raw)
        result: dict[str, Any] = json.loads(decoded)
        return result

    async def increment_probe_failures(self) -> int:
        """Increment the probe-failure counter and return the new value.

        The probe-failure counter tracks how many successive connectivity
        probes have failed since the rate limit was activated.  The
        orchestrator uses this to decide whether to keep waiting or give
        up entirely.
        """
        r = await self._conn()
        state = await self.get_state()
        new_count: int = state.get("probe_failures", 0) + 1
        state["probe_failures"] = new_count
        await r.set(self._key(), json.dumps(state))
        return new_count

    async def reset_probe_failures(self) -> None:
        """Reset the probe-failure counter to 0."""
        r = await self._conn()
        state = await self.get_state()
        state["probe_failures"] = 0
        await r.set(self._key(), json.dumps(state))
