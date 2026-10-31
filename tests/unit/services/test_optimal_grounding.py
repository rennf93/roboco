"""Grounding-layer regression tests for the Optimal/RAG stack.

Covers three failure modes that surfaced at runtime:

1. ``get_optimal_service`` published a not-yet-initialized singleton while
   ``initialize()`` was still awaiting, so a concurrent caller hit
   "OptimalService not initialized. Call initialize() first." during indexing.
2. ``roboco_kb_search`` forwarded the legacy alias ``index_types=['docs']``,
   which is not a valid ``IndexType`` value ('documentation' is), producing a
   400 at the route.
3. The mentor route let exceptions from ``mentor.ask`` escape as a bare 500
   with no log of the true upstream cause.
4. The service was one per process, though its vector-store pools belong to
   the loop that opened them: a test's ``close_optimal_service`` closed an
   earlier test's service on the wrong loop, and its connections stayed open
   until garbage collection.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from roboco.mcp.optimal_server import normalize_index_types
from roboco.models.optimal import IndexType
from roboco.services import optimal as optimal_module
from roboco.services.optimal import (
    OptimalService,
    close_optimal_service,
    get_optimal_service,
)
from roboco.services.optimal_brain.indexes.base import BaseIndexPlugin

# ---------------------------------------------------------------------------
# Sub-issue 2: kb_search must not forward the invalid 'docs' alias
# ---------------------------------------------------------------------------


def test_normalize_index_types_maps_docs_alias_to_documentation() -> None:
    """The legacy 'docs' alias must become the valid 'documentation' value.

    ``IndexType('docs')`` raises ``ValueError`` — the only valid value is
    ``IndexType.DOCUMENTATION`` whose string value is 'documentation'.
    """
    assert normalize_index_types(["docs"]) == ["documentation"]
    # Every normalized value must be a constructible IndexType.
    normalized = normalize_index_types(["docs"])
    assert normalized is not None
    for value in normalized:
        IndexType(value)


def test_normalize_index_types_passes_valid_values_through() -> None:
    assert normalize_index_types(["documentation", "decisions"]) == [
        "documentation",
        "decisions",
    ]


def test_normalize_index_types_none_returns_none() -> None:
    assert normalize_index_types(None) is None


# ---------------------------------------------------------------------------
# Sub-issue 1: the init entrypoint must never expose an uninitialized singleton
# ---------------------------------------------------------------------------


class _SlowInitService(OptimalService):
    """OptimalService whose initialize() yields control mid-flight.

    This reproduces the publish-before-initialize race: while one coroutine
    is awaiting inside ``initialize()``, a second coroutine calls
    ``get_optimal_service()``. With the old code the second caller received
    the instance with ``_initialized == False``.
    """

    init_calls = 0

    async def initialize(self) -> None:
        type(self).init_calls += 1
        # Cooperatively yield so a concurrent get_optimal_service() can run
        # during the window the old code left the instance unpublished/uninit.
        await asyncio.sleep(0)
        self._initialized = True


@pytest.mark.asyncio
async def test_get_optimal_service_never_returns_uninitialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent callers must all receive a fully-initialized singleton.

    The indexing entrypoint calls ``_get_plugin`` which raises
    "OptimalService not initialized" when ``_initialized`` is False. This test
    asserts no concurrent caller can observe that state.
    """
    _SlowInitService.init_calls = 0
    monkeypatch.setattr(optimal_module, "OptimalService", _SlowInitService)

    try:
        results: Any = await asyncio.gather(
            get_optimal_service(),
            get_optimal_service(),
            get_optimal_service(),
        )
        for svc in results:
            assert svc._initialized is True
        # All callers share the one singleton, initialized exactly once.
        assert len({id(s) for s in results}) == 1
        assert _SlowInitService.init_calls == 1
    finally:
        await close_optimal_service()


@pytest.mark.asyncio
async def test_indexing_entrypoint_does_not_raise_not_initialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller obtaining the service mid-init must not hit _get_plugin's guard."""
    _SlowInitService.init_calls = 0
    monkeypatch.setattr(optimal_module, "OptimalService", _SlowInitService)

    async def _use_service() -> None:
        svc = await get_optimal_service()
        # Mirror what index_documentation does first: resolve the plugin,
        # which raises RuntimeError("OptimalService not initialized...") if
        # the singleton was published before initialize() completed.
        svc._plugins[IndexType.DOCUMENTATION] = _FakePlugin()
        svc._get_plugin(IndexType.DOCUMENTATION)

    try:
        await asyncio.gather(_use_service(), _use_service())
    finally:
        await close_optimal_service()


class _FakePlugin(BaseIndexPlugin):
    """Minimal stand-in so _get_plugin returns without a real plugin."""

    @property
    def index_type(self) -> IndexType:
        return IndexType.DOCUMENTATION

    def prepare_metadata(self, content: str, **kwargs: Any) -> dict[str, Any]:
        return {}

    def build_source_uri(self, doc_id: str | None = None, **kwargs: Any) -> str | None:
        return None

    async def close(self) -> None:
        return None


class _FailingInitService(OptimalService):
    """An initialize that fails, as a plugin's error or a shutdown's
    cancellation makes it fail after earlier plugins opened their stores."""

    closed = 0

    async def initialize(self) -> None:
        raise RuntimeError("plugin init failed")

    async def close(self) -> None:
        type(self).closed += 1


@pytest.mark.asyncio
async def test_a_failed_initialize_closes_the_unpublished_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An initialize that raises leaves the service unpublished, where
    close_optimal_service can't reach it: get_optimal_service closes it,
    releasing the stores the plugins already opened."""
    _FailingInitService.closed = 0
    monkeypatch.setattr(optimal_module, "OptimalService", _FailingInitService)
    try:
        with pytest.raises(RuntimeError, match="plugin init failed"):
            await get_optimal_service()
        assert _FailingInitService.closed == 1
        assert asyncio.get_running_loop() not in (
            optimal_module._OptimalServiceHolder.instances
        )
    finally:
        await close_optimal_service()


class _CountingCloseService(OptimalService):
    """A service with no stores, counting its closes."""

    closed = 0

    async def initialize(self) -> None:
        self._initialized = True

    async def close(self) -> None:
        type(self).closed += 1


@pytest.mark.asyncio
async def test_each_event_loop_gets_its_own_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Another loop can't use this loop's vector-store pools, nor close them."""
    _CountingCloseService.closed = 0
    monkeypatch.setattr(optimal_module, "OptimalService", _CountingCloseService)

    async def _theirs() -> OptimalService:
        service = await get_optimal_service()
        await close_optimal_service()
        return service

    try:
        ours = await get_optimal_service()
        theirs = await asyncio.to_thread(asyncio.run, _theirs())
        assert theirs is not ours
        assert await get_optimal_service() is ours
        assert _CountingCloseService.closed == 1
    finally:
        await close_optimal_service()


@pytest.mark.asyncio
async def test_close_optimal_service_leaves_another_loops_service_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closed from here, another loop's pools stay open: that loop's owner
    closes them, before the loop ends."""
    _CountingCloseService.closed = 0
    monkeypatch.setattr(optimal_module, "OptimalService", _CountingCloseService)

    async def _theirs() -> tuple[OptimalService, asyncio.AbstractEventLoop]:
        return await get_optimal_service(), asyncio.get_running_loop()

    theirs, their_loop = await asyncio.to_thread(asyncio.run, _theirs())
    try:
        await close_optimal_service()
        assert _CountingCloseService.closed == 0
        assert optimal_module._OptimalServiceHolder.instances[their_loop] is theirs
    finally:
        optimal_module._OptimalServiceHolder.instances.pop(their_loop, None)
