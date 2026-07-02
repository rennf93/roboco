"""QA dispatch must not pre-claim the review task.

Live 2026-07-02 (ba7b751c): the unassigned-QA branch claimed the task
BEFORE spawning (awaiting_qa -> claimed via the transitioning claim), then
spawned a QA agent whose own verbs demand awaiting_qa — claim_review bounced
("cannot claim from 'claimed'"), pass_review bounced, and the agent gave up
and unclaimed. The assigned-QA branch and the external-PR reviewer dispatch
both already spawn WITHOUT pre-claiming (the agent claims itself via
claim_review); the unassigned branch must match.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator


def _orch() -> tuple[AgentOrchestrator, AsyncMock, AsyncMock]:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._pm_respawn_tracker = {}
    orch._bg_tasks = set()
    any_orch = cast("Any", orch)
    any_orch._is_task_handled_this_tick = lambda _tid: False
    any_orch._select_agent_for_cell = lambda _team, _role: "be-qa"
    any_orch._is_agent_active = lambda _slug: False
    any_orch._pm_respawn_should_gate = AsyncMock(return_value=False)
    any_orch._build_qa_prompt = lambda _t: "review it"
    any_orch._task_git_context = lambda _t: None
    claim = AsyncMock(return_value=True)
    spawn = AsyncMock()
    any_orch._claim_task_for_agent = claim
    any_orch.spawn_agent = spawn
    return orch, claim, spawn


@pytest.mark.asyncio
async def test_unassigned_qa_dispatch_spawns_without_preclaim() -> None:
    orch, claim, spawn = _orch()
    task = {"id": str(uuid4()), "team": "backend", "assigned_to": None}
    cast("Any", orch)._fetch_tasks = AsyncMock(return_value=[task])

    await orch._dispatch_qa_work(MagicMock())

    claim.assert_not_awaited()
    spawn.assert_awaited_once()
    spawn_call = spawn.await_args
    assert spawn_call is not None
    assert spawn_call.kwargs["task_id"] == task["id"]
    assert spawn_call.kwargs["agent_id"] == "be-qa"
