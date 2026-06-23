"""Request / response schemas for the live intake (Prompter) chat bridge.

Moved out of the route module so the HTTP layer stays handler-only and these
models live with the other API schemas (architectural-conventions placement).
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID  # noqa: TC003 — pydantic resolves these annotations at runtime

from pydantic import BaseModel, Field, model_validator


class StartLiveRequest(BaseModel):
    """Open a live intake chat scoped to a project, a product, or a MegaTask.

    Exactly one scope: a single ``project_id`` (single-cell), a ``product_id``
    (board-led multi-cell), or ``project_ids`` (a MegaTask spanning several
    possibly-unrelated repos — the agent reads them all and proposes a batch).
    """

    project_id: UUID | None = None
    product_id: UUID | None = None
    project_ids: list[UUID] | None = None
    initial_message: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _exactly_one_scope(self) -> StartLiveRequest:
        chosen = sum(
            1 for scope in (self.project_id, self.product_id, self.project_ids) if scope
        )
        if chosen != 1:
            raise ValueError(
                "provide exactly one of project_id / product_id / project_ids"
            )
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


class BatchConfirmRequest(BaseModel):
    """Confirm a MegaTask — a batch of drafts sequenced into collision-free waves.

    Each entry in ``drafts`` is a normal intake draft dict that ALSO carries its
    own ``project_id`` (the batch spans many projects) and an optional collision
    surface the analyzer reads (``intends_to_touch`` globs, ``adds_migration``,
    ``touches_shared``). ``route`` is the same start button as a single confirm:
    ``"board"`` (Board reviews the batch first) or ``"main_pm"`` (straight to the
    Main PM). ``title`` names the umbrella.
    """

    title: str = Field(..., min_length=1)
    drafts: list[dict[str, Any]] = Field(..., min_length=1)
    route: Literal["board", "main_pm"] = "board"
