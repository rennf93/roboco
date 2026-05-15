"""Smoke-8: _check_health distinguishes graceful (exit 0) from crash exits.

Original bug: every container stop bumped error_count and triggered
spawn_agent(agent_id, task_id=instance.current_task_id). After QA failed a
PR and cleanly idled, the health check respawned QA on the (now
needs_revision) task — the gateway rejected claim_review every time, and
QA respawned again on the next health tick. Token-burning tight loop.

Fix: read exit code via `docker inspect`. exit_code == 0 → graceful;
reset error_count and DO NOT auto-restart. Non-zero → crash; keep
existing retry behavior.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator, AgentState


def _make_orchestrator() -> AgentOrchestrator:
    with patch.object(AgentOrchestrator, "__init__", return_value=None):
        orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._lock = MagicMock()
    return orch


def _instance(task_id: str | None) -> MagicMock:
    inst = MagicMock()
    inst.state = AgentState.ACTIVE
    inst.container_id = "deadbeef1234"
    inst.current_task_id = task_id
    inst.error_count = 0
    inst.config = MagicMock(git_context=None)
    return inst


async def _docker_inspect_returning(*, running: bool, exit_code: int) -> bytes:
    return f"{'true' if running else 'false'} {exit_code}\n".encode()


@pytest.mark.asyncio
async def test_graceful_exit_does_not_respawn() -> None:
    """Container exit_code=0 means clean shutdown. No auto-restart."""
    orch = _make_orchestrator()
    inst = _instance(task_id=str(uuid4()))
    orch._instances["be-qa"] = inst

    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"false 0\n", b""))
    spawn = AsyncMock()
    orch.spawn_agent = spawn

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        await orch._check_health()

    spawn.assert_not_awaited()
    assert inst.state == AgentState.OFFLINE
    assert inst.error_count == 0, (
        "Graceful exit must reset error_count, not bump it. Otherwise a "
        "long-running agent that idles clean every time eventually trips "
        "max_retries and gets flagged as stranded."
    )


@pytest.mark.asyncio
async def test_crash_exit_triggers_restart() -> None:
    """Container exit_code != 0 means crash. Auto-restart (existing behavior)."""
    orch = _make_orchestrator()
    task_id = str(uuid4())
    inst = _instance(task_id=task_id)
    orch._instances["be-dev-1"] = inst

    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"false 137\n", b""))
    spawn = AsyncMock()
    orch.spawn_agent = spawn

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        await orch._check_health()

    spawn.assert_awaited_once()
    args = spawn.await_args.kwargs
    assert args["agent_id"] == "be-dev-1"
    assert args["task_id"] == task_id
    assert inst.error_count == 1


@pytest.mark.asyncio
async def test_still_running_no_action() -> None:
    """If the container is still running, no state change."""
    orch = _make_orchestrator()
    inst = _instance(task_id=str(uuid4()))
    orch._instances["be-dev-1"] = inst

    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"true 0\n", b""))
    spawn = AsyncMock()
    orch.spawn_agent = spawn

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        await orch._check_health()

    spawn.assert_not_awaited()
    assert inst.state == AgentState.ACTIVE
    assert inst.error_count == 0
    assert inst.container_id == "deadbeef1234"


@pytest.mark.asyncio
async def test_crash_max_retries_does_not_restart() -> None:
    """Hit max_retries → don't restart (existing behavior preserved)."""
    orch = _make_orchestrator()
    inst = _instance(task_id=str(uuid4()))
    starting_error_count = 3
    inst.error_count = starting_error_count
    orch._instances["be-dev-1"] = inst

    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"false 1\n", b""))
    spawn = AsyncMock()
    orch.spawn_agent = spawn
    orch._notify_agent_stranded = AsyncMock()

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        await orch._check_health()

    spawn.assert_not_awaited()
    assert inst.error_count == starting_error_count + 1


@pytest.mark.asyncio
async def test_malformed_inspect_treated_as_crash() -> None:
    """If `docker inspect` returns malformed output, default to crash path."""
    orch = _make_orchestrator()
    inst = _instance(task_id=str(uuid4()))
    orch._instances["be-dev-1"] = inst

    proc = MagicMock()
    # No exit code field at all.
    proc.communicate = AsyncMock(return_value=(b"false\n", b""))
    spawn = AsyncMock()
    orch.spawn_agent = spawn

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        await orch._check_health()

    # exit_code is None → not graceful → counts as crash.
    spawn.assert_awaited_once()
    assert inst.error_count == 1
