"""System monitoring endpoints.

Provides read-only introspection into orchestrator-level state that is
useful for operators and the control panel but doesn't fit cleanly into
the per-resource routers (agents, tasks, etc.).

Currently exposed:

    GET /api/system/rate-limits
        Returns the current per-provider rate-limit state from Redis,
        shaped for the control panel's rate-limit store.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from roboco.api.deps import require_panel_token
from roboco.api.schemas.system import RateLimitEntry, RateLimitListResponse
from roboco.api.utils.system import _resume_at
from roboco.services.gateway.rate_limit_tracker import RateLimitStateTracker

router = APIRouter(dependencies=[Depends(require_panel_token)])


@router.get(
    "/rate-limits",
    summary="List per-provider rate-limit state",
    response_model=RateLimitListResponse,
    tags=["System"],
)
async def get_rate_limits() -> RateLimitListResponse:
    """Return rate-limit state for every currently rate-limited provider.

    Backed by
    :class:`~roboco.services.gateway.rate_limit_tracker.RateLimitStateTracker`.
    Shaped as the panel's rate-limit store consumes it — a
    ``{ "entries": [...] }`` envelope where each entry is
    ``{provider, affectedAgents, hitAt, resumeAt, retryAfterSeconds}``.

    ``entries`` is empty when no provider is currently rate-limited.
    """
    states = await RateLimitStateTracker.list_rate_limited_providers()
    entries = [
        RateLimitEntry(
            provider=provider,
            affected_agents=state.get("affected_agents", []),
            hit_at=state.get("activated_at"),
            resume_at=_resume_at(state.get("activated_at"), state.get("retry_after")),
            retry_after_seconds=state.get("retry_after"),
        )
        for provider, state in states
    ]
    return RateLimitListResponse(entries=entries)
