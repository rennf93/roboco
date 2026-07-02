"""_dispatch_revision_coordination_roots — un-deadlock a CEO-rejected root (#5).

A coordination root (team=main_pm, product-linked, no repo) the CEO sends back
lands in needs_revision. The dev dispatcher skips it (not a cell team) and the
closure path only handles paused parents, so without this dispatcher it sits
forever. This re-spawns its owning PM so it re-coordinates the revision.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import roboco.runtime.orchestrator as orch_mod
from roboco.runtime.orchestrator import AgentOrchestrator


def _orch(
    tasks: list[dict[str, Any]], *, slug: str, active: bool
) -> tuple[AgentOrchestrator, AsyncMock]:
    """A bare orchestrator with its dispatch helpers mocked; returns (orch, spawn)."""
    orch = object.__new__(AgentOrchestrator)
    orch._pm_respawn_tracker = {}
    orch._schedule_respawn_persist = lambda *_a, **_k: None
    spawn = AsyncMock()
    object.__setattr__(orch, "_fetch_tasks", AsyncMock(return_value=tasks))
    object.__setattr__(
        orch, "_is_task_handled_this_tick", MagicMock(return_value=False)
    )
    object.__setattr__(orch, "_resolve_agent_slug", MagicMock(return_value=slug))
    object.__setattr__(orch, "_is_agent_active", MagicMock(return_value=active))
    object.__setattr__(orch, "_get_prompt_for_agent", MagicMock(return_value="p"))
    object.__setattr__(orch, "_task_git_context", MagicMock(return_value=None))
    object.__setattr__(orch, "spawn_agent", spawn)
    return orch, spawn


def _task() -> dict[str, Any]:
    return {
        "id": "t1",
        "status": "needs_revision",
        "assigned_to": "u1",
        "team": "main_pm",
    }


@pytest.mark.asyncio
async def test_respawns_pm_for_rejected_coordination_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orch_mod, "_is_coordination_task", lambda _t: True)
    orch, spawn = _orch([_task()], slug="main-pm", active=False)
    await orch._dispatch_revision_coordination_roots(MagicMock())
    spawn.assert_awaited_once()
    call = spawn.await_args
    assert call is not None
    assert call.kwargs["agent_id"] == "main-pm"
    assert call.kwargs["task_id"] == "t1"


@pytest.mark.asyncio
async def test_skips_non_coordination_needs_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A normal (code) needs_revision task → left to the dev dispatcher.
    monkeypatch.setattr(orch_mod, "_is_coordination_task", lambda _t: False)
    orch, spawn = _orch([_task()], slug="be-dev-1", active=False)
    await orch._dispatch_revision_coordination_roots(MagicMock())
    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_when_pm_already_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orch_mod, "_is_coordination_task", lambda _t: True)
    orch, spawn = _orch([_task()], slug="main-pm", active=True)
    await orch._dispatch_revision_coordination_roots(MagicMock())
    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_non_pm_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    # A coordination root owned by a non-PM role → role guard skips it.
    monkeypatch.setattr(orch_mod, "_is_coordination_task", lambda _t: True)
    orch, spawn = _orch([_task()], slug="be-dev-1", active=False)
    await orch._dispatch_revision_coordination_roots(MagicMock())
    spawn.assert_not_awaited()
