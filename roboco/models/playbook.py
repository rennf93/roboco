"""Playbook domain models — curated, reusable procedures.

A *learning* records "this happened"; a *playbook* records "here is how to do X".
An agent drafts one; the Auditor approves it; approved playbooks are embedded into
the PLAYBOOKS RAG index and auto-suggested in briefings. Orthogonal to the task
lifecycle — its own entity, with a status independent of any task status.
"""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import ConfigDict, Field

from roboco.models.base import PlaybookStatus, RobocoBase


class Playbook(RobocoBase):
    """A curated procedure, at any point in the draft -> approved/archived flow."""

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: UUID = Field(default_factory=uuid4)
    title: str = Field(..., min_length=1, max_length=200)
    slug: str = Field(..., min_length=1, max_length=80)
    problem: str = Field(..., min_length=1)
    procedure: str = Field(..., min_length=1)
    tags: list[str] = Field(default_factory=list)
    team: str | None = None
    scope: str = "org"
    source_task_ids: list[UUID] = Field(default_factory=list)
    status: PlaybookStatus = PlaybookStatus.DRAFT
    created_by: UUID
    approved_by: UUID | None = None
    created_at: datetime | None = None
    approved_at: datetime | None = None


class PlaybookCreate(RobocoBase):
    """Service-layer create DTO — the slug is derived from the title by the service."""

    title: str = Field(..., min_length=1, max_length=200)
    problem: str = Field(..., min_length=1)
    procedure: str = Field(..., min_length=1)
    tags: list[str] = Field(default_factory=list)
    team: str | None = None
    scope: str = "org"
    source_task_id: UUID | None = None


class PlaybookUpdate(RobocoBase):
    """Partial update DTO (all fields optional)."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    problem: str | None = Field(default=None, min_length=1)
    procedure: str | None = Field(default=None, min_length=1)
    tags: list[str] | None = None
    team: str | None = None
    scope: str | None = None
