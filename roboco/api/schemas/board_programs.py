"""Schemas for the Board Programs registry's CEO surface."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class BoardProgramResponse(BaseModel):
    """One registry entry's live status — the panel card + edit-project
    dialog's opt-in controls both read this shape."""

    key: str
    title: str
    description: str
    role: str
    trigger: str
    scope: str
    enabled: bool
    opted_in_project_slugs: list[str]
    last_opened_at: str | None
    open_cycle: bool
    last_cycle_summary: str | None


class BoardProgramDecisionResponse(BaseModel):
    """One CEO approve/reject on a cycle. ``item_snapshot`` is the bounded
    payload ``BoardProgramEngine.record_decision`` stamps at decision time,
    present whenever the source item was resolvable, absent for a decision
    recorded before that existed."""

    item_ref: str
    verdict: str
    reason: str | None = None
    item_snapshot: dict[str, Any] | None = None


class BoardProgramCycleResponse(BaseModel):
    """One recorded cycle for a program: the durable LEARN-history read
    surface. Survives the exploration task's deletion: every decision's
    ``item_snapshot`` (when resolved) stands on its own."""

    id: str
    opened_at: str
    closed_at: str | None
    items_proposed: int
    items_approved: int
    items_rejected: int
    nothing_to_propose_reason: str | None
    decisions: list[BoardProgramDecisionResponse]
