"""
Optimal (RAG) Models

Data classes for knowledge base and RAG operations.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID


class IndexType(str, Enum):
    """Types of content indexes."""

    CODE = "code"
    DOCUMENTATION = "documentation"
    CONVERSATIONS = "conversations"
    JOURNALS = "journals"


@dataclass
class SearchResult:
    """A single search result from the knowledge base."""

    content: str
    source: str
    score: float
    index_type: IndexType
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RAGResponse:
    """Response from a RAG query."""

    answer: str
    citations: list[SearchResult]
    query: str
    context_used: int  # Number of context chunks used


@dataclass
class QueryContext:
    """Context for filtering RAG queries."""

    project: str | None = None
    task_id: UUID | None = None
    agent_id: UUID | None = None
    channel_id: UUID | None = None
    index_types: list[IndexType] | None = None


@dataclass
class IndexConversationParams:
    """Parameters for indexing a conversation message."""

    content: str
    channel_id: UUID
    session_id: UUID
    agent_id: UUID
    task_id: UUID | None = None
    message_type: str | None = None


@dataclass
class IndexJournalEntryParams:
    """Parameters for indexing a journal entry."""

    entry_id: UUID
    agent_id: UUID
    content: str
    entry_type: str
    task_id: UUID | None = None
    tags: list[str] | None = None
