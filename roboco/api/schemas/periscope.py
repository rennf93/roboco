"""Schemas for the Periscope (Board Program) engine's CEO-facing surface.

The report (headline/findings/threats/opportunities/positioning_note) is
read-only — a brief has no per-item approve/reject at the TASK level (the
exploration task completes atomically at propose time). Each FINDING still
carries its own proposed/approved/rejected status the CEO decides on
afterward — ``MarketBriefFindingResponse`` and the action schemas below back
that per-finding queue, mirrored on ``roboco.api.schemas.roadmap``."""

from __future__ import annotations

from pydantic import BaseModel, Field


class MarketBriefFindingResponse(BaseModel):
    """One cited finding within a Periscope market brief."""

    id: str
    claim: str
    source_url: str
    relevance: str
    # Defaults cover a finding authored before this feature shipped, whose
    # stored marker carries none of these three keys.
    status: str = "proposed"
    reject_reason: str | None = None
    materialized_task_id: str | None = None


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


class MarketBriefFindingRejectRequest(BaseModel):
    """The CEO's reason for dismissing one market-brief finding."""

    reason: str = Field(..., min_length=4)


class MarketBriefFindingActionResponse(BaseModel):
    """The outcome of an approve/reject call on one market-brief finding."""

    status: str
    finding_id: str
    materialized_task_id: str | None = None
    detail: str
