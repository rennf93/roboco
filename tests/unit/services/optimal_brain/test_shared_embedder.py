"""close_shared_embedder releases the embedder it drops."""

from __future__ import annotations

import pytest
from roboco.services.optimal_brain import shared_embedder
from roboco.services.optimal_brain.ollama_embedder import OllamaEmbedder
from roboco.services.optimal_brain.shared_embedder import (
    close_shared_embedder,
    get_shared_embedder,
)


@pytest.mark.asyncio
async def test_close_shared_embedder_closes_its_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dropped with its sync client open, the embedder's pooled connections
    stay open until garbage collection."""
    monkeypatch.setattr(shared_embedder._SharedEmbedderHolder, "instance", None)
    embedder = await get_shared_embedder()
    assert isinstance(embedder, OllamaEmbedder)
    client = embedder._get_sync_client()

    await close_shared_embedder()

    assert client.is_closed
    assert shared_embedder._SharedEmbedderHolder.instance is None
