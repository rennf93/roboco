"""Gateway-health recovery: probe a broken-but-alive agent + reap it past grace.

The verb-heartbeat can't tell a quiet-healthy agent from one whose MCP gateway
is broken (corrupted /app/.venv) yet whose container is up. The reaper now probes
out-of-band and, past a grace window, kills + evicts the broken container so it
falls through to release + respawn instead of being protected forever.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast
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


def _orch(monkeypatch: pytest.MonkeyPatch) -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)  # bypass __init__
    orch._instances = {}
    orch._gateway_broken_since = {}
    monkeypatch.setattr(orch, "_resolve_agent_slug", lambda _owner: "be-dev-1")
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


def _task() -> Any:
    return type("T", (), {"id": uuid4(), "assigned_to": uuid4(), "claimed_by": None})()


@pytest.mark.asyncio
async def test_disabled_never_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "gateway_health_enabled", False)
    orch = _orch(monkeypatch)
    remove = AsyncMock()
    monkeypatch.setattr(orch, "_remove_container", remove)
    monkeypatch.setattr(orch, "_probe_gateway_health", AsyncMock(return_value=False))
    assert await orch._maybe_recover_broken_gateway(_task()) is False
    remove.assert_not_awaited()


@pytest.mark.asyncio
async def test_healthy_is_spared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "gateway_health_enabled", True)
    orch = _orch(monkeypatch)
    orch._gateway_broken_since["be-dev-1"] = datetime.now(UTC)  # stale mark
    remove = AsyncMock()
    monkeypatch.setattr(orch, "_remove_container", remove)
    monkeypatch.setattr(orch, "_probe_gateway_health", AsyncMock(return_value=True))
    assert await orch._maybe_recover_broken_gateway(_task()) is False
    remove.assert_not_awaited()
    assert "be-dev-1" not in orch._gateway_broken_since  # mark cleared


@pytest.mark.asyncio
async def test_first_broken_sighting_waits_for_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gateway_health_enabled", True)
    orch = _orch(monkeypatch)
    remove = AsyncMock()
    monkeypatch.setattr(orch, "_remove_container", remove)
    monkeypatch.setattr(orch, "_probe_gateway_health", AsyncMock(return_value=False))
    assert await orch._maybe_recover_broken_gateway(_task()) is False
    remove.assert_not_awaited()
    assert "be-dev-1" in orch._gateway_broken_since  # grace mark recorded


@pytest.mark.asyncio
async def test_broken_past_grace_is_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "gateway_health_enabled", True)
    orch = _orch(monkeypatch)
    # _gateway_health_grace is a test-only injection read via getattr(..., None);
    # 0 means "past grace immediately".
    monkeypatch.setattr(orch, "_gateway_health_grace", 0, raising=False)
    orch._gateway_broken_since["be-dev-1"] = datetime.now(UTC) - timedelta(seconds=5)
    orch._instances["be-dev-1"] = cast("Any", object())
    remove = AsyncMock()
    monkeypatch.setattr(orch, "_remove_container", remove)
    monkeypatch.setattr(orch, "_probe_gateway_health", AsyncMock(return_value=False))
    assert await orch._maybe_recover_broken_gateway(_task()) is True
    remove.assert_awaited_once_with("roboco-agent-be-dev-1")
    assert "be-dev-1" not in orch._instances  # evicted


@pytest.mark.asyncio
async def test_inconclusive_probe_is_spared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "gateway_health_enabled", True)
    orch = _orch(monkeypatch)
    remove = AsyncMock()
    monkeypatch.setattr(orch, "_remove_container", remove)
    monkeypatch.setattr(orch, "_probe_gateway_health", AsyncMock(return_value=None))
    assert await orch._maybe_recover_broken_gateway(_task()) is False
    remove.assert_not_awaited()


# ─── reaper wiring ──────────────────────────────────────────────────────────


def _stale_task() -> Any:
    return type(
        "T",
        (),
        {
            "id": uuid4(),
            "last_heartbeat_at": datetime.now(UTC) - timedelta(seconds=600),
        },
    )()


@pytest.mark.asyncio
async def test_reaper_reaps_broken_gateway_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._claim_heartbeat_ttl = 300
    monkeypatch.setattr(orch, "_assignee_has_active_instance", lambda _t: True)
    monkeypatch.setattr(orch, "_maybe_kill_wedged_grok", AsyncMock(return_value=False))
    monkeypatch.setattr(
        orch, "_maybe_recover_broken_gateway", AsyncMock(return_value=True)
    )
    task = _stale_task()
    svc = AsyncMock()
    svc.list_in_progress_or_claimed.return_value = [task]
    svc.unclaim_for_reaper = AsyncMock()
    await orch._reap_with_service(svc)
    svc.unclaim_for_reaper.assert_awaited_once_with(task.id)


@pytest.mark.asyncio
async def test_reaper_spares_healthy_live_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._claim_heartbeat_ttl = 300
    monkeypatch.setattr(orch, "_assignee_has_active_instance", lambda _t: True)
    monkeypatch.setattr(orch, "_maybe_kill_wedged_grok", AsyncMock(return_value=False))
    monkeypatch.setattr(
        orch, "_maybe_recover_broken_gateway", AsyncMock(return_value=False)
    )
    svc = AsyncMock()
    svc.list_in_progress_or_claimed.return_value = [_stale_task()]
    svc.unclaim_for_reaper = AsyncMock()
    await orch._reap_with_service(svc)
    svc.unclaim_for_reaper.assert_not_awaited()


# ─── config flag ────────────────────────────────────────────────────────────


def test_flag_defaults_on_and_is_registered() -> None:
    assert settings.gateway_health_enabled is True
    assert "gateway_health_enabled" in {key for key, _ in FEATURE_FLAGS}
