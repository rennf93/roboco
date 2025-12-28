"""
Base Index Plugin

Abstract base class for all knowledge index plugins.
Each plugin handles a specific content type (code, docs, errors, standards, etc.)
and implements specialized chunking, metadata handling, and search strategies.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, cast

import structlog
from piragi import AsyncRagi
from piragi.types import Document

from roboco.config import settings
from roboco.models.optimal import IndexType, SearchResult

logger = structlog.get_logger()


@dataclass
class IndexConfig:
    """Configuration for an index plugin."""

    persist_dir: str
    store_url: str | None = None
    chunk_strategy: str = "semantic"
    chunk_size: int = 512
    chunk_overlap: int = 50
    use_hyde: bool = True
    use_hybrid_search: bool = True
    use_cross_encoder: bool = False
    embedding_model: str = "all-MiniLM-L6-v2"
    llm_model: str = "llama3.2"
    llm_base_url: str = "http://localhost:11434/v1"

    @classmethod
    def from_settings(cls, index_type: IndexType) -> "IndexConfig":
        """Create config from application settings."""
        return cls(
            persist_dir=f"{settings.rag_persist_dir}/{index_type.value}",
            store_url=settings.rag_store_url,
            chunk_strategy=settings.rag_chunk_strategy,
            chunk_size=settings.rag_chunk_size,
            chunk_overlap=settings.rag_chunk_overlap,
            use_hyde=settings.rag_use_hyde,
            use_hybrid_search=settings.rag_use_hybrid_search,
            use_cross_encoder=settings.rag_use_cross_encoder,
            embedding_model=settings.default_embedding_model,
        )


@dataclass
class ChunkResult:
    """Result of chunking a document."""

    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class IngestResult:
    """Result of ingesting a document."""

    doc_id: str
    chunk_count: int
    success: bool
    error: str | None = None


class BaseIndexPlugin(ABC):
    """
    Abstract base class for index plugins.

    Each plugin handles a specific content type and implements:
    - Specialized metadata extraction
    - Custom chunking strategies (optional)
    - Content-specific search filtering

    Subclasses MUST implement:
    - index_type property
    - prepare_metadata() method
    - build_source_uri() method

    Subclasses CAN override:
    - chunk() for custom chunking
    - search() for custom search behavior
    - validate_content() for content validation
    """

    def __init__(self, config: IndexConfig | None = None) -> None:
        """Initialize the plugin with optional config override."""
        self._config = config
        self._ragi: AsyncRagi | None = None
        self._initialized = False

    @property
    @abstractmethod
    def index_type(self) -> IndexType:
        """The index type this plugin handles."""
        ...

    @abstractmethod
    def prepare_metadata(self, content: str, **kwargs: Any) -> dict[str, Any]:
        """
        Prepare metadata for a document.

        Args:
            content: The document content
            **kwargs: Additional context-specific arguments

        Returns:
            Metadata dict to be stored with chunks
        """
        ...

    @abstractmethod
    def build_source_uri(self, doc_id: str | None = None, **kwargs: Any) -> str:
        """
        Build a source URI for the document.

        Args:
            doc_id: Optional document ID
            **kwargs: Additional context for URI building

        Returns:
            A source URI string (e.g., "roboco://errors/err-001")
        """
        ...

    @property
    def config(self) -> IndexConfig:
        """Get the index configuration."""
        if self._config is None:
            self._config = IndexConfig.from_settings(self.index_type)
        return self._config

    def _build_piragi_config(self) -> dict[str, Any]:
        """Build piragi configuration dict."""
        return {
            "llm": {
                "model": self.config.llm_model,
                "base_url": self.config.llm_base_url,
            },
            "embedding": {
                "model": self.config.embedding_model,
            },
            "chunk": {
                "strategy": self.config.chunk_strategy,
                "size": self.config.chunk_size,
                "overlap": self.config.chunk_overlap,
            },
            "retrieval": {
                "use_hyde": self.config.use_hyde,
                "use_hybrid_search": self.config.use_hybrid_search,
                "use_cross_encoder": self.config.use_cross_encoder,
            },
        }

    def _build_piragi_config_no_embed(self) -> dict[str, Any]:
        """
        Build piragi config with dummy embedding URL.

        This prevents piragi from loading its own SentenceTransformer model
        during initialization. We'll replace the embedder with our shared
        instance immediately after creation.
        """
        config = self._build_piragi_config()
        # Set a dummy base_url to prevent local model loading
        # EmbeddingGenerator checks: if base_url is not None, skip SentenceTransformer
        config["embedding"]["base_url"] = "http://dummy-prevents-model-load"
        config["embedding"]["api_key"] = "not-needed"
        return config

    async def initialize(self) -> None:
        """Initialize the index backend."""
        if self._initialized:
            return

        logger.info(
            f"Initializing {self.index_type.value} index plugin",
            persist_dir=self.config.persist_dir,
        )

        # Get shared embedder FIRST (one-time load for all plugins)
        from roboco.services.optimal_brain.shared_embedder import get_shared_embedder

        shared_embedder = await get_shared_embedder(
            model=self.config.embedding_model,
        )

        # Create store with correct vector dimension for embedding model
        # Piragi's factory defaults to 768 for PostgresStore, but we use
        # all-MiniLM-L6-v2 which produces 384-dimensional embeddings
        store = self._create_store_with_dimension()

        # Use config with dummy embedding URL to prevent model loading
        # Piragi's EmbeddingGenerator skips SentenceTransformer if base_url is set
        self._ragi = AsyncRagi(
            [],
            persist_dir=self.config.persist_dir,
            config=self._build_piragi_config_no_embed(),
            store=store,
        )

        # Replace AsyncRagi's dummy embedder with shared instance
        # This is the key optimization: one model load for all 9 plugins
        self._ragi._sync.embedder = shared_embedder

        self._initialized = True
        logger.info(f"{self.index_type.value} index plugin initialized")

    def _create_store_with_dimension(self) -> Any:
        """
        Create vector store with correct dimension for embedding model.

        Piragi's factory defaults to 768 for PostgresStore, but doesn't
        infer dimension from the embedding model. This method fixes that
        by creating PostgresStore with the correct dimension.
        """
        from roboco.config import settings

        store_url = self.config.store_url
        if not store_url:
            # Use default LanceStore (handles dimension correctly)
            return None

        # For PostgreSQL, create store with correct dimension
        if store_url.startswith("postgres://") or store_url.startswith("postgresql://"):
            from piragi.stores.postgres import PostgresStore

            # Get dimension from settings (384 for all-MiniLM-L6-v2)
            vector_dimension = settings.embedding_dimensions

            logger.debug(
                "Creating PostgresStore with correct dimension",
                vector_dimension=vector_dimension,
                embedding_model=self.config.embedding_model,
            )

            return PostgresStore(
                connection_string=store_url,
                table_name=f"chunks_{self.index_type.value}",
                vector_dimension=vector_dimension,
            )

        # For other stores, let piragi handle it
        return store_url

    async def close(self) -> None:
        """Cleanup resources."""
        self._ragi = None
        self._initialized = False
        logger.info(f"{self.index_type.value} index plugin closed")

    @property
    def ragi(self) -> AsyncRagi:
        """Get the underlying AsyncRagi instance."""
        if not self._initialized or self._ragi is None:
            msg = f"{self.index_type.value} index not initialized."
            raise RuntimeError(msg)
        return self._ragi

    def validate_content(
        self,
        content: str,
        **_kwargs: Any,
    ) -> tuple[bool, str | None]:
        """
        Validate content before ingestion.

        Override in subclasses for content-specific validation.

        Args:
            content: The content to validate
            **kwargs: Additional validation context

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not content or not content.strip():
            return False, "Content cannot be empty"
        return True, None

    async def ingest(
        self,
        content: str,
        doc_id: str | None = None,
        **kwargs: Any,
    ) -> IngestResult:
        """
        Ingest a document into the index.

        Args:
            content: Document content
            doc_id: Optional unique document ID
            **kwargs: Additional context for metadata/URI building

        Returns:
            IngestResult with ingestion details
        """
        import asyncio

        # Validate content
        is_valid, error = self.validate_content(content, **kwargs)
        if not is_valid:
            return IngestResult(
                doc_id=doc_id or "unknown",
                chunk_count=0,
                success=False,
                error=error,
            )

        # Prepare metadata and source URI
        metadata = self.prepare_metadata(content, **kwargs)
        source = self.build_source_uri(doc_id, **kwargs)

        # Create document
        doc = Document(
            content=content,
            source=source,
            metadata=metadata,
        )

        # Access piragi's sync internals for direct processing
        ragi_sync = self.ragi._sync

        chunk_count = 0

        def _process_and_store() -> int:
            nonlocal chunk_count

            # Chunk the document
            chunks = ragi_sync.chunker.chunk_document(doc)

            # Add metadata to each chunk
            for chunk in chunks:
                chunk.metadata = {**chunk.metadata, **metadata}

            chunk_count = len(chunks)

            # Generate embeddings
            chunks_with_embeddings = ragi_sync.embedder.embed_chunks(chunks)

            # Store with retry on transaction errors
            max_retries = 2
            for attempt in range(max_retries):
                try:
                    ragi_sync.store.add_chunks(chunks_with_embeddings)
                    return chunk_count
                except Exception as e:
                    if "transaction is aborted" in str(e) and attempt < max_retries - 1:
                        logger.warning(
                            "Retrying store after transaction error",
                            index_type=self.index_type.value,
                            attempt=attempt + 1,
                        )
                        # Force connection reset - rollback alone isn't enough
                        if hasattr(ragi_sync.store, "_conn") and ragi_sync.store._conn:
                            try:
                                ragi_sync.store._conn.close()
                                # Force reconnection by reinitializing schema
                                ragi_sync.store._init_schema()
                            except Exception as reset_err:
                                logger.warning(
                                    "Failed to reset connection",
                                    error=str(reset_err),
                                )
                        continue
                    raise

            return chunk_count

        try:
            await asyncio.to_thread(_process_and_store)
            logger.debug(
                "Ingested document",
                index_type=self.index_type.value,
                doc_id=doc_id,
                chunk_count=chunk_count,
            )
            return IngestResult(
                doc_id=doc_id or source,
                chunk_count=chunk_count,
                success=True,
            )
        except Exception as e:
            logger.error(
                "Failed to ingest document",
                index_type=self.index_type.value,
                doc_id=doc_id,
                error=str(e),
            )
            return IngestResult(
                doc_id=doc_id or "unknown",
                chunk_count=0,
                success=False,
                error=str(e),
            )

    async def search(
        self,
        query: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """
        Search the index.

        Args:
            query: Natural language search query
            top_k: Number of results to return
            filters: Optional metadata filters

        Returns:
            List of search results sorted by relevance
        """
        try:
            chunks = await self.ragi.retrieve(query, top_k=top_k)
            results = []
            for chunk in chunks:
                # Apply filters if provided
                if filters:
                    chunk_meta = chunk.metadata or {}
                    if not all(chunk_meta.get(k) == v for k, v in filters.items()):
                        continue

                results.append(
                    SearchResult(
                        content=chunk.chunk,
                        source=chunk.source,
                        score=chunk.score,
                        index_type=self.index_type,
                        metadata=chunk.metadata or {},
                    )
                )

            return results
        except Exception as e:
            logger.warning(
                "Search failed",
                index_type=self.index_type.value,
                error=str(e),
            )
            return []

    async def ask(
        self,
        query: str,
        top_k: int = 5,
    ) -> tuple[str, list[SearchResult]]:
        """
        RAG query - get an answer with citations.

        Args:
            query: Natural language question
            top_k: Number of context chunks to use

        Returns:
            Tuple of (answer, citations)
        """
        try:
            answer = await self.ragi.ask(query, top_k=top_k)
            citations = []
            for cite in answer.citations:
                citations.append(
                    SearchResult(
                        content=cite.chunk if hasattr(cite, "chunk") else str(cite),
                        source=cite.source if hasattr(cite, "source") else "unknown",
                        score=cite.score if hasattr(cite, "score") else 0.0,
                        index_type=self.index_type,
                        metadata={},
                    )
                )
            return answer.text, citations
        except Exception as e:
            logger.warning(
                "RAG query failed",
                index_type=self.index_type.value,
                error=str(e),
            )
            return "", []

    async def count(self) -> int:
        """Get the number of documents in the index."""
        try:
            return cast("int", await self.ragi.count())
        except Exception:
            return 0

    async def clear(self) -> None:
        """Clear all documents from the index."""
        await self.ragi.clear()
        logger.info(f"Cleared {self.index_type.value} index")

    async def list_documents(
        self, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        """
        List documents in the index.

        Returns list of documents with id, source, indexed_at, and metadata.
        """
        try:
            # Use piragi's list method if available
            if hasattr(self.ragi, "list"):
                docs = await self.ragi.list(limit=limit, offset=offset)
                return [
                    {
                        "id": str(doc.get("id", "")),
                        "source": doc.get("source", ""),
                        "indexed_at": doc.get("indexed_at", ""),
                        "metadata": doc.get("metadata", {}),
                    }
                    for doc in docs
                ]
            # Fallback: return empty list
            return []
        except Exception as e:
            logger.warning(
                f"Failed to list documents in {self.index_type.value}: {e}"
            )
            return []

    async def add_sources(self, sources: list[str]) -> int:
        """
        Add file/directory sources to the index.

        Used for indexing code and documentation from disk.

        Args:
            sources: List of file paths, directories, or glob patterns

        Returns:
            Number of documents indexed
        """
        import hashlib
        from pathlib import Path

        from roboco.db import get_db_context
        from roboco.db.tables import IndexedDocumentTable

        await self.ragi.add(sources)

        # Track indexed documents in database
        async with get_db_context() as db:
            for source in sources:
                source_path = Path(source)

                # Handle glob patterns and directories
                if "*" in source or source_path.is_dir():
                    if source_path.is_dir():
                        files = list(source_path.rglob("*"))
                    else:
                        files = list(Path(".").glob(source))
                else:
                    files = [source_path] if source_path.exists() else []

                for file_path in files:
                    if not file_path.is_file():
                        continue

                    # Generate hash for dedup
                    source_str = str(file_path.absolute())
                    source_hash = hashlib.sha256(source_str.encode()).hexdigest()

                    # Extract title from filename or first line
                    title = file_path.stem.replace("-", " ").replace("_", " ").title()

                    # Get preview (first 500 chars)
                    preview = None
                    try:
                        content = file_path.read_text(errors="ignore")[:500]
                        preview = content.strip()
                    except Exception:
                        pass

                    # Upsert document record
                    from sqlalchemy import select

                    existing = await db.execute(
                        select(IndexedDocumentTable).where(
                            IndexedDocumentTable.index_type == self.index_type.value,
                            IndexedDocumentTable.source_hash == source_hash,
                        )
                    )
                    doc = existing.scalar_one_or_none()

                    if doc:
                        doc.title = title
                        doc.preview = preview
                    else:
                        doc = IndexedDocumentTable(
                            index_type=self.index_type.value,
                            source=source_str,
                            source_hash=source_hash,
                            title=title,
                            preview=preview,
                            chunk_count=0,  # Could be calculated later
                        )
                        db.add(doc)

            await db.commit()

        return await self.count()
