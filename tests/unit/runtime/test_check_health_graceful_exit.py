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
    orch._auth_ceo_notified = set()
    orch._auth_parked_agents = set()
    # NOTE: the crash path consults the dispatch maintenance-pause gate and
    # the bare __new__ double has no session factory, so the real lookup
    # fails closed ("treating scope as paused") and no respawn fires. Each
    # respawn-expecting test patches _is_paused to not-paused.
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

    with (
        patch.object(orch, "spawn_agent", new=spawn),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
    ):
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

    with (
        patch.object(orch, "spawn_agent", new=spawn),
        patch.object(orch, "_is_paused", AsyncMock(return_value=False)),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
    ):
        await orch._check_health()

    spawn.assert_awaited_once()
    assert spawn.await_args is not None
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

    with (
        patch.object(orch, "spawn_agent", new=spawn),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
    ):
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
    notify_stranded = AsyncMock()

    with (
        patch.object(orch, "spawn_agent", new=spawn),
        patch.object(orch, "_notify_agent_stranded", new=notify_stranded),
        patch.object(orch, "_is_paused", AsyncMock(return_value=False)),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
    ):
        await orch._check_health()

    spawn.assert_not_awaited()
    assert inst.error_count == starting_error_count + 1


@pytest.mark.asyncio
async def test_crash_cap_trips_across_instance_replacement() -> None:
    """Defect B: real spawn_agent (-> _prepare_agent_spawn) always builds a
    brand-new AgentInstance for self._instances[agent_id]. Without inheriting
    error_count from the instance it replaces, every respawn reset the crash
    counter to 0 and the max_retries escalation was unreachable. This drives
    _crash_retry_or_escalate with a stub spawn_agent that replaces
    self._instances[agent_id] the way the fixed real spawn does (carrying
    error_count forward) and asserts the cap now trips on the third crash."""
    orch = _make_orchestrator()
    agent_id = "be-dev-1"
    task_id = str(uuid4())
    orch._instances[agent_id] = _instance(task_id=task_id)
    notify_stranded = AsyncMock()

    async def _respawn(
        *, agent_id: str, task_id: str | None, **_kwargs: object
    ) -> None:
        prior = orch._instances.get(agent_id)
        new_inst = _instance(task_id=task_id)
        new_inst.error_count = prior.error_count if prior else 0
        orch._instances[agent_id] = new_inst

    spawn = AsyncMock(side_effect=_respawn)
    max_retries = 3  # mirrors _crash_retry_or_escalate's own local cap

    with (
        patch.object(orch, "spawn_agent", new=spawn),
        patch.object(orch, "_notify_agent_stranded", new=notify_stranded),
        patch.object(orch, "_is_paused", AsyncMock(return_value=False)),
    ):
        for _ in range(max_retries):
            await orch._crash_retry_or_escalate(agent_id, orch._instances[agent_id])

    assert spawn.await_count == max_retries - 1  # crashes 1 and 2 respawn
    notify_stranded.assert_awaited_once()  # crash 3 hits max_retries, escalates


@pytest.mark.asyncio
async def test_crash_budget_resets_on_new_task() -> None:
    """Finding 1: the carry-over in _prepare_agent_spawn must be keyed by
    (agent_id, task_id), not agent_id alone. An agent stranded on task A at
    error_count == max_retries that's later dispatched to a DIFFERENT task B
    must start B's crash budget at 0 - otherwise its first crash on B lands
    at error_count == 4, matching neither the respawn branch (< 3) nor the
    escalate branch (== 3), and the agent is silently dead forever."""
    orch = _make_orchestrator()
    agent_id = "be-dev-1"
    task_a = str(uuid4())
    task_b = str(uuid4())
    stranded = _instance(task_id=task_a)
    stranded.error_count = 3
    orch._instances[agent_id] = stranded
    notify_stranded = AsyncMock()

    def _spawn_onto_task(task_id: str | None) -> None:
        """Mirrors the FIXED _prepare_agent_spawn carry-over: 0 unless this
        spawn resumes the SAME task the prior instance was on."""
        prior = orch._instances.get(agent_id)
        carried = (
            prior.error_count
            if prior is not None and prior.current_task_id == task_id
            else 0
        )
        new_inst = _instance(task_id=task_id)
        new_inst.error_count = carried
        orch._instances[agent_id] = new_inst

    # Dispatch onto the new task, independent of the crash under test below.
    _spawn_onto_task(task_b)
    assert orch._instances[agent_id].error_count == 0

    spawn = AsyncMock()
    with (
        patch.object(orch, "spawn_agent", new=spawn),
        patch.object(orch, "_notify_agent_stranded", new=notify_stranded),
        patch.object(orch, "_is_paused", AsyncMock(return_value=False)),
    ):
        await orch._crash_retry_or_escalate(agent_id, orch._instances[agent_id])

    spawn.assert_awaited_once()  # fresh budget: one crash on B still respawns
    notify_stranded.assert_not_awaited()
    assert orch._instances[agent_id].error_count == 1


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

    with (
        patch.object(orch, "spawn_agent", new=spawn),
        patch.object(orch, "_is_paused", AsyncMock(return_value=False)),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
    ):
        await orch._check_health()

    # exit_code is None → not graceful → counts as crash.
    spawn.assert_awaited_once()
    assert inst.error_count == 1


@pytest.mark.asyncio
async def test_crash_no_respawn_while_dispatch_paused() -> None:
    """Dispatch pause armed: a crash leaves the agent offline with its
    error_count untouched, so the post-resume crash gets a full budget."""
    orch = _make_orchestrator()
    inst = _instance(task_id=str(uuid4()))
    orch._instances["be-dev-1"] = inst

    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"false 137\n", b""))
    spawn = AsyncMock()

    with (
        patch.object(orch, "spawn_agent", new=spawn),
        patch.object(orch, "_is_paused", AsyncMock(return_value=True)),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
    ):
        await orch._check_health()

    spawn.assert_not_awaited()
    assert inst.error_count == 0
