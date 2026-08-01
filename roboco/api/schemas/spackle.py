"""Schemas for the Spackle (Board Program) engine's CEO surface.
Mirrors ``roboco.api.schemas.pest_control`` exactly."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from roboco.foundation.policy.content import markers

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable


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


def spackle_status_value(task: TaskTable) -> str:
    raw = task.status
    return raw.value if hasattr(raw, "value") else str(raw)


def task_to_spackle_cycle_response(task: TaskTable) -> SpackleCycleResponse:
    payload = markers.get_gap_fill(task) or {}
    items = [GapFillItemResponse(**item) for item in payload.get("items", [])]
    return SpackleCycleResponse(
        task_id=str(task.id),
        title=task.title,
        status=spackle_status_value(task),
        items=items,
    )
