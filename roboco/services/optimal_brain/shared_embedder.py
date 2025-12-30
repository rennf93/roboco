"""
Shared Embedder Singleton

Provides a single EmbeddingGenerator instance shared across all index plugins
to avoid loading the SentenceTransformer model 9 times (~3s each = 27s startup).
"""

import asyncio
from typing import TYPE_CHECKING

from roboco.logging import get_logger

if TYPE_CHECKING:
    from piragi.embeddings import EmbeddingGenerator

logger = get_logger(__name__)


class _SharedEmbedderHolder:
    """Holder class for shared embedder state (avoids global statement)."""

    instance: "EmbeddingGenerator | None" = None
    lock: asyncio.Lock | None = None

    @classmethod
    def get_lock(cls) -> asyncio.Lock:
        """Get or create the lock."""
        if cls.lock is None:
            cls.lock = asyncio.Lock()
        return cls.lock


async def get_shared_embedder(
    model: str = "BAAI/bge-base-en-v1.5",
    device: str | None = None,
    timeout: float = 60.0,
) -> "EmbeddingGenerator":
    """Get or create the shared embedder instance.

    Thread-safe singleton that loads the model only once.

    Args:
        model: Embedding model name (default: BAAI/bge-base-en-v1.5)
        device: Device to use (None = auto-detect)
        timeout: Max seconds to wait for model loading (default: 60)

    Returns:
        Shared EmbeddingGenerator instance

    Raises:
        TimeoutError: If model loading takes too long
        RuntimeError: If model loading fails
    """
    if _SharedEmbedderHolder.instance is not None:
        return _SharedEmbedderHolder.instance

    async with _SharedEmbedderHolder.get_lock():
        # Double-check after acquiring lock
        if _SharedEmbedderHolder.instance is not None:
            return _SharedEmbedderHolder.instance

        logger.info(
            "Creating shared embedder (one-time load)",
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
            logger.error("Embedder initialization failed", model=model, error=str(e))
            raise RuntimeError(f"Failed to load embedding model: {e}") from e

        logger.info("Shared embedder created successfully")
        return _SharedEmbedderHolder.instance


async def close_shared_embedder() -> None:
    """Release the shared embedder resources."""
    async with _SharedEmbedderHolder.get_lock():
        if _SharedEmbedderHolder.instance is not None:
            logger.info("Closing shared embedder")
            _SharedEmbedderHolder.instance = None
