"""Gateway-health recovery: probe a broken-but-alive agent + reap it past grace.

The verb-heartbeat can't tell a quiet-healthy agent from one whose MCP gateway
is broken (corrupted /app/.venv) yet whose container is up. The reaper now probes
out-of-band and, past a grace window, kills + evicts the broken container so it
falls through to release + respawn instead of being protected forever.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.settings import FEATURE_FLAGS


class _FakeProc:
    def __init__(self, rc: int) -> None:
        self._rc = rc

    async def wait(self) -> int:
        return self._rc


def _orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)  # bypass __init__
    orch._instances = {}
    orch._gateway_broken_since = {}
    orch._resolve_agent_slug = lambda _owner: "be-dev-1"  # type: ignore[method-assign]
    return orch


# ─── probe ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_probe_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", AsyncMock(return_value=_FakeProc(0))
    )
    assert await AgentOrchestrator._probe_gateway_health("be-dev-1") is True


@pytest.mark.asyncio
async def test_probe_broken(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", AsyncMock(return_value=_FakeProc(1))
    )
    assert await AgentOrchestrator._probe_gateway_health("be-dev-1") is False


@pytest.mark.asyncio
async def test_probe_infra_error_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("no docker"))
    )
    assert await AgentOrchestrator._probe_gateway_health("be-dev-1") is None


# ─── recovery decision ──────────────────────────────────────────────────────


def _task() -> object:
    return type("T", (), {"id": uuid4(), "assigned_to": uuid4(), "claimed_by": None})()


@pytest.mark.asyncio
async def test_disabled_never_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "gateway_health_enabled", False)
    orch = _orch()
    orch._remove_container = AsyncMock()  # type: ignore[method-assign]
    orch._probe_gateway_health = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert await orch._maybe_recover_broken_gateway(_task()) is False
    orch._remove_container.assert_not_awaited()


@pytest.mark.asyncio
async def test_healthy_is_spared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "gateway_health_enabled", True)
    orch = _orch()
    orch._gateway_broken_since["be-dev-1"] = datetime.now(UTC)  # stale mark
    orch._remove_container = AsyncMock()  # type: ignore[method-assign]
    orch._probe_gateway_health = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert await orch._maybe_recover_broken_gateway(_task()) is False
    orch._remove_container.assert_not_awaited()
    assert "be-dev-1" not in orch._gateway_broken_since  # mark cleared


@pytest.mark.asyncio
async def test_first_broken_sighting_waits_for_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gateway_health_enabled", True)
    orch = _orch()
    orch._remove_container = AsyncMock()  # type: ignore[method-assign]
    orch._probe_gateway_health = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert await orch._maybe_recover_broken_gateway(_task()) is False
    orch._remove_container.assert_not_awaited()
    assert "be-dev-1" in orch._gateway_broken_since  # grace mark recorded


@pytest.mark.asyncio
async def test_broken_past_grace_is_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "gateway_health_enabled", True)
    orch = _orch()
    orch._gateway_health_grace = 0  # past grace immediately
    orch._gateway_broken_since["be-dev-1"] = datetime.now(UTC) - timedelta(seconds=5)
    orch._instances["be-dev-1"] = object()
    orch._remove_container = AsyncMock()  # type: ignore[method-assign]
    orch._probe_gateway_health = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert await orch._maybe_recover_broken_gateway(_task()) is True
    orch._remove_container.assert_awaited_once_with("roboco-agent-be-dev-1")
    assert "be-dev-1" not in orch._instances  # evicted


@pytest.mark.asyncio
async def test_inconclusive_probe_is_spared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "gateway_health_enabled", True)
    orch = _orch()
    orch._remove_container = AsyncMock()  # type: ignore[method-assign]
    orch._probe_gateway_health = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert await orch._maybe_recover_broken_gateway(_task()) is False
    orch._remove_container.assert_not_awaited()


# ─── reaper wiring ──────────────────────────────────────────────────────────


def _stale_task() -> object:
    return type(
        "T",
        (),
        {
            "id": uuid4(),
            "last_heartbeat_at": datetime.now(UTC) - timedelta(seconds=600),
        },
    )()


@pytest.mark.asyncio
async def test_reaper_reaps_broken_gateway_agent() -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._claim_heartbeat_ttl = 300
    orch._assignee_has_active_instance = lambda _t: True  # type: ignore[method-assign]
    orch._maybe_kill_wedged_grok = AsyncMock(return_value=False)  # type: ignore[method-assign]
    orch._maybe_recover_broken_gateway = AsyncMock(return_value=True)  # type: ignore[method-assign]
    task = _stale_task()
    svc = AsyncMock()
    svc.list_in_progress_or_claimed.return_value = [task]
    svc.unclaim_for_reaper = AsyncMock()
    await orch._reap_with_service(svc)
    svc.unclaim_for_reaper.assert_awaited_once_with(task.id)


@pytest.mark.asyncio
async def test_reaper_spares_healthy_live_agent() -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._claim_heartbeat_ttl = 300
    orch._assignee_has_active_instance = lambda _t: True  # type: ignore[method-assign]
    orch._maybe_kill_wedged_grok = AsyncMock(return_value=False)  # type: ignore[method-assign]
    orch._maybe_recover_broken_gateway = AsyncMock(return_value=False)  # type: ignore[method-assign]
    svc = AsyncMock()
    svc.list_in_progress_or_claimed.return_value = [_stale_task()]
    svc.unclaim_for_reaper = AsyncMock()
    await orch._reap_with_service(svc)
    svc.unclaim_for_reaper.assert_not_awaited()


# ─── config flag ────────────────────────────────────────────────────────────


def test_flag_defaults_on_and_is_registered() -> None:
    assert settings.gateway_health_enabled is True
    assert "gateway_health_enabled" in {key for key, _ in FEATURE_FLAGS}
