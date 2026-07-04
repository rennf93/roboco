"""
Journals API Schemas

Request/response models for agent journal endpoints.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ListEntriesParams(BaseModel):
    """Query parameters for listing journal entries."""

    entry_type: str | None = Field(None, description="Filter by entry type")
    task_id: UUID | None = Field(None, description="Filter by task")
    limit: int = Field(50, ge=1, le=100, description="Maximum entries to return")
    offset: int = Field(0, ge=0, description="Number of entries to skip")


class JournalResponse(BaseModel):
    """Journal response."""

    id: UUID
    agent_id: UUID
    total_entries: int
    last_entry_at: datetime | None
    latest_summary: str | None
    summary_updated_at: datetime | None
    entries_by_type: dict[str, int]
    created_at: datetime
    updated_at: datetime | None = None


class JournalEntryResponse(BaseModel):
    """Journal entry response."""

    id: UUID
    journal_id: UUID
    type: str
    title: str
    content: str
    task_id: UUID | None
    timestamp: datetime
    tags: list[str]
    sentiment: str | None
    is_private: bool
    created_at: datetime
    updated_at: datetime | None = None


class CreateEntryRequest(BaseModel):
    """Request to create a journal entry."""

    type: str = Field(
        ...,
        description="Entry type (task_reflection, decision_log, learning, etc.)",
    )
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1)
    task_id: UUID | None = None
    tags: list[str] = Field(default_factory=list)
    sentiment: str | None = None
    is_private: bool = False


class TaskReflectionRequest(BaseModel):
    """Request to create a task reflection entry."""

    task_id: UUID
    title: str = Field(..., min_length=1, max_length=200)
    what_done: str
    what_learned: str
    what_struggled: str
    next_steps: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class DecisionLogRequest(BaseModel):
    """Request to create a decision log entry."""

    title: str = Field(..., min_length=1, max_length=200)
    context: str
    options: list[dict[str, str]] = Field(..., min_length=2)
    chosen: str
    rationale: str
    consequences: list[str] = Field(default_factory=list)
    task_id: UUID | None = None
    tags: list[str] = Field(default_factory=list)


class LearningRequest(BaseModel):
    """Request to create a learning entry."""

    title: str = Field(..., min_length=1, max_length=200)
    what_learned: str
    how_applied: str | None = None
    source: str | None = None
    task_id: UUID | None = None
    tags: list[str] = Field(default_factory=list)


class StruggleRequest(BaseModel):
    """Request to create a struggle entry."""

    title: str = Field(..., min_length=1, max_length=200)
    what_struggled: str
    attempted_solutions: list[str] = Field(default_factory=list)
    resolution: str | None = None
    help_needed: str | None = None
    task_id: UUID | None = None
    tags: list[str] = Field(default_factory=list)


class GeneralEntryRequest(BaseModel):
    """Request to create a general journal entry."""

    title: str = Field(..., min_length=1, max_length=200)
    content: str
    task_id: UUID | None = None
    tags: list[str] = Field(default_factory=list)
    is_private: bool = False


class JournalStatsResponse(BaseModel):
    """Journal statistics response."""

    total_entries: int
    entries_by_type: dict[str, int]
    last_entry_at: datetime | None
    has_summary: bool


class GrowthMetricsResponse(BaseModel):
    """Growth metrics response."""

    total_reflections: int
    total_learnings: int
    total_struggles: int
    total_decisions: int
    struggle_resolution_rate: float
    learning_frequency: float
    sentiment_trend: str


class SearchEntriesRequest(BaseModel):
    """Request to search journal entries."""

    query: str = Field(..., min_length=1)
    top_k: int = Field(5, ge=1, le=20)
