"""Schemas for the system monitoring endpoints.

Serialized with camelCase aliases so the control panel consumes the fields
directly. Moved out of the route module so the HTTP layer stays handler-only
(architectural-conventions placement).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class _CamelModel(BaseModel):
    """Serialize with camelCase aliases so the panel consumes fields directly."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class RateLimitEntry(_CamelModel):
    """A single provider's active rate-limit state, in the panel's shape."""

    provider: str
    affected_agents: list[str]
    hit_at: str | None
    resume_at: str | None
    retry_after_seconds: float | None


class RateLimitListResponse(_CamelModel):
    """The envelope the panel's rate-limit store expects: ``{ "entries": [...] }``."""

    entries: list[RateLimitEntry]
