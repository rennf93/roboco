"""
Base Index Plugin

Abstract base class for all knowledge index plugins.
Each plugin handles a specific content type (code, docs, errors, standards, etc.)
and implements specialized chunking, metadata handling, and search strategies.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, cast

import structlog
from piragi import AsyncRagi
from piragi.types import Citation, Document

from roboco.config import settings
from roboco.models.optimal import IndexType, SearchOutcome, SearchResult

logger = structlog.get_logger()


@dataclass
class IndexConfig:
    """Configuration for an index plugin."""

    persist_dir: str
    store_url: str | None = None
    chunk_strategy: str = "fixed"
    chunk_size: int = 512
    chunk_overlap: int = 50
    use_hyde: bool = True
    use_hybrid_search: bool = True
    use_cross_encoder: bool = False
    embedding_model: str = "embeddinggemma:300m"
    llm_model: str = "glm-4.7:cloud"
    llm_base_url: str = "http://roboco-ollama:11434/v1"

    @classmethod
    def from_settings(cls, index_type: IndexType) -> "IndexConfig":
        """Create config from application settings."""
        # Use per-index-type chunk sizes where available
        chunk_size = settings.rag_chunk_size
        if index_type == IndexType.DOCUMENTATION:
            chunk_size = settings.rag_chunk_size_docs
        elif index_type == IndexType.JOURNALS:
            chunk_size = settings.rag_chunk_size_journals

        return cls(
            persist_dir=f"{settings.rag_persist_dir}/{index_type.value}",
            store_url=settings.rag_store_url,
            chunk_strategy=settings.rag_chunk_strategy,
            chunk_size=chunk_size,
            chunk_overlap=settings.rag_chunk_overlap,
            use_hyde=settings.rag_use_hyde,
            use_hybrid_search=settings.rag_use_hybrid_search,
            use_cross_encoder=settings.rag_use_cross_encoder,
            embedding_model=settings.default_embedding_model,
            llm_model=settings.local_llm_model,
            llm_base_url=settings.local_llm_base_url,
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

    def _extract_from_think_tags(self, text: str) -> str:
        """Extract answer from LLM response, handling think tags."""
        # First try: get content outside think tags
        outside = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        outside = re.sub(r"</think>", "", outside).strip()
        if outside:
            return outside

        # If empty, extract content FROM inside think tags
        inside_match = re.search(r"<think>(.*?)</think>", text, flags=re.DOTALL)
        if inside_match:
            return inside_match.group(1).strip()

        return text.strip()

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

    async def _validate_embedding_dimensions(self, embedder: Any) -> None:
        """
        Validate that embedder produces expected dimensions.

        Catches dimension mismatches early (e.g., wrong model configured)
        instead of failing silently during vector search.
        """
        import asyncio

        from roboco.config import settings

        expected_dim = settings.embedding_dimensions

        try:
            # Use async method if available
            if hasattr(embedder, "aembed_query"):
                test_embedding = await embedder.aembed_query("dimension test")
            else:
                test_embedding = await asyncio.to_thread(
                    embedder.embed_query, "dimension test"
                )

            actual_dim = len(test_embedding)

            if actual_dim != expected_dim:
                raise RuntimeError(
                    f"Embedding dimension mismatch for {self.index_type.value}: "
                    f"model produces {actual_dim} dimensions, "
                    f"but settings.embedding_dimensions={expected_dim}. "
                    f"Update ROBOCO_EMBEDDING_DIMENSIONS or use correct model."
                )

            logger.debug(
                "Embedding dimension validated",
                index_type=self.index_type.value,
                dimensions=actual_dim,
            )
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning(
                "Could not validate embedding dimensions",
                index_type=self.index_type.value,
                error=str(e),
            )

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

        # Validate embedding dimensions match configuration
        # This catches mismatches early instead of failing silently during search
        await self._validate_embedding_dimensions(shared_embedder)

        # Create store with correct vector dimension for embedding model
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

            # Get dimension from settings
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
            raw_chunks = ragi_sync.chunker.chunk_document(doc)

            # Filter garbage chunks at index time (not search time!)
            # This prevents tiny/garbage chunks from polluting vector space
            MIN_CHUNK_LENGTH = 200  # Minimum meaningful content
            chunks = []
            for chunk in raw_chunks:
                text = chunk.text.strip()
                # Skip tiny chunks, markdown artifacts, code fences only
                if len(text) < MIN_CHUNK_LENGTH:
                    continue
                # Skip chunks that are mostly markdown formatting
                non_formatting = (
                    text.replace("```", "").replace("---", "").replace("#", "").strip()
                )
                if len(non_formatting) < MIN_CHUNK_LENGTH // 2:
                    continue
                chunks.append(chunk)

            if not chunks:
                logger.warning(
                    "All chunks filtered as garbage",
                    doc_source=doc.source,
                    raw_count=len(raw_chunks),
                )
                return 0

            # Add metadata to each chunk
            for chunk in chunks:
                chunk.metadata = {**chunk.metadata, **metadata}

            chunk_count = len(chunks)
            logger.debug(
                "Chunks after quality filter",
                raw=len(raw_chunks),
                kept=chunk_count,
                filtered=len(raw_chunks) - chunk_count,
            )

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

    async def ingest_batch(
        self,
        documents: list[tuple[str, str | None, dict[str, Any]]],
    ) -> list[IngestResult]:
        """
        Batch ingest multiple documents efficiently.

        This method processes all documents together, batching:
        - Chunking (fast, ~100ms total)
        - Embedding (main bottleneck - batched in groups of 32)
        - Storage (single transaction)

        For 179 files, this achieves ~10-15x speedup vs sequential ingest().

        Args:
            documents: List of (content, doc_id, kwargs) tuples

        Returns:
            List of IngestResult for each document
        """
        import asyncio

        if not documents:
            return []

        # Validate and prepare all documents
        docs_to_process: list[tuple[Document, str | None, dict[str, Any]]] = []
        results: list[IngestResult] = []

        for content, doc_id, kwargs in documents:
            is_valid, error = self.validate_content(content, **kwargs)
            if not is_valid:
                results.append(
                    IngestResult(
                        doc_id=doc_id or "unknown",
                        chunk_count=0,
                        success=False,
                        error=error,
                    )
                )
                continue

            metadata = self.prepare_metadata(content, **kwargs)
            source = self.build_source_uri(doc_id, **kwargs)
            doc = Document(content=content, source=source, metadata=metadata)
            docs_to_process.append((doc, doc_id, kwargs))

        if not docs_to_process:
            return results

        # Process all valid documents in batch
        ragi_sync = self.ragi._sync
        chunk_counts: dict[int, int] = {}

        def _batch_process() -> None:
            # Chunk ALL documents with quality filtering
            MIN_CHUNK_LENGTH = 200  # Minimum meaningful content
            all_chunks = []
            total_raw = 0
            total_filtered = 0

            for idx, (doc, _, _) in enumerate(docs_to_process):
                raw_chunks = ragi_sync.chunker.chunk_document(doc)
                total_raw += len(raw_chunks)

                # Filter garbage chunks at index time
                good_chunks = []
                for chunk in raw_chunks:
                    text = chunk.text.strip()
                    if len(text) < MIN_CHUNK_LENGTH:
                        continue
                    non_formatting = (
                        text.replace("```", "")
                        .replace("---", "")
                        .replace("#", "")
                        .strip()
                    )
                    if len(non_formatting) < MIN_CHUNK_LENGTH // 2:
                        continue
                    chunk.metadata = {**chunk.metadata, **doc.metadata}
                    good_chunks.append(chunk)

                all_chunks.extend(good_chunks)
                chunk_counts[idx] = len(good_chunks)
                total_filtered += len(raw_chunks) - len(good_chunks)

            logger.info(
                f"Batch: {len(all_chunks)} chunks from {len(docs_to_process)} docs "
                f"(filtered {total_filtered} garbage chunks)"
            )

            if all_chunks:
                # Embed ALL chunks (piragi batches internally at 32)
                chunks_with_embeddings = ragi_sync.embedder.embed_chunks(all_chunks)
                # Store ALL in single transaction
                ragi_sync.store.add_chunks(chunks_with_embeddings)

        try:
            await asyncio.to_thread(_batch_process)

            # Build success results
            for idx, (doc, doc_id, _) in enumerate(docs_to_process):
                results.append(
                    IngestResult(
                        doc_id=doc_id or doc.source,
                        chunk_count=chunk_counts.get(idx, 0),
                        success=True,
                    )
                )

            logger.info(
                "Batch ingest complete",
                index_type=self.index_type.value,
                documents=len(docs_to_process),
                total_chunks=sum(chunk_counts.values()),
            )
        except Exception as e:
            logger.error(
                "Batch ingest failed",
                index_type=self.index_type.value,
                error=str(e),
            )
            # Mark all as failed
            for _doc, doc_id, _ in docs_to_process:
                results.append(
                    IngestResult(
                        doc_id=doc_id or "unknown",
                        chunk_count=0,
                        success=False,
                        error=str(e),
                    )
                )

        return results

    def _preprocess_query(self, query: str) -> str:
        """
        Preprocess query for better semantic matching.

        Natural language questions like "What are the different agent roles?"
        don't embed well for direct vector similarity search. This extracts
        key terms to improve matching.

        Returns:
            Preprocessed query with filler words removed
        """
        import re

        # Minimal stop words - only truly meaningless filler
        # Keep words that add semantic meaning (different, explain, help, etc.)
        STOP_WORDS = {
            # Articles only
            "a",
            "an",
            "the",
            # Basic pronouns
            "i",
            "me",
            "my",
            "we",
            "us",
            "you",
            "your",
            # Question starters (but keep "how" - often meaningful)
            "what",
            "which",
            # Filler verbs
            "is",
            "are",
            "was",
            "were",
            "do",
            "does",
            "did",
            # Politeness
            "please",
        }

        # Lowercase and tokenize
        query_lower = query.lower()

        # Remove punctuation except hyphens (keep compound words)
        query_clean = re.sub(r"[^\w\s-]", " ", query_lower)

        # Split into words
        words = query_clean.split()

        # Remove stop words but keep at least some content
        key_terms = [w for w in words if w not in STOP_WORDS and len(w) > 1]

        # If we removed everything, use original query
        if not key_terms:
            return query

        preprocessed = " ".join(key_terms)

        # Log if we significantly changed the query
        if preprocessed != query_lower.strip():
            logger.debug(
                "Query preprocessed",
                original=query[:50],
                preprocessed=preprocessed[:50],
            )

        return preprocessed

    async def search(
        self,
        query: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> SearchOutcome:
        """
        Search the index.

        Args:
            query: Natural language search query
            top_k: Number of results to return
            filters: Optional metadata filters

        Returns:
            SearchOutcome with results and success status
        """
        import asyncio
        import time

        start_time = time.time()

        try:
            ragi_sync = self.ragi._sync
            embedder = ragi_sync.embedder

            # Preprocess query for better semantic matching
            processed_query = self._preprocess_query(query)
            if processed_query != query:
                logger.debug(
                    "Query preprocessed",
                    original=query[:50],
                    processed=processed_query[:50],
                    index_type=self.index_type.value,
                )

            # Use async embedding if available (OllamaEmbedder has aembed_query)
            if hasattr(embedder, "aembed_query"):
                query_embedding = await embedder.aembed_query(processed_query)
            else:
                # Fallback to sync in thread for SentenceTransformers
                query_embedding = await asyncio.to_thread(
                    embedder.embed_query, processed_query
                )

            # Over-fetch when filters are provided to account for post-filtering
            # This ensures we have enough results after filtering
            fetch_k = top_k * 3 if filters else top_k

            # Store search is sync - run in thread
            # min_chunk_length=100 filters out tiny header-only chunks
            def _do_search() -> list[Citation]:
                results: list[Citation] = ragi_sync.store.search(
                    query_embedding,
                    top_k=fetch_k,
                    min_chunk_length=100,
                )
                return results

            chunks = await asyncio.to_thread(_do_search)

            results: list[SearchResult] = []
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

                # Truncate to original top_k after filtering
                if len(results) >= top_k:
                    break

            elapsed_ms = (time.time() - start_time) * 1000
            return SearchOutcome(
                results=results,
                success=True,
                index_type=self.index_type,
                search_time_ms=elapsed_ms,
            )

        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000
            logger.warning(
                "Search failed",
                index_type=self.index_type.value,
                error=str(e),
                search_time_ms=elapsed_ms,
            )
            return SearchOutcome(
                results=[],
                success=False,
                error_message=str(e),
                index_type=self.index_type,
                search_time_ms=elapsed_ms,
            )

    def _fallback_answer(self, _search_results: list[SearchResult]) -> str:
        """
        Return empty to let OptimalService continue searching other indexes.

        Previously this returned a formatted string of search results, but that
        caused query() to early-return and skip remaining indexes. Now we return
        empty and let the service-level aggregation handle fallback synthesis.
        """
        return ""

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
            Tuple of (answer, citations). Returns ("", []) on failure
            to allow OptimalService to continue to next index.
        """
        import asyncio

        import httpx

        # Per-index timeout to prevent one slow index from blocking everything
        INDEX_TIMEOUT = 15.0

        search_results: list[SearchResult] = []

        try:
            async with asyncio.timeout(INDEX_TIMEOUT):
                # First get context using our properly async search
                logger.info(
                    "ask() starting search",
                    index_type=self.index_type.value,
                    query=query[:50],
                )
                outcome = await self.search(query, top_k=top_k)
                search_results = outcome.results

                logger.info(
                    "ask() search completed",
                    index_type=self.index_type.value,
                    num_results=len(search_results),
                    search_success=outcome.success,
                )

                if not search_results:
                    # Return empty to continue to next index
                    return "", []

                # Build context for LLM
                context_texts = [r.content for r in search_results]
                context = "\n\n---\n\n".join(context_texts)

                # Build prompt
                prompt = (
                    "You are a technical knowledge base assistant. "
                    "Based on the context, provide a thorough, actionable answer.\n\n"
                    "Your response should:\n"
                    "- Be specific and detailed, not vague\n"
                    "- Include code examples or steps when relevant\n"
                    "- Reference the source material when citing facts\n"
                    "- Warn about pitfalls if mentioned in context\n\n"
                    "Do NOT use <think> tags.\n\n"
                    f"Context:\n{context}\n\n"
                    f"Question: {query}\n\n"
                    "Detailed Answer:"
                )

                # Call LLM via Ollama API (async HTTP)
                llm_url = f"{self.config.llm_base_url}/chat/completions"
                logger.info(
                    "ask() calling LLM",
                    index_type=self.index_type.value,
                    llm_url=llm_url,
                    model=self.config.llm_model,
                )
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.post(
                        llm_url,
                        json={
                            "model": self.config.llm_model,
                            "messages": [{"role": "user", "content": prompt}],
                            "max_tokens": 4096,
                            "options": {"num_ctx": 8192},
                        },
                    )
                    if resp.is_success:
                        data = resp.json()
                        answer_text = data["choices"][0]["message"]["content"]
                        # Extract answer from think tags if needed
                        answer_text = self._extract_from_think_tags(answer_text)
                        return answer_text, search_results
                    else:
                        logger.warning(
                            "LLM call failed in ask",
                            index_type=self.index_type.value,
                            status=resp.status_code,
                            error=resp.text[:200] if resp.text else "no error text",
                        )
                        # Return empty to let service aggregate and synthesize
                        return "", search_results

        except TimeoutError:
            logger.warning(
                "Index ask() timed out",
                index_type=self.index_type.value,
                timeout=INDEX_TIMEOUT,
            )
            # Return search results even on timeout - service can aggregate them
            return "", search_results
        except httpx.TimeoutException:
            logger.warning(
                "LLM HTTP call timed out in ask",
                index_type=self.index_type.value,
            )
            return "", search_results
        except Exception as e:
            logger.warning(
                "RAG query failed",
                index_type=self.index_type.value,
                error=str(e),
            )
            # Return whatever search results we have for aggregation
            return "", search_results

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
            logger.warning(f"Failed to list documents in {self.index_type.value}: {e}")
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
                        files = list(Path().glob(source))
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
