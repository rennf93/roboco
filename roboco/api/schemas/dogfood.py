"""Schemas for the Dogfood (Board Program) engine's CEO surface.
Mirrors ``roboco.api.schemas.spackle`` exactly."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from roboco.foundation.policy.content import markers

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable


class FrictionFixItemResponse(BaseModel):
    """One evidence-backed UX-friction item draft within a Dogfood walk."""

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


class DogfoodCycleResponse(BaseModel):
    """A held dogfood exploration cycle: its item drafts."""

    task_id: str
    title: str
    status: str
    items: list[FrictionFixItemResponse]


class FrictionFixRejectRequest(BaseModel):
    """The CEO's reason for rejecting one friction-fix item."""

    reason: str = Field(..., min_length=4)


class FrictionFixItemActionResponse(BaseModel):
    """The outcome of an approve/reject call on one friction-fix item."""

    status: str
    item_id: str
    materialized_task_id: str | None = None
    detail: str


def dogfood_status_value(task: TaskTable) -> str:
    raw = task.status
    return raw.value if hasattr(raw, "value") else str(raw)


def task_to_dogfood_cycle_response(task: TaskTable) -> DogfoodCycleResponse:
    payload = markers.get_friction_fixes(task) or {}
    items = [FrictionFixItemResponse(**item) for item in payload.get("items", [])]
    return DogfoodCycleResponse(
        task_id=str(task.id),
        title=task.title,
        status=dogfood_status_value(task),
        items=items,
    )
