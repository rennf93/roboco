"""
Prompter API Routes

Session-based conversational assistant endpoints for drafting tasks:
- POST /api/prompter/sessions               : create a new session
- POST /api/prompter/sessions/{id}/messages : send user message, get AI reply
- GET  /api/prompter/sessions/{id}/draft    : get structured task draft
- POST /api/prompter/sessions/{id}/confirm  : confirm draft → create real task

Legacy stateless endpoints (retained for backward compatibility):
- POST /api/prompter/chat   : back-and-forth conversation (stateless)
- POST /api/prompter/draft  : structured task draft generation (stateless)
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.prompter import (
    ChatMessage,
    PrompterChatRequest,
    PrompterChatResponse,
    PrompterDraftRequest,
    PrompterDraftResponse,
    PrompterDraftTask,
    PrompterMessageRequest,
    PrompterMessageResponse,
    PrompterSessionCreateRequest,
    PrompterSessionResponse,
    TaskConfirmRequest,
    TaskDraftResponse,
)
from roboco.services.base import NotFoundError, ServiceError, ValidationError
from roboco.services.prompter import ConfirmOverrides, get_prompter_service

router = APIRouter()


def _translate_error(e: ServiceError) -> HTTPException:
    """Service errors → HTTP status."""
    if isinstance(e, NotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": e.message},
        )
    if isinstance(e, ValidationError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "validation_error",
                "message": e.message,
                "field": e.field,
            },
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"error": "internal_error", "message": e.message},
    )


# =============================================================================
# SESSION-BASED ENDPOINTS
# =============================================================================


@router.post(
    "/sessions",
    response_model=PrompterSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    data: PrompterSessionCreateRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> PrompterSessionResponse:
    """Create a new Prompter conversation session linked to the authenticated agent."""
    service = get_prompter_service(db)
    try:
        session = await service.create_session(
            agent_id=agent.agent_id,
            context=data.context,
        )
    except ServiceError as e:
        raise _translate_error(e) from e

    return PrompterSessionResponse(
        id=session.id,  # type: ignore[arg-type]
        agent_id=session.agent_id,  # type: ignore[arg-type]
        status=session.status,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.post(
    "/sessions/{session_id}/messages",
    response_model=list[PrompterMessageResponse],
)
async def send_message(
    session_id: UUID,
    data: PrompterMessageRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> list[PrompterMessageResponse]:
    """
    Accept a user message, append it and an AI assistant response to the
    conversation, and return the updated message list.
    """
    service = get_prompter_service(db)
    try:
        messages = await service.send_message(
            session_id=session_id,
            agent_id=agent.agent_id,
            content=data.content,
            context=data.context,
        )
    except ServiceError as e:
        raise _translate_error(e) from e

    return [
        PrompterMessageResponse(
            id=msg.id,  # type: ignore[arg-type]
            session_id=msg.session_id,  # type: ignore[arg-type]
            role=msg.role,
            content=msg.content,
            created_at=msg.created_at,
        )
        for msg in messages
    ]


@router.get(
    "/sessions/{session_id}/draft",
    response_model=TaskDraftResponse,
)
async def get_draft(
    session_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
) -> TaskDraftResponse:
    """
    Return a structured task draft extracted from conversation history via LLM.

    The draft contains: title, description, acceptance_criteria, team,
    task_type, nature, and estimated_complexity.
    """
    service = get_prompter_service(db)
    try:
        draft_record = await service.get_or_generate_draft(
            session_id=session_id,
            agent_id=agent.agent_id,
        )
    except ServiceError as e:
        raise _translate_error(e) from e

    # Parse the stored draft_data into PrompterDraftTask for validation
    try:
        draft_task = PrompterDraftTask(**draft_record.draft_data)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "draft_schema_error",
                "message": f"Stored draft did not match schema: {exc}",
                "raw_draft": draft_record.draft_data,
            },
        ) from exc

    return TaskDraftResponse(
        id=draft_record.id,  # type: ignore[arg-type]
        session_id=draft_record.session_id,  # type: ignore[arg-type]
        draft=draft_task,
        confirmed_at=draft_record.confirmed_at,
        task_id=draft_record.task_id,  # type: ignore[arg-type]
        created_at=draft_record.created_at,
    )


@router.post(
    "/sessions/{session_id}/confirm",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def confirm_draft(
    session_id: UUID,
    data: TaskConfirmRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> dict:
    """
    Validate the draft and create a real Task using the existing TaskService.

    Returns the created task ID.
    """
    service = get_prompter_service(db)
    try:
        task_id = await service.confirm_draft(
            session_id=session_id,
            agent_id=agent.agent_id,
            confirm_overrides=ConfirmOverrides(
                project_id=data.project_id,
                product_id=data.product_id,
                assigned_to=data.assigned_to,
                extra=data.overrides,
            ),
        )
    except ServiceError as e:
        raise _translate_error(e) from e

    return {"task_id": str(task_id)}


# =============================================================================
# LEGACY STATELESS ENDPOINTS (backward compatibility)
# =============================================================================


@router.post("/chat", response_model=PrompterChatResponse)
async def prompter_chat(
    data: PrompterChatRequest,
    _agent: CurrentAgentContext,
) -> PrompterChatResponse:
    """
    Continue a Prompter conversation (stateless).

    The frontend sends the full conversation history (including the new user
    message). The assistant replies, optionally signalling that enough context
    has been gathered to generate a draft (`draft_ready=True`).
    """
    service = get_prompter_service()
    try:
        result = await service.chat(
            messages=[msg.model_dump() for msg in data.messages],
            context=data.context,
        )
    except ServiceError as e:
        raise _translate_error(e) from e

    return PrompterChatResponse(
        message=result["message"],
        draft_ready=result["draft_ready"],
    )


@router.post("/draft", response_model=PrompterDraftResponse)
async def prompter_draft(
    data: PrompterDraftRequest,
    _agent: CurrentAgentContext,
) -> PrompterDraftResponse:
    """
    Generate a structured task draft from conversation context (stateless).

    The frontend sends the full conversation history. The backend calls the
    LLM to produce a JSON draft conforming to the TaskCreate schema.
    """
    service = get_prompter_service()
    try:
        result = await service.draft(
            messages=[msg.model_dump() for msg in data.messages],
            context=data.context,
        )
    except ServiceError as e:
        raise _translate_error(e) from e

    draft_raw = result["draft"]
    try:
        draft = PrompterDraftTask(**draft_raw)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "draft_schema_error",
                "message": f"Generated draft did not match schema: {e}",
                "raw_draft": draft_raw,
            },
        ) from e

    return PrompterDraftResponse(
        draft=draft,
        reasoning=result["reasoning"],
    )


def _messages_to_dicts(messages: list[ChatMessage]) -> list[dict[str, str]]:
    """Convert ChatMessage list to dict list (internal helper)."""
    return [msg.model_dump() for msg in messages]
