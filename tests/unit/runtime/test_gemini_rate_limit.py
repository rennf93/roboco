"""GEMINI quota/auth parking: break the exit -> respawn cost loop.

A one-shot gemini run that hits a quota error is remapped to exit 75 by the
entrypoint wrapper (see gemini_cli_usage.classify_exit_code); a missing/empty
OAuth credential exits 41 (the CLI's own dedicated auth-failure code). Both
park the GEMINI provider instead of crash-retrying, mirroring grok's exit-75 /
exit-78 parks (see test_grok_rate_limit.py) but tracked independently.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from roboco.models.runtime import AgentInstance
from roboco.runtime.orchestrator import (
    _GEMINI_AUTH_EXIT_CODE,
    _GEMINI_RATE_LIMIT_EXIT_CODE,
    _GEMINI_REPARK_BACKOFF_CAP,
    AgentOrchestrator,
    AgentState,
)


def _gemini_instance(provider_type: str = "gemini") -> AgentInstance:
    cfg = type("C", (), {"provider_type": provider_type, "model": "gemini-2.5-pro"})()
    inst = AgentInstance(agent_id="be-dev-1", state=AgentState.ACTIVE, config=cfg)
    inst.current_task_id = "task-1"
    inst.container_id = "cid"
    return inst


class _FakeTracker:
    def __init__(self) -> None:
        self.activated_with: dict[str, object] | None = None

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


class _RecordingTracker:
    """Records every activate() retry_after across multiple re-parks."""

    def __init__(self) -> None:
        self.retry_afters: list[float] = []
        self.kinds: list[str] = []

    async def activate(
        self, *, retry_after: float, affected_agents: list[str], kind: str
    ) -> None:
        del affected_agents
        self.retry_afters.append(retry_after)
        self.kinds.append(kind)


def _orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._waiting_records = {}
    orch._rate_limit_ceo_notified = set()
    orch._gemini_last_park_at = None
    orch._gemini_repark_count = 0
    orch._gemini_rate_limit_retry_after_s = 60.0
    orch._gemini_auth_retry_after_s = 60.0
    return orch


def test_is_gemini_rate_limit_exit() -> None:
    inst = _gemini_instance()
    assert AgentOrchestrator._is_gemini_rate_limit_exit(
        inst, _GEMINI_RATE_LIMIT_EXIT_CODE
    )
    assert not AgentOrchestrator._is_gemini_rate_limit_exit(inst, 0)
    assert not AgentOrchestrator._is_gemini_rate_limit_exit(inst, 1)
    assert not AgentOrchestrator._is_gemini_rate_limit_exit(
        _gemini_instance(provider_type="anthropic"), _GEMINI_RATE_LIMIT_EXIT_CODE
    )
    # Same numeric exit code as grok's own detector, but provider-scoped: a
    # grok instance exiting 75 is NOT a gemini rate-limit exit.
    assert not AgentOrchestrator._is_gemini_rate_limit_exit(
        _gemini_instance(provider_type="grok"), _GEMINI_RATE_LIMIT_EXIT_CODE
    )


def test_is_gemini_auth_exit() -> None:
    inst = _gemini_instance()
    assert AgentOrchestrator._is_gemini_auth_exit(inst, _GEMINI_AUTH_EXIT_CODE)
    assert not AgentOrchestrator._is_gemini_auth_exit(inst, 0)
    assert not AgentOrchestrator._is_gemini_auth_exit(inst, 1)
    assert not AgentOrchestrator._is_gemini_auth_exit(
        _gemini_instance(provider_type="anthropic"), _GEMINI_AUTH_EXIT_CODE
    )


@pytest.mark.asyncio
async def test_park_gemini_rate_limited_activates_and_offlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _orch()
    inst = _gemini_instance()
    inst.error_count = 2  # pretend prior crashes — parking must NOT count one
    tracker = _FakeTracker()
    monkeypatch.setattr(orch, "_make_tracker", lambda _p: tracker)
    finalize = AsyncMock()
    monkeypatch.setattr(orch, "_finalize_spawn_session", finalize)
    monkeypatch.setattr(orch, "_persist_waiting_record", AsyncMock())

    await orch._park_gemini_rate_limited("be-dev-1", inst)

    finalize.assert_awaited_once()
    assert inst.state == AgentState.OFFLINE
    assert inst.container_id is None
    assert inst.error_count == 0  # a quota park is not a crash
    assert tracker.activated_with == {
        "retry_after": pytest.approx(60.0),
        "affected_agents": ["be-dev-1"],
        "kind": "rate_limited",
    }


@pytest.mark.asyncio
async def test_park_gemini_auth_unavailable_activates_with_auth_missing_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _orch()
    inst = _gemini_instance()
    inst.error_count = 2
    tracker = _FakeTracker()
    monkeypatch.setattr(orch, "_make_tracker", lambda _p: tracker)
    monkeypatch.setattr(orch, "_finalize_spawn_session", AsyncMock())
    monkeypatch.setattr(orch, "_persist_waiting_record", AsyncMock())

    await orch._park_gemini_auth_unavailable("be-dev-1", inst)

    assert inst.state == AgentState.OFFLINE
    assert inst.container_id is None
    assert inst.error_count == 0
    assert tracker.activated_with == {
        "retry_after": pytest.approx(60.0),
        "affected_agents": ["be-dev-1"],
        "kind": "auth_missing",
    }


@pytest.mark.asyncio
async def test_handle_stopped_container_parks_on_gemini_quota_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    inst = _gemini_instance()
    park = AsyncMock()
    finalize = AsyncMock()
    monkeypatch.setattr(orch, "_park_gemini_rate_limited", park)
    monkeypatch.setattr(orch, "_finalize_spawn_session", finalize)

    await orch._handle_stopped_container("be-dev-1", inst, _GEMINI_RATE_LIMIT_EXIT_CODE)

    park.assert_awaited_once_with("be-dev-1", inst)
    finalize.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_stopped_container_parks_on_gemini_auth_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    inst = _gemini_instance()
    park = AsyncMock()
    finalize = AsyncMock()
    monkeypatch.setattr(orch, "_park_gemini_auth_unavailable", park)
    monkeypatch.setattr(orch, "_finalize_spawn_session", finalize)

    await orch._handle_stopped_container("be-dev-1", inst, _GEMINI_AUTH_EXIT_CODE)

    park.assert_awaited_once_with("be-dev-1", inst)
    finalize.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Gemini has no real recovery probe either (an OAuth-login daily quota cap has
# no cheap balance-check API) — mirrors grok's repark-backoff tests exactly.
# --------------------------------------------------------------------------- #


def _backoff_orchestrator() -> AgentOrchestrator:
    return _orch()


@pytest.mark.asyncio
async def test_gemini_repark_backs_off_within_episode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _backoff_orchestrator()
    tracker = _RecordingTracker()
    monkeypatch.setattr(orch, "_make_tracker", lambda _p: tracker)
    monkeypatch.setattr(orch, "_finalize_spawn_session", AsyncMock())
    monkeypatch.setattr(orch, "_persist_waiting_record", AsyncMock())
    inst = _gemini_instance()

    await orch._park_gemini_rate_limited("be-dev-1", inst)
    await orch._park_gemini_rate_limited("be-dev-1", inst)
    await orch._park_gemini_rate_limited("be-dev-1", inst)

    assert tracker.retry_afters == [60.0, 120.0, 240.0]
    assert tracker.kinds == ["rate_limited", "rate_limited", "rate_limited"]


@pytest.mark.asyncio
async def test_gemini_repark_resets_after_episode_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _backoff_orchestrator()
    orch._gemini_repark_count = 3
    orch._gemini_last_park_at = datetime.now(UTC) - timedelta(hours=2)
    tracker = _RecordingTracker()
    monkeypatch.setattr(orch, "_make_tracker", lambda _p: tracker)
    monkeypatch.setattr(orch, "_finalize_spawn_session", AsyncMock())
    monkeypatch.setattr(orch, "_persist_waiting_record", AsyncMock())
    inst = _gemini_instance()

    await orch._park_gemini_rate_limited("be-dev-1", inst)

    assert tracker.retry_afters == [60.0]
    assert orch._gemini_repark_count == 0


@pytest.mark.asyncio
async def test_gemini_repark_backoff_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    orch = _backoff_orchestrator()
    tracker = _RecordingTracker()
    monkeypatch.setattr(orch, "_make_tracker", lambda _p: tracker)
    monkeypatch.setattr(orch, "_finalize_spawn_session", AsyncMock())
    monkeypatch.setattr(orch, "_persist_waiting_record", AsyncMock())
    inst = _gemini_instance()

    for _ in range(_GEMINI_REPARK_BACKOFF_CAP + 3):
        await orch._park_gemini_rate_limited("be-dev-1", inst)

    max_expected = 60.0 * (2**_GEMINI_REPARK_BACKOFF_CAP)
    assert all(
        r == max_expected for r in tracker.retry_afters[_GEMINI_REPARK_BACKOFF_CAP:]
    )
    assert max(tracker.retry_afters) == max_expected
