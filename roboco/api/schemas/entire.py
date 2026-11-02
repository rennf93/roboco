"""Schemas for the Entire trails bridge's CEO surface."""

from pydantic import BaseModel, Field


class EntireTokenStatus(BaseModel):
    """Whether the Entire token is stored. Never the token itself."""

    has_token: bool


class EntireTokenSetRequest(BaseModel):
    """Set the Entire token, or clear it with an empty string."""

    token: str = Field(default="", max_length=8192)


class EntireTrailsStatusResponse(BaseModel):
    """Whether the trails bridge can run, and what its outbox holds."""

    enabled: bool
    has_token: bool
    cli_available: bool
    open_trails: int
    pending: int
    dead: int
    last_error: str | None = None


class EntireTrailsRetryResponse(BaseModel):
    """How many stopped operations were put back in their queues."""

    retried: int
