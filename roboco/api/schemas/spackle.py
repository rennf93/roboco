"""Schemas for the Spackle (Board Program) engine's CEO surface.
Mirrors ``roboco.api.schemas.pest_control`` exactly."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GapFillItemResponse(BaseModel):
    """One evidence-backed gap-fill item draft within a Spackle audit."""

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


class SpackleCycleResponse(BaseModel):
    """A held spackle exploration cycle: its item drafts."""

    task_id: str
    title: str
    status: str
    items: list[GapFillItemResponse]


class GapFillRejectRequest(BaseModel):
    """The CEO's reason for rejecting one gap-fill item."""

    reason: str = Field(..., min_length=4)


class GapFillItemActionResponse(BaseModel):
    """The outcome of an approve/reject call on one gap-fill item."""

    status: str
    item_id: str
    materialized_task_id: str | None = None
    detail: str
