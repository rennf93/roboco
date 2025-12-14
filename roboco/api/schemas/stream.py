"""
Stream Processing API Schemas

Request/response models for stream processing endpoints.
"""

from uuid import UUID

from pydantic import BaseModel, Field


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
