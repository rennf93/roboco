"""X post/reply proposals are CEO-gated artifacts, never delivery work.

Mirrors the release-manager dispatch-skip tests: an ``x_post``/``x_reply``
task must never be handed to the PM-triage path or the dev-assignment path,
regardless of ``confirmed_by_human`` (unlike self-heal, there is no CEO gate
that ever lifts an X draft into delivery work — it is acted on only by the
x routes + post service).
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.task import X_POST_SOURCE, X_REPLY_SOURCE


def _task(tid: str, source: str, *, assigned_to: str | None = None) -> dict[str, Any]:
    return {"id": tid, "source": source, "assigned_to": assigned_to}


@pytest.mark.asyncio
async def test_x_posts_are_never_routed_by_pm_dispatch() -> None:
    tasks = [
        _task("A", X_POST_SOURCE, assigned_to="secretary-1"),
        _task("B", X_REPLY_SOURCE, assigned_to="secretary-1"),
        _task("C", "manual"),  # ordinary unassigned -> routing still happens
    ]
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=tasks)
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._resolve_agent_slug = MagicMock(return_value="secretary-1")
    stub._BOARD_AGENTS = frozenset()
    stub._route_unassigned_pm_task = AsyncMock()
    stub._handle_pm_assigned_task = AsyncMock()
    stub._handle_board_assigned_task = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_pm_work(cast("AgentOrchestrator", stub), client)

    stub._handle_pm_assigned_task.assert_not_awaited()
    stub._handle_board_assigned_task.assert_not_awaited()
    routed = [c.args[1]["id"] for c in stub._route_unassigned_pm_task.await_args_list]
    assert routed == ["C"]


@pytest.mark.asyncio
async def test_x_posts_are_never_routed_by_dev_dispatch() -> None:
    tasks = [
        _task("A", X_POST_SOURCE, assigned_to="secretary-1"),
        _task("B", X_REPLY_SOURCE, assigned_to="secretary-1"),
    ]
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=tasks)
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._dev_dispatch_one = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_dev_work(cast("AgentOrchestrator", stub), client)

    stub._dev_dispatch_one.assert_not_awaited()


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
