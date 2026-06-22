"""Request / response schemas for the live Secretary chat bridge.

Moved out of the route module so the HTTP layer stays handler-only and these
models live with the other API schemas (architectural-conventions placement).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class StartSecretaryRequest(BaseModel):
    """Open a live Secretary chat (optionally with an opening message)."""

    initial_message: str | None = Field(default=None, min_length=1)


class StartSecretaryResponse(BaseModel):
    session_id: str


class LiveMessageRequest(BaseModel):
    text: str = Field(..., min_length=1)


class AgentEvent(BaseModel):
    """One event relayed from the container onto the session stream."""

    kind: str
    text: str = ""
    tool: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
