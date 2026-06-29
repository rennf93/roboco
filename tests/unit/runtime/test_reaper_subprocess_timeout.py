"""Reaper Docker subprocess calls (``docker inspect`` / ``docker exec``) are
bounded with ``asyncio.wait_for`` so a hung Docker daemon can't freeze the shared
asyncio event loop (the reaper runs before every dispatch tick). On timeout the
child is killed and the call either raises (``inspect`` /
``resolve_container_id``) or returns ``None`` (gateway probe — inconclusive).
``_check_health`` is hardened per-agent so one hung inspect skips that agent, not
the whole sweep, preserving the per-tick check-all-agents invariant.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.runtime.orchestrator import (
    _DOCKER_EXEC_TIMEOUT_SECONDS,
    _DOCKER_INSPECT_TIMEOUT_SECONDS,
    AgentOrchestrator,
    AgentState,
)

# Floors encoding the logical-regression guard: a deadline below these would
# wrongly abort a legitimate slow docker call (a loaded daemon, a cold venv
# import). Named (not magic) for ruff PLR2004.
_MIN_DOCKER_INSPECT_TIMEOUT = 5.0
_MIN_DOCKER_EXEC_TIMEOUT = 15.0

# Return codes / parsed states reused across assertions.
_RC_HEALTHY = 0
_RC_BROKEN = 1


# ---------------------------------------------------------------------------
# Fakes: hanging (never-resolving) + done subprocesses, recording kill().
# ---------------------------------------------------------------------------


class _HangingInspectProc:
    """``docker inspect`` whose ``communicate()`` never resolves — a hung daemon."""

    def __init__(self) -> None:
        self.killed = False
        self._never: asyncio.Future[None] = asyncio.Future()

    async def communicate(self) -> tuple[bytes, bytes]:
        await self._never  # wait_for cancels on timeout -> TimeoutError
        return (b"", b"")

    def kill(self) -> None:
        self.killed = True


class _DoneInspectProc:
    """``docker inspect`` that completes immediately with fixed stdout."""

    def __init__(self, out: bytes) -> None:
        self.returncode = 0
        self._out = out
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return (self._out, b"")

    def kill(self) -> None:
        self.killed = True


class _HangingExecProc:
    """``docker exec`` whose ``wait()`` never resolves — a stuck container."""

    def __init__(self) -> None:
        self.killed = False
        self._never: asyncio.Future[int] = asyncio.Future()

    async def wait(self) -> int:
        await self._never
        return 0

    def kill(self) -> None:
        self.killed = True


class _DoneExecProc:
    """``docker exec`` that completes immediately with a fixed return code."""

    def __init__(self, rc: int) -> None:
        self._rc = rc
        self.killed = False

    async def wait(self) -> int:
        return self._rc

    def kill(self) -> None:
        self.killed = True


def _exec_returning(proc: object) -> object:
    async def _exec(*_args: object, **_kwargs: object) -> object:
        return proc

    return _exec


def _orch() -> AgentOrchestrator:
    with patch.object(AgentOrchestrator, "__init__", return_value=None):
        orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._lock = MagicMock()
    return orch


def _instance() -> MagicMock:
    inst = MagicMock()
    inst.state = AgentState.ACTIVE
    inst.container_id = "deadbeef1234"
    inst.current_task_id = None
    inst.error_count = 0
    inst.config = MagicMock(git_context=None)
    return inst


# ---------------------------------------------------------------------------
# Constant shape — generous floors so a legitimate slow docker call survives.
# ---------------------------------------------------------------------------


def test_docker_subprocess_timeouts_are_named_module_constants() -> None:
    for c in (_DOCKER_INSPECT_TIMEOUT_SECONDS, _DOCKER_EXEC_TIMEOUT_SECONDS):
        assert isinstance(c, int | float)
        assert c > 0


def test_docker_subprocess_timeouts_are_generous() -> None:
    """A loaded daemon or a cold-venv import can legitimately take seconds; the
    deadlines must not wrongly abort a healthy call. This guards the logical
    regression: a too-short deadline would turn a slow-but-healthy docker call
    into a spurious reaper action (releasing a live agent's task, or skipping a
    real crash)."""
    assert _DOCKER_INSPECT_TIMEOUT_SECONDS >= _MIN_DOCKER_INSPECT_TIMEOUT
    assert _DOCKER_EXEC_TIMEOUT_SECONDS >= _MIN_DOCKER_EXEC_TIMEOUT


# ---------------------------------------------------------------------------
# docker inspect / resolve: hung -> raise (caller applies its fail-direction).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inspect_container_state_times_out_raises_and_kills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "roboco.runtime.orchestrator._DOCKER_INSPECT_TIMEOUT_SECONDS", 0.05
    )
    proc = _HangingInspectProc()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec_returning(proc))
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            AgentOrchestrator._inspect_container_state("roboco-agent-x"), timeout=2.0
        )
    assert proc.killed


@pytest.mark.asyncio
async def test_resolve_container_id_times_out_raises_and_kills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "roboco.runtime.orchestrator._DOCKER_INSPECT_TIMEOUT_SECONDS", 0.05
    )
    proc = _HangingInspectProc()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec_returning(proc))
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            AgentOrchestrator._resolve_container_id("roboco-agent-x"), timeout=2.0
        )
    assert proc.killed


# ---------------------------------------------------------------------------
# docker exec gateway probe: hung -> None (inconclusive, caller declines to act).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_gateway_health_times_out_returns_none_and_kills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "roboco.runtime.orchestrator._DOCKER_EXEC_TIMEOUT_SECONDS", 0.05
    )
    proc = _HangingExecProc()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec_returning(proc))
    result = await asyncio.wait_for(
        AgentOrchestrator._probe_gateway_health("be-dev-1"), timeout=2.0
    )
    assert result is None
    assert proc.killed


# ---------------------------------------------------------------------------
# Regression: the happy path still parses / returns the real result.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inspect_container_state_green_path_parses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = _DoneInspectProc(b"true 0\n")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec_returning(proc))
    is_running, exit_code = await asyncio.wait_for(
        AgentOrchestrator._inspect_container_state("roboco-agent-x"), timeout=2.0
    )
    assert is_running is True
    assert exit_code == 0
    assert not proc.killed


@pytest.mark.asyncio
async def test_resolve_container_id_green_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = _DoneInspectProc(b"deadbeef1234567890\n")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec_returning(proc))
    cid = await asyncio.wait_for(
        AgentOrchestrator._resolve_container_id("roboco-agent-x"), timeout=2.0
    )
    assert cid == "deadbeef1234567890"
    assert not proc.killed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rc,expected",
    [(_RC_HEALTHY, True), (_RC_BROKEN, False)],
)
async def test_probe_gateway_health_green_path_returns_rc(
    rc: int, expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = _DoneExecProc(rc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec_returning(proc))
    assert await AgentOrchestrator._probe_gateway_health("be-dev-1") is expected


# ---------------------------------------------------------------------------
# _check_health resilience: one agent's hung inspect skips it, not the sweep.
# This is the invariant the timeout-then-raise would otherwise break: without
# per-agent isolation, converting the hang to a raise would abort the whole
# sweep every tick until docker recovered, so NO agent gets health-checked.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_health_skips_agent_on_inspect_timeout_not_aborts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent a1's docker inspect hangs (times out -> skipped); agent a2 is then
    inspected and found stopped -> _handle_stopped_container is called for a2.
    Reaching a2 proves the sweep did NOT abort on a1's timeout."""
    monkeypatch.setattr(
        "roboco.runtime.orchestrator._DOCKER_INSPECT_TIMEOUT_SECONDS", 0.05
    )
    orch = _orch()
    orch._instances["a1"] = _instance()
    orch._instances["a2"] = _instance()
    hang = _HangingInspectProc()
    # a2 reports stopped (false) so the handler fires for it.
    done = _DoneInspectProc(b"false 0\n")
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", AsyncMock(side_effect=[hang, done])
    )
    handle = AsyncMock()
    monkeypatch.setattr(orch, "_handle_stopped_container", handle)

    await asyncio.wait_for(orch._check_health(), timeout=2.0)

    # a1's hung inspect was killed + skipped (no handler call for a1).
    assert hang.killed
    # a2 WAS reached despite a1's timeout — the sweep continued.
    handle.assert_awaited_once()
    call = handle.await_args
    assert call is not None
    assert call.args[0] == "a2"
