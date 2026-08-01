"""Schemas for the Mirror (Board Program) engine's CEO surface.
Mirrors ``roboco.api.schemas.spackle`` exactly."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from roboco.foundation.policy.content import markers

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable


class MessagingFixItemResponse(BaseModel):
    """One evidence-backed messaging-fix item draft within a Mirror audit."""

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


class MirrorCycleResponse(BaseModel):
    """A held mirror exploration cycle: its item drafts."""

    task_id: str
    title: str
    status: str
    items: list[MessagingFixItemResponse]


class MessagingFixRejectRequest(BaseModel):
    """The CEO's reason for rejecting one messaging-fix item."""

    reason: str = Field(..., min_length=4)


class MessagingFixItemActionResponse(BaseModel):
    """The outcome of an approve/reject call on one messaging-fix item."""

    status: str
    item_id: str
    materialized_task_id: str | None = None
    detail: str


def mirror_status_value(task: TaskTable) -> str:
    raw = task.status
    return raw.value if hasattr(raw, "value") else str(raw)


def task_to_mirror_cycle_response(task: TaskTable) -> MirrorCycleResponse:
    payload = markers.get_messaging_fixes(task) or {}
    items = [MessagingFixItemResponse(**item) for item in payload.get("items", [])]
    return MirrorCycleResponse(
        task_id=str(task.id),
        title=task.title,
        status=mirror_status_value(task),
        items=items,
    )
