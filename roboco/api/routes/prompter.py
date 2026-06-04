"""
Prompter API Routes

LLM-powered task-creation assistant: streaming chat, model catalog,
session CRUD, and the Create+Launch action.

Endpoints
---------
GET  /api/prompter/models
    Human-readable model catalog.  No raw provider model IDs exposed.

POST /api/prompter/sessions
    Create a new prompt session (DRAFT).

GET  /api/prompter/sessions
    List sessions, optionally filtered by created_by / status.

GET  /api/prompter/sessions/{session_id}
    Fetch a single session including its full conversation history.

PATCH /api/prompter/sessions/{session_id}/status
    Transition the session to a new status.

POST /api/prompter/chat
    Send a message and receive an SSE stream of LLM response tokens.
    The system prompt instructs the LLM to produce structured output
    matching the TaskCreate schema.

POST /api/prompter/sessions/{session_id}/launch
    Validate the latest assistant turn against TaskCreate constraints,
    create the task, and set the session to LAUNCHED.
"""

import json
import re
from collections.abc import AsyncGenerator
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.prompter import (
    ChatRequest,
    LaunchRequest,
    LaunchResponse,
    ModelInfo,
    PromptSessionCreate,
    PromptSessionResponse,
    PromptSessionStatusUpdate,
    session_to_response,
)
from roboco.models.base import (
    Complexity,
    PromptSessionStatus,
    TaskNature,
    TaskType,
    Team,
)
from roboco.models.llm_catalog import MODEL_CATALOG
from roboco.models.runtime import MODEL_MAP
from roboco.models.task import TaskCreateRequest
from roboco.services.base import NotFoundError, ValidationError
from roboco.services.prompter import get_prompt_service
from roboco.services.task import get_task_service

router = APIRouter()

# =============================================================================
# SYSTEM PROMPT
# =============================================================================

_TASK_CREATE_SYSTEM_PROMPT = """\
You are a task creation assistant for RoboCo, an AI software development company.
Your role is to help users define well-structured development tasks.

When describing a task, engage conversationally to clarify requirements, then
produce a structured JSON block at the end of your response.

The JSON block MUST be wrapped in a ```json ... ``` code fence and contain
exactly these fields (all required):

{
  "title": "<concise task title, 1-200 characters>",
  "description": "<detailed description, minimum 20 characters>",
  "acceptance_criteria": ["<criterion 1>", "<criterion 2>", ...],
  "team": "<one of: backend | frontend | ux_ui>",
  "task_type": "<code|documentation|research|planning|design|administrative>",
  "nature": "<one of: technical | non_technical>",
  "estimated_complexity": "<one of: low | medium | high>"
}

Guidelines:
- acceptance_criteria must be a non-empty list of clear, measurable criteria.
- Choose team based on the technology involved:
    backend - Python/API/database work
    frontend - JavaScript/TypeScript/UI work
    ux_ui - design, wireframes, user research
- Choose task_type accurately:
    code - writing or modifying source code
    documentation - writing or updating docs
    research - investigation or spike work
    planning - architecture or coordination
    design - visual or system design
    administrative - process, configuration, or non-technical work
- estimated_complexity reflects effort:
    low - hours, well-understood work
    medium - 1-3 days, some unknowns
    high - 3+ days, significant complexity or risk

Always end your response with the JSON block, even for follow-up turns.
Update the JSON as the conversation refines the requirements.
"""

# =============================================================================
# MODELS CATALOG
# =============================================================================

# Human-friendly descriptions keyed by routing slug.  These never expose raw
# provider model IDs such as "claude-opus-4-6" or "claude-sonnet-4-6".
_MODEL_DESCRIPTIONS: dict[str, str] = {
    "opus": (
        "Most capable model. Best for complex, nuanced tasks requiring deep reasoning."
    ),
    "sonnet": (
        "Balanced model. Excellent for most tasks with a good "
        "speed/capability tradeoff."
    ),
    "haiku": (
        "Fastest and most efficient model. Ideal for quick, straightforward tasks."
    ),
    "glm-5.1:cloud": (
        "GLM 5.1 via Ollama Cloud. Top SWE-Bench score; strong iterative reasoning."
    ),
    "kimi-k2.6:cloud": (
        "Kimi K2.6 via Ollama Cloud. Leading HLE score; built for agentic tool use."
    ),
    "minimax-m3:cloud": (
        "MiniMax M3 via Ollama Cloud. Purpose-built for coding; fastest Ollama option."
    ),
}

_RAW_ID_SUFFIX = re.compile(r"\s*[··]\s*\S+$")


def _friendly_label(display_name: str) -> str:
    """Strip the raw model-ID suffix from a catalog display name.

    Removes the `` · <model-id>`` suffix that ``llm_catalog.py`` appends so
    the response never exposes a raw provider identifier.
    """
    return _RAW_ID_SUFFIX.sub("", display_name).strip()


# Build the prompter-specific model list once at import time.
PROMPTER_MODELS: list[ModelInfo] = [
    ModelInfo(
        id=entry.model_name,
        label=_friendly_label(entry.display_name),
        description=_MODEL_DESCRIPTIONS.get(
            entry.model_name,
            f"{_friendly_label(entry.display_name)} model.",
        ),
    )
    for entry in MODEL_CATALOG
]

# =============================================================================
# LLM CALL HELPERS
# =============================================================================


def _resolve_anthropic_model(model_key: str | None) -> str:
    """Map a prompter model routing key to the full Anthropic model ID.

    Falls back to the ``"sonnet"`` slot when the key is absent or unknown.
    """
    key = model_key or "sonnet"
    return MODEL_MAP.get(key, MODEL_MAP.get("sonnet", "claude-sonnet-4-6"))


def _is_ollama_model(model_key: str | None) -> bool:
    """Return True when the model key routes to an Ollama Cloud provider."""
    return bool(model_key and model_key.endswith(":cloud"))


# =============================================================================
# MODEL CATALOG ENDPOINT
# =============================================================================


@router.get("/models", response_model=list[ModelInfo])
async def list_models(
    _agent: CurrentAgentContext,
) -> list[ModelInfo]:
    """Return the available LLM models for prompt sessions.

    Human-readable labels and descriptions only — no raw provider model
    IDs are included in the response.
    """
    return PROMPTER_MODELS


# =============================================================================
# SESSION CRUD ENDPOINTS
# =============================================================================


@router.post(
    "/sessions",
    response_model=PromptSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    data: PromptSessionCreate,
    db: DbSession,
    agent: CurrentAgentContext,
) -> PromptSessionResponse:
    """Create a new prompt session in DRAFT status."""
    svc = get_prompt_service(db)
    created_by = data.created_by or agent.agent_id
    row = await svc.create_session(
        created_by=created_by,
        system_prompt=data.system_prompt,
        model=data.model,
    )
    return session_to_response(row)


@router.get("/sessions", response_model=list[PromptSessionResponse])
async def list_sessions(
    db: DbSession,
    _agent: CurrentAgentContext,
    created_by: Annotated[UUID | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> list[PromptSessionResponse]:
    """List prompt sessions with optional filters."""
    svc = get_prompt_service(db)
    rows = await svc.list_sessions(
        created_by=created_by,
        status=status_filter,
    )
    return [session_to_response(row) for row in rows]


@router.get("/sessions/{session_id}", response_model=PromptSessionResponse)
async def get_session(
    session_id: UUID,
    db: DbSession,
    _agent: CurrentAgentContext,
) -> PromptSessionResponse:
    """Fetch a single session with its full conversation history."""
    svc = get_prompt_service(db)
    try:
        row = await svc.get_session(session_id)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.message,
        ) from exc
    turns = await svc.list_turns(session_id)
    return session_to_response(row, turns=turns)


@router.patch(
    "/sessions/{session_id}/status",
    response_model=PromptSessionResponse,
)
async def update_session_status(
    session_id: UUID,
    data: PromptSessionStatusUpdate,
    db: DbSession,
    _agent: CurrentAgentContext,
) -> PromptSessionResponse:
    """Transition a session to a new status."""
    svc = get_prompt_service(db)
    try:
        row = await svc.update_session_status(session_id, data.status)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.message,
        ) from exc
    turns = await svc.list_turns(session_id)
    return session_to_response(row, turns=turns)


# =============================================================================
# CHAT STREAMING ENDPOINT
# =============================================================================


@router.post("/chat")
async def chat(
    request: Request,
    body: ChatRequest,
    db: DbSession,
    _agent: CurrentAgentContext,
) -> Any:
    """Stream LLM response tokens for the given session and user message.

    Saves the user turn to the session, calls the configured LLM with the
    full conversation history, and streams each response token as a
    Server-Sent Event.

    SSE event types
    ---------------
    ``token``
        One chunk of response text from the LLM.
    ``done``
        Emitted after the last token; the ``data`` field contains the full
        concatenated assistant response.
    ``error``
        Emitted if the LLM call fails.
    """
    from sse_starlette import EventSourceResponse

    svc = get_prompt_service(db)

    # Validate session exists.
    try:
        session_row = await svc.get_session(body.session_id)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.message,
        ) from exc

    # Compute next turn_index.
    existing_turns = await svc.list_turns(body.session_id)
    user_turn_index = len(existing_turns)

    # Save user turn.
    await svc.create_turn(
        body.session_id,
        role="user",
        content=body.message,
        turn_index=user_turn_index,
    )

    # Re-fetch all turns (including the new user turn) for LLM context.
    all_turns = await svc.list_turns(body.session_id)

    # Build the messages array for the LLM.
    messages: list[dict[str, str]] = [
        {"role": t.role, "content": t.content}
        for t in all_turns
        if t.role in ("user", "assistant")
    ]

    system_prompt = session_row.system_prompt or _TASK_CREATE_SYSTEM_PROMPT
    model_key = session_row.model

    async def generate_tokens() -> AsyncGenerator[dict[str, Any]]:
        """Stream LLM tokens as SSE events, then persist the full response."""
        full_response: list[str] = []

        try:
            if _is_ollama_model(model_key):
                from openai import AsyncOpenAI

                from roboco.config import settings

                ollama_client = AsyncOpenAI(
                    base_url=settings.local_llm_base_url,
                    api_key="ollama",
                )
                system_msg: dict[str, str] = {
                    "role": "system",
                    "content": system_prompt,
                }
                all_messages = [system_msg, *messages]
                stream = await ollama_client.chat.completions.create(
                    model=model_key or "",
                    messages=all_messages,
                    stream=True,
                )
                async for chunk in stream:
                    if await request.is_disconnected():
                        break
                    delta = chunk.choices[0].delta.content
                    if delta:
                        full_response.append(delta)
                        yield {"event": "token", "data": delta}
            else:
                from anthropic import AsyncAnthropic

                from roboco.config import settings

                anthropic_client = AsyncAnthropic(api_key=settings.anthropic_api_key)
                model_id = _resolve_anthropic_model(model_key)

                async with anthropic_client.messages.stream(
                    model=model_id,
                    max_tokens=4096,
                    system=system_prompt,
                    messages=messages,
                ) as stream:
                    async for text in stream.text_stream:
                        if await request.is_disconnected():
                            break
                        full_response.append(text)
                        yield {"event": "token", "data": text}

        except Exception as exc:
            yield {"event": "error", "data": str(exc)}
            return

        full_text = "".join(full_response)
        if full_text:
            await svc.create_turn(
                body.session_id,
                role="assistant",
                content=full_text,
                turn_index=user_turn_index + 1,
            )

        yield {"event": "done", "data": full_text}

    return EventSourceResponse(generate_tokens(), ping=15)


# =============================================================================
# LAUNCH ENDPOINT
# =============================================================================

_REQUIRED_LAUNCH_FIELDS: frozenset[str] = frozenset(
    {
        "title",
        "description",
        "acceptance_criteria",
        "team",
        "task_type",
        "nature",
        "estimated_complexity",
    }
)


def _extract_json_from_content(content: str) -> dict[str, Any]:
    """Parse the structured JSON block from an assistant turn's content.

    Searches for the first ``json ... `` code fence.  Falls back to
    bare-JSON parsing if no fence is found.

    Raises:
        ValueError: If no valid JSON object can be extracted.
    """
    fence_match = re.search(
        r"```json\s*(\{.*?\})\s*```",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if fence_match:
        return dict(json.loads(fence_match.group(1)))

    bare_match = re.search(r"\{[^{}]*\}", content, re.DOTALL)
    if bare_match:
        return dict(json.loads(bare_match.group()))

    raise ValueError("No JSON object found in assistant turn content.")


@router.post(
    "/sessions/{session_id}/launch",
    response_model=LaunchResponse,
    status_code=status.HTTP_201_CREATED,
)
async def launch_session(
    session_id: UUID,
    data: LaunchRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> LaunchResponse:
    """Validate the latest assistant turn and create a task from its content.

    The latest assistant turn must contain a JSON block with all required
    TaskCreate fields.  On success the session transitions to LAUNCHED and
    the new task ID is returned.

    Raises:
        400: No assistant turns exist, JSON cannot be parsed, required
            fields are missing or invalid, or both/neither
            project_id/product_id are supplied.
        404: Session not found.
        422: Provided field values fail TaskCreate validation.
    """
    if (data.project_id is None) == (data.product_id is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=("Exactly one of project_id or product_id must be provided."),
        )

    prompt_svc = get_prompt_service(db)

    try:
        await prompt_svc.get_session(session_id)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.message,
        ) from exc

    turns = await prompt_svc.list_turns(session_id)
    assistant_turns = [t for t in turns if t.role == "assistant"]
    if not assistant_turns:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No assistant turns found in this session. "
                "Send at least one message via POST /api/prompter/chat first."
            ),
        )
    latest_turn = assistant_turns[-1]

    try:
        payload = _extract_json_from_content(latest_turn.content)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Could not extract valid JSON from the latest assistant turn: {exc}"
            ),
        ) from exc

    missing_fields = _REQUIRED_LAUNCH_FIELDS - set(payload.keys())
    if missing_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "JSON payload is missing required TaskCreate fields: "
                f"{sorted(missing_fields)}"
            ),
        )

    try:
        team = Team(payload["team"])
        task_type = TaskType(payload["task_type"])
        nature = TaskNature(payload["nature"])
        complexity = Complexity(payload["estimated_complexity"])
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid enum value in task payload: {exc}",
        ) from exc

    acceptance_criteria = payload["acceptance_criteria"]
    if not isinstance(acceptance_criteria, list) or not acceptance_criteria:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="acceptance_criteria must be a non-empty list of strings.",
        )

    task_svc = get_task_service(db)
    create_req = TaskCreateRequest(
        title=str(payload["title"]),
        description=str(payload["description"]),
        acceptance_criteria=[str(c) for c in acceptance_criteria],
        team=team,
        created_by=agent.agent_id,
        task_type=task_type,
        nature=nature,
        estimated_complexity=complexity,
        project_id=data.project_id,
        product_id=data.product_id,
    )
    try:
        task_row = await task_svc.create(create_req)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.message,
        ) from exc

    updated_session = await prompt_svc.update_session_status(
        session_id, PromptSessionStatus.LAUNCHED
    )

    return LaunchResponse(
        task_id=UUID(str(task_row.id)),
        session_id=UUID(str(updated_session.id)),
        session_status=updated_session.status,
    )
