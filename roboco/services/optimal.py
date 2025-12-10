"""
Optimal API Service

Knowledge base, RAG queries, and prompt optimization using piragi.
This service provides semantic search across code, documentation,
conversations, and journal entries.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID

import structlog
from piragi import AsyncRagi

from roboco.config import settings

logger = structlog.get_logger()


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


class OptimalService:
    """
    Service for knowledge base operations and RAG queries.

    Uses piragi with PostgreSQL/pgvector for vector storage.
    Manages multiple indexes for different content types:
    - Code: Repositories, functions, classes
    - Documentation: READMEs, API docs, guides
    - Conversations: Extracted messages from agent streams
    - Journals: Agent journal entries
    """

    def __init__(self) -> None:
        self._initialized = False
        self._indexes: dict[IndexType, AsyncRagi] = {}
        self._config = self._build_config()

    def _build_config(self) -> dict[str, Any]:
        """Build piragi configuration from settings."""
        return {
            "llm": {
                "model": "llama3.2",  # Local Ollama model
                "base_url": "http://localhost:11434/v1",
            },
            "embedding": {
                "model": settings.default_embedding_model,
            },
            "chunk": {
                "strategy": settings.rag_chunk_strategy,
                "size": settings.rag_chunk_size,
                "overlap": settings.rag_chunk_overlap,
            },
            "retrieval": {
                "use_hyde": settings.rag_use_hyde,
                "use_hybrid_search": settings.rag_use_hybrid_search,
                "use_cross_encoder": settings.rag_use_cross_encoder,
            },
            "auto_update": {
                "enabled": settings.rag_auto_update_enabled,
                "interval": settings.rag_auto_update_interval,
            },
        }

    async def initialize(self) -> None:
        """Initialize all knowledge base indexes."""
        if self._initialized:
            return

        logger.info("Initializing OptimalService with piragi")

        # Create indexes for each content type
        for index_type in IndexType:
            persist_dir = f"{settings.rag_persist_dir}/{index_type.value}"
            self._indexes[index_type] = AsyncRagi(
                [],  # Start with no sources, we'll add dynamically
                persist_dir=persist_dir,
                config=self._config,
                store=settings.rag_store_url,
            )
            logger.info(
                f"Initialized {index_type.value} index", persist_dir=persist_dir
            )

        self._initialized = True
        logger.info("OptimalService initialization complete")

    async def close(self) -> None:
        """Cleanup resources."""
        # piragi handles cleanup automatically
        self._initialized = False
        self._indexes.clear()
        logger.info("OptimalService closed")

    def _get_index(self, index_type: IndexType) -> AsyncRagi:
        """Get the index for a content type."""
        if not self._initialized:
            raise RuntimeError(
                "OptimalService not initialized. Call initialize() first."
            )
        return self._indexes[index_type]

    # =========================================================================
    # INDEXING OPERATIONS
    # =========================================================================

    async def index_code(
        self,
        sources: list[str],
        project: str | None = None,
    ) -> int:
        """
        Index code files/directories.

        Args:
            sources: List of file paths, directories, or glob patterns
            project: Optional project identifier for filtering

        Returns:
            Number of documents indexed
        """
        index = self._get_index(IndexType.CODE)
        await index.add(sources)
        count = await index.count()
        logger.info("Indexed code", sources=sources, project=project, count=count)
        return count

    async def index_documentation(
        self,
        sources: list[str],
        project: str | None = None,
    ) -> int:
        """
        Index documentation files (markdown, text, etc.).

        Args:
            sources: List of file paths, directories, URLs, or glob patterns
            project: Optional project identifier for filtering

        Returns:
            Number of documents indexed
        """
        index = self._get_index(IndexType.DOCUMENTATION)
        await index.add(sources)
        count = await index.count()
        logger.info(
            "Indexed documentation", sources=sources, project=project, count=count
        )
        return count

    async def index_conversation(
        self,
        content: str,
        channel_id: UUID,
        session_id: UUID,
        agent_id: UUID,
        task_id: UUID | None = None,
        message_type: str | None = None,
    ) -> None:
        """
        Index a conversation message.

        Called by the transcription pipeline when messages are extracted.

        Args:
            content: Message content
            channel_id: Channel where message was posted
            session_id: Session ID
            agent_id: Agent who posted the message
            task_id: Related task if any
            message_type: Type of message (reasoning, dialogue, etc.)
        """
        # For conversations, we write to a temporary file and index it
        # This is a workaround since piragi expects file sources
        # In production, we'd extend piragi with a custom document loader
        import tempfile
        from pathlib import Path

        index = self._get_index(IndexType.CONVERSATIONS)

        # Create metadata-rich content
        enriched_content = f"""
Channel: {channel_id}
Session: {session_id}
Agent: {agent_id}
Task: {task_id or "None"}
Type: {message_type or "unknown"}

{content}
"""
        # Write to temp file and index
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(enriched_content)
            temp_path = f.name

        try:
            await index.add([temp_path])
            logger.debug(
                "Indexed conversation",
                channel_id=str(channel_id),
                agent_id=str(agent_id),
            )
        finally:
            Path(temp_path).unlink(missing_ok=True)

    async def index_journal_entry(
        self,
        entry_id: UUID,
        agent_id: UUID,
        content: str,
        entry_type: str,
        task_id: UUID | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """
        Index a journal entry.

        Called by the Journal API when entries are created.

        Args:
            entry_id: Journal entry ID
            agent_id: Agent who owns the journal
            content: Entry content
            entry_type: Type of entry (reflection, decision, learning, etc.)
            task_id: Related task if any
            tags: Entry tags
        """
        import tempfile
        from pathlib import Path

        index = self._get_index(IndexType.JOURNALS)

        # Create metadata-rich content
        enriched_content = f"""
Entry ID: {entry_id}
Agent: {agent_id}
Type: {entry_type}
Task: {task_id or "None"}
Tags: {", ".join(tags or [])}

{content}
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(enriched_content)
            temp_path = f.name

        try:
            await index.add([temp_path])
            logger.debug(
                "Indexed journal entry",
                entry_id=str(entry_id),
                agent_id=str(agent_id),
            )
        finally:
            Path(temp_path).unlink(missing_ok=True)

    # =========================================================================
    # SEARCH OPERATIONS
    # =========================================================================

    async def search(
        self,
        query: str,
        context: QueryContext | None = None,
        top_k: int = 5,
    ) -> list[SearchResult]:
        """
        Semantic search across knowledge base.

        Args:
            query: Natural language query
            context: Optional context for filtering results
            top_k: Number of results per index type

        Returns:
            List of search results sorted by relevance
        """
        if not self._initialized:
            raise RuntimeError("OptimalService not initialized")

        results: list[SearchResult] = []
        index_types = (
            context.index_types if context and context.index_types else list(IndexType)
        )

        for index_type in index_types:
            index = self._indexes[index_type]
            try:
                chunks = await index.retrieve(query, top_k=top_k)
                for chunk in chunks:
                    results.append(
                        SearchResult(
                            content=chunk.chunk,
                            source=chunk.source,
                            score=chunk.score,
                            index_type=index_type,
                            metadata={},
                        )
                    )
            except Exception as e:
                logger.warning(
                    "Search failed for index",
                    index_type=index_type.value,
                    error=str(e),
                )

        # Sort by score descending
        results.sort(key=lambda r: r.score, reverse=True)
        return results[: top_k * len(index_types)]

    async def query(
        self,
        query: str,
        context: QueryContext | None = None,
        top_k: int = 5,
    ) -> RAGResponse:
        """
        Query the knowledge base with RAG.

        Retrieves relevant context and generates an answer.

        Args:
            query: Natural language question
            context: Optional context for filtering
            top_k: Number of context chunks to use

        Returns:
            RAGResponse with answer and citations
        """
        if not self._initialized:
            raise RuntimeError("OptimalService not initialized")

        # Collect context from all relevant indexes
        all_chunks: list[SearchResult] = []
        index_types = (
            context.index_types if context and context.index_types else list(IndexType)
        )

        for index_type in index_types:
            index = self._indexes[index_type]
            try:
                answer = await index.ask(query, top_k=top_k)
                # Extract citations
                for cite in answer.citations:
                    all_chunks.append(
                        SearchResult(
                            content=cite.chunk if hasattr(cite, "chunk") else str(cite),
                            source=cite.source
                            if hasattr(cite, "source")
                            else "unknown",
                            score=cite.score if hasattr(cite, "score") else 0.0,
                            index_type=index_type,
                        )
                    )
                # Return the first valid answer
                return RAGResponse(
                    answer=answer.text,
                    citations=all_chunks,
                    query=query,
                    context_used=len(all_chunks),
                )
            except Exception as e:
                logger.warning(
                    "RAG query failed for index",
                    index_type=index_type.value,
                    error=str(e),
                )

        # If no index could answer, return empty response
        return RAGResponse(
            answer="I couldn't find relevant information to answer your question.",
            citations=[],
            query=query,
            context_used=0,
        )

    # =========================================================================
    # UTILITY OPERATIONS
    # =========================================================================

    async def get_stats(self) -> dict[str, Any]:
        """Get statistics about all indexes."""
        if not self._initialized:
            return {"initialized": False}

        stats: dict[str, Any] = {"initialized": True, "indexes": {}}
        for index_type, index in self._indexes.items():
            try:
                count = await index.count()
                stats["indexes"][index_type.value] = {"document_count": count}
            except Exception as e:
                stats["indexes"][index_type.value] = {"error": str(e)}

        return stats

    async def clear_index(self, index_type: IndexType) -> None:
        """Clear a specific index."""
        if not self._initialized:
            raise RuntimeError("OptimalService not initialized")

        index = self._indexes[index_type]
        await index.clear()
        logger.info("Cleared index", index_type=index_type.value)

    async def refresh_index(self, index_type: IndexType, sources: list[str]) -> None:
        """Refresh an index with new sources."""
        if not self._initialized:
            raise RuntimeError("OptimalService not initialized")

        index = self._indexes[index_type]
        for source in sources:
            await index.refresh(source)
        logger.info("Refreshed index", index_type=index_type.value, sources=sources)


# Global service instance
_optimal_service: OptimalService | None = None


async def get_optimal_service() -> OptimalService:
    """Get or create the OptimalService instance."""
    global _optimal_service
    if _optimal_service is None:
        _optimal_service = OptimalService()
        await _optimal_service.initialize()
    return _optimal_service


async def close_optimal_service() -> None:
    """Close the OptimalService instance."""
    global _optimal_service
    if _optimal_service is not None:
        await _optimal_service.close()
        _optimal_service = None
