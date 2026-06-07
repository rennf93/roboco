"""
Prompter API Routes

Conversational assistant endpoints for drafting tasks:
- POST /api/prompter/chat    : back-and-forth conversation
- POST /api/prompter/draft   : structured task draft generation
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, status

from roboco.api.schemas.prompter import (
    PrompterChatRequest,
    PrompterChatResponse,
    PrompterDraftRequest,
    PrompterDraftResponse,
)
from roboco.services.base import ServiceError, ValidationError
from roboco.services.prompter import get_prompter_service

if TYPE_CHECKING:
    from roboco.api.deps import CurrentAgentContext

router = APIRouter()


def _translate_error(e: ServiceError) -> HTTPException:
    """Service errors → HTTP status."""
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


@router.post("/chat", response_model=PrompterChatResponse)
async def prompter_chat(
    data: PrompterChatRequest,
    _agent: CurrentAgentContext,
) -> PrompterChatResponse:
    """
    Continue a Prompter conversation.

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
    Generate a structured task draft from conversation context.

    The frontend sends the full conversation history. The backend calls the
    LLM to produce a JSON draft conforming to the TaskCreate schema. The
    draft always has `source="prompter"` and `confirmed_by_human=False`.

    The human must review the draft in the panel and explicitly confirm
    before POST /api/tasks is called with `confirmed_by_human=True`.
    """
    service = get_prompter_service()
    try:
        result = await service.draft(
            messages=[msg.model_dump() for msg in data.messages],
            context=data.context,
        )
    except ServiceError as e:
        raise _translate_error(e) from e

    from roboco.api.schemas.prompter import PrompterDraftTask

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
