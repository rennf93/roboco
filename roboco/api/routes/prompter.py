"""
Prompter API Routes

Endpoints:
  POST   /api/prompter/chat
      Send a user message (and optionally a conversation_id) to the LLM.
      Auto-creates a new conversation when conversation_id is omitted.

  GET    /api/prompter/conversations
      List all prompter conversations.

  GET    /api/prompter/conversations/{conversation_id}
      Get a single conversation including its full message history.

  DELETE /api/prompter/conversations/{conversation_id}
      Delete a conversation and all its messages (cascade).

  POST   /api/prompter/conversations/{conversation_id}/create-task
      Generate a structured task draft from the conversation history,
      validate it, and create a real Task record.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from roboco.api.deps import CurrentAgentId, CurrentAgentSlug, DbSession
from roboco.api.schemas.prompter import (
    PrompterChatRequest,
    PrompterChatResponse,
    PrompterConversationDetailResponse,
    PrompterConversationResponse,
    PrompterCreateTaskRequest,
    PrompterCreateTaskResponse,
    PrompterMessageResponse,
)
from roboco.services.base import NotFoundError, ServiceError
from roboco.services.prompter import get_prompter_service

router = APIRouter()


# =============================================================================
# Chat
# =============================================================================


@router.post(
    "/chat", response_model=PrompterChatResponse, status_code=status.HTTP_200_OK
)
async def chat(
    body: PrompterChatRequest,
    db: DbSession,
    agent_slug: CurrentAgentSlug,
) -> PrompterChatResponse:
    """Send a user message to the LLM and get a response.

    If ``conversation_id`` is not provided a new conversation is created
    automatically and its id is returned in the response.
    """
    svc = get_prompter_service(db)
    try:
        conv, assistant_text, model_used = await svc.chat(
            user_message=body.message,
            agent_slug=agent_slug,
            conversation_id=body.conversation_id,
        )
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM call failed: {exc}",
        ) from exc

    await db.commit()
    return PrompterChatResponse(
        conversation_id=UUID(str(conv.id)),
        assistant_text=assistant_text,
        model_used=model_used,
    )


# =============================================================================
# Conversations CRUD
# =============================================================================


@router.get(
    "/conversations",
    response_model=list[PrompterConversationResponse],
)
async def list_conversations(
    db: DbSession,
    agent_slug: CurrentAgentSlug,  # noqa: ARG001 — presence ensures auth header
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[PrompterConversationResponse]:
    """Return a paginated list of all conversations."""
    svc = get_prompter_service(db)
    conversations = await svc.list_conversations(limit=limit, offset=offset)
    return [PrompterConversationResponse.model_validate(c) for c in conversations]


@router.get(
    "/conversations/{conversation_id}",
    response_model=PrompterConversationDetailResponse,
)
async def get_conversation(
    conversation_id: UUID,
    db: DbSession,
    agent_slug: CurrentAgentSlug,  # noqa: ARG001 — ensures auth header present
) -> PrompterConversationDetailResponse:
    """Get a conversation with its full message history."""
    svc = get_prompter_service(db)
    try:
        conv = await svc.get_conversation(conversation_id)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    messages = [
        PrompterMessageResponse.model_validate(m)
        for m in sorted(conv.messages, key=lambda m: m.created_at)
    ]
    return PrompterConversationDetailResponse(
        id=UUID(str(conv.id)),
        title=conv.title,
        message_count=conv.message_count,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=messages,
    )


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_conversation(
    conversation_id: UUID,
    db: DbSession,
    agent_slug: CurrentAgentSlug,  # noqa: ARG001 — ensures auth header present
) -> None:
    """Delete a conversation and all its messages."""
    svc = get_prompter_service(db)
    try:
        await svc.delete_conversation(conversation_id)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    await db.commit()


# =============================================================================
# Task creation from conversation
# =============================================================================


@router.post(
    "/conversations/{conversation_id}/create-task",
    response_model=PrompterCreateTaskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_task_from_conversation(
    conversation_id: UUID,
    body: PrompterCreateTaskRequest,
    db: DbSession,
    agent_slug: CurrentAgentSlug,
    agent_id: CurrentAgentId,
) -> PrompterCreateTaskResponse:
    """Generate a task draft from the conversation and create a real Task record.

    The LLM reads the conversation history and produces a structured task
    draft (title, description, acceptance_criteria, team, task_type, nature,
    estimated_complexity).  The draft is validated and a ``Task`` row is
    created and returned by id.
    """
    svc = get_prompter_service(db)
    try:
        task_id = await svc.create_task_from_conversation(
            conversation_id=conversation_id,
            agent_slug=agent_slug,
            created_by=agent_id,
            project_id=body.project_id,
            product_id=body.product_id,
        )
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    await db.commit()
    return PrompterCreateTaskResponse(task_id=task_id)
