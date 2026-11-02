"""Structural type for the composed OllamaEmbedder, TYPE_CHECKING-only.

``OllamaEmbedder`` is composed from mixins
(``roboco/services/optimal_brain/ollama_embedder.py``) whose methods were moved verbatim
out of the one class. mypy checks each mixin against its own bases only, so a call into
a sibling mixin, or into OllamaEmbedder's own members, would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``OllamaEmbedderSelf``, which
declares that cross-class surface: ``FooSurface`` holds what ``Foo`` offers the other
classes (``OllamaEmbedderSurface`` what ``OllamaEmbedder`` itself does), with each
method's own decorators, parameters and return type, so no moved body changes.
``tests/unit/services/optimal_brain/test_ollama_embedder_mixins.py`` fails on a stub its
surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import asyncio

    import httpx

    from roboco.services.optimal_brain.ollama_embedder import EmbeddingCache


class OllamaEmbedResponsesSurface(Protocol):
    """``OllamaEmbedResponses``'s members that other classes reach."""

    @staticmethod
    async def _asleep_connect_retry(
        attempt: int, last_error: Exception | None, label: str, **extra: Any
    ) -> None: ...
    def _handle_embed_response(
        self, response: httpx.Response, input_count: int = ...
    ) -> list[list[float]]: ...
    @staticmethod
    def _log_429(rl_attempt: int, backoff: float) -> None: ...
    @staticmethod
    def _map_embed_error(e: Exception, base_url: str) -> Exception: ...
    @staticmethod
    def _rl_backoff(retry_after: float | None, rl_attempt: int) -> float: ...
    @staticmethod
    def _sleep_connect_retry(
        attempt: int, last_error: Exception | None, label: str, **extra: Any
    ) -> None: ...


class OllamaEmbedderSurface(Protocol):
    """``OllamaEmbedder``'s members that other classes reach."""

    def _create_async_client(self) -> httpx.AsyncClient: ...
    def _embed_payload(self, input_data: str | list[str]) -> dict[str, object]: ...
    def _get_semaphore(self) -> asyncio.Semaphore: ...
    def _get_sync_client(self) -> httpx.Client: ...


class OllamaEmbedderSelf(OllamaEmbedResponsesSurface, OllamaEmbedderSurface, Protocol):
    """Every cross-class member a moved method body reaches via ``self``:
    OllamaEmbedder's instance attributes here, each class's members on its surface."""

    _cache: EmbeddingCache
    _dimensions: int | None
    _semaphore: asyncio.Semaphore | None
    _semaphore_loop: asyncio.AbstractEventLoop | None
    _sync_client: httpx.Client | None
    base_url: str
    batch_size: int
    max_concurrent: int
    model: str
    timeout: float
