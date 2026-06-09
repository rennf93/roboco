"""
Prompter API Schemas

Request/response models for the conversational Prompter assistant
that helps users draft tasks through natural language.

Includes both the session-based schemas (for the DB-persisted approach)
and the legacy stateless schemas retained for backward compatibility.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from roboco.models.base import (
    Complexity,
    TaskNature,
    TaskType,
    Team,
)

# =============================================================================
# SHARED MESSAGE SCHEMA
# =============================================================================


class ChatMessage(BaseModel):
    """A single message in the Prompter conversation."""

    role: str = Field(..., description="One of: user, assistant, system")
    content: str = Field(..., min_length=1, description="Message text")

    @field_validator("role")
    @classmethod
    def _valid_role(cls, v: str) -> str:
        if v not in {"user", "assistant", "system"}:
            raise ValueError("role must be one of: user, assistant, system")
        return v


# =============================================================================
# SESSION-BASED SCHEMAS (acceptance-criteria-required names)
# =============================================================================


class PrompterSessionResponse(BaseModel):
    """Response for session creation and retrieval."""

    id: UUID
    agent_id: UUID
    status: str
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class PrompterMessageRequest(BaseModel):
    """Request body for POST /api/prompter/sessions/{id}/messages."""

    content: str = Field(..., min_length=1, description="The user's message text")
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional per-turn context overrides",
    )


class PrompterMessageResponse(BaseModel):
    """A single message record returned to the client."""

    id: UUID
    session_id: UUID
    role: str
    content: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PrompterTurnResponse(BaseModel):
    """Result of a chat turn: the full message list plus the readiness signal.

    Carries ``draft_ready`` (and the coarse ``scale`` hint) so the frontend
    consumes the backend's judgement instead of re-deriving it by string match.
    """

    messages: list[PrompterMessageResponse]
    draft_ready: bool = False
    scale: str | None = Field(
        default=None,
        description="Coarse size hint from the assistant: 'single' or 'multi'",
    )


class TaskConfirmRequest(BaseModel):
    """Request body for POST /api/prompter/sessions/{id}/confirm.

    Allows the frontend to pass overrides that should be applied
    to the draft before the real task is created.
    """

    project_id: UUID | None = Field(
        default=None,
        description="Override project_id from the draft (required if draft omits it)",
    )
    product_id: UUID | None = Field(
        default=None,
        description="Override product_id from the draft",
    )
    assigned_to: str | None = Field(
        default=None,
        description="Agent slug or UUID to assign the task to",
    )
    overrides: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional fields to override in the draft before task creation",
    )
    draft: "PrompterDraftTask | None" = Field(
        default=None,
        description=(
            "The human-edited structured draft. When present it replaces the "
            "stored draft (after re-validation and description re-composition) "
            "before the task is created."
        ),
    )


# =============================================================================
# DRAFT TASK SCHEMA  (shared between session and legacy paths)
# =============================================================================


class CellWork(BaseModel):
    """One cell's slice of a task's work — the per-cell breakdown of The Work.

    For a single-cell task there is exactly one entry; a board-led feature
    carries one entry per participating cell.
    """

    team: Team = Field(
        ..., description="The cell (or coordinating team) doing this work"
    )
    summary: str = Field(
        ..., min_length=1, description="One-line summary of this cell's slice"
    )
    items: list[str] = Field(
        default_factory=list, description="Concrete deliverables for this cell"
    )


class PrompterDraftTask(BaseModel):
    """A task draft produced by the Prompter.

    Mirrors TaskCreate fields so the frontend can POST /api/tasks
    with confirmed_by_human=True after human review.

    The structured spec fields (``objective``, ``what_this_builds``,
    ``the_work``, ``notes``) are first-class in this contract but persisted
    inside the existing ``draft_data`` JSONB column — no migration. The backend
    composes ``description`` deterministically from them; ``acceptance_criteria``
    renders as Success Criteria.
    """

    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=20)
    acceptance_criteria: list[str] = Field(..., min_length=1)
    team: Team = Field(...)
    priority: int = Field(default=2, ge=0, le=3)
    task_type: TaskType = Field(...)
    nature: TaskNature = Field(...)
    estimated_complexity: Complexity = Field(...)

    # Structured spec fields — optional for backward compatibility.
    objective: str | None = Field(
        default=None,
        description="The outcome this task delivers, in one or two sentences",
    )
    what_this_builds: list[str] = Field(
        default_factory=list, description="Concrete artifacts this task produces"
    )
    the_work: list[CellWork] = Field(
        default_factory=list,
        description="Per-cell breakdown; length drives single vs multi-cell",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Constraints, reuse pointers, things to confirm with the human",
    )
    project_id: str | None = Field(
        default=None,
        description=(
            "Project UUID as string; exactly one of project_id or "
            "product_id must be set"
        ),
    )
    product_id: str | None = Field(
        default=None,
        description=(
            "Product UUID as string; exactly one of project_id or "
            "product_id must be set"
        ),
    )
    assigned_to: str | None = Field(
        default=None,
        description="Agent slug or UUID to assign the task to",
    )
    target_date: str | None = Field(
        default=None,
        description="ISO-8601 target completion date",
    )

    # Provenance — always set by the prompter backend
    source: str = "prompter"
    confirmed_by_human: bool = False


# Resolve TaskConfirmRequest.draft now that PrompterDraftTask exists.
TaskConfirmRequest.model_rebuild()


class TaskDraftResponse(BaseModel):
    """Response for GET /api/prompter/sessions/{id}/draft."""

    id: UUID
    session_id: UUID
    draft: PrompterDraftTask
    confirmed_at: datetime | None = None
    task_id: UUID | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# LEGACY STATELESS SCHEMAS (retained for backward compatibility)
# =============================================================================


class PrompterChatRequest(BaseModel):
    """Request to continue a Prompter conversation (stateless)."""

    messages: list[ChatMessage] = Field(
        ...,
        min_length=1,
        description="Conversation history including the new user message",
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional context (project_id, team, prior drafts, etc.)",
    )


class PrompterChatResponse(BaseModel):
    """Response from the Prompter chat endpoint (stateless)."""

    message: str = Field(..., description="Assistant's reply")
    conversation_id: str | None = Field(
        default=None, description="Client-managed conversation identifier"
    )
    draft_ready: bool = Field(
        default=False,
        description=(
            "True when the assistant believes enough context exists to draft a task"
        ),
    )


class PrompterDraftRequest(BaseModel):
    """Request to generate a task draft from conversation context (stateless)."""

    messages: list[ChatMessage] = Field(
        ..., min_length=1, description="Full conversation used as drafting context"
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional overrides (project_id, team, assigned_to, etc.)",
    )


class PrompterDraftResponse(BaseModel):
    """Response from the Prompter draft endpoint (stateless)."""

    draft: PrompterDraftTask = Field(..., description="Structured task draft")
    reasoning: str = Field(
        default="",
        description="Assistant's explanation of how the draft was derived",
    )

    model_config = ConfigDict(from_attributes=True)
