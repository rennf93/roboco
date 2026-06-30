"""#96/#97: the periodic flush must (a) actually remove a ready buffer after
notifying callbacks (``get_ready_buffers`` only peeks — without a flush the
buffer map grows unbounded), and (b) offload each sync callback to a thread so
a slow callback can't block the flush task / event loop.
"""

from __future__ import annotations

import threading
from uuid import UUID, uuid4

import pytest
from roboco.models.transcription import TranscriptionConfig
from roboco.services.transcription import TranscriptionService


def _svc() -> TranscriptionService:
    # Tiny intervals so a single flush tick is fast and deterministic.
    return TranscriptionService(
        TranscriptionConfig(
            min_chars_for_extraction=2,
            idle_threshold_seconds=0.0,
            flush_interval_seconds=0.01,
        )
    )


def _ready_buffer(svc: TranscriptionService, content: str) -> tuple[UUID, UUID]:
    """Create a buffer for a fresh agent/session, append content, mark it
    complete (ready for extraction). Returns the (agent_id, session_id)."""
    agent_id, session_id, channel_id = uuid4(), uuid4(), uuid4()
    buffer = svc.get_buffer(
        agent_id=agent_id, session_id=session_id, channel_id=channel_id
    )
    buffer.append(content)
    buffer.is_complete = True
    return agent_id, session_id


@pytest.mark.asyncio
async def test_periodic_flush_removes_ready_buffer() -> None:
    """#96: after a flush tick, a ready buffer is popped from ``_buffers``
    (not re-yielded forever). Without the flush call the map grew unbounded."""
    svc = _svc()
    agent_id, session_id = _ready_buffer(svc, "some content")
    assert agent_id in svc._buffers

    await svc._flush_ready_buffers()

    # The ready buffer was flushed (removed), not left to accumulate.
    assert agent_id not in svc._buffers or session_id not in svc._buffers[agent_id]


@pytest.mark.asyncio
async def test_periodic_flush_invokes_callbacks() -> None:
    """#96: callbacks are still notified on the flush tick."""
    svc = _svc()
    _ready_buffer(svc, "content for callback")

    seen: list[object] = []
    svc.register_callback(seen.append)

    await svc._flush_ready_buffers()

    assert len(seen) == 1


@pytest.mark.asyncio
async def test_periodic_flush_offloads_sync_callback_to_thread() -> None:
    """#97: a sync callback runs off the event loop (in a worker thread) so a
    slow callback can't block the flush task. Assert the callback executes in a
    thread that is NOT the running event loop's main thread."""
    svc = _svc()
    _ready_buffer(svc, "content for offload")

    callback_thread: list[object] = []

    def _slow_callback(_b: object) -> None:
        # Record the thread id; a sleep here would block the loop if not offloaded.
        callback_thread.append(threading.get_ident())

    svc.register_callback(_slow_callback)

    await svc._flush_ready_buffers()

    assert len(callback_thread) == 1
    # The callback ran in a worker thread, not on the event loop's main thread.
    assert callback_thread[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_periodic_flush_callback_error_does_not_abort_flush() -> None:
    """A failing callback must not abort the flush (the buffer is still removed
    and a sibling callback still runs)."""
    svc = _svc()
    agent_id, session_id = _ready_buffer(svc, "content with bad callback")

    sibling_seen: list[object] = []

    def _bad(_b: object) -> None:
        raise RuntimeError("boom")

    def _good(b: object) -> None:
        sibling_seen.append(b)

    svc.register_callback(_bad)
    svc.register_callback(_good)

    await svc._flush_ready_buffers()  # must not raise

    assert len(sibling_seen) == 1
    assert agent_id not in svc._buffers or session_id not in svc._buffers[agent_id]
