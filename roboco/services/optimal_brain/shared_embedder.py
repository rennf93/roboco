"""
Shared Embedder Singleton

Provides a single embedder instance shared across all index plugins.
Supports both Ollama models (qwen3-embedding, ...) and SentenceTransformers (BGE, ...).
"""

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, Union

from piragi.types import Chunk

from roboco.config import settings
from roboco.logging import get_logger

if TYPE_CHECKING:
    from piragi.embeddings import EmbeddingGenerator

    from roboco.services.optimal_brain.ollama_embedder import OllamaEmbedder

logger = get_logger(__name__)


class Embedder(Protocol):
    """Protocol for embedder interface."""

    def embed_query(
        self, query: str, task_instruction: str | None = None
    ) -> list[float]: ...

    def embed_documents(
        self,
        documents: list[str],
        task_instruction: str | None = None,
        batch_size: int = 32,
    ) -> list[list[float]]: ...

    def embed_chunks(
        self,
        chunks: list[Chunk],
        on_progress: Callable[[str], None] | None = None,
    ) -> list[Chunk]: ...


# Known Ollama embedding models
OLLAMA_EMBEDDING_MODELS = {
    "qwen3-embedding",
    "embeddinggemma",
    "nomic-embed-text",
    "mxbai-embed-large",
    "all-minilm",
    "snowflake-arctic-embed",
}


def _is_ollama_model(model: str) -> bool:
    """Check if model name is an Ollama embedding model."""
    model_base = model.split(":", maxsplit=1)[0].lower()
    return model_base in OLLAMA_EMBEDDING_MODELS


class _SharedEmbedderHolder:
    """Holder class for shared embedder state (avoids global statement)."""

    instance: Union["EmbeddingGenerator", "OllamaEmbedder", None] = None
    lock: asyncio.Lock | None = None

    @classmethod
    def get_lock(cls) -> asyncio.Lock:
        """Get or create the lock."""
        if cls.lock is None:
            cls.lock = asyncio.Lock()
        return cls.lock


async def get_shared_embedder(
    model: str | None = None,
    device: str | None = None,
    timeout: float = 60.0,
) -> Embedder:
    """Get or create the shared embedder instance.

    Thread-safe singleton that loads the model only once.
    Automatically selects Ollama or SentenceTransformers based on model name.

    Args:
        model: Embedding model name (default: from settings)
        device: Device to use for SentenceTransformers (None = auto-detect)
        timeout: Max seconds to wait for model loading (default: 60)

    Returns:
        Shared embedder instance (OllamaEmbedder or EmbeddingGenerator)

    Raises:
        TimeoutError: If model loading takes too long
        RuntimeError: If model loading fails
    """
    if _SharedEmbedderHolder.instance is not None:
        return _SharedEmbedderHolder.instance

    async with _SharedEmbedderHolder.get_lock():
        # Double-check after acquiring lock (another coroutine may have created it)
        if _SharedEmbedderHolder.instance is None:
            model = model or settings.default_embedding_model

            # Use Ollama for Ollama models, SentenceTransformers otherwise
            if _is_ollama_model(model):
                logger.info(
                    "Creating shared Ollama embedder",
                    model=model,
                    base_url=settings.ollama_base_url,
                )

                from roboco.services.optimal_brain.ollama_embedder import OllamaEmbedder

                _SharedEmbedderHolder.instance = OllamaEmbedder(
                    model=model,
                    base_url=settings.ollama_base_url,
                )
            else:
                logger.info(
                    "Creating shared SentenceTransformers embedder",
                    model=model,
                    device=device or "auto",
                )

                # Import here to avoid circular imports and defer heavy import
                from piragi.embeddings import EmbeddingGenerator

                # Run model loading in thread to not block event loop
                def _create_embedder() -> "EmbeddingGenerator":
                    return EmbeddingGenerator(
                        model=model,
                        device=device,
                        batch_size=32,
                    )

                try:
                    async with asyncio.timeout(timeout):
                        _SharedEmbedderHolder.instance = await asyncio.to_thread(
                            _create_embedder
                        )
                except TimeoutError:
                    logger.error(
                        "Embedder initialization timed out",
                        model=model,
                        timeout=timeout,
                    )
                    raise TimeoutError(
                        f"Embedding model loading timed out after {timeout}s. "
                        "This may indicate network issues or corrupted model cache."
                    ) from None
                except Exception as e:
                    logger.error(
                        "Embedder initialization failed", model=model, error=str(e)
                    )
                    raise RuntimeError(f"Failed to load embedding model: {e}") from e

            # Validate embedder implements required protocol methods.
            # Using explicit check (not assert) so this survives `python -O`.
            if _SharedEmbedderHolder.instance is None:
                raise RuntimeError(
                    "Shared embedder construction succeeded but instance is None"
                )
            _validate_embedder_protocol(_SharedEmbedderHolder.instance, model)

            logger.info("Shared embedder created successfully", model=model)

        if _SharedEmbedderHolder.instance is None:
            raise RuntimeError("Shared embedder not initialized")
        return _SharedEmbedderHolder.instance


def _validate_embedder_protocol(embedder: Embedder, model: str) -> None:
    """
    Validate that embedder implements the required protocol methods.

    Checks at creation time rather than failing during first use.

    Args:
        embedder: The embedder instance to validate
        model: Model name for error messages

    Raises:
        RuntimeError: If embedder is missing required methods
    """
    required_methods = ["embed_query", "embed_documents", "embed_chunks"]
    missing = [m for m in required_methods if not callable(getattr(embedder, m, None))]

    if missing:
        raise RuntimeError(
            f"Embedder for model '{model}' is missing required methods: {missing}. "
            f"Embedder type: {type(embedder).__name__}"
        )


async def close_shared_embedder() -> None:
    """Release the shared embedder resources."""
    async with _SharedEmbedderHolder.get_lock():
        if _SharedEmbedderHolder.instance is not None:
            logger.info("Closing shared embedder")
            _SharedEmbedderHolder.instance = None
