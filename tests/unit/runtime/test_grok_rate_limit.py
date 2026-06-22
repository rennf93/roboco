"""GROK 429 parking: break the 429 -> exit -> respawn cost loop (B4).

A one-shot grok run that hits an xAI 429 exits 75; the orchestrator parks the
grok provider rate-limited instead of crash-retrying, and the spawn guard
suppresses re-spawns until the probe-resume loop clears the park. These tests
exercise the decision points deterministically (tracker + finalize stubbed).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from roboco.models.runtime import AgentInstance
from roboco.runtime.orchestrator import (
    _GROK_RATE_LIMIT_EXIT_CODE,
    AgentOrchestrator,
    AgentState,
)


def _grok_instance(provider_type: str = "grok") -> AgentInstance:
    cfg = type("C", (), {"provider_type": provider_type, "model": "grok-build-0.1"})()
    inst = AgentInstance(agent_id="be-dev-1", state=AgentState.ACTIVE, config=cfg)
    inst.current_task_id = "task-1"
    inst.container_id = "cid"
    return inst


class _FakeTracker:
    def __init__(self, *, limited: bool = False) -> None:
        self._limited = limited
        self.activated_with: dict[str, object] | None = None

    async def is_rate_limited(self) -> bool:
        return self._limited

    async def activate(
        self,
        *,
        retry_after: float,
        affected_agents: list[str],
        kind: str = "rate_limited",
    ) -> None:
        self.activated_with = {
            "retry_after": retry_after,
            "affected_agents": affected_agents,
            "kind": kind,
        }


def test_is_grok_rate_limit_exit() -> None:
    inst = _grok_instance()
    assert AgentOrchestrator._is_grok_rate_limit_exit(inst, _GROK_RATE_LIMIT_EXIT_CODE)
    # Wrong exit code, or a non-grok provider, is not a grok-429 exit.
    assert not AgentOrchestrator._is_grok_rate_limit_exit(inst, 0)
    assert not AgentOrchestrator._is_grok_rate_limit_exit(inst, 1)
    assert not AgentOrchestrator._is_grok_rate_limit_exit(
        _grok_instance(provider_type="anthropic"), _GROK_RATE_LIMIT_EXIT_CODE
    )


@pytest.mark.asyncio
async def test_grok_spawn_parked_true_when_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(orch, "_make_tracker", lambda _p: _FakeTracker(limited=True))
    assert await orch._grok_spawn_parked("grok") is True


@pytest.mark.asyncio
async def test_grok_spawn_parked_false_when_not_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(orch, "_make_tracker", lambda _p: _FakeTracker(limited=False))
    assert await orch._grok_spawn_parked("grok") is False


@pytest.mark.asyncio
async def test_grok_spawn_parked_false_for_non_grok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    # Non-grok never consults the tracker (a tracker call would error here).
    monkeypatch.setattr(
        orch, "_make_tracker", lambda _p: (_ for _ in ()).throw(AssertionError)
    )
    assert await orch._grok_spawn_parked("anthropic") is False


@pytest.mark.asyncio
async def test_grok_spawn_parked_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    # A tracker error must never block spawning (fail-open -> False).
    def _boom(_p: str) -> object:
        raise RuntimeError("redis down")

    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(orch, "_make_tracker", _boom)
    assert await orch._grok_spawn_parked("grok") is False


@pytest.mark.asyncio
async def test_park_grok_rate_limited_activates_and_offlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    inst = _grok_instance()
    inst.error_count = 2  # pretend prior crashes — parking must NOT count one
    tracker = _FakeTracker()
    monkeypatch.setattr(orch, "_make_tracker", lambda _p: tracker)
    finalize = AsyncMock()
    monkeypatch.setattr(orch, "_finalize_spawn_session", finalize)

    await orch._park_grok_rate_limited("be-dev-1", inst)

    finalize.assert_awaited_once()
    assert inst.state == AgentState.OFFLINE
    assert inst.container_id is None
    assert inst.error_count == 0  # a 429 is not a crash
    assert tracker.activated_with == {
        "retry_after": pytest.approx(60.0),
        "affected_agents": ["be-dev-1"],
        "kind": "rate_limited",
    }


@pytest.mark.asyncio
async def test_handle_stopped_container_parks_on_grok_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    inst = _grok_instance()
    park = AsyncMock()
    finalize = AsyncMock()
    monkeypatch.setattr(orch, "_park_grok_rate_limited", park)
    monkeypatch.setattr(orch, "_finalize_spawn_session", finalize)

    await orch._handle_stopped_container("be-dev-1", inst, _GROK_RATE_LIMIT_EXIT_CODE)

    park.assert_awaited_once_with("be-dev-1", inst)
    # Early-return: the normal crash/graceful finalize path never runs.
    finalize.assert_not_awaited()
