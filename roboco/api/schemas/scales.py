"""Schemas for the Scales (Board Program) engine's CEO surface.
Mirrors ``roboco.api.schemas.pest_control`` — an item references a LIVE task
(``task_ref``/``target_task_id``) instead of drafting a new one, so there is
no ``materialized_task_id``; ``executed_detail`` records what approval did
to the target task instead."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from roboco.foundation.policy.content import markers

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable


class RebalanceItemResponse(BaseModel):
    """One re-priority/cancellation item draft within a Scales rebalance plan."""

    id: str
    task_ref: str
    target_task_id: str
    target_task_title: str
    action: str
    new_priority: int | None = None
    rationale: str
    status: str
    reject_reason: str | None = None
    executed_detail: str | None = None


class RebalanceCycleResponse(BaseModel):
    """A held Scales exploration cycle: its item drafts."""

    task_id: str
    title: str
    status: str
    items: list[RebalanceItemResponse]


class RebalanceRejectRequest(BaseModel):
    """The CEO's reason for rejecting one rebalance item."""

    reason: str = Field(..., min_length=4)


class RebalanceItemActionResponse(BaseModel):
    """The outcome of an approve/reject call on one rebalance item."""

    status: str
    item_id: str
    executed_detail: str | None = None
    detail: str


def scales_status_value(task: TaskTable) -> str:
    raw = task.status
    return raw.value if hasattr(raw, "value") else str(raw)


def task_to_rebalance_cycle_response(task: TaskTable) -> RebalanceCycleResponse:
    payload = markers.get_rebalance_plan(task) or {}
    items = [RebalanceItemResponse(**item) for item in payload.get("items", [])]
    return RebalanceCycleResponse(
        task_id=str(task.id),
        title=task.title,
        status=scales_status_value(task),
        items=items,
    )
