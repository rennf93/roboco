"""System monitoring endpoints.

Provides read-only introspection into orchestrator-level state that is
useful for operators and the control panel but doesn't fit cleanly into
the per-resource routers (agents, tasks, etc.).

Currently exposed:

    GET /api/system/rate-limits
        Returns the current per-provider rate-limit state from Redis.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from roboco.services.gateway.rate_limit_tracker import RateLimitStateTracker

router = APIRouter()


@router.get(
    "/rate-limits",
    summary="List per-provider rate-limit state",
    response_model=list[dict[str, Any]],
    tags=["System"],
)
async def get_rate_limits() -> list[dict[str, Any]]:
    """Return rate-limit state for every currently rate-limited provider.

    Backed by
    :class:`~roboco.services.gateway.rate_limit_tracker.RateLimitStateTracker`.
    Each entry is the raw state dict (``rate_limited``, ``activated_at``,
    ``retry_after``, ``affected_agents``, ``probe_failures``) augmented
    with a ``provider`` key.

    Returns an empty list ``[]`` when no provider is currently rate-limited.
    """
    entries = await RateLimitStateTracker.list_rate_limited_providers()
    result: list[dict[str, Any]] = []
    for provider, state in entries:
        item = dict(state)
        item["provider"] = provider
        result.append(item)
    return result
