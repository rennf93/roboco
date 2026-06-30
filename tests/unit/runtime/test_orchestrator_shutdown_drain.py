"""Drain ``_bg_tasks`` on shutdown so fire-and-forget writes (respawn_tracker
upserts, audit-log writes, intake first-message delivery) are not abandoned.

Invariant: ``Orchestrator.stop()`` drains ``_bg_tasks`` with a bounded timeout —
short DB writes finish before the process exits (data preserved), a stuck task
is cancelled once the deadline passes (can't hang shutdown). The ``stop_agent``
loop is wrapped so one agent's stop error can't skip the drain.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from roboco.models.runtime import AgentInstance
from roboco.runtime.orchestrator import (
    _SHUTDOWN_DRAIN_TIMEOUT_SECONDS,
    AgentOrchestrator,
)

# Floor encoding the logical-regression guard: a drain deadline below this
# would risk dropping a legitimate short DB write (an upsert that needs a
# second under load) before it commits — the exact data loss this fix targets.
# Named (not magic) for ruff PLR2004.
_MIN_DRAIN_TIMEOUT = 3.0


def _make_orchestrator() -> AgentOrchestrator:
    """AgentOrchestrator with constructor I/O skipped; stop() deps ready.

    ``stop()`` cancels the named loop tasks (all None here → no-op) and the
    agents in ``_instances`` (empty here), then must drain ``_bg_tasks``.
    """
    with patch.object(AgentOrchestrator, "__init__", return_value=None):
        orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._bg_tasks = set()
    orch._pm_respawn_tracker = {}  # #74: stop() flushes this after the drain
    # Every named background loop ``stop()`` cancels — None makes each a no-op
    # so the test exercises ONLY the _bg_tasks drain.
    for attr in (
        "_health_task",
        "_dispatcher_task",
        "_sweeper_task",
        "_rate_limit_probe_task",
        "_strategy_engine_task",
        "_external_pr_poll_task",
        "_self_heal_task",
        "_ci_watch_task",
        "_dep_update_task",
        "_release_manager_task",
    ):
        setattr(orch, attr, None)
    return orch


def test_shutdown_drain_timeout_is_named_module_constant() -> None:
    assert isinstance(_SHUTDOWN_DRAIN_TIMEOUT_SECONDS, int | float)
    assert _SHUTDOWN_DRAIN_TIMEOUT_SECONDS > 0


def test_shutdown_drain_timeout_is_generous() -> None:
    """A short DB upsert under load can legitimately take a moment; the drain
    deadline must not drop it. This guards the logical regression: a too-short
    drain would silently lose the very writes it exists to preserve."""
    assert _SHUTDOWN_DRAIN_TIMEOUT_SECONDS >= _MIN_DRAIN_TIMEOUT


@pytest.mark.asyncio
async def test_stop_drains_completing_bg_task_before_returning() -> None:
    """A bg task that finishes quickly MUST complete (its side effect observed)
    before ``stop()`` returns. Without the drain, ``stop()`` returns immediately
    and the task is abandoned mid-flight — the data-loss tail."""
    orch = _make_orchestrator()
    ran: list[bool] = []

    async def _completes() -> None:
        await asyncio.sleep(0.01)
        ran.append(True)

    orch._bg_tasks.add(asyncio.create_task(_completes()))

    await asyncio.wait_for(orch.stop(), timeout=5.0)

    assert ran == [True], "completing bg task was abandoned by stop()"


@pytest.mark.asyncio
async def test_stop_does_not_hang_on_stuck_bg_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bg task that never completes MUST NOT hang shutdown past the drain
    deadline — it is cancelled once the drain times out. Without the drain,
    a stuck bg task would let ``stop()`` (and thus the process) hang forever.

    Deterministic: the drain deadline is patched tiny so a bounded fail-close is
    asserted in well under a second, never relying on the real 5s default."""
    monkeypatch.setattr(
        "roboco.runtime.orchestrator._SHUTDOWN_DRAIN_TIMEOUT_SECONDS", 0.05
    )
    orch = _make_orchestrator()

    async def _hangs() -> None:
        await asyncio.Future()  # never resolves

    stuck = asyncio.create_task(_hangs())
    orch._bg_tasks.add(stuck)

    await asyncio.wait_for(orch.stop(), timeout=2.0)

    assert stuck.cancelled(), "stuck bg task was not cancelled by the drain"


@pytest.mark.asyncio
async def test_stop_failing_agent_does_not_skip_drain() -> None:
    """If one agent's ``stop_agent`` raises, the drain must still run —
    otherwise a single bad agent would re-introduce the data-loss tail for every
    in-flight bg write. The completing bg task should still finish."""
    orch = _make_orchestrator()
    orch._instances["bad-agent"] = AgentInstance(agent_id="bad-agent")

    async def _raises(_aid: str, **_kw: Any) -> None:
        raise RuntimeError("boom")

    patch.object(orch, "stop_agent", _raises).start()

    ran: list[bool] = []

    async def _completes() -> None:
        await asyncio.sleep(0.01)
        ran.append(True)

    orch._bg_tasks.add(asyncio.create_task(_completes()))

    await asyncio.wait_for(orch.stop(), timeout=5.0)

    assert ran == [True], "failing stop_agent skipped the drain (data lost)"


@pytest.mark.asyncio
async def test_stop_is_idempotent_double_call_is_noop() -> None:
    """stop() is idempotent: the lifespan path and bootstrap's finally block both
    call it, so the second call must be a clean no-op — not a re-drain or re-stop
    of already-stopped agents — guarded by ``_stopped``."""
    orch = _make_orchestrator()
    real_drain = orch._drain_bg_tasks
    drain_calls = 0

    async def counting_drain() -> None:
        nonlocal drain_calls
        drain_calls += 1
        await real_drain()

    # Override via an Any-typed view so the assignment bypasses mypy's
    # method-assign check while staying a plain attribute write (no setattr).
    orch_any: Any = orch
    orch_any._drain_bg_tasks = counting_drain

    await orch.stop()
    assert drain_calls == 1, "first stop() drained the bg tasks"
    assert orch._stopped is True

    await orch.stop()  # safety-net double-call (lifespan already stopped it)
    assert drain_calls == 1, "second stop() must not re-drain (idempotent no-op)"
    assert orch._stopped is True


# ---------------------------------------------------------------------------
# #74: stop() flushes the authoritative in-memory respawn snapshot AFTER the
# bounded drain so a deadline-cancelled fire-and-forget persist can't leave the
# durable count lagging the in-memory counter (which would re-burn the strike
# threshold on the next restart).
# ---------------------------------------------------------------------------

_BE_PM_TID = "11111111-1111-1111-1111-111111111111"
_FE_PM_TID = "22222222-2222-2222-2222-222222222222"
_EXPECTED_FLUSHES = 2  # two seeded respawn rows → two shutdown persists


def _respawn_record(count: int) -> dict[str, Any]:
    return {
        "count": count,
        "last_status": "blocked",
        "last_check": None,
        "tracing_resets": 0,
        "notified": False,
    }


@pytest.mark.asyncio
async def test_stop_flushes_respawn_tracker_after_drain() -> None:
    """#74: stop() writes every in-memory respawn row after the drain, carrying
    the latest count so the durable counter matches memory on the next restart."""
    orch = _make_orchestrator()
    orch_any: Any = orch
    orch_any._pm_respawn_tracker = {
        ("be-pm", _BE_PM_TID): _respawn_record(4),
        ("fe-pm", _FE_PM_TID): _respawn_record(2),
    }
    persist = AsyncMock()
    orch_any._persist_respawn_record = persist

    await asyncio.wait_for(orch.stop(), timeout=5.0)

    assert persist.await_count == _EXPECTED_FLUSHES
    keys = {(c.args[0], c.args[1]) for c in persist.await_args_list}
    assert keys == {("be-pm", _BE_PM_TID), ("fe-pm", _FE_PM_TID)}
    counts = {c.args[0]: c.args[2]["count"] for c in persist.await_args_list}
    assert counts == {"be-pm": 4, "fe-pm": 2}


@pytest.mark.asyncio
async def test_flush_respawn_tracker_noop_when_empty() -> None:
    """An empty tracker flushes nothing — no DB churn on a clean shutdown."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch_any: Any = orch
    orch_any._pm_respawn_tracker = {}
    persist = AsyncMock()
    orch_any._persist_respawn_record = persist

    await orch._flush_respawn_tracker()

    persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_flush_respawn_tracker_swallows_row_errors() -> None:
    """#74: a row whose persist raises must not skip the remaining rows or crash
    shutdown — the in-memory value is gone once the process exits anyway."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch_any: Any = orch
    orch_any._pm_respawn_tracker = {
        ("be-pm", _BE_PM_TID): _respawn_record(4),
        ("fe-pm", _FE_PM_TID): _respawn_record(2),
    }

    async def _persist(slug: str, _tid: str, _record: dict[str, Any]) -> None:
        if slug == "be-pm":
            raise RuntimeError("db down")
        # fe-pm succeeds

    orch_any._persist_respawn_record = _persist

    # Must not raise — the failing row is logged and the rest still flushed.
    await orch._flush_respawn_tracker()
