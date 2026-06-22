"""Request / response schemas for the live intake (Prompter) chat bridge.

Moved out of the route module so the HTTP layer stays handler-only and these
models live with the other API schemas (architectural-conventions placement).
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID  # noqa: TC003 — pydantic resolves these annotations at runtime

from pydantic import BaseModel, Field, model_validator


class StartLiveRequest(BaseModel):
    """Open a live intake chat scoped to a project XOR a product."""

    project_id: UUID | None = None
    product_id: UUID | None = None
    initial_message: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _exactly_one_scope(self) -> StartLiveRequest:
        if bool(self.project_id) == bool(self.product_id):
            raise ValueError("provide exactly one of project_id / product_id")
        return self


class StartLiveResponse(BaseModel):
    """The new session's id — the panel opens its stream and posts messages to it."""

    session_id: str


class LiveMessageRequest(BaseModel):
    """The human's message in an active intake chat."""

    text: str = Field(..., min_length=1)


class AgentEvent(BaseModel):
    """One normalized event the container relays (mirrors driver.StreamChunk)."""

    kind: str
    text: str = ""
    tool: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class LiveConfirmRequest(BaseModel):
    """Confirm the agent's draft → a task, scoped to exactly one target.

    ``route`` is which start button the human pressed: ``"board"`` (Board review
    & Start → PO + HoM review first) or ``"main_pm"`` (Approve & Start → straight
    to the Main PM).
    """

    project_id: UUID | None = None
    product_id: UUID | None = None
    draft: dict[str, Any]
    route: Literal["board", "main_pm"] = "board"
    # Set on a board-informed re-draft: confirm updates this existing task in
    # place instead of creating a new one. When present, project/product scope
    # is taken from the task, so neither is required here.
    task_id: UUID | None = None

    @model_validator(mode="after")
    def _exactly_one_target(self) -> LiveConfirmRequest:
        if self.task_id is not None:
            return self
        if bool(self.project_id) == bool(self.product_id):
            raise ValueError("provide exactly one of project_id / product_id")
        return self
