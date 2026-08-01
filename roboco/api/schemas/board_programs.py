"""Schemas for the Board Programs registry API — the CEO's status + off-
schedule "run now" surface, mirroring ``roboco.api.schemas.roadmap``."""

from __future__ import annotations

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
