"""
Prompter API Schemas

Request / response Pydantic models for the Prompter chat endpoints.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

# =============================================================================
# Request schemas
# =============================================================================


class PrompterChatRequest(BaseModel):
    """Request body for POST /api/prompter/chat."""

    message: str = Field(..., min_length=1, description="User message to send")
    conversation_id: UUID | None = Field(
        default=None,
        description=(
            "Existing conversation to continue. "
            "Omit (or pass null) to start a new conversation."
        ),
    )


class PrompterCreateTaskRequest(BaseModel):
    """Request body for POST /api/prompter/conversations/{id}/create-task.

    Exactly one of ``project_id`` or ``product_id`` must be supplied —
    the same constraint as ``TaskCreate``.
    """

    project_id: UUID | None = Field(
        default=None,
        description="Target git repository project UUID (single-cell task)",
    )
    product_id: UUID | None = Field(
        default=None,
        description="Product UUID for fan-out (multi-cell) task",
    )

    @model_validator(mode="after")
    def _require_one(self) -> "PrompterCreateTaskRequest":
        if self.project_id is None and self.product_id is None:
            raise ValueError("Either project_id or product_id must be provided")
        return self


# =============================================================================
# Response schemas
# =============================================================================


class PrompterMessageResponse(BaseModel):
    """A single message within a conversation."""

    id: UUID
    role: str = Field(..., description="'user' or 'assistant'")
    content: str
    model_used: str | None = Field(
        default=None,
        description="Model name used to generate this message (assistant only)",
    )
    created_at: datetime

    model_config = {"from_attributes": True}


class PrompterConversationResponse(BaseModel):
    """Summary of a prompter conversation (no messages)."""

    id: UUID
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PrompterConversationDetailResponse(PrompterConversationResponse):
    """Full conversation including message history."""

    messages: list[PrompterMessageResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class PrompterChatResponse(BaseModel):
    """Response from POST /api/prompter/chat."""

    conversation_id: UUID
    assistant_text: str = Field(..., description="The LLM's response text")
    model_used: str = Field(
        ..., description="Model name resolved by ModelRoutingService"
    )


class PrompterCreateTaskResponse(BaseModel):
    """Response from POST /api/prompter/conversations/{id}/create-task."""

    task_id: UUID = Field(..., description="UUID of the newly created task")
