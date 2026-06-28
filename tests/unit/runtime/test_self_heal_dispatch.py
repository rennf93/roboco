"""Self-heal fix tasks dispatch through the PM dispatcher only after the CEO
approves them (F059).

The loop opens a ``source='self_heal'`` task assigned to the Main PM agent but
HELD (``confirmed_by_human=False``) for the CEO's Approve-&-Start. The dispatcher
holds an unconfirmed self-heal task out of the assigned-PM path; once the CEO's
``approve_and_start`` flips ``confirmed_by_human`` True, it routes through the
assigned-PM path like any other PM task. Unassigned tasks route normally.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.task import SELF_HEAL_SOURCE


def _task(
    tid: str,
    source: str,
    *,
    assigned_to: str | None = None,
    confirmed: bool | None = None,
) -> dict[str, Any]:
    task: dict[str, Any] = {"id": tid, "source": source, "assigned_to": assigned_to}
    if confirmed is not None:
        task["confirmed_by_human"] = confirmed
    return task


@pytest.mark.asyncio
async def test_ceo_approved_self_heal_task_dispatches_through_assigned_pm_path() -> (
    None
):
    """A self-heal task the CEO has approved (confirmed_by_human=True) is handed
    to the assigned-PM path — the CEO's gate has lifted."""
    tasks = [
        _task(
            "A", SELF_HEAL_SOURCE, assigned_to="main-pm", confirmed=True
        ),  # CEO-approved → assigned-PM path
        _task("C", "manual"),  # ordinary unassigned → routing
    ]
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=tasks)
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._resolve_agent_slug = MagicMock(return_value="main-pm")
    stub._BOARD_AGENTS = frozenset()
    stub._route_unassigned_pm_task = AsyncMock()
    stub._handle_pm_assigned_task = AsyncMock()
    stub._handle_board_assigned_task = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_pm_work(cast("AgentOrchestrator", stub), client)

    handled = [c.args[0]["id"] for c in stub._handle_pm_assigned_task.await_args_list]
    assert handled == ["A"]
    routed = [c.args[1]["id"] for c in stub._route_unassigned_pm_task.await_args_list]
    assert routed == ["C"]


@pytest.mark.asyncio
async def test_held_self_heal_task_is_not_dispatched() -> None:
    """A self-heal task the CEO has NOT yet approved (confirmed_by_human=False)
    is held — neither the assigned-PM path nor routing touches it."""
    tasks = [
        _task(
            "A", SELF_HEAL_SOURCE, assigned_to="main-pm", confirmed=False
        ),  # held → skip
        _task("B", SELF_HEAL_SOURCE, confirmed=False),  # held + unassigned → skip
        _task("C", "manual"),  # ordinary unassigned → routing still happens
    ]
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=tasks)
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._resolve_agent_slug = MagicMock(return_value="main-pm")
    stub._BOARD_AGENTS = frozenset()
    stub._route_unassigned_pm_task = AsyncMock()
    stub._handle_pm_assigned_task = AsyncMock()
    stub._handle_board_assigned_task = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_pm_work(cast("AgentOrchestrator", stub), client)

    stub._handle_pm_assigned_task.assert_not_awaited()
    # Only the non-self-heal unassigned task routes; the held self-heal one does not.
    routed = [c.args[1]["id"] for c in stub._route_unassigned_pm_task.await_args_list]
    assert routed == ["C"]


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
