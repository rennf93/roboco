"""Schemas for the on-demand video-request API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class VideoRequestBody(BaseModel):
    """The CEO's on-demand video brief."""

    occasion: str = Field(..., min_length=1)
    brief: str = Field(..., min_length=1)
    platforms: list[str] = Field(..., min_length=1)


class VideoRequestResponse(BaseModel):
    """The outcome of an on-demand video request."""

    status: str  # "opened" | "disabled" | "not_opened"
    task_id: str | None = None
    detail: str
