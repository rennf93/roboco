"""
Optimal API Service

Knowledge base, RAG queries, and prompt optimization using piragi.
This service provides semantic search across code, documentation,
conversations, and journal entries.

Document ingestion:
    For in-memory content (conversations, journals), we use piragi's
    internal components directly (chunker, embedder, store) to avoid
    temp files and preserve structured metadata.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, cast
from uuid import UUID

import structlog
from piragi import AsyncRagi
from piragi.types import Document

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
    # DOCUMENT INGESTION (direct in-memory, no temp files)
    # =========================================================================

    async def ingest_document(
        self,
        index_type: IndexType,
        content: str,
        metadata: dict[str, Any],
        doc_id: str | None = None,
    ) -> None:
        """
        Ingest a document with metadata directly into an index.

        Uses piragi's internal components (chunker, embedder, store) to
        add content directly without temp files. Metadata is preserved
        in the chunk metadata for filtering during retrieval.

        Args:
            index_type: Which index to add to
            content: The document content
            metadata: Structured metadata dict (preserved in chunk metadata)
            doc_id: Optional unique ID for the document
        """
        import asyncio

        index = self._get_index(index_type)

        # Access piragi's internal sync Ragi instance
        ragi = index._sync

        # Create a Document object with metadata
        source = f"roboco://{index_type.value}/{doc_id or 'unknown'}"
        doc = Document(
            content=content,
            source=source,
            metadata=metadata,
        )

        # Use piragi's internal pipeline: chunk -> embed -> store
        # Run in thread since piragi internals are synchronous
        def _process_and_store() -> None:
            # Chunk the document
            chunks = ragi.chunker.chunk_document(doc)

            # Add metadata to each chunk
            for chunk in chunks:
                chunk.metadata = {**chunk.metadata, **metadata}

            # Generate embeddings
            chunks_with_embeddings = ragi.embedder.embed_chunks(chunks)

            # Store directly in vector database
            ragi.store.add_chunks(chunks_with_embeddings)

        await asyncio.to_thread(_process_and_store)

        logger.debug(
            "Ingested document",
            index_type=index_type.value,
            doc_id=doc_id,
            metadata_keys=list(metadata.keys()),
        )

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
        count = cast("int", await index.count())
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
        count = cast("int", await index.count())
        logger.info(
            "Indexed documentation", sources=sources, project=project, count=count
        )
        return count

    async def index_conversation(self, params: IndexConversationParams) -> None:
        """
        Index a conversation message.

        Called by the transcription pipeline when messages are extracted.

        Args:
            params: IndexConversationParams containing content, channel_id,
                    session_id, agent_id, task_id, and message_type
        """
        metadata = {
            "type": "conversation",
            "channel_id": str(params.channel_id),
            "session_id": str(params.session_id),
            "agent_id": str(params.agent_id),
            "task_id": str(params.task_id) if params.task_id else "none",
            "message_type": params.message_type or "unknown",
        }

        await self.ingest_document(
            index_type=IndexType.CONVERSATIONS,
            content=params.content,
            metadata=metadata,
            doc_id=f"{params.session_id}-{params.agent_id}"[:50],
        )

        logger.debug(
            "Indexed conversation",
            channel_id=str(params.channel_id),
            agent_id=str(params.agent_id),
        )

    async def index_journal_entry(self, params: IndexJournalEntryParams) -> None:
        """
        Index a journal entry.

        Called by the Journal API when entries are created.

        Args:
            params: IndexJournalEntryParams containing entry_id, agent_id,
                    content, entry_type, task_id, and tags
        """
        metadata = {
            "type": "journal",
            "entry_id": str(params.entry_id),
            "agent_id": str(params.agent_id),
            "entry_type": params.entry_type,
            "task_id": str(params.task_id) if params.task_id else "none",
            "tags": params.tags or [],
        }

        await self.ingest_document(
            index_type=IndexType.JOURNALS,
            content=params.content,
            metadata=metadata,
            doc_id=str(params.entry_id)[:50],
        )

        logger.debug(
            "Indexed journal entry",
            entry_id=str(params.entry_id),
            agent_id=str(params.agent_id),
        )

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


class _OptimalServiceHolder:
    """Holder for singleton OptimalService instance."""

    instance: OptimalService | None = None


async def get_optimal_service() -> OptimalService:
    """Get or create the OptimalService instance."""
    if _OptimalServiceHolder.instance is None:
        _OptimalServiceHolder.instance = OptimalService()
        await _OptimalServiceHolder.instance.initialize()
    return _OptimalServiceHolder.instance


async def close_optimal_service() -> None:
    """Close the OptimalService instance."""
    if _OptimalServiceHolder.instance is not None:
        await _OptimalServiceHolder.instance.close()
        _OptimalServiceHolder.instance = None
