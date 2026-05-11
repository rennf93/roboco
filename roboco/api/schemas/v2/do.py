"""Request schemas for /api/v2/do/* content tools."""

from uuid import UUID

from pydantic import BaseModel, Field


class CommitRequest(BaseModel):
    message: str = Field(..., min_length=1)
    files: list[str] | None = None


class NoteRequest(BaseModel):
    """Journal entry. ``text`` is always the short summary line.

    Scope-specific fields are optional but pre-gateway parity expected
    them filled for `decision` and `reflect`:

    - decision: ``context``, ``options``, ``chosen``, ``rationale``,
      ``consequences``
    - reflect: ``what_done``, ``what_learned``, ``what_struggled``,
      ``next_steps``

    When provided, these are formatted into the journal entry's content
    as structured markdown — the panel UI's decision/reflect views show
    them as named sections instead of a one-line phrase.
    """

    text: str = Field(..., min_length=1)
    scope: str = "note"
    task_id: UUID | None = None
    title: str | None = None
    # decision scope
    context: str | None = None
    options: list[str] | None = None
    chosen: str | None = None
    rationale: str | None = None
    consequences: str | None = None
    # reflect scope
    what_done: str | None = None
    what_learned: str | None = None
    what_struggled: str | None = None
    next_steps: str | None = None


class SayRequest(BaseModel):
    channel: str
    text: str = Field(..., min_length=1)
    task_id: UUID | None = None


class DmRequest(BaseModel):
    recipient: str  # agent slug
    text: str = Field(..., min_length=1)
    task_id: UUID | None = None
    skill: str | None = None


class NotifyRequest(BaseModel):
    target: str  # agent slug
    text: str = Field(..., min_length=1)
    priority: str = "normal"  # normal | high | urgent
    task_id: UUID | None = None


class EvidenceRequest(BaseModel):
    task_id: UUID


# =============================================================================
# Wave 1 — Pre-gateway parity restoration
# =============================================================================


class OpenSessionRequest(BaseModel):
    """PM creates a discussion session for one or more tasks.

    Pre-gateway parity for `roboco_session_create_for_tasks`. Populates the
    panel's Sessions tab.
    """

    task_id: UUID
    channel: str = Field(..., min_length=1)
    topic: str = Field(..., min_length=1, max_length=200)
    relationship_type: str = "discussion"  # discussion|planning|review|retrospective
    group_id: UUID | None = None


class LinkSessionRequest(BaseModel):
    """Link an existing session to a task. Idempotent."""

    session_id: UUID
    task_id: UUID
    is_primary: bool = False
    relationship_type: str = "discussion"


class ProgressRequest(BaseModel):
    """Narrative progress update with 0..100 percentage.

    Pre-gateway parity for `roboco_task_progress`. Populates the panel's
    Progress tab.
    """

    task_id: UUID
    message: str = Field(..., min_length=1)
    percentage: int = Field(..., ge=0, le=100)


class NotifyListRequest(BaseModel):
    """Read this agent's notification inbox."""

    unread_only: bool = True
    pending_ack_only: bool = False
    limit: int = Field(default=20, ge=1, le=100)


class NotifyGetRequest(BaseModel):
    notification_id: UUID


class NotifyAckRequest(BaseModel):
    notification_id: UUID
