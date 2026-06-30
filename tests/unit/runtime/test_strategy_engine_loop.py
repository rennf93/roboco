"""#193: a persistently failing strategy-engine cycle must surface to the CEO.

``_strategy_engine_loop`` swallows cycle exceptions with only a log — a
persistent ``assess`` failure (bad DB, bad goals row, …) leaves the engine
silently producing nothing every tick while the CEO sees no signal. Mirror the
rate-limit probe loop: count consecutive failures, notify the CEO once per
failure episode, reset on the first success.
"""

from __future__ import annotations

import types
from typing import cast

import pytest
import roboco.db as db_mod
import roboco.services.strategy_engine as strat_mod
from roboco.config import settings as cfg
from roboco.runtime import orchestrator as orch_mod
from roboco.runtime.orchestrator import AgentOrchestrator


class _FakeDbCtx:
    """An async context manager yielding a dummy session."""

    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_exc: object) -> bool:
        return False


def _engine_that_raises(message: str) -> types.SimpleNamespace:
    async def _run_cycle() -> None:
        raise RuntimeError(message)

    return types.SimpleNamespace(run_cycle=_run_cycle)


def _engine_that_succeeds() -> types.SimpleNamespace:
    async def _run_cycle() -> None:
        return None

    return types.SimpleNamespace(run_cycle=_run_cycle)


def _stub(notify_spy: object) -> AgentOrchestrator:
    """A minimal orchestrator stub: running + a spied failure-notify hook."""
    return cast(
        "AgentOrchestrator",
        types.SimpleNamespace(
            _running=True,
            _notify_strategy_engine_failure=notify_spy,
        ),
    )


@pytest.mark.asyncio
async def test_strategy_engine_loop_returns_immediately_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "strategy_engine_enabled", False)
    stub = cast("AgentOrchestrator", types.SimpleNamespace(_running=True))
    await AgentOrchestrator._strategy_engine_loop(stub)


@pytest.mark.asyncio
async def test_persistent_cycle_failure_notifies_ceo_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "strategy_engine_enabled", True)
    monkeypatch.setattr(db_mod, "get_db_context", _FakeDbCtx)
    raises_engine = _engine_that_raises("assess blew up")
    monkeypatch.setattr(strat_mod, "get_strategy_engine", lambda _db: raises_engine)

    notified: list[int] = []

    async def _spy(fail_count: int) -> None:
        notified.append(fail_count)

    stub = _stub(_spy)
    state = AgentOrchestrator._new_strategy_loop_state()
    threshold = orch_mod._STRATEGY_FAIL_CEO_NOTIFY_THRESHOLD

    # Threshold - 1 failures: no CEO notify yet.
    for _ in range(threshold - 1):
        await AgentOrchestrator._strategy_engine_cycle(stub, state)
    assert notified == []
    assert state.failures == threshold - 1

    # The threshold-th failure fires the one-time CEO alert.
    await AgentOrchestrator._strategy_engine_cycle(stub, state)
    assert notified == [threshold]
    assert state.notified is True

    # Further failures must NOT re-notify (one per episode).
    await AgentOrchestrator._strategy_engine_cycle(stub, state)
    await AgentOrchestrator._strategy_engine_cycle(stub, state)
    assert notified == [threshold]


@pytest.mark.asyncio
async def test_success_resets_failure_state_so_a_new_episode_re_notifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "strategy_engine_enabled", True)
    monkeypatch.setattr(db_mod, "get_db_context", _FakeDbCtx)

    engine = {"obj": _engine_that_raises("boom")}

    def _get_strategy_engine(_db: object) -> types.SimpleNamespace:
        return engine["obj"]

    monkeypatch.setattr(strat_mod, "get_strategy_engine", _get_strategy_engine)

    notified: list[int] = []

    async def _spy(fail_count: int) -> None:
        notified.append(fail_count)

    stub = _stub(_spy)
    state = AgentOrchestrator._new_strategy_loop_state()
    threshold = orch_mod._STRATEGY_FAIL_CEO_NOTIFY_THRESHOLD

    for _ in range(threshold):
        await AgentOrchestrator._strategy_engine_cycle(stub, state)
    assert notified == [threshold]

    # Engine recovers → state resets, episode closes.
    engine["obj"] = _engine_that_succeeds()
    await AgentOrchestrator._strategy_engine_cycle(stub, state)
    assert state.failures == 0
    assert state.notified is False

    # A fresh failure episode notifies again after the threshold.
    engine["obj"] = _engine_that_raises("boom2")
    for _ in range(threshold):
        await AgentOrchestrator._strategy_engine_cycle(stub, state)
    assert notified == [threshold, threshold]
