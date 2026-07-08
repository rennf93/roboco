"""Sandbox teardown/janitor wiring in the orchestrator's removal + reaper paths.

`_remove_container` is the single chokepoint every removal path routes
through (stop_agent, reaper kills, pre-spawn stale-clear), so sandbox
teardown lives there rather than duplicated at each call site. Gated on the
flag: when off, behavior must stay byte-for-byte identical to before this
feature (no extra docker calls).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.config import settings
from roboco.runtime.orchestrator import AgentOrchestrator


class _FakeProc:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode

    async def wait(self) -> int:
        return self.returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b""


async def _fake_create_subprocess_exec(*args: Any, **_kwargs: Any) -> _FakeProc:
    if args[1] == "inspect":
        return _FakeProc(1)  # container does not exist -> skip log dump
    if args[1] == "rm":
        return _FakeProc(0)
    raise AssertionError(f"unexpected docker args: {args}")


def _make_orchestrator() -> tuple[AgentOrchestrator, MagicMock]:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    sandbox = MagicMock()
    sandbox.teardown = AsyncMock()
    sandbox.janitor_sweep = AsyncMock()
    orch._sandbox = sandbox
    return orch, sandbox


@pytest.mark.asyncio
async def test_remove_container_tears_down_sandbox_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    orch, sandbox = _make_orchestrator()

    await orch._remove_container("roboco-agent-dev-1")

    sandbox.teardown.assert_awaited_once_with("dev-1")


@pytest.mark.asyncio
async def test_remove_container_teardown_sandbox_false_skips_even_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    orch, sandbox = _make_orchestrator()

    await orch._remove_container("roboco-agent-dev-1", teardown_sandbox=False)

    sandbox.teardown.assert_not_called()


@pytest.mark.asyncio
async def test_remove_container_skips_sandbox_teardown_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", False)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    orch, sandbox = _make_orchestrator()

    await orch._remove_container("roboco-agent-dev-1")

    sandbox.teardown.assert_not_called()


@pytest.mark.asyncio
async def test_sandbox_janitor_sweep_noop_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", False)
    orch, sandbox = _make_orchestrator()

    await orch._sandbox_janitor_sweep()

    sandbox.janitor_sweep.assert_not_called()


@pytest.mark.asyncio
async def test_sandbox_janitor_sweep_runs_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    orch, sandbox = _make_orchestrator()

    await orch._sandbox_janitor_sweep()

    sandbox.janitor_sweep.assert_awaited_once()


@pytest.mark.asyncio
async def test_sandbox_janitor_sweep_swallows_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    orch, sandbox = _make_orchestrator()
    sandbox.janitor_sweep.side_effect = RuntimeError("boom")

    await orch._sandbox_janitor_sweep()  # must not raise


# ---------------------------------------------------------------------------
# ensure_sandbox cache eviction (request_sandbox on-demand provisioning)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_container_evicts_ensure_sandbox_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    orch, _sandbox = _make_orchestrator()
    orch._sandbox_info = {"dev-1": MagicMock(), "dev-2": MagicMock()}

    await orch._remove_container("roboco-agent-dev-1")

    assert "dev-1" not in orch._sandbox_info
    assert "dev-2" in orch._sandbox_info


@pytest.mark.asyncio
async def test_remove_container_teardown_false_spares_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    orch, _sandbox = _make_orchestrator()
    orch._sandbox_info = {"dev-1": MagicMock()}

    await orch._remove_container("roboco-agent-dev-1", teardown_sandbox=False)

    assert "dev-1" in orch._sandbox_info


@pytest.mark.asyncio
async def test_janitor_sweep_evicts_cache_for_reaped_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    orch, _sandbox = _make_orchestrator()
    orch._sandbox_info = {"dev-1": MagicMock(), "dev-2": MagicMock()}
    orch._instances = {"dev-2": MagicMock()}  # dev-1's agent instance is gone

    await orch._sandbox_janitor_sweep()

    assert "dev-1" not in orch._sandbox_info
    assert "dev-2" in orch._sandbox_info
