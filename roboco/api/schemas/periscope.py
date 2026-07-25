"""Schemas for the Periscope (Board Program) engine's CEO-facing read surface.
Mirrors ``roboco.api.schemas.pest_control`` — a report has no per-item
approve/reject, so this is list-only."""

from __future__ import annotations

from pydantic import BaseModel


class MarketBriefFindingResponse(BaseModel):
    """One cited finding within a Periscope market brief."""

    id: str
    claim: str
    source_url: str
    relevance: str


class MarketBriefResponse(BaseModel):
    """One completed Periscope exploration's filed brief."""

    task_id: str
    title: str
    completed_at: str | None
    headline: str
    findings: list[MarketBriefFindingResponse]
    threats: list[str]
    opportunities: list[str]
    positioning_note: str
