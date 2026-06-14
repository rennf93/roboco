"""
roboco.kb — Knowledge Base primitives (piragi-free).

Public API:
    Chunk               — lightweight text+embedding container
    OllamaEmbedder      — HTTP embedder via Ollama native /api/embed
    get_shared_embedder — async singleton factory
    close_shared_embedder — release the shared singleton
"""

from roboco.kb.ollama_embedder import (
    Chunk,
    EmbeddingCache,
    OllamaConnectionError,
    OllamaEmbedder,
    OllamaEmbedderError,
    OllamaModelError,
)
from roboco.kb.shared_embedder import (
    Embedder,
    close_shared_embedder,
    get_shared_embedder,
)

__all__ = [
    "Chunk",
    "Embedder",
    "EmbeddingCache",
    "OllamaConnectionError",
    "OllamaEmbedder",
    "OllamaEmbedderError",
    "OllamaModelError",
    "close_shared_embedder",
    "get_shared_embedder",
]
