"""
Ollama Embedder

Provides embedding generation using Ollama's native API.
Drop-in replacement for the embedding layer when using Ollama models.

Features:
- Parallel batch processing for faster embedding
- Content-based caching to avoid re-embedding
- Connection pooling for efficiency
- Retry logic for transient failures
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

import httpx

from roboco.config import settings
from roboco.logging import get_logger
from roboco.services.exceptions import (
    HTTP_TOO_MANY_REQUESTS,
    RateLimitError,
    parse_retry_after_header,
)

logger = get_logger(__name__)

# Retry configuration — ConnectError / Timeout (existing, unchanged)
MAX_RETRIES = 3
RETRY_DELAY_BASE = 0.5  # seconds, exponential backoff

# Retry configuration — HTTP 429 / RateLimitError (new outer loop)
RATE_LIMIT_MAX_RETRIES = 5

# Parallel processing configuration
MAX_CONCURRENT_BATCHES = 4  # Number of batches to process in parallel
DEFAULT_BATCH_SIZE = 32  # Default batch size

# Keep the embedding model resident in Ollama. It runs on CPU and Ollama's
# default 5-min idle unload means a `say` after an idle window pays a cold 2.4 GB
# reload before embedding; under contention with glm-5:cloud that overran the
# embed retry window and dropped the background conversation ingest. -1 = never
# unload (sent as `keep_alive` on every /api/embed request).
EMBED_KEEP_ALIVE = -1


@dataclass
class Chunk:
    """Lightweight document chunk carrying text and its embedding vector.

    Replaces the piragi.types.Chunk dependency so this module compiles
    without piragi installed.
    """

    text: str
    embedding: list[float] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


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
        model: str = "qwen3-embedding:0.6b",
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
        # Semaphore for limiting concurrent requests. Track the loop it was
        # created on so we can rebuild when the loop rotates.
        self._semaphore: asyncio.Semaphore | None = None
        self._semaphore_loop: asyncio.AbstractEventLoop | None = None

    def _embed_payload(self, input_data: str | list[str]) -> dict[str, object]:
        """Build the /api/embed JSON body, pinning keep_alive so the model stays
        resident in Ollama (see EMBED_KEEP_ALIVE)."""
        return {
            "model": self.model,
            "input": input_data,
            "keep_alive": EMBED_KEEP_ALIVE,
        }

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
        """Get or create semaphore for limiting concurrent requests.

        asyncio.Semaphore binds to the event loop it was created in. If the
        orchestrator's loop rotates (e.g. lifespan restart, test teardown),
        a cached semaphore raises "bound to a different event loop". Detect
        loop rotation by comparing the current running loop to the one we
        recorded at creation time, and rebuild if they differ.
        """
        current_loop = asyncio.get_running_loop()
        if self._semaphore is None or self._semaphore_loop is not current_loop:
            self._semaphore = asyncio.Semaphore(self.max_concurrent)
            self._semaphore_loop = current_loop
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

    @staticmethod
    def _rl_backoff(retry_after: float | None, rl_attempt: int) -> float:
        """Backoff seconds for a 429: honor Retry-After, else exponential."""
        return retry_after if retry_after is not None else float(2**rl_attempt)

    @staticmethod
    def _map_embed_error(e: Exception, base_url: str) -> Exception:
        """Map a raw request exception to the appropriate Ollama embedder error."""
        if isinstance(e, httpx.ConnectError):
            return OllamaConnectionError(f"Cannot connect to Ollama at {base_url}: {e}")
        if isinstance(e, httpx.TimeoutException):
            return OllamaConnectionError(f"Ollama request timed out: {e}")
        return OllamaEmbedderError(f"Unexpected error: {e}")

    @staticmethod
    def _log_429(rl_attempt: int, backoff: float) -> None:
        """Log an Ollama 429 rate-limit retry."""
        logger.warning(
            "Ollama rate limited (429), retrying",
            provider="ollama",
            attempt=rl_attempt + 1,
            max_retries=RATE_LIMIT_MAX_RETRIES,
            backoff_duration=backoff,
        )

    @staticmethod
    def _sleep_connect_retry(
        attempt: int, last_error: Exception | None, label: str, **extra: Any
    ) -> None:
        """Exponential backoff between ConnectError/Timeout retries.

        No sleep on the final attempt — the caller raises ``last_error`` then.
        """
        if attempt < MAX_RETRIES - 1:
            delay = RETRY_DELAY_BASE * (2**attempt)
            logger.warning(
                label, attempt=attempt + 1, delay=delay, error=str(last_error), **extra
            )
            time.sleep(delay)

    @staticmethod
    async def _asleep_connect_retry(
        attempt: int, last_error: Exception | None, label: str, **extra: Any
    ) -> None:
        """Async counterpart of :meth:`_sleep_connect_retry`."""
        if attempt < MAX_RETRIES - 1:
            delay = RETRY_DELAY_BASE * (2**attempt)
            logger.warning(
                label, attempt=attempt + 1, delay=delay, error=str(last_error), **extra
            )
            await asyncio.sleep(delay)

    def embed_query(
        self,
        query: str,
        task_instruction: str | None = None,
    ) -> list[float]:
        """Generate embedding for a single query.

        Retry behaviour (two independent concerns, non-overlapping):
        - ConnectError / TimeoutException: up to MAX_RETRIES=3 attempts with
          0.5/1/2 s exponential backoff (existing behaviour, unchanged).
        - HTTP 429 (rate limit): outer loop up to RATE_LIMIT_MAX_RETRIES=5,
          respecting Retry-After header.  A 429 response does NOT trigger the
          ConnectError path.
        """
        _ = task_instruction

        # Check cache first
        cached = self._cache.get(query)
        if cached is not None:
            return cached

        client = self._get_sync_client()
        last_rl_retry_after: float | None = None

        for rl_attempt in range(RATE_LIMIT_MAX_RETRIES):
            got_429 = False
            last_error: Exception | None = None

            # --- inner loop: ConnectError / Timeout (unchanged) ---
            for attempt in range(MAX_RETRIES):
                try:
                    response = client.post(
                        f"{self.base_url}/api/embed",
                        json=self._embed_payload(query),
                    )
                    # 429 check — must NOT enter the ConnectError path
                    if response.status_code == HTTP_TOO_MANY_REQUESTS:
                        last_rl_retry_after = parse_retry_after_header(response)
                        self._log_429(
                            rl_attempt,
                            self._rl_backoff(last_rl_retry_after, rl_attempt),
                        )
                        got_429 = True
                        break  # break inner loop; outer loop will sleep + retry

                    embeddings = self._handle_embed_response(response, input_count=1)
                    result = embeddings[0]
                    self._cache.put(query, result)
                    return result

                except (OllamaModelError, OllamaEmbedderError):
                    raise
                except Exception as e:
                    last_error = self._map_embed_error(e, self.base_url)

                self._sleep_connect_retry(
                    attempt, last_error, "Ollama embed_query retry"
                )
            # --- end inner loop ---

            if not got_429:
                # ConnectError / Timeout exhausted — same behaviour as before
                raise last_error or OllamaEmbedderError("Max retries exceeded")

            # 429: sleep and try again (outer loop)
            if rl_attempt < RATE_LIMIT_MAX_RETRIES - 1:
                time.sleep(self._rl_backoff(last_rl_retry_after, rl_attempt))

        raise RateLimitError(provider="ollama", retry_after=last_rl_retry_after)

    def _embed_batch_sync(
        self, client: httpx.Client, batch: list[str], batch_index: int
    ) -> list[list[float]]:
        """Embed a single batch synchronously.

        Same two-concern retry composition as :meth:`embed_query`:
        inner ConnectError/Timeout loop (unchanged) + outer 429 loop.
        """
        last_rl_retry_after: float | None = None

        for rl_attempt in range(RATE_LIMIT_MAX_RETRIES):
            got_429 = False
            last_error: Exception | None = None

            for attempt in range(MAX_RETRIES):
                try:
                    response = client.post(
                        f"{self.base_url}/api/embed",
                        json=self._embed_payload(batch),
                    )
                    if response.status_code == HTTP_TOO_MANY_REQUESTS:
                        last_rl_retry_after = parse_retry_after_header(response)
                        self._log_429(
                            rl_attempt,
                            self._rl_backoff(last_rl_retry_after, rl_attempt),
                        )
                        got_429 = True
                        break

                    return self._handle_embed_response(response, input_count=len(batch))

                except (OllamaModelError, OllamaEmbedderError):
                    raise
                except Exception as e:
                    last_error = self._map_embed_error(e, self.base_url)

                self._sleep_connect_retry(
                    attempt,
                    last_error,
                    "Ollama embed_documents retry",
                    batch_index=batch_index,
                )

            if not got_429:
                raise last_error or OllamaEmbedderError("Max retries exceeded")

            if rl_attempt < RATE_LIMIT_MAX_RETRIES - 1:
                time.sleep(self._rl_backoff(last_rl_retry_after, rl_attempt))

        raise RateLimitError(provider="ollama", retry_after=last_rl_retry_after)

    def _partition_cached_documents(
        self, documents: list[str]
    ) -> tuple[list[list[float] | None], list[int], list[str]]:
        """Split documents into (pre-filled slots, uncached indices, uncached texts)."""
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
        return result_embeddings, uncached_indices, uncached_docs

    @staticmethod
    def _merge_embeddings(
        result_embeddings: list[list[float] | None],
        uncached_indices: list[int],
        new_embeddings: list[list[float]],
    ) -> list[list[float]]:
        """Merge freshly-computed embeddings into preallocated result list."""
        for idx, emb in zip(uncached_indices, new_embeddings, strict=True):
            result_embeddings[idx] = emb
        return [e for e in result_embeddings if e is not None]

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

        (
            result_embeddings,
            uncached_indices,
            uncached_docs,
        ) = self._partition_cached_documents(documents)

        if uncached_indices:
            logger.info(
                "Embedding cache stats",
                cached=len(documents) - len(uncached_indices),
                uncached=len(uncached_indices),
                total=len(documents),
            )

        if not uncached_docs:
            return [e for e in result_embeddings if e is not None]

        client = self._get_sync_client()
        new_embeddings: list[list[float]] = []

        for i in range(0, len(uncached_docs), batch_size):
            batch = uncached_docs[i : i + batch_size]
            embeddings = self._embed_batch_sync(client, batch, batch_index=i)
            new_embeddings.extend(embeddings)
            for doc, emb in zip(batch, embeddings, strict=True):
                self._cache.put(doc, emb)

        return self._merge_embeddings(
            result_embeddings, uncached_indices, new_embeddings
        )

    async def _embed_batch_async(
        self,
        client: httpx.AsyncClient,
        batch: list[str],
        batch_index: int,
    ) -> list[list[float]]:
        """Embed a single batch with semaphore-limited concurrency.

        Same two-concern retry composition as :meth:`embed_query`:
        inner ConnectError/Timeout loop (unchanged) + outer 429 loop (async).
        """
        semaphore = self._get_semaphore()

        async with semaphore:
            last_rl_retry_after: float | None = None

            for rl_attempt in range(RATE_LIMIT_MAX_RETRIES):
                got_429 = False
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
                            json=self._embed_payload(batch),
                        )
                        if response.status_code == HTTP_TOO_MANY_REQUESTS:
                            last_rl_retry_after = parse_retry_after_header(response)
                            self._log_429(
                                rl_attempt,
                                self._rl_backoff(last_rl_retry_after, rl_attempt),
                            )
                            got_429 = True
                            break

                        return self._handle_embed_response(
                            response, input_count=len(batch)
                        )

                    except (OllamaModelError, OllamaEmbedderError):
                        raise
                    except Exception as e:
                        last_error = self._map_embed_error(e, self.base_url)

                    await self._asleep_connect_retry(
                        attempt,
                        last_error,
                        "Parallel embed batch retry",
                        batch_index=batch_index,
                    )

                if not got_429:
                    raise last_error or OllamaEmbedderError("Max retries exceeded")

                if rl_attempt < RATE_LIMIT_MAX_RETRIES - 1:
                    await asyncio.sleep(
                        self._rl_backoff(last_rl_retry_after, rl_attempt)
                    )

            raise RateLimitError(provider="ollama", retry_after=last_rl_retry_after)

    async def _run_parallel_batches(
        self, batches: list[list[str]]
    ) -> list[list[list[float]]]:
        """Execute all batches concurrently via a shared async client."""
        async with self._create_async_client() as client:
            tasks = [
                self._embed_batch_async(client, batch, i)
                for i, batch in enumerate(batches)
            ]
            return await asyncio.gather(*tasks)

    def _collect_and_cache(
        self,
        batches: list[list[str]],
        batch_results: list[list[list[float]]],
    ) -> list[list[float]]:
        """Flatten batch results and populate the embedding cache."""
        new_embeddings: list[list[float]] = []
        for batch, embeddings in zip(batches, batch_results, strict=True):
            for doc, emb in zip(batch, embeddings, strict=True):
                new_embeddings.append(emb)
                self._cache.put(doc, emb)
        return new_embeddings

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

        (
            result_embeddings,
            uncached_indices,
            uncached_docs,
        ) = self._partition_cached_documents(documents)

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

        batches: list[list[str]] = [
            uncached_docs[i : i + batch_size]
            for i in range(0, len(uncached_docs), batch_size)
        ]

        logger.info(
            "Parallel embedding starting",
            total_docs=len(uncached_docs),
            batches=len(batches),
            batch_size=batch_size,
            max_concurrent=self.max_concurrent,
        )

        start_time = time.time()
        batch_results = await self._run_parallel_batches(batches)
        new_embeddings = self._collect_and_cache(batches, batch_results)

        elapsed = time.time() - start_time
        docs_per_sec = len(uncached_docs) / elapsed if elapsed > 0 else 0

        logger.info(
            "Parallel embedding complete",
            docs=len(uncached_docs),
            elapsed=f"{elapsed:.1f}s",
            docs_per_sec=f"{docs_per_sec:.1f}",
        )

        return self._merge_embeddings(
            result_embeddings, uncached_indices, new_embeddings
        )

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
        """Async version of embed_query with retry logic and caching.

        Same two-concern retry composition as :meth:`embed_query`:
        inner ConnectError/Timeout loop (unchanged) + outer 429 loop (async).
        """
        # Check cache first
        cached = self._cache.get(query)
        if cached is not None:
            return cached

        last_rl_retry_after: float | None = None

        for rl_attempt in range(RATE_LIMIT_MAX_RETRIES):
            got_429 = False
            last_error: Exception | None = None

            for attempt in range(MAX_RETRIES):
                # Create fresh client each attempt to avoid event loop issues
                async with self._create_async_client() as client:
                    try:
                        response = await client.post(
                            f"{self.base_url}/api/embed",
                            json=self._embed_payload(query),
                        )
                        if response.status_code == HTTP_TOO_MANY_REQUESTS:
                            last_rl_retry_after = parse_retry_after_header(response)
                            self._log_429(
                                rl_attempt,
                                self._rl_backoff(last_rl_retry_after, rl_attempt),
                            )
                            got_429 = True
                            break

                        embeddings = self._handle_embed_response(
                            response, input_count=1
                        )
                        result = embeddings[0]
                        self._cache.put(query, result)
                        return result

                    except (OllamaModelError, OllamaEmbedderError):
                        raise
                    except Exception as e:
                        last_error = self._map_embed_error(e, self.base_url)

                # A 429 already broke the inner loop above; otherwise back off.
                await self._asleep_connect_retry(
                    attempt, last_error, "Ollama aembed_query retry"
                )

            if not got_429:
                raise last_error or OllamaEmbedderError("Max retries exceeded")

            if rl_attempt < RATE_LIMIT_MAX_RETRIES - 1:
                await asyncio.sleep(self._rl_backoff(last_rl_retry_after, rl_attempt))

        raise RateLimitError(provider="ollama", retry_after=last_rl_retry_after)

    async def aembed_documents(
        self, documents: list[str], batch_size: int = DEFAULT_BATCH_SIZE
    ) -> list[list[float]]:
        """Async embed_documents - delegates to parallel implementation."""
        return await self.aembed_documents_parallel(documents, batch_size=batch_size)
