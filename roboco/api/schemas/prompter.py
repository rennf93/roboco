"""
Prompter API Schemas

Request/response models for the Prompter endpoints and conversion helpers
that map ORM rows to Pydantic responses.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from roboco.db.tables import PromptSessionTable, PromptTurnTable
from roboco.models.base import PromptSessionStatus

# =============================================================================
# TURN SCHEMAS
# =============================================================================


class PromptTurnResponse(BaseModel):
    """A single turn (user or assistant message) within a prompt session."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    role: str
    content: str
    turn_index: int
    created_at: datetime


# =============================================================================
# SESSION SCHEMAS
# =============================================================================


class PromptSessionCreate(BaseModel):
    """Request body for creating a new prompt session."""

    system_prompt: str | None = Field(
        default=None,
        description=("Optional system-level prompt to initialise the conversation."),
    )
    model: str | None = Field(
        default=None,
        description=(
            "Model identifier from GET /api/prompter/models. "
            "Defaults to the server-side default when omitted."
        ),
    )
    created_by: UUID | None = Field(
        default=None,
        description="UUID of the agent/user creating the session.",
    )


class PromptSessionResponse(BaseModel):
    """Full representation of a prompt session, including its turns."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_by: UUID | None
    status: str
    system_prompt: str | None
    model: str | None
    created_at: datetime
    turns: list[PromptTurnResponse] = Field(default_factory=list)


class PromptSessionStatusUpdate(BaseModel):
    """Request body for updating a session's status."""

    status: PromptSessionStatus


# =============================================================================
# CHAT SCHEMAS
# =============================================================================


class ChatRequest(BaseModel):
    """Request body for the SSE-streaming chat endpoint."""

    session_id: UUID = Field(..., description="ID of the prompt session to continue.")
    message: str = Field(..., min_length=1, description="The user's message.")


# =============================================================================
# MODELS CATALOG SCHEMAS
# =============================================================================


class ModelInfo(BaseModel):
    """A selectable LLM model with a human-readable label.

    The ``id`` is the routing key stored in ``PromptSession.model`` and
    accepted by ``PromptSessionCreate.model``.  Raw provider model IDs (such
    as ``claude-opus-4-6``) are never exposed here.
    """

    id: str = Field(..., description="Routing key used when creating a session.")
    label: str = Field(..., description="Human-readable display name.")
    description: str = Field(
        ..., description="Brief description of this model's strengths."
    )


# =============================================================================
# LAUNCH SCHEMAS
# =============================================================================


class LaunchRequest(BaseModel):
    """Request body for the launch action.

    The LLM-generated content in the latest assistant turn provides
    ``title``, ``description``, ``acceptance_criteria``, ``team``,
    ``task_type``, ``nature``, and ``estimated_complexity``.  The caller
    must supply the repository context (``project_id`` **or**
    ``product_id``) because the LLM cannot know which repo to target.

    Exactly one of ``project_id`` / ``product_id`` must be set.
    """

    project_id: UUID | None = Field(
        default=None,
        description="Project the new task will be linked to.",
    )
    product_id: UUID | None = Field(
        default=None,
        description="Product (multi-cell) the new task will be linked to.",
    )


class LaunchResponse(BaseModel):
    """Response returned after a successful launch."""

    task_id: UUID
    session_id: UUID
    session_status: str


# =============================================================================
# CONVERTERS
# =============================================================================


def turn_to_response(row: PromptTurnTable) -> PromptTurnResponse:
    """Convert a :class:`PromptTurnTable` ORM row to a response schema."""
    return PromptTurnResponse(
        id=UUID(str(row.id)),
        session_id=UUID(str(row.session_id)),
        role=row.role,
        content=row.content,
        turn_index=row.turn_index,
        created_at=row.created_at,
    )


def session_to_response(
    row: PromptSessionTable,
    turns: list[PromptTurnTable] | None = None,
) -> PromptSessionResponse:
    """Convert a :class:`PromptSessionTable` ORM row to a response schema.

    Args:
        row: The ORM session row.
        turns: Optional pre-fetched list of turns to embed in the response.
            When ``None``, the ``turns`` field on the response will be empty.
    """
    return PromptSessionResponse(
        id=UUID(str(row.id)),
        created_by=UUID(str(row.created_by)) if row.created_by else None,
        status=row.status,
        system_prompt=row.system_prompt,
        model=row.model,
        created_at=row.created_at,
        turns=[turn_to_response(t) for t in (turns or [])],
    )
