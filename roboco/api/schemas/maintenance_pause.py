"""Schemas for the operator maintenance pause's CEO surface."""

from __future__ import annotations

from pydantic import BaseModel, Field

from roboco.foundation.policy.maintenance_pause import (
    DEFAULT_PAUSE_HOURS,
    MAX_PAUSE_HOURS,
)


class MaintenancePauseResponse(BaseModel):
    """One scope's live pause status, the shape the panel's maintenance-pause
    card reads for all three scopes."""

    scope: str
    paused: bool
    paused_by: str | None = None
    paused_at: str | None = None
    reason: str | None = None
    expires_at: str | None = None
    # Set when this scope's runtime gate (``is_paused``, consulted by the
    # orchestrator/engines) is CURRENTLY reading fail-closed on a lookup
    # error -- distinct from ``paused``, which reflects the persisted state
    # this same request just read successfully. A scope can show
    # ``paused=False`` and a non-null ``read_degraded_since`` at once: the
    # stored setting genuinely says not-paused, but the runtime gate is
    # treating it as paused until its own next lookup succeeds.
    read_degraded_since: str | None = None


class MaintenancePauseRequest(BaseModel):
    """Body for ``POST /maintenance-pause/{scope}``."""

    reason: str | None = Field(default=None, max_length=500)
    hours: float = Field(default=DEFAULT_PAUSE_HOURS, gt=0, le=MAX_PAUSE_HOURS)
