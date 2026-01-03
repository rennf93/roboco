"""
Ollama Embedder

Provides embedding generation using Ollama's native API.
Drop-in replacement for piragi's EmbeddingGenerator when using Ollama models.

Features:
- Parallel batch processing for faster embedding
- Content-based caching to avoid re-embedding
- Connection pooling for efficiency
- Retry logic for transient failures
"""

import asyncio
import hashlib
import time
from collections.abc import Callable
from typing import Any

import httpx
from piragi.types import Chunk

from roboco.config import settings
from roboco.logging import get_logger

logger = get_logger(__name__)

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY_BASE = 0.5  # seconds, exponential backoff

# Parallel processing configuration
MAX_CONCURRENT_BATCHES = 4  # Number of batches to process in parallel
DEFAULT_BATCH_SIZE = 32  # piragi's default batch size


class OllamaEmbedderError(Exception):
    """Base exception for Ollama embedder errors."""

    pass


class OllamaConnectionError(OllamaEmbedderError):
    """Raised when Ollama server is unreachable."""

    pass


class OllamaModelError(OllamaEmbedderError):
    """Raised when the embedding model is unavailable or returns invalid data."""

    pass


class EmbeddingCache:
    """
    LRU cache for embeddings keyed by content hash.

    Avoids re-computing embeddings for identical content.
    """

    def __init__(self, max_size: int = 10000):
        self._cache: dict[str, list[float]] = {}
        self._access_order: list[str] = []
        self._max_size = max_size
        self._hits = 0
        self._misses = 0

    @staticmethod
    def _hash_content(content: str) -> str:
        """Generate hash for content."""
        return hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()

    def get(self, content: str) -> list[float] | None:
        """Get cached embedding by content."""
        key = self._hash_content(content)
        if key in self._cache:
            self._hits += 1
            # Move to end (most recently used)
            self._access_order.remove(key)
            self._access_order.append(key)
            return self._cache[key]
        self._misses += 1
        return None

    def put(self, content: str, embedding: list[float]) -> None:
        """Cache embedding for content."""
        key = self._hash_content(content)
        if key in self._cache:
            return  # Already cached

        # Evict oldest if at capacity
        while len(self._cache) >= self._max_size:
            oldest = self._access_order.pop(0)
            del self._cache[oldest]

        self._cache[key] = embedding
        self._access_order.append(key)

    def get_many(self, contents: list[str]) -> tuple[list[int], list[list[float]]]:
        """
        Get cached embeddings for multiple contents.

        Returns:
            Tuple of (indices of cached items, their embeddings)
        """
        cached_indices = []
        cached_embeddings = []
        for i, content in enumerate(contents):
            emb = self.get(content)
            if emb is not None:
                cached_indices.append(i)
                cached_embeddings.append(emb)
        return cached_indices, cached_embeddings

    def put_many(self, contents: list[str], embeddings: list[list[float]]) -> None:
        """Cache multiple embeddings."""
        for content, emb in zip(contents, embeddings, strict=True):
            self.put(content, emb)

    @property
    def stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": f"{hit_rate:.1f}%",
        }


class OllamaEmbedder:
    """
    Embedding generator using Ollama's native API.

    Features:
    - Parallel batch processing (configurable concurrency)
    - Content-based caching (avoids re-embedding identical content)
    - Connection pooling for efficiency
    - Retry logic with exponential backoff
    """

    def __init__(
        self,
        model: str = "embeddinggemma:300m",
        base_url: str | None = None,
        timeout: float = 120.0,
        max_concurrent: int = MAX_CONCURRENT_BATCHES,
        cache_size: int = 10000,
    ):
        """Initialize Ollama embedder.

        Args:
            model: Ollama model name for embeddings
            base_url: Ollama API base URL (default: from settings)
            timeout: Request timeout in seconds (default 120s for CPU embedding)
            max_concurrent: Max concurrent batch requests (default 4)
            cache_size: Max cached embeddings (default 10000)
        """
        self.model = model
        self.base_url = base_url or settings.ollama_base_url
        self.timeout = timeout
        self.max_concurrent = max_concurrent
        self._dimensions: int | None = None
        self._cache = EmbeddingCache(max_size=cache_size)
        # Reusable sync client (async clients created per-operation)
        self._sync_client: httpx.Client | None = None
        # Semaphore for limiting concurrent requests
        self._semaphore: asyncio.Semaphore | None = None

    def _get_sync_client(self) -> httpx.Client:
        """Get or create sync HTTP client with connection pooling."""
        if self._sync_client is None or self._sync_client.is_closed:
            timeout = httpx.Timeout(
                connect=10.0,
                read=self.timeout,
                write=30.0,
                pool=10.0,
            )
            self._sync_client = httpx.Client(
                timeout=timeout,
                limits=httpx.Limits(
                    max_connections=self.max_concurrent * 2,
                    max_keepalive_connections=self.max_concurrent,
                ),
            )
        return self._sync_client

    def _create_async_client(self) -> httpx.AsyncClient:
        """Create a fresh async HTTP client.

        Always creates a new client to avoid 'Event loop is closed' errors
        that occur when a cached client is bound to a different/closed loop.
        """
        timeout = httpx.Timeout(
            connect=10.0,
            read=self.timeout,
            write=30.0,
            pool=10.0,
        )
        return httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(
                max_connections=self.max_concurrent * 2,
                max_keepalive_connections=self.max_concurrent,
            ),
        )

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Get or create semaphore for limiting concurrent requests."""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrent)
        return self._semaphore

    def close(self) -> None:
        """Close HTTP clients and release resources."""
        if self._sync_client and not self._sync_client.is_closed:
            self._sync_client.close()
        self._sync_client = None

    async def aclose(self) -> None:
        """Async close HTTP clients and release resources."""
        # Async clients are now created per-request, nothing to close
        self.close()

    @property
    def dimensions(self) -> int:
        """Get embedding dimensions (cached after first call)."""
        if self._dimensions is None:
            test_embedding = self.embed_query("test")
            self._dimensions = len(test_embedding)
        return self._dimensions

    def set_dimensions(self, dim: int) -> None:
        """Pre-set dimensions to avoid blocking call."""
        self._dimensions = dim

    async def get_dimensions_async(self) -> int:
        """Async-friendly way to get embedding dimensions."""
        if self._dimensions is None:
            test_embedding = await self.aembed_query("test")
            self._dimensions = len(test_embedding)
        return self._dimensions

    @property
    def cache_stats(self) -> dict[str, Any]:
        """Get embedding cache statistics."""
        return self._cache.stats

    def _handle_embed_response(
        self, response: httpx.Response, input_count: int = 1
    ) -> list[list[float]]:
        """Validate and extract embeddings from API response."""
        if not response.is_success:
            error_text = response.text[:200] if response.text else "Unknown error"
            if response.status_code == httpx.codes.NOT_FOUND:
                raise OllamaModelError(
                    f"Model '{self.model}' not found. "
                    f"Run 'ollama pull {self.model}' to download it."
                )
            raise OllamaEmbedderError(
                f"Ollama API error {response.status_code}: {error_text}"
            )

        try:
            data = response.json()
        except Exception as e:
            raise OllamaEmbedderError(f"Invalid JSON response: {e}") from e

        embeddings: list[list[float]] | None = data.get("embeddings")
        if not embeddings:
            raise OllamaModelError(
                f"No embeddings returned for model '{self.model}'. "
                "The model may not support embeddings."
            )

        if len(embeddings) != input_count:
            raise OllamaModelError(
                f"Expected {input_count} embeddings, got {len(embeddings)}"
            )

        for i, emb in enumerate(embeddings):
            if not emb or not isinstance(emb, list):
                raise OllamaModelError(f"Invalid embedding at index {i}")

        return embeddings

    def embed_query(
        self,
        query: str,
        task_instruction: str | None = None,
    ) -> list[float]:
        """Generate embedding for a single query with retry logic."""
        _ = task_instruction

        # Check cache first
        cached = self._cache.get(query)
        if cached is not None:
            return cached

        client = self._get_sync_client()
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            try:
                response = client.post(
                    f"{self.base_url}/api/embed",
                    json={"model": self.model, "input": query},
                )
                embeddings = self._handle_embed_response(response, input_count=1)
                result = embeddings[0]
                self._cache.put(query, result)
                return result

            except httpx.ConnectError as e:
                last_error = OllamaConnectionError(
                    f"Cannot connect to Ollama at {self.base_url}: {e}"
                )
            except httpx.TimeoutException as e:
                last_error = OllamaConnectionError(f"Ollama request timed out: {e}")
            except (OllamaModelError, OllamaEmbedderError):
                raise
            except Exception as e:
                last_error = OllamaEmbedderError(f"Unexpected error: {e}")

            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAY_BASE * (2**attempt)
                logger.warning(
                    "Ollama embed_query retry",
                    attempt=attempt + 1,
                    delay=delay,
                    error=str(last_error),
                )
                time.sleep(delay)

        raise last_error or OllamaEmbedderError("Max retries exceeded")

    def _embed_batch_sync(
        self, client: httpx.Client, batch: list[str], batch_index: int
    ) -> list[list[float]]:
        """Embed a single batch synchronously with retry logic."""
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            try:
                response = client.post(
                    f"{self.base_url}/api/embed",
                    json={"model": self.model, "input": batch},
                )
                return self._handle_embed_response(response, input_count=len(batch))

            except httpx.ConnectError as e:
                last_error = OllamaConnectionError(
                    f"Cannot connect to Ollama at {self.base_url}: {e}"
                )
            except httpx.TimeoutException as e:
                last_error = OllamaConnectionError(f"Ollama request timed out: {e}")
            except (OllamaModelError, OllamaEmbedderError):
                raise
            except Exception as e:
                last_error = OllamaEmbedderError(f"Unexpected error: {e}")

            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAY_BASE * (2**attempt)
                logger.warning(
                    "Ollama embed_documents retry",
                    attempt=attempt + 1,
                    batch_index=batch_index,
                    delay=delay,
                    error=str(last_error),
                )
                time.sleep(delay)

        raise last_error or OllamaEmbedderError("Max retries exceeded")

    def embed_documents(
        self,
        documents: list[str],
        task_instruction: str | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> list[list[float]]:
        """Generate embeddings for multiple documents (sequential, uses cache)."""
        _ = task_instruction

        if not documents:
            return []

        # Check cache for all documents
        result_embeddings: list[list[float] | None] = [None] * len(documents)
        uncached_indices: list[int] = []
        uncached_docs: list[str] = []

        for i, doc in enumerate(documents):
            cached = self._cache.get(doc)
            if cached is not None:
                result_embeddings[i] = cached
            else:
                uncached_indices.append(i)
                uncached_docs.append(doc)

        if uncached_indices:
            logger.info(
                "Embedding cache stats",
                cached=len(documents) - len(uncached_indices),
                uncached=len(uncached_indices),
                total=len(documents),
            )

        if not uncached_docs:
            return [e for e in result_embeddings if e is not None]

        # Embed uncached documents in batches
        client = self._get_sync_client()
        new_embeddings: list[list[float]] = []

        for i in range(0, len(uncached_docs), batch_size):
            batch = uncached_docs[i : i + batch_size]
            embeddings = self._embed_batch_sync(client, batch, batch_index=i)
            new_embeddings.extend(embeddings)
            # Cache new embeddings
            for doc, emb in zip(batch, embeddings, strict=True):
                self._cache.put(doc, emb)

        # Merge cached and new embeddings
        for idx, emb in zip(uncached_indices, new_embeddings, strict=True):
            result_embeddings[idx] = emb

        return [e for e in result_embeddings if e is not None]

    async def _embed_batch_async(
        self,
        client: httpx.AsyncClient,
        batch: list[str],
        batch_index: int,
    ) -> list[list[float]]:
        """Embed a single batch with semaphore-limited concurrency."""
        semaphore = self._get_semaphore()

        async with semaphore:
            last_error: Exception | None = None

            for attempt in range(MAX_RETRIES):
                try:
                    logger.debug(
                        "Parallel embed batch",
                        batch_index=batch_index,
                        batch_size=len(batch),
                        attempt=attempt,
                    )
                    response = await client.post(
                        f"{self.base_url}/api/embed",
                        json={"model": self.model, "input": batch},
                    )
                    return self._handle_embed_response(
                        response, input_count=len(batch)
                    )

                except httpx.ConnectError as e:
                    last_error = OllamaConnectionError(
                        f"Cannot connect to Ollama at {self.base_url}: {e}"
                    )
                except httpx.TimeoutException as e:
                    last_error = OllamaConnectionError(
                        f"Ollama request timed out: {e}"
                    )
                except (OllamaModelError, OllamaEmbedderError):
                    raise
                except Exception as e:
                    last_error = OllamaEmbedderError(f"Unexpected error: {e}")

                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAY_BASE * (2**attempt)
                    logger.warning(
                        "Parallel embed batch retry",
                        batch_index=batch_index,
                        attempt=attempt + 1,
                        delay=delay,
                        error=str(last_error),
                    )
                    await asyncio.sleep(delay)

            raise last_error or OllamaEmbedderError("Max retries exceeded")

    async def aembed_documents_parallel(
        self,
        documents: list[str],
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> list[list[float]]:
        """
        Embed documents in parallel batches for maximum throughput.

        Processes multiple batches concurrently (limited by max_concurrent).
        Uses caching to skip already-embedded content.
        """
        if not documents:
            return []

        # Check cache for all documents
        result_embeddings: list[list[float] | None] = [None] * len(documents)
        uncached_indices: list[int] = []
        uncached_docs: list[str] = []

        for i, doc in enumerate(documents):
            cached = self._cache.get(doc)
            if cached is not None:
                result_embeddings[i] = cached
            else:
                uncached_indices.append(i)
                uncached_docs.append(doc)

        cache_hits = len(documents) - len(uncached_indices)
        if cache_hits > 0:
            logger.info(
                "Embedding cache hits",
                cached=cache_hits,
                uncached=len(uncached_indices),
                total=len(documents),
                hit_rate=f"{cache_hits / len(documents) * 100:.1f}%",
            )

        if not uncached_docs:
            return [e for e in result_embeddings if e is not None]

        # Split uncached docs into batches
        batches: list[list[str]] = []
        for i in range(0, len(uncached_docs), batch_size):
            batches.append(uncached_docs[i : i + batch_size])

        logger.info(
            "Parallel embedding starting",
            total_docs=len(uncached_docs),
            batches=len(batches),
            batch_size=batch_size,
            max_concurrent=self.max_concurrent,
        )

        start_time = time.time()

        # Create one client for all batches (connection pooling)
        async with self._create_async_client() as client:
            # Process all batches in parallel (semaphore limits concurrency)
            tasks = [
                self._embed_batch_async(client, batch, i)
                for i, batch in enumerate(batches)
            ]
            batch_results = await asyncio.gather(*tasks)

        # Flatten results and cache
        new_embeddings: list[list[float]] = []
        doc_idx = 0
        for batch, embeddings in zip(batches, batch_results, strict=True):
            for doc, emb in zip(batch, embeddings, strict=True):
                new_embeddings.append(emb)
                self._cache.put(doc, emb)
            doc_idx += len(batch)

        elapsed = time.time() - start_time
        docs_per_sec = len(uncached_docs) / elapsed if elapsed > 0 else 0

        logger.info(
            "Parallel embedding complete",
            docs=len(uncached_docs),
            elapsed=f"{elapsed:.1f}s",
            docs_per_sec=f"{docs_per_sec:.1f}",
        )

        # Merge cached and new embeddings
        for idx, emb in zip(uncached_indices, new_embeddings, strict=True):
            result_embeddings[idx] = emb

        return [e for e in result_embeddings if e is not None]

    def embed_chunks(
        self,
        chunks: list[Chunk],
        on_progress: Callable[[str], None] | None = None,
    ) -> list[Chunk]:
        """Generate embeddings for chunks using parallel processing."""
        if not chunks:
            return chunks

        texts = [chunk.text for chunk in chunks]

        # Use async parallel embedding via event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Already in async context - use sync fallback
                embeddings = self.embed_documents(texts, batch_size=DEFAULT_BATCH_SIZE)
            else:
                embeddings = loop.run_until_complete(
                    self.aembed_documents_parallel(texts)
                )
        except RuntimeError:
            # No event loop - create one
            embeddings = asyncio.run(self.aembed_documents_parallel(texts))

        for chunk, embedding in zip(chunks, embeddings, strict=True):
            chunk.embedding = embedding

        if on_progress:
            on_progress(f"Embedded {len(chunks)} chunks")

        return chunks

    async def aembed_chunks(
        self,
        chunks: list[Chunk],
        on_progress: Callable[[str], None] | None = None,
    ) -> list[Chunk]:
        """Async version of embed_chunks with parallel processing."""
        if not chunks:
            return chunks

        texts = [chunk.text for chunk in chunks]
        embeddings = await self.aembed_documents_parallel(texts)

        for chunk, embedding in zip(chunks, embeddings, strict=True):
            chunk.embedding = embedding

        if on_progress:
            on_progress(f"Embedded {len(chunks)} chunks")

        return chunks

    async def aembed_query(self, query: str) -> list[float]:
        """Async version of embed_query with retry logic and caching."""
        # Check cache first
        cached = self._cache.get(query)
        if cached is not None:
            return cached

        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            # Create fresh client each attempt to avoid event loop issues
            async with self._create_async_client() as client:
                try:
                    response = await client.post(
                        f"{self.base_url}/api/embed",
                        json={"model": self.model, "input": query},
                    )
                    embeddings = self._handle_embed_response(response, input_count=1)
                    result = embeddings[0]
                    self._cache.put(query, result)
                    return result

                except httpx.ConnectError as e:
                    last_error = OllamaConnectionError(
                        f"Cannot connect to Ollama at {self.base_url}: {e}"
                    )
                except httpx.TimeoutException as e:
                    last_error = OllamaConnectionError(
                        f"Ollama request timed out: {e}"
                    )
                except (OllamaModelError, OllamaEmbedderError):
                    raise
                except Exception as e:
                    last_error = OllamaEmbedderError(f"Unexpected error: {e}")

            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAY_BASE * (2**attempt)
                logger.warning(
                    "Ollama aembed_query retry",
                    attempt=attempt + 1,
                    delay=delay,
                    error=str(last_error),
                )
                await asyncio.sleep(delay)

        raise last_error or OllamaEmbedderError("Max retries exceeded")

    async def aembed_documents(
        self, documents: list[str], batch_size: int = DEFAULT_BATCH_SIZE
    ) -> list[list[float]]:
        """Async embed_documents - delegates to parallel implementation."""
        return await self.aembed_documents_parallel(documents, batch_size=batch_size)
