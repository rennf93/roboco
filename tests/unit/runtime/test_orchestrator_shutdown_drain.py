"""F070 — fire-and-forget ``_bg_tasks`` (respawn_tracker upserts, audit-log
writes, intake first-message delivery) were never cancelled or drained on
shutdown. ``Orchestrator.stop()`` cancelled only the named loop tasks and the
agents, then returned, abandoning any in-flight ``_schedule_bg`` work.

The data-loss tail: an in-flight ``_persist_respawn_record`` upsert dropped at
shutdown means the last few gate-mutation strikes never reach the DB. The
in-memory counter dies with the process; ``restore_respawn_tracker()`` on the
next start repopulates a stale lower count and the dispatcher re-burns the
full strike threshold (4 spawns) against a still-wedged task — the exact
re-burn the durable tracker exists to stop. Audit-log writes (load-bearing for
the cycle-time / rework metrics) are similarly dropped.

The fix DRAINs ``_bg_tasks`` with a bounded timeout on shutdown — short DB
writes finish before the process exits (data preserved), while a stuck task
can't hang shutdown (it is cancelled once the drain deadline passes). Cancels
outright would lose the data (the opposite of the goal), so the drain tries to
let work complete first. The ``stop_agent`` loop is also wrapped so one agent's
stop error can't skip the drain (which would still drop the data).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

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
    """F117: stop() is idempotent. The lifespan shutdown path now stops the
    orchestrator before closing the DB, and bootstrap's finally block re-calls
    stop() as a safety net. The second call must be a clean no-op — not a
    re-drain, not a re-stop of already-stopped agents — guarded by ``_stopped``."""
    orch = _make_orchestrator()
    real_drain = orch._drain_bg_tasks
    drain_calls = 0

    async def counting_drain() -> None:
        nonlocal drain_calls
        drain_calls += 1
        await real_drain()

    orch._drain_bg_tasks = counting_drain

    await orch.stop()
    assert drain_calls == 1, "first stop() drained the bg tasks"
    assert orch._stopped is True

    await orch.stop()  # safety-net double-call (lifespan already stopped it)
    assert drain_calls == 1, "second stop() must not re-drain (idempotent no-op)"
    assert orch._stopped is True
