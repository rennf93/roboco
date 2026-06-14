"""
Shared Embedder Singleton

Provides a single OllamaEmbedder instance shared across all index plugins.
"""

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, Union

from roboco.config import settings
from roboco.logging import get_logger
from roboco.services.optimal_brain.text_chunker import Chunk

if TYPE_CHECKING:
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

    instance: Union["OllamaEmbedder", None] = None
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
    """Get or create the shared OllamaEmbedder instance.

    Thread-safe singleton that loads the model only once.  All embedding is
    performed via Ollama over HTTP; no local model weights are loaded.

    Args:
        model: Embedding model name (default: from settings).
        device: Ignored — kept for API compatibility.
        timeout: Ignored — Ollama embedder connects lazily; kept for
                 API compatibility.

    Returns:
        Shared :class:`~roboco.services.optimal_brain.ollama_embedder.OllamaEmbedder`
        instance.

    Raises:
        RuntimeError: If the embedder instance could not be constructed.
    """
    _ = device
    _ = timeout

    if _SharedEmbedderHolder.instance is not None:
        return _SharedEmbedderHolder.instance

    async with _SharedEmbedderHolder.get_lock():
        # Double-check after acquiring lock (another coroutine may have created it)
        if _SharedEmbedderHolder.instance is None:
            model = model or settings.default_embedding_model

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
