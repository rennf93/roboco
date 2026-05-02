"""Request schemas for /api/v2/do/* content tools."""

from uuid import UUID

from pydantic import BaseModel, Field


class CommitRequest(BaseModel):
    message: str = Field(..., min_length=1)
    files: list[str] | None = None


class NoteRequest(BaseModel):
    text: str = Field(..., min_length=1)
    scope: str = "note"
    task_id: UUID | None = None


class SayRequest(BaseModel):
    channel: str
    text: str = Field(..., min_length=1)
    task_id: UUID | None = None


class DmRequest(BaseModel):
    recipient: str  # agent slug
    text: str = Field(..., min_length=1)
    task_id: UUID | None = None
    skill: str | None = None


class EvidenceRequest(BaseModel):
    task_id: UUID
