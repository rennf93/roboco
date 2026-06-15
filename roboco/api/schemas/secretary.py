"""Secretary API schemas — directives + company-state reads."""

from typing import Any

from pydantic import BaseModel, Field


class DirectiveSubmit(BaseModel):
    """The Secretary submits an action on the CEO's command."""

    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)


class DirectiveDecision(BaseModel):
    """CEO confirm/reject payload."""

    reason: str | None = None


class DirectiveResponse(BaseModel):
    """A directive as returned to the Secretary / CEO."""

    id: str
    kind: str
    status: str
    payload: dict[str, Any]
    requested_by: str
    requested_at: str | None = None
    decided_by: str | None = None
    decided_at: str | None = None
    result: str | None = None


class CompanyStateResponse(BaseModel):
    """A compact snapshot of company state for the CEO."""

    goals: dict[str, Any]
    task_counts: dict[str, int]
    pending_pitches: list[dict[str, Any]]
    pending_directives: list[dict[str, Any]]
