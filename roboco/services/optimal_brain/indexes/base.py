"""
Base Index Plugin

Abstract base class for all knowledge index plugins.
Each plugin handles a specific content type (code, docs, errors, standards, etc.)
and implements specialized chunking, metadata handling, and search strategies.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import structlog

from roboco.config import settings
from roboco.models.optimal import IndexType, SearchOutcome, SearchResult
from roboco.services.exceptions import (
    HTTP_TOO_MANY_REQUESTS,
    MAX_RATE_LIMIT_RETRIES,
    RateLimitError,
    parse_retry_after_header,
)
from roboco.services.optimal_brain.text_chunker import (
    Chunk,
    Citation,
    Document,
    TextChunker,
)
from roboco.services.optimal_brain.vector_store import VectorStore

logger = structlog.get_logger()


def build_doc_source(*, kind: str, id_: str | None) -> str | None:
    """Construct a roboco:// doc source URI; returns None if id_ is None."""
    if id_ is None:
        return None
    return f"roboco://{kind}/{id_}"


_MIN_CHUNK_LENGTH = 200


def _filter_quality_chunks(raw_chunks: list[Any]) -> list[Any]:
    """Drop tiny chunks and chunks that are mostly markdown formatting."""
    kept: list[Any] = []
    for chunk in raw_chunks:
        text = chunk.text.strip()
        if len(text) < _MIN_CHUNK_LENGTH:
            continue
        non_formatting = (
            text.replace("```", "").replace("---", "").replace("#", "").strip()
        )
        if len(non_formatting) < _MIN_CHUNK_LENGTH // 2:
            continue
        kept.append(chunk)
    return kept


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
    embedding_model: str = "qwen3-embedding:0.6b"
    llm_model: str = "glm-5:cloud"
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
        self._store: VectorStore | None = None
        self._chunker: TextChunker | None = None
        self._embedder: Any = None
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
    def build_source_uri(self, doc_id: str | None = None, **kwargs: Any) -> str | None:
        """
        Build a source URI for the document.

        Args:
            doc_id: Optional document ID
            **kwargs: Additional context for URI building

        Returns:
            A source URI string (e.g., "roboco://errors/err-001"), or None if the
            required ID is missing — callers must skip indexing when None is returned.
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

    async def _validate_embedding_dimensions(self, embedder: Any) -> None:
        """
        Validate that embedder produces expected dimensions.

        Catches dimension mismatches early (e.g., wrong model configured)
        instead of failing silently during vector search.
        """
        import asyncio

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
        """Initialize the index backend (store, chunker, embedder)."""
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
        await self._validate_embedding_dimensions(shared_embedder)

        self._embedder = shared_embedder

        # Create the character-based chunker (no external tokenizers)
        self._chunker = TextChunker(
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
        )

        # Create and initialise the vector store
        if not self.config.store_url:
            raise RuntimeError(
                f"store_url is required for {self.index_type.value} VectorStore. "
                "Set ROBOCO_DATABASE_* environment variables."
            )

        self._store = VectorStore(
            dsn=self.config.store_url,
            table_name=f"chunks_{self.index_type.value}",
            vector_dimension=settings.embedding_dimensions,
        )
        await self._store.initialize()

        self._initialized = True
        logger.info(f"{self.index_type.value} index plugin initialized")

    async def close(self) -> None:
        """Cleanup resources."""
        if self._store is not None:
            await self._store.close()
        self._store = None
        self._chunker = None
        self._embedder = None
        self._initialized = False
        logger.info(f"{self.index_type.value} index plugin closed")

    # ------------------------------------------------------------------
    # Internal accessors (raise if not initialised)
    # ------------------------------------------------------------------

    @property
    def _require_store(self) -> VectorStore:
        if not self._initialized or self._store is None:
            msg = f"{self.index_type.value} index not initialized."
            raise RuntimeError(msg)
        return self._store

    @property
    def _require_chunker(self) -> TextChunker:
        if not self._initialized or self._chunker is None:
            msg = f"{self.index_type.value} index not initialized."
            raise RuntimeError(msg)
        return self._chunker

    @property
    def _require_embedder(self) -> Any:
        if not self._initialized or self._embedder is None:
            msg = f"{self.index_type.value} index not initialized."
            raise RuntimeError(msg)
        return self._embedder

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

        if source is None:
            logger.debug(
                "Skipping ingest: build_source_uri returned None",
                index_type=self.index_type.value,
                doc_id=doc_id,
            )
            return IngestResult(
                doc_id=doc_id or "unknown",
                chunk_count=0,
                success=False,
                error="source URI could not be built (missing ID)",
            )

        # Create document
        doc = Document(
            content=content,
            source=source,
            metadata=metadata,
        )

        try:
            chunk_count = await self._chunk_filter_embed_store(doc, metadata)
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

    async def _chunk_filter_embed_store(
        self, doc: Document, metadata: dict[str, Any]
    ) -> int:
        """Chunk → filter → embed → store. Returns count of stored chunks."""
        chunker = self._require_chunker
        store = self._require_store
        embedder = self._require_embedder

        raw_chunks: list[Chunk] = chunker.chunk_document(doc)
        chunks = _filter_quality_chunks(raw_chunks)
        if not chunks:
            logger.warning(
                "All chunks filtered as garbage",
                doc_source=doc.source,
                raw_count=len(raw_chunks),
            )
            return 0

        for chunk in chunks:
            chunk.metadata = {**chunk.metadata, **metadata}

        logger.debug(
            "Chunks after quality filter",
            raw=len(raw_chunks),
            kept=len(chunks),
            filtered=len(raw_chunks) - len(chunks),
        )

        # Embed (async)
        if hasattr(embedder, "aembed_chunks"):
            chunks_with_embeddings: list[Chunk] = await embedder.aembed_chunks(chunks)
        else:
            import asyncio

            chunks_with_embeddings = await asyncio.to_thread(
                embedder.embed_chunks, chunks
            )

        await store.add_chunks(chunks_with_embeddings)
        return len(chunks)

    def _prepare_docs_for_batch(
        self,
        documents: list[tuple[str, str | None, dict[str, Any]]],
        results: list[IngestResult],
    ) -> list[tuple[Document, str | None, dict[str, Any]]]:
        """Validate input documents and return the list to process.

        Appends failure IngestResults to ``results`` in place for invalid docs.
        """
        docs_to_process: list[tuple[Document, str | None, dict[str, Any]]] = []
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
            if source is None:
                logger.debug(
                    "Skipping batch ingest: build_source_uri returned None",
                    index_type=self.index_type.value,
                    doc_id=doc_id,
                )
                results.append(
                    IngestResult(
                        doc_id=doc_id or "unknown",
                        chunk_count=0,
                        success=False,
                        error="source URI could not be built (missing ID)",
                    )
                )
                continue
            doc = Document(content=content, source=source, metadata=metadata)
            docs_to_process.append((doc, doc_id, kwargs))
        return docs_to_process

    @staticmethod
    def _filter_good_chunks(raw_chunks: list[Any], doc: Document) -> list[Any]:
        """Drop tiny / formatting-only chunks and merge doc metadata in."""
        MIN_CHUNK_LENGTH = 200  # Minimum meaningful content
        good_chunks: list[Any] = []
        for chunk in raw_chunks:
            text = chunk.text.strip()
            if len(text) < MIN_CHUNK_LENGTH:
                continue
            non_formatting = (
                text.replace("```", "").replace("---", "").replace("#", "").strip()
            )
            if len(non_formatting) < MIN_CHUNK_LENGTH // 2:
                continue
            chunk.metadata = {**chunk.metadata, **doc.metadata}
            good_chunks.append(chunk)
        return good_chunks

    async def _run_batch_process(
        self,
        docs_to_process: list[tuple[Document, str | None, dict[str, Any]]],
        chunk_counts: dict[int, int],
    ) -> None:
        """Chunk, embed, and store all documents in a single batch."""
        chunker = self._require_chunker
        store = self._require_store
        embedder = self._require_embedder

        all_chunks: list[Chunk] = []
        total_filtered = 0

        for idx, (doc, _, _) in enumerate(docs_to_process):
            raw_chunks = chunker.chunk_document(doc)
            good_chunks = self._filter_good_chunks(raw_chunks, doc)
            all_chunks.extend(good_chunks)
            chunk_counts[idx] = len(good_chunks)
            total_filtered += len(raw_chunks) - len(good_chunks)

        logger.info(
            f"Batch: {len(all_chunks)} chunks from {len(docs_to_process)} docs "
            f"(filtered {total_filtered} garbage chunks)"
        )

        if all_chunks:
            if hasattr(embedder, "aembed_chunks"):
                chunks_with_embeddings: list[Chunk] = await embedder.aembed_chunks(
                    all_chunks
                )
            else:
                import asyncio

                chunks_with_embeddings = await asyncio.to_thread(
                    embedder.embed_chunks, all_chunks
                )
            await store.add_chunks(chunks_with_embeddings)

    @staticmethod
    def _mark_batch_failed(
        results: list[IngestResult],
        docs_to_process: list[tuple[Document, str | None, dict[str, Any]]],
        error: str,
    ) -> None:
        """Append failure IngestResults for every document in the batch."""
        for _doc, doc_id, _ in docs_to_process:
            results.append(
                IngestResult(
                    doc_id=doc_id or "unknown",
                    chunk_count=0,
                    success=False,
                    error=error,
                )
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
        if not documents:
            return []

        results: list[IngestResult] = []
        docs_to_process = self._prepare_docs_for_batch(documents, results)
        if not docs_to_process:
            return results

        chunk_counts: dict[int, int] = {}

        try:
            await self._run_batch_process(docs_to_process, chunk_counts)

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
            self._mark_batch_failed(results, docs_to_process, str(e))

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

    async def _generate_hyde_passage(self, query: str) -> str:
        """Generate a hypothetical passage for HyDE query expansion.

        Calls the configured Ollama LLM to produce a short passage that would
        plausibly answer *query*.  Returns an empty string on any failure so
        the caller can fall back to raw query embedding.

        Args:
            query: The (preprocessed) search query.

        Returns:
            A short hypothetical passage, or ``""`` on LLM error.
        """
        import httpx

        prompt = (
            "Write a concise technical passage (2-4 sentences) that directly "
            "answers the following question. Include specific technical details "
            "and terminology. Do not use <think> tags.\n\n"
            f"Question: {query}\n\n"
            "Answer:"
        )

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self.config.llm_base_url}/chat/completions",
                    json={
                        "model": self.config.llm_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 200,
                    },
                )
            if resp.is_success:
                data = resp.json()
                passage = self._extract_from_think_tags(
                    data["choices"][0]["message"]["content"]
                )
                logger.debug(
                    "HyDE passage generated",
                    index_type=self.index_type.value,
                    passage_len=len(passage),
                )
                return passage
            logger.debug(
                "HyDE LLM call returned non-success status",
                status=resp.status_code,
                index_type=self.index_type.value,
            )
        except Exception as e:
            logger.debug(
                "HyDE LLM call failed, will use raw query embedding",
                index_type=self.index_type.value,
                error=str(e),
            )
        return ""

    async def _compute_query_embedding(self, query: str) -> list[float]:
        """Preprocess the query and embed it using the configured embedder.

        When ``config.use_hyde`` is ``True``, first calls the Ollama LLM to
        generate a hypothetical answer passage (HyDE) and embeds that instead
        of the raw query.  Falls back to raw query embedding if the LLM call
        fails or returns an empty passage.
        """
        import asyncio

        embedder = self._require_embedder

        processed_query = self._preprocess_query(query)
        if processed_query != query:
            logger.debug(
                "Query preprocessed",
                original=query[:50],
                processed=processed_query[:50],
                index_type=self.index_type.value,
            )

        # HyDE: try to embed a hypothetical passage instead of the raw query
        text_to_embed = processed_query
        if self.config.use_hyde:
            passage = await self._generate_hyde_passage(processed_query)
            if passage:
                text_to_embed = passage

        if hasattr(embedder, "aembed_query"):
            result: list[float] = await embedder.aembed_query(text_to_embed)
            return result
        return await asyncio.to_thread(embedder.embed_query, text_to_embed)

    async def _fetch_citations(
        self,
        query_embedding: list[float],
        top_k: int,
        has_filters: bool,
    ) -> list[Citation]:
        """Fetch citations from the vector store."""
        store = self._require_store
        fetch_k = top_k * 3 if has_filters else top_k
        return await store.search(
            query_embedding,
            top_k=fetch_k,
            min_chunk_length=100,
        )

    def _citations_to_results(
        self,
        chunks: list[Citation],
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[SearchResult]:
        """Apply metadata filters and map to SearchResult up to ``top_k``."""
        results: list[SearchResult] = []
        for chunk in chunks:
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

            if len(results) >= top_k:
                break
        return results

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
        import time

        start_time = time.time()

        try:
            query_embedding = await self._compute_query_embedding(query)
            chunks = await self._fetch_citations(
                query_embedding, top_k, has_filters=bool(filters)
            )
            results = self._citations_to_results(chunks, top_k, filters)

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

        The Ollama LLM call is retried up to MAX_RATE_LIMIT_RETRIES times on
        HTTP 429, respecting the Retry-After header.  The vector-search phase
        is NOT retried and still runs inside the 15-second index timeout.
        """
        import asyncio

        import httpx

        # Per-index timeout to prevent one slow index from blocking everything.
        # Applied to the search phase only; LLM retries run outside this timeout.
        INDEX_TIMEOUT = 15.0

        search_results: list[SearchResult] = []
        prompt: str = ""

        # ---- Search phase (inside timeout) -----------------------------------
        try:
            async with asyncio.timeout(INDEX_TIMEOUT):
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
                    return "", []

                # Build context and prompt while still inside the timeout
                context_texts = [r.content for r in search_results]
                context = "\n\n---\n\n".join(context_texts)
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

        except (TimeoutError, httpx.TimeoutException, Exception) as e:
            logger.warning(
                "Index ask() search phase failed",
                index_type=self.index_type.value,
                error_type=type(e).__name__,
                error=str(e) if not isinstance(e, TimeoutError) else "timed out",
            )
            return "", search_results

        # ---- LLM call phase with 429 retry (outside index timeout) -----------
        return await self._ask_llm(prompt, search_results)

    async def _ask_llm(
        self, prompt: str, search_results: list[SearchResult]
    ) -> tuple[str, list[SearchResult]]:
        """Run the 429-retried LLM synthesis for :meth:`ask`.

        Returns ``(answer, citations)``; ``("", citations)`` on any non-429
        failure. Raises RateLimitError only when every 429 retry is exhausted.
        """
        import asyncio

        import httpx

        llm_url = f"{self.config.llm_base_url}/chat/completions"
        logger.info(
            "ask() calling LLM",
            index_type=self.index_type.value,
            llm_url=llm_url,
            model=self.config.llm_model,
        )

        last_rl_retry_after: float | None = None

        for rl_attempt in range(MAX_RATE_LIMIT_RETRIES):
            try:
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
            except (httpx.TimeoutException, Exception) as e:
                logger.warning(
                    "LLM call failed in ask (non-429)",
                    index_type=self.index_type.value,
                    error=str(e),
                )
                return "", search_results

            if resp.status_code == HTTP_TOO_MANY_REQUESTS:
                last_rl_retry_after = parse_retry_after_header(resp)
                backoff = (
                    last_rl_retry_after
                    if last_rl_retry_after is not None
                    else float(2**rl_attempt)
                )
                logger.warning(
                    "Ollama rate limited (429), retrying",
                    provider="ollama",
                    attempt=rl_attempt + 1,
                    max_retries=MAX_RATE_LIMIT_RETRIES,
                    backoff_duration=backoff,
                )
                if rl_attempt < MAX_RATE_LIMIT_RETRIES - 1:
                    await asyncio.sleep(backoff)
                continue

            if resp.is_success:
                data = resp.json()
                answer_text = self._extract_from_think_tags(
                    data["choices"][0]["message"]["content"]
                )
                return answer_text, search_results
            logger.warning(
                "LLM call failed in ask",
                index_type=self.index_type.value,
                status=resp.status_code,
                error=resp.text[:200] if resp.text else "no error text",
            )
            return "", search_results

        raise RateLimitError(provider="ollama", retry_after=last_rl_retry_after)

    async def count(self) -> int:
        """Get the number of chunks in the index."""
        try:
            return await self._require_store.count()
        except Exception:
            return 0

    async def clear(self) -> None:
        """Clear all documents from the index."""
        await self._require_store.clear()
        logger.info(f"Cleared {self.index_type.value} index")

    async def list_documents(
        self, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        """
        List documents in the index.

        Returns list of documents with id, source, indexed_at, and metadata.
        """
        try:
            return await self._require_store.list_docs(limit=limit, offset=offset)
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
        await self._ingest_source_files(sources)
        await self._track_source_files_in_db(sources)
        return await self.count()

    @staticmethod
    def _expand_source_paths(source: str) -> "list[Any]":
        """Expand a source string into a list of Path objects."""
        from pathlib import Path

        source_path = Path(source)
        if "*" in source:
            return list(Path().glob(source))
        if source_path.is_dir():
            return list(source_path.rglob("*"))
        return [source_path] if source_path.exists() else []

    async def _ingest_source_files(self, sources: list[str]) -> None:
        """Ingest each file found under sources into the vector store."""
        for source in sources:
            for file_path in self._expand_source_paths(source):
                if not file_path.is_file():
                    continue
                try:
                    content = file_path.read_text(errors="ignore")
                    await self.ingest(
                        content=content,
                        doc_id=str(file_path.absolute()),
                        file_path=str(file_path),
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to index source file",
                        file=str(file_path),
                        error=str(e),
                    )

    async def _track_source_files_in_db(self, sources: list[str]) -> None:
        """Record each indexed file in the database for tracking."""
        from roboco.db import get_db_context

        async with get_db_context() as db:
            for source in sources:
                for file_path in self._expand_source_paths(source):
                    if not file_path.is_file():
                        continue
                    await self._upsert_doc_record(db, file_path)
            await db.commit()

    async def _upsert_doc_record(self, db: Any, file_path: Any) -> None:
        """Upsert one file record into the indexed-documents table."""
        import contextlib
        import hashlib

        from sqlalchemy import select

        from roboco.db.tables import IndexedDocumentTable

        source_str = str(file_path.absolute())
        source_hash = hashlib.sha256(source_str.encode()).hexdigest()
        title = file_path.stem.replace("-", " ").replace("_", " ").title()

        preview = None
        with contextlib.suppress(Exception):
            preview = file_path.read_text(errors="ignore")[:500].strip()

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
            db.add(
                IndexedDocumentTable(
                    index_type=self.index_type.value,
                    source=source_str,
                    source_hash=source_hash,
                    title=title,
                    preview=preview,
                    chunk_count=0,
                )
            )
