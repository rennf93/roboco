"""Schemas for the board roadmap engine's CEO surface."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from roboco.foundation.policy.content import markers

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable


class RoadmapItemResponse(BaseModel):
    """One roadmap item draft within a themed cycle."""

    id: str
    title: str
    description: str
    acceptance_criteria: list[str]
    project_slug: str
    team: str
    priority: int
    rationale: str
    status: str
    reject_reason: str | None = None
    materialized_task_id: str | None = None


class RoadmapCycleResponse(BaseModel):
    """A held roadmap exploration cycle: a goal + its item drafts."""

    task_id: str
    title: str
    status: str
    goal: str
    items: list[RoadmapItemResponse]


class RoadmapRejectRequest(BaseModel):
    """The CEO's reason for rejecting one roadmap item."""

    reason: str = Field(..., min_length=4)


class RoadmapItemActionResponse(BaseModel):
    """The outcome of an approve/reject call on one roadmap item."""

    status: str
    item_id: str
    materialized_task_id: str | None = None
    detail: str


def roadmap_status_value(task: TaskTable) -> str:
    raw = task.status
    return raw.value if hasattr(raw, "value") else str(raw)


def task_to_roadmap_cycle_response(task: TaskTable) -> RoadmapCycleResponse:
    payload = markers.get_roadmap_cycle(task) or {}
    items = [RoadmapItemResponse(**item) for item in payload.get("items", [])]
    return RoadmapCycleResponse(
        task_id=str(task.id),
        title=task.title,
        status=roadmap_status_value(task),
        goal=str(payload.get("goal") or ""),
        items=items,
    )
