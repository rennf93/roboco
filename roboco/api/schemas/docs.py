"""
Documentation API Schemas

Request/response models for documentation file management endpoints.
"""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

# =============================================================================
# ENUMS
# =============================================================================


class DocType(StrEnum):
    """Documentation file types determining folder placement."""

    API = "api"  # /docs/{team}/api/
    QA = "qa"  # /docs/{team}/qa/
    GUIDE = "guide"  # /docs/{team}/guides/
    README = "readme"  # /docs/{team}/
    CHANGELOG = "changelog"  # /docs/{team}/
    ARCHITECTURE = "architecture"  # /docs/{team}/architecture/
    DESIGN = "design"  # /docs/{team}/design/ (UX/UI)


# =============================================================================
# REQUEST MODELS
# =============================================================================


class WriteDocRequest(BaseModel):
    """Request to write a documentation file."""

    task_id: UUID = Field(..., description="Task this documentation belongs to")
    filename: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Filename (e.g., 'endpoints.md') - no path",
    )
    doc_type: DocType = Field(..., description="Type determines subfolder placement")
    title: str = Field(
        ..., min_length=1, max_length=500, description="Human-readable title"
    )
    content: str = Field(..., min_length=1, description="Full markdown content")


class ReadDocRequest(BaseModel):
    """Request to read a documentation file."""

    path: str = Field(
        ..., description="Normalized path (e.g., 'backend/api/endpoints.md')"
    )


class UpdateDocRequest(BaseModel):
    """Request to update a documentation file."""

    path: str = Field(
        ..., description="Normalized path (e.g., 'backend/api/endpoints.md')"
    )
    title: str | None = Field(default=None, max_length=500, description="New title")
    content: str | None = Field(default=None, description="New content")


class DeleteDocRequest(BaseModel):
    """Request to delete a documentation file."""

    path: str = Field(
        ..., description="Normalized path (e.g., 'backend/api/endpoints.md')"
    )


# =============================================================================
# RESPONSE MODELS
# =============================================================================


class DocRefResponse(BaseModel):
    """Response model for a document reference."""

    path: str
    title: str
    doc_type: str
    version: str | None = None
    created_by: str | None = None
    created_at: str | None = None
    updated_by: str | None = None
    updated_at: str | None = None

    class Config:
        from_attributes = True


class WriteDocResponse(BaseModel):
    """Response after writing a documentation file."""

    status: str = Field(..., description="'created' or 'updated'")
    path: str = Field(..., description="Full path where doc was written")
    doc_ref: DocRefResponse = Field(..., description="The created document reference")


class ReadDocResponse(BaseModel):
    """Response containing documentation file content."""

    path: str
    content: str
    size_bytes: int


class ListDocsResponse(BaseModel):
    """Response listing documentation files."""

    documents: list[DocRefResponse]
    team: str
    count: int
