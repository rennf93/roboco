"""
Prompter API Schemas

Request/response models for the conversational Prompter assistant
that helps users draft tasks through natural language.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from roboco.models.base import Complexity, TaskNature, TaskType, Team


# =============================================================================
# CHAT SCHEMAS
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


class PrompterChatRequest(BaseModel):
    """Request to continue a Prompter conversation."""

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
    """Response from the Prompter chat endpoint."""

    message: str = Field(..., description="Assistant's reply")
    conversation_id: str | None = Field(
        default=None, description="Client-managed conversation identifier"
    )
    # If the assistant has gathered enough info to propose a draft,
    # it signals this so the frontend can offer a "Generate Draft" CTA.
    draft_ready: bool = Field(
        default=False,
        description=(
            "True when the assistant believes enough context exists "
            "to draft a task"
        ),
    )


# =============================================================================
# DRAFT SCHEMAS
# =============================================================================


class PrompterDraftRequest(BaseModel):
    """Request to generate a structured task draft from conversation context."""

    messages: list[ChatMessage] = Field(
        ..., min_length=1, description="Full conversation used as drafting context"
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional overrides (project_id, team, assigned_to, etc.)",
    )


class PrompterDraftTask(BaseModel):
    """A task draft produced by the Prompter.

    Mirrors TaskCreate fields so the frontend can POST /api/tasks
    with confirmed_by_human=True after human review.
    """

    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=20)
    acceptance_criteria: list[str] = Field(..., min_length=1)
    team: Team = Field(...)
    priority: int = Field(default=2, ge=0, le=3)
    task_type: TaskType = Field(...)
    nature: TaskNature = Field(...)
    estimated_complexity: Complexity = Field(...)
    project_id: str | None = Field(  # UUID string; route layer converts if needed
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


class PrompterDraftResponse(BaseModel):
    """Response from the Prompter draft endpoint."""

    draft: PrompterDraftTask = Field(..., description="Structured task draft")
    reasoning: str = Field(
        default="",
        description="Assistant's explanation of how the draft was derived",
    )

    model_config = ConfigDict(from_attributes=True)
