"""GROK 429 parking: break the 429 -> exit -> respawn cost loop (B4).

A one-shot grok run that hits an xAI 429 exits 75; the orchestrator parks the
grok provider rate-limited instead of crash-retrying, and the spawn guard
suppresses re-spawns until the probe-resume loop clears the park. These tests
exercise the decision points deterministically (tracker + finalize stubbed).
The same spawn guard now protects every provider (not just GROK), so the
Claude session/overload paths get the same loop-break for free.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from roboco.models.runtime import AgentInstance
from roboco.runtime.orchestrator import (
    _GROK_AUTH_EXIT_CODE,
    _GROK_RATE_LIMIT_EXIT_CODE,
    _GROK_RATE_LIMIT_RETRY_AFTER_S,
    _GROK_REPARK_BACKOFF_CAP,
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
async def test_provider_spawn_parked_true_when_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(orch, "_make_tracker", lambda _p: _FakeTracker(limited=True))
    assert await orch._provider_spawn_parked("grok") is True
    assert await orch._provider_spawn_parked("anthropic") is True


@pytest.mark.asyncio
async def test_provider_spawn_parked_false_when_not_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(orch, "_make_tracker", lambda _p: _FakeTracker(limited=False))
    assert await orch._provider_spawn_parked("grok") is False
    assert await orch._provider_spawn_parked("anthropic") is False


@pytest.mark.asyncio
async def test_provider_spawn_parked_false_when_provider_unknown() -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    assert await orch._provider_spawn_parked(None) is False


@pytest.mark.asyncio
async def test_provider_spawn_parked_fails_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A tracker error must never block spawning (fail-open -> False).
    def _boom(_p: str) -> object:
        raise RuntimeError("redis down")

    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(orch, "_make_tracker", _boom)
    assert await orch._provider_spawn_parked("grok") is False
    assert await orch._provider_spawn_parked("anthropic") is False


@pytest.mark.asyncio
async def test_park_grok_rate_limited_activates_and_offlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    # _park_provider_unavailable registers a WaitingRecord (F035) so the
    # probe-resume loop can revive the task; the bare __new__ orchestrator
    # needs the dict + persist stub to exercise that without AttributeError.
    orch._waiting_records = {}
    orch._rate_limit_ceo_notified = set()
    # F097 backoff state — the constructor (skipped here) initializes these.
    orch._grok_last_park_at = None
    orch._grok_repark_count = 0
    inst = _grok_instance()
    inst.error_count = 2  # pretend prior crashes — parking must NOT count one
    tracker = _FakeTracker()
    monkeypatch.setattr(orch, "_make_tracker", lambda _p: tracker)
    finalize = AsyncMock()
    monkeypatch.setattr(orch, "_finalize_spawn_session", finalize)
    monkeypatch.setattr(orch, "_persist_waiting_record", AsyncMock())

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


# ---------------------------------------------------------------------------
# F041: exit 78 (auth missing/expired) parks instead of crash-retrying
# ---------------------------------------------------------------------------


def test_is_grok_auth_exit() -> None:
    inst = _grok_instance()
    assert AgentOrchestrator._is_grok_auth_exit(inst, _GROK_AUTH_EXIT_CODE)
    # Wrong exit code, or a non-grok provider, is not a grok-auth exit.
    assert not AgentOrchestrator._is_grok_auth_exit(inst, 0)
    assert not AgentOrchestrator._is_grok_auth_exit(inst, 1)
    assert not AgentOrchestrator._is_grok_auth_exit(
        _grok_instance(provider_type="anthropic"), _GROK_AUTH_EXIT_CODE
    )


@pytest.mark.asyncio
async def test_handle_stopped_container_parks_on_grok_auth_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # F041: a grok container whose entrypoint ran `grok_auth --check` and found
    # the token missing/expired exits 78 (EX_CONFIG). Crash-retrying 3x burns
    # tokens for zero progress (the agent can't start without a valid token);
    # park it like the 429 exit-75 path so the probe-resume loop revives the
    # task once grok_auth.refresh_if_stale mints a fresh token.
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    inst = _grok_instance()
    park = AsyncMock()
    finalize = AsyncMock()
    monkeypatch.setattr(orch, "_park_grok_auth_unavailable", park)
    monkeypatch.setattr(orch, "_finalize_spawn_session", finalize)

    await orch._handle_stopped_container("be-dev-1", inst, _GROK_AUTH_EXIT_CODE)

    park.assert_awaited_once_with("be-dev-1", inst)
    # Early-return: the crash-retry path never runs (no token burn).
    finalize.assert_not_awaited()


@pytest.mark.asyncio
async def test_park_grok_auth_unavailable_activates_with_auth_missing_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._waiting_records = {}
    orch._rate_limit_ceo_notified = set()
    inst = _grok_instance()
    inst.error_count = 2  # prior crashes — parking must NOT count one
    tracker = _FakeTracker()
    monkeypatch.setattr(orch, "_make_tracker", lambda _p: tracker)
    monkeypatch.setattr(orch, "_finalize_spawn_session", AsyncMock())
    monkeypatch.setattr(orch, "_persist_waiting_record", AsyncMock())

    await orch._park_grok_auth_unavailable("be-dev-1", inst)

    assert inst.state == AgentState.OFFLINE
    assert inst.container_id is None
    assert inst.error_count == 0  # an auth-missing exit is not a crash
    assert tracker.activated_with == {
        "retry_after": pytest.approx(60.0),
        "affected_agents": ["be-dev-1"],
        "kind": "auth_missing",
    }


# --------------------------------------------------------------------------- #
# F097 — grok has no real probe, so an optimistic clear respawns into a still-
# active xAI 429 every ~90s. Back off the re-park retry_after within one rate-
# limit episode so the churn dampens instead of spinning flat at 60s.
# --------------------------------------------------------------------------- #


class _RecordingTracker:
    """Records every activate() retry_after across multiple re-parks."""

    def __init__(self) -> None:
        self.retry_afters: list[float] = []
        self.kinds: list[str] = []
        self.agents_lists: list[list[str]] = []

    async def activate(
        self, *, retry_after: float, affected_agents: list[str], kind: str
    ) -> None:
        self.retry_afters.append(retry_after)
        self.kinds.append(kind)
        self.agents_lists.append(affected_agents)


def _backoff_orchestrator() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._waiting_records = {}
    orch._rate_limit_ceo_notified = set()
    # Backoff state — the constructor (skipped here) initializes these.
    orch._grok_last_park_at = None
    orch._grok_repark_count = 0
    return orch


@pytest.mark.asyncio
async def test_grok_repark_backs_off_within_episode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three re-parks within one episode (microseconds apart) must grow the
    retry_after — 60 -> 120 -> 240 — not stay flat at 60s (the ~90s crash-retry
    cycle the optimistic clear produces today)."""
    orch = _backoff_orchestrator()
    tracker = _RecordingTracker()
    monkeypatch.setattr(orch, "_make_tracker", lambda _p: tracker)
    monkeypatch.setattr(orch, "_finalize_spawn_session", AsyncMock())
    monkeypatch.setattr(orch, "_persist_waiting_record", AsyncMock())
    inst = _grok_instance()

    await orch._park_grok_rate_limited("be-dev-1", inst)
    await orch._park_grok_rate_limited("be-dev-1", inst)
    await orch._park_grok_rate_limited("be-dev-1", inst)

    assert tracker.retry_afters == [60.0, 120.0, 240.0]
    assert tracker.kinds == ["rate_limited", "rate_limited", "rate_limited"]


@pytest.mark.asyncio
async def test_grok_repark_resets_after_episode_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A re-park AFTER the episode gap is a fresh episode — the retry_after
    resets to the base 60s even if the prior episode had backed off."""
    orch = _backoff_orchestrator()
    orch._grok_repark_count = 3  # pretend a prior episode backed off hard
    orch._grok_last_park_at = datetime.now(UTC) - timedelta(hours=2)
    tracker = _RecordingTracker()
    monkeypatch.setattr(orch, "_make_tracker", lambda _p: tracker)
    monkeypatch.setattr(orch, "_finalize_spawn_session", AsyncMock())
    monkeypatch.setattr(orch, "_persist_waiting_record", AsyncMock())
    inst = _grok_instance()

    await orch._park_grok_rate_limited("be-dev-1", inst)

    # Fresh episode -> base retry_after, no carried-over backoff.
    assert tracker.retry_afters == [60.0]
    assert orch._grok_repark_count == 0


@pytest.mark.asyncio
async def test_grok_repark_backoff_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    """The backoff is capped so a long xAI rate-limit window doesn't push the
    retry_after toward infinity (bounded cycle, recovery still reachable)."""
    orch = _backoff_orchestrator()
    tracker = _RecordingTracker()
    monkeypatch.setattr(orch, "_make_tracker", lambda _p: tracker)
    monkeypatch.setattr(orch, "_finalize_spawn_session", AsyncMock())
    monkeypatch.setattr(orch, "_persist_waiting_record", AsyncMock())
    inst = _grok_instance()

    # Park once, then re-park well past the cap.
    for _ in range(_GROK_REPARK_BACKOFF_CAP + 3):
        await orch._park_grok_rate_limited("be-dev-1", inst)

    max_expected = _GROK_RATE_LIMIT_RETRY_AFTER_S * (2**_GROK_REPARK_BACKOFF_CAP)
    # Every retry_after from the cap onward is the same capped value.
    assert all(
        r == max_expected for r in tracker.retry_afters[_GROK_REPARK_BACKOFF_CAP:]
    )
    assert max(tracker.retry_afters) == max_expected
