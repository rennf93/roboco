"""Board agents (Product Owner / Head of Marketing) must be dispatched for
assigned board-team tasks — and only ONCE.

Before this, no dispatcher spawned board roles (only PMs, devs, QA, doc, and
marketing were wired), so a task assigned to the Product Owner sat `pending`
forever. Board roles also have no verb to claim/plan/delegate/complete, so a
respawn cannot advance the task — dispatch is one-shot per (agent, task); the
CEO reassigns to Main PM after the board review is recorded.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator


def _make_orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._board_dispatched = set()
    return orch


def _board_task(assigned_to: str) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "status": "pending",
        "team": "board",
        "title": "Strategic feature",
        "description": "A board-level task to review and shape.",
        "assigned_to": assigned_to,
    }


@pytest.mark.asyncio
async def test_board_agent_spawned_once_for_assigned_board_task() -> None:
    orch = _make_orch()
    task = _board_task("product-owner")
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._handle_board_assigned_task(task, "product-owner")
        # Second tick: task is still pending (board has no progression verb) —
        # must NOT respawn (no loop).
        await orch._handle_board_assigned_task(task, "product-owner")

    spawn.assert_awaited_once()
    assert spawn.await_args.kwargs["agent_id"] == "product-owner"
    assert spawn.await_args.kwargs["task_id"] == task["id"]


@pytest.mark.asyncio
async def test_board_handler_skips_when_agent_active() -> None:
    orch = _make_orch()
    task = _board_task("head-marketing")
    with (
        patch.object(orch, "_is_agent_active", return_value=True),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._handle_board_assigned_task(task, "head-marketing")
    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_board_handler_ignores_non_board_assignee() -> None:
    orch = _make_orch()
    task = _board_task("be-pm")
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._handle_board_assigned_task(task, "be-pm")
    spawn.assert_not_awaited()


def test_board_review_prompt_uses_board_verbs_only() -> None:
    """The prompt must steer board agents to their real verbs (triage / note /
    say / i_am_idle) and away from claim/plan/delegate they do not have."""
    orch = _make_orch()
    prompt = orch._build_board_prompt(_board_task("product-owner"))
    assert "triage()" in prompt
    assert "note(" in prompt
    assert "i_am_idle()" in prompt
    assert "do NOT" in prompt.lower() or "do not" in prompt.lower()
