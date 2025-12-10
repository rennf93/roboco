"""
Stream Processing Routes

API endpoints for processing agent LLM streams through
transcription and extraction pipelines.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from roboco.api.deps import CurrentAgentContext, CurrentAgentId, PermissionServiceDep
from roboco.models import MessageType
from roboco.models.message import RawStream

router = APIRouter()


# =============================================================================
# REQUEST/RESPONSE SCHEMAS
# =============================================================================


class StreamChunkRequest(BaseModel):
    """Request to process a stream chunk."""

    channel_id: UUID = Field(..., description="Target channel")
    session_id: UUID = Field(..., description="Current session")
    chunk: str = Field(..., description="Raw LLM output chunk")


class StreamCompleteRequest(BaseModel):
    """Request to mark a stream as complete."""

    session_id: UUID = Field(..., description="Session to complete")


class ExtractRequest(BaseModel):
    """Request to extract messages from content."""

    channel_id: UUID = Field(..., description="Target channel")
    session_id: UUID = Field(..., description="Current session")
    group_id: UUID = Field(..., description="Group within channel")
    content: str = Field(..., description="Content to extract from")
    task_id: UUID | None = Field(default=None, description="Related task")


class ExtractedMessageResponse(BaseModel):
    """Response for an extracted message."""

    id: UUID
    type: str
    content: str
    content_length: int
    confidence: float


class ExtractionResponse(BaseModel):
    """Response from extraction."""

    message_count: int
    messages: list[ExtractedMessageResponse]
    types_extracted: list[str]


class TranscriptionStatsResponse(BaseModel):
    """Response for transcription service stats."""

    active_agents: int
    total_buffers: int
    total_buffered_chars: int
    running: bool


# =============================================================================
# STREAM PROCESSING ROUTES
# =============================================================================


@router.post("/chunk", status_code=status.HTTP_202_ACCEPTED)
async def process_chunk(
    request: Request,
    body: StreamChunkRequest,
    agent_id: CurrentAgentId,
) -> dict[str, Any]:
    """
    Process a raw LLM stream chunk.

    Chunks are buffered until ready for extraction.
    Returns buffer status.
    """
    transcription = request.app.state.transcription
    if not transcription:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Transcription service not available",
        )

    # Create RawStream from request
    raw_stream = RawStream(
        agent_id=agent_id,
        channel_id=body.channel_id,
        connection_id=body.session_id,  # Use session as connection
        chunk=body.chunk,
    )

    # Process chunk
    buffer = await transcription.process_chunk(raw_stream)

    return {
        "status": "buffered",
        "ready_for_extraction": buffer is not None,
        "buffer_size": buffer.char_count if buffer else 0,
    }


@router.post("/complete", status_code=status.HTTP_200_OK)
async def complete_stream(
    request: Request,
    body: StreamCompleteRequest,
    agent_id: CurrentAgentId,
) -> dict[str, Any]:
    """
    Mark a stream as complete.

    Returns the final buffer content for extraction.
    """
    transcription = request.app.state.transcription
    if not transcription:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Transcription service not available",
        )

    buffer = await transcription.process_stream_complete(
        agent_id=agent_id,
        session_id=body.session_id,
    )

    if buffer:
        content = await transcription.flush_buffer(agent_id, body.session_id)
        return {
            "status": "completed",
            "content_length": len(content) if content else 0,
            "content": content,
        }

    return {
        "status": "no_content",
        "content_length": 0,
        "content": None,
    }


@router.post("/extract", response_model=ExtractionResponse)
async def extract_messages(
    request: Request,
    body: ExtractRequest,
    agent_id: CurrentAgentId,
) -> ExtractionResponse:
    """
    Extract structured messages from raw content.

    Returns list of extracted messages with their types.
    """
    extraction = request.app.state.extraction
    if not extraction:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Extraction service not available",
        )

    result = await extraction.process_buffer(
        content=body.content,
        agent_id=agent_id,
        channel_id=body.channel_id,
        session_id=body.session_id,
        group_id=body.group_id,
        task_id=body.task_id,
    )

    return ExtractionResponse(
        message_count=result.message_count,
        messages=[
            ExtractedMessageResponse(
                id=msg.id,
                type=msg.type.value if isinstance(msg.type, MessageType) else msg.type,
                content=msg.content,
                content_length=msg.content_length,
                confidence=msg.confidence,
            )
            for msg in result.messages
        ],
        types_extracted=[
            t.value if isinstance(t, MessageType) else t for t in result.types_extracted
        ],
    )


@router.get("/stats", response_model=TranscriptionStatsResponse)
async def get_transcription_stats(
    request: Request,
) -> TranscriptionStatsResponse:
    """
    Get transcription service statistics.
    """
    transcription = request.app.state.transcription
    if not transcription:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Transcription service not available",
        )

    stats = transcription.get_stats()
    return TranscriptionStatsResponse(**stats)


# =============================================================================
# PERMISSION CHECK ROUTES
# =============================================================================


@router.get("/permissions")
async def get_my_permissions(
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
) -> dict[str, Any]:
    """
    Get permission summary for the current agent.

    Returns accessible channels, allowed actions, etc.
    """
    return permissions.check_all(agent)


@router.get("/permissions/channel/{channel_name}")
async def check_channel_permission(
    channel_name: str,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
) -> dict[str, Any]:
    """
    Check permissions for a specific channel.
    """
    return {
        "channel": channel_name,
        "can_read": permissions.can_read_channel(agent, channel_name),
        "can_write": permissions.can_write_channel(agent, channel_name),
    }
