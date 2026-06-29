"""OptimalService.close() must cancel the startup indexing task, not just the
periodic update task.

The startup auto-indexing task (``_indexing_task``) runs in the background and
can still be mid-flight when ``close()`` runs (slow Ollama, a large repo). If
``close()`` only cancels the periodic update task and then clears the plugins,
the still-running indexing task writes against closed/cleared plugins —
errors on shutdown. ``close()`` must cancel and await ``_indexing_task`` too.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.models.optimal import IndexType
from roboco.services.optimal import OptimalService


def _svc() -> OptimalService:
    svc = OptimalService.__new__(OptimalService)
    svc._initialized = True
    svc._plugins = {}
    svc._indexing_task = None
    svc._periodic_update_task = None
    svc._file_mtimes = {}
    svc._docs_root = None
    return svc


@pytest.mark.asyncio
async def test_close_cancels_running_indexing_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A still-running startup indexing task is cancelled + awaited on close,
    so it can't write against cleared plugins."""
    svc = _svc()
    plugin = MagicMock()
    plugin.close = AsyncMock()
    svc._plugins[IndexType.DOCUMENTATION] = plugin

    # A real background task that would otherwise run for the whole test.
    indexing_task = asyncio.create_task(asyncio.sleep(100))
    svc._indexing_task = indexing_task

    monkeypatch.setattr(
        "roboco.services.optimal_brain.shared_embedder.close_shared_embedder",
        AsyncMock(),
    )

    await svc.close()

    assert indexing_task.cancelled(), "indexing task was not cancelled on close"
    assert svc._plugins == {}, "plugins were not cleared on close"
    assert svc._initialized is False


@pytest.mark.asyncio
async def test_close_cancels_indexing_task_before_clearing_plugins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The indexing task is cancelled BEFORE the plugins are closed/cleared —
    otherwise a mid-flight index write lands on a closed plugin."""
    svc = _svc()

    closed_order: list[str] = []

    async def _slow_plugin_close() -> None:
        closed_order.append("plugin")
        await asyncio.sleep(0)

    plugin = MagicMock()
    plugin.close = _slow_plugin_close
    svc._plugins[IndexType.CODE] = plugin

    async def _indexing_work() -> None:
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            closed_order.append("indexing_cancelled")
            raise

    svc._indexing_task = asyncio.create_task(_indexing_work())
    # Let the indexing task start and reach its await point so a cancel
    # actually runs its body's except-branch (a not-yet-started task is
    # cancelled without ever entering the coroutine).
    await asyncio.sleep(0)

    monkeypatch.setattr(
        "roboco.services.optimal_brain.shared_embedder.close_shared_embedder",
        AsyncMock(),
    )

    await svc.close()

    assert closed_order[0] == "indexing_cancelled", (
        f"indexing task must be cancelled before plugins are closed, got {closed_order}"
    )
