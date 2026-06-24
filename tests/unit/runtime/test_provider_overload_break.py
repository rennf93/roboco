"""Server-overload parking: break the 529/500 -> crash -> respawn cost loop.

A persistent overload (HTTP 529 / 500 / 503) from the model API kills the run;
the orchestrator parks the provider — the same break as a 429 rate limit —
instead of crash-retrying straight back into the overload. The probe-resume
loop revives the task when the provider recovers. These tests exercise the
decision points deterministically (logs + tracker + finalize stubbed).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from roboco.config import settings
from roboco.models.runtime import AgentInstance
from roboco.runtime.orchestrator import (
    _OVERLOAD_RETRY_AFTER_S,
    _RATE_LIMIT_RETRY_AFTER_S,
    AgentOrchestrator,
    AgentState,
)

_OVERLOAD_LOG = (
    'API Error: 529 {"type":"error","error":{"type":"overloaded_error",'
    '"message":"Overloaded"}}'
)
_CLEAN_LOG = "be-dev-1 finished editing src/app.py; all checks passed"
_SESSION_LIMIT_LOG = (
    '{"type":"result","is_error":true,"api_error_status":429,'
    '"result":"You have hit your session limit - resets 1am (UTC)",'
    '"rate_limit_info":{"rateLimitType":"five_hour"}}'
)


def _instance(provider_type: str | None = "anthropic") -> AgentInstance:
    cfg = type(
        "C",
        (),
        {"provider_type": provider_type, "model": "claude-x", "git_context": None},
    )()
    inst = AgentInstance(agent_id="be-dev-1", state=AgentState.ACTIVE, config=cfg)
    inst.current_task_id = "task-1"
    inst.container_id = "cid"
    return inst


@pytest.fixture
def orch() -> AgentOrchestrator:
    return AgentOrchestrator.__new__(AgentOrchestrator)


class _FakeTracker:
    def __init__(self) -> None:
        self.activated_with: dict[str, object] | None = None

    async def activate(
        self, *, retry_after: float, affected_agents: list[str], kind: str
    ) -> None:
        self.activated_with = {
            "retry_after": retry_after,
            "affected_agents": affected_agents,
            "kind": kind,
        }


# ---------------------------------------------------------------------------
# _provider_overload_park_target — the detection decision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detects_overload_marker_for_anthropic(
    orch: AgentOrchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "overload_break_enabled", True)
    monkeypatch.setattr(
        orch, "_tail_container_logs", AsyncMock(return_value=_OVERLOAD_LOG)
    )
    assert (
        await orch._provider_overload_park_target("be-dev-1", _instance())
        == "anthropic"
    )


@pytest.mark.asyncio
async def test_clean_output_is_not_overload(
    orch: AgentOrchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "overload_break_enabled", True)
    monkeypatch.setattr(
        orch, "_tail_container_logs", AsyncMock(return_value=_CLEAN_LOG)
    )
    assert await orch._provider_overload_park_target("be-dev-1", _instance()) is None


@pytest.mark.asyncio
async def test_disabled_flag_never_parks(
    orch: AgentOrchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "overload_break_enabled", False)
    tail = AsyncMock(return_value=_OVERLOAD_LOG)
    monkeypatch.setattr(orch, "_tail_container_logs", tail)
    assert await orch._provider_overload_park_target("be-dev-1", _instance()) is None
    tail.assert_not_awaited()  # short-circuits before reading logs


@pytest.mark.asyncio
async def test_grok_provider_is_skipped(
    orch: AgentOrchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Grok has its own exit-75 detector; the log-marker path ignores it."""
    monkeypatch.setattr(settings, "overload_break_enabled", True)
    monkeypatch.setattr(
        orch, "_tail_container_logs", AsyncMock(return_value=_OVERLOAD_LOG)
    )
    assert (
        await orch._provider_overload_park_target("gk-dev-1", _instance("grok")) is None
    )


# ---------------------------------------------------------------------------
# _park_provider_unavailable — the park action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_park_offlines_and_activates_with_kind(
    orch: AgentOrchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    inst = _instance()
    inst.error_count = 2  # prior crashes — parking must NOT count one
    tracker = _FakeTracker()
    monkeypatch.setattr(orch, "_make_tracker", lambda _p: tracker)
    monkeypatch.setattr(orch, "_finalize_spawn_session", AsyncMock())

    await orch._park_provider_unavailable(
        "be-dev-1", inst, provider="anthropic", retry_after=45.0, kind="overloaded"
    )

    assert inst.state == AgentState.OFFLINE
    assert inst.container_id is None
    assert inst.error_count == 0
    assert tracker.activated_with == {
        "retry_after": pytest.approx(45.0),
        "affected_agents": ["be-dev-1"],
        "kind": "overloaded",
    }


# ---------------------------------------------------------------------------
# _handle_stopped_container — overload short-circuits the crash-retry path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stopped_container_parks_on_overload(
    orch: AgentOrchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    inst = _instance()
    park = AsyncMock()
    spawn = AsyncMock()
    monkeypatch.setattr(orch, "_is_grok_rate_limit_exit", lambda _i, _e: False)
    monkeypatch.setattr(
        orch, "_provider_rate_limit_park_target", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        orch, "_provider_overload_park_target", AsyncMock(return_value="anthropic")
    )
    monkeypatch.setattr(orch, "_park_provider_unavailable", park)
    monkeypatch.setattr(orch, "_finalize_spawn_session", AsyncMock())
    monkeypatch.setattr(orch, "spawn_agent", spawn)

    await orch._handle_stopped_container("be-dev-1", inst, exit_code=1)

    park.assert_awaited_once_with(
        "be-dev-1",
        inst,
        provider="anthropic",
        retry_after=_OVERLOAD_RETRY_AFTER_S,
        kind="overloaded",
    )
    spawn.assert_not_awaited()  # the crash-retry path is short-circuited


@pytest.mark.asyncio
async def test_stopped_container_crash_retries_when_not_overload(
    orch: AgentOrchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    inst = _instance()
    inst.error_count = 0
    spawn = AsyncMock()
    monkeypatch.setattr(orch, "_is_grok_rate_limit_exit", lambda _i, _e: False)
    monkeypatch.setattr(
        orch, "_provider_rate_limit_park_target", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        orch, "_provider_overload_park_target", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(orch, "_finalize_spawn_session", AsyncMock())
    monkeypatch.setattr(orch, "spawn_agent", spawn)

    await orch._handle_stopped_container("be-dev-1", inst, exit_code=1)

    # Not an overload → the normal crash-retry path runs.
    spawn.assert_awaited_once()


# ---------------------------------------------------------------------------
# Session/usage-limit (429) parking — the same break for a different signal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detects_session_limit_marker_for_anthropic(
    orch: AgentOrchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "overload_break_enabled", True)
    monkeypatch.setattr(
        orch, "_tail_container_logs", AsyncMock(return_value=_SESSION_LIMIT_LOG)
    )
    assert (
        await orch._provider_rate_limit_park_target("be-dev-1", _instance())
        == "anthropic"
    )


@pytest.mark.asyncio
async def test_clean_output_is_not_a_session_limit(
    orch: AgentOrchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "overload_break_enabled", True)
    monkeypatch.setattr(
        orch, "_tail_container_logs", AsyncMock(return_value=_CLEAN_LOG)
    )
    assert await orch._provider_rate_limit_park_target("be-dev-1", _instance()) is None


@pytest.mark.asyncio
async def test_session_limit_disabled_flag_never_parks(
    orch: AgentOrchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "overload_break_enabled", False)
    tail = AsyncMock(return_value=_SESSION_LIMIT_LOG)
    monkeypatch.setattr(orch, "_tail_container_logs", tail)
    assert await orch._provider_rate_limit_park_target("be-dev-1", _instance()) is None
    tail.assert_not_awaited()  # short-circuits before reading logs


@pytest.mark.asyncio
async def test_stopped_container_parks_on_session_limit(
    orch: AgentOrchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    inst = _instance()
    park = AsyncMock()
    spawn = AsyncMock()
    overload = AsyncMock(return_value=None)
    monkeypatch.setattr(orch, "_is_grok_rate_limit_exit", lambda _i, _e: False)
    monkeypatch.setattr(
        orch, "_provider_rate_limit_park_target", AsyncMock(return_value="anthropic")
    )
    monkeypatch.setattr(orch, "_provider_overload_park_target", overload)
    monkeypatch.setattr(orch, "_park_provider_unavailable", park)
    monkeypatch.setattr(orch, "_finalize_spawn_session", AsyncMock())
    monkeypatch.setattr(orch, "spawn_agent", spawn)

    await orch._handle_stopped_container("be-dev-1", inst, exit_code=1)

    park.assert_awaited_once_with(
        "be-dev-1",
        inst,
        provider="anthropic",
        retry_after=_RATE_LIMIT_RETRY_AFTER_S,
        kind="rate_limited",
    )
    spawn.assert_not_awaited()  # crash-retry short-circuited
    overload.assert_not_awaited()  # session-limit checked before the overload path
