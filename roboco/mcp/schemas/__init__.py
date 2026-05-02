"""
MCP Input Schemas

Pydantic models for MCP tool input validation. After Phase 4 T9 deleted
the task/journal/notify/a2a/message/project MCP servers, only the docs
server remains as a consumer — so only `WriteDocInput` is exported.
"""

from pydantic import BaseModel, Field


class WriteDocInput(BaseModel):
    """Input for writing a documentation file."""

    task_id: str = Field(..., description="Task UUID this documentation belongs to")
    filename: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Filename (e.g., 'endpoints.md') - no path separators",
    )
    doc_type: str = Field(
        ...,
        description="Type: api, qa, guide, readme, changelog, architecture, design",
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Human-readable title",
    )
    content: str = Field(..., min_length=1, description="Full markdown content")


__all__ = ["WriteDocInput"]
