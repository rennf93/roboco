"""Verb runner — wraps spec.composed_actions_for in a savepoint.

Atomicity invariant: preconditions checked BEFORE side effects.
A mid-sequence atomic-action failure rolls the DB back to the
pre-call state; git side effects are runs AFTER the savepoint
commits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.lifecycle import spec
from roboco.services.gateway.choreographer._verb_runner import (
    VerbRunner,
)

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.asyncio
async def test_runner_runs_composed_actions_in_order() -> None:
    """For i_will_work_on the runner runs claim, then set_plan, then start."""
    task_svc = AsyncMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock())
    )
    runner = VerbRunner(task_service=task_svc, git_service=AsyncMock())

    calls: list[str] = []

    def _record(name: str, status: str) -> Callable[..., MagicMock]:
        def _inner(*_args: object, **_kwargs: object) -> MagicMock:
            calls.append(name)
            return MagicMock(status=status)

        return _inner

    task_svc.claim = AsyncMock(side_effect=_record("claim", "claimed"))
    task_svc.set_plan = AsyncMock(side_effect=_record("set_plan", "claimed"))
    task_svc.start = AsyncMock(side_effect=_record("start", "in_progress"))

    task = MagicMock(id=uuid4(), status="pending", plan=None, commits=[])
    agent = MagicMock(id=uuid4(), role="developer")
    ctx = spec.Context(plan="my plan")

    final_task = await runner.run_intent("i_will_work_on", task, agent, ctx)
    assert calls == ["claim", "set_plan", "start"]
    assert final_task.status == "in_progress"


@pytest.mark.asyncio
async def test_runner_runs_side_effects_after_db_commit() -> None:
    """For open_pr: composes is empty; side_effects (push_branch, create_pr) run."""
    task_svc = AsyncMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock())
    )
    git_svc = AsyncMock()
    git_svc.push_branch = AsyncMock()
    git_svc.create_pr = AsyncMock(return_value={"pr_number": 42})
    runner = VerbRunner(task_service=task_svc, git_service=git_svc)

    task = MagicMock(
        id=uuid4(),
        status="in_progress",
        commits=["abc"],
        pr_number=None,
        branch_name="feature/backend/ABC12345",
    )
    agent = MagicMock(id=uuid4(), role="developer")
    ctx = spec.Context()

    await runner.run_intent("open_pr", task, agent, ctx)
    git_svc.push_branch.assert_awaited_once()
    git_svc.create_pr.assert_awaited_once()


@pytest.mark.asyncio
async def test_runner_does_not_run_side_effects_if_compose_fails() -> None:
    """If a composed atomic action raises, side effects must NOT run."""
    task_svc = AsyncMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(),
            __aexit__=AsyncMock(side_effect=RuntimeError("rolled back")),
        )
    )
    task_svc.claim = AsyncMock(side_effect=RuntimeError("workspace down"))
    git_svc = AsyncMock()
    runner = VerbRunner(task_service=task_svc, git_service=git_svc)

    task = MagicMock(id=uuid4(), status="pending", plan=None, commits=[])
    agent = MagicMock(id=uuid4(), role="developer")
    ctx = spec.Context(plan="x")

    with pytest.raises(RuntimeError):
        await runner.run_intent("i_will_work_on", task, agent, ctx)
    git_svc.push_branch.assert_not_called()
    git_svc.create_pr.assert_not_called()
