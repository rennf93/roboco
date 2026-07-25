"""Schemas for the Pest Control (Board Program) engine's CEO surface.
Mirrors ``roboco.api.schemas.roadmap`` — ``rationale`` becomes the required
``evidence`` field, and a cycle has no top-level theme goal."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PestHuntItemResponse(BaseModel):
    """One evidence-backed bug item draft within a Pest Control hunt."""

    id: str
    title: str
    description: str
    acceptance_criteria: list[str]
    project_slug: str
    team: str
    priority: int
    evidence: str
    status: str
    reject_reason: str | None = None
    materialized_task_id: str | None = None


class PestHuntCycleResponse(BaseModel):
    """A held pest-control exploration cycle: its item drafts."""

    task_id: str
    title: str
    status: str
    items: list[PestHuntItemResponse]


class PestHuntRejectRequest(BaseModel):
    """The CEO's reason for rejecting one pest-hunt item."""

    reason: str = Field(..., min_length=4)


class PestHuntItemActionResponse(BaseModel):
    """The outcome of an approve/reject call on one pest-hunt item."""

    status: str
    item_id: str
    materialized_task_id: str | None = None
    detail: str
