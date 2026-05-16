"""#170: the closure dispatcher auto-resumes a paused parent before respawn.

A PM auto-pauses its owned parent on i_am_idle (by design, so the
closure dispatcher knows to respawn it when subtasks finish). Pre-gateway
the parent was resumed at respawn so the PM landed actionable; the
gateway refactor dropped that, so the respawned PM had to issue
`resume()` itself — which minimax reliably failed, wedging smoke-15.
_maybe_spawn_pm_closure must resume a `paused` parent (and only a
paused one) immediately before spawning its PM.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator


def _orch() -> AgentOrchestrator:
    with patch.object(AgentOrchestrator, "__init__", return_value=None):
        return AgentOrchestrator.__new__(AgentOrchestrator)


def _ready_orch() -> AgentOrchestrator:
    """Orchestrator with every closure gate stubbed so _maybe_spawn_pm_closure
    reaches the spawn (descendants terminal, not recently paused, not
    already promoted, PM idle)."""
    orch = _orch()
    orch._is_recently_paused = MagicMock(return_value=False)  # type: ignore[method-assign]
    orch._fetch_all_descendants = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"id": "leaf", "status": "completed"}]
    )
    orch._all_descendants_terminal = MagicMock(return_value=True)  # type: ignore[method-assign]
    orch._already_promoted_for_closure = MagicMock(return_value=False)  # type: ignore[method-assign]
    orch._closure_pm_for_team = MagicMock(return_value="be-pm")  # type: ignore[method-assign]
    orch._is_agent_active = MagicMock(return_value=False)  # type: ignore[method-assign]
    orch._build_pm_closure_prompt = MagicMock(return_value="PROMPT")  # type: ignore[method-assign]
    orch._task_git_context = MagicMock(return_value=None)  # type: ignore[method-assign]
    orch.spawn_agent = AsyncMock()  # type: ignore[method-assign]
    orch._auto_resume_paused_parent = AsyncMock()  # type: ignore[method-assign]
    return orch


@pytest.mark.asyncio
async def test_paused_parent_is_resumed_before_spawn() -> None:
    orch = _ready_orch()
    client = AsyncMock()
    task = {"id": "parent-1", "status": "paused", "team": "backend"}

    await orch._maybe_spawn_pm_closure(client, task)

    orch._auto_resume_paused_parent.assert_awaited_once_with(client, "parent-1")
    orch.spawn_agent.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_paused_parent_is_not_resumed() -> None:
    """awaiting_pm_review / in_progress parents must NOT be touched."""
    for st in ("awaiting_pm_review", "in_progress"):
        orch = _ready_orch()
        client = AsyncMock()
        task = {"id": "p", "status": st, "team": "backend"}

        await orch._maybe_spawn_pm_closure(client, task)

        orch._auto_resume_paused_parent.assert_not_awaited()
        orch.spawn_agent.assert_awaited_once()


@pytest.mark.asyncio
async def test_resume_skipped_when_closure_gate_blocks_spawn() -> None:
    """If descendants aren't terminal there is no spawn — and no resume."""
    orch = _ready_orch()
    orch._all_descendants_terminal = MagicMock(return_value=False)  # type: ignore[method-assign]
    client = AsyncMock()

    await orch._maybe_spawn_pm_closure(
        client, {"id": "p", "status": "paused", "team": "backend"}
    )

    orch._auto_resume_paused_parent.assert_not_awaited()
    orch.spawn_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_resume_patches_status_in_progress() -> None:
    orch = _orch()
    client = AsyncMock()

    await orch._auto_resume_paused_parent(client, "parent-9")

    client.patch.assert_awaited_once()
    call = client.patch.await_args
    assert call.args[0].endswith("/tasks/parent-9")
    assert call.kwargs["json"] == {"status": "in_progress"}


@pytest.mark.asyncio
async def test_auto_resume_swallows_errors() -> None:
    """A resume failure must not block the spawn (best-effort)."""
    orch = _orch()
    client = AsyncMock()
    client.patch = AsyncMock(side_effect=RuntimeError("api down"))

    # Must not raise.
    await orch._auto_resume_paused_parent(client, "p")
