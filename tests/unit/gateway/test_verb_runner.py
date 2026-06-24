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
from roboco.foundation.policy import lifecycle as spec
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
async def test_runner_rejects_none_task_or_agent() -> None:
    """A None task/agent fails loud with a clean error, not a NoneType crash.

    The atomic handlers dereference task.id / agent.id; without the guard a
    missing one crashes with "'NoneType' object has no attribute 'id'" (observed
    when a task was forced into an unexpected state out-of-band).
    """
    runner = VerbRunner(task_service=AsyncMock(), git_service=AsyncMock())
    ctx = spec.Context(plan="p")
    agent = MagicMock(id=uuid4(), role="cell_pm")
    task = MagicMock(id=uuid4(), status="in_progress")

    with pytest.raises(ValueError, match="INVALID_STATE"):
        await runner.run_intent("i_will_plan", None, agent, ctx)
    with pytest.raises(ValueError, match="INVALID_STATE"):
        await runner.run_intent("i_will_plan", task, None, ctx)


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
        parent_task_id=None,
        branch_name="feature/backend/ABC12345",
    )
    agent = MagicMock(id=uuid4(), role="developer")
    ctx = spec.Context()

    await runner.run_intent("open_pr", task, agent, ctx)
    git_svc.push_branch.assert_awaited_once()
    git_svc.create_pr.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_up_creates_pr_before_transition() -> None:
    """submit_up's create_pr (pre_side_effect) runs BEFORE the
    submit_for_review transition.

    submit_for_review needs the cell→root PR to already exist (the reviewer
    reviews it); create_pr persists pr_number onto the task row. With a
    composes→side_effects ordering the transition would run first and the
    trailing create_pr would crash — so the pre-side-effect must run first.
    """
    calls: list[str] = []

    task_svc = AsyncMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock())
    )

    def _submit_for_review(*_args: object, **_kwargs: object) -> MagicMock:
        calls.append("submit_for_review")
        return MagicMock(status="awaiting_pr_review")

    task_svc.submit_for_review = AsyncMock(side_effect=_submit_for_review)

    git_svc = AsyncMock()

    def _create_pr(*_args: object, **_kwargs: object) -> dict[str, int]:
        calls.append("create_pr")
        return {"pr_number": 31}

    git_svc.create_pr = AsyncMock(side_effect=_create_pr)
    runner = VerbRunner(task_service=task_svc, git_service=git_svc)

    task = MagicMock(
        id=uuid4(),
        status="in_progress",
        parent_task_id=None,
        branch_name="feature/backend/ABC12345--DEF67890",
    )
    agent = MagicMock(id=uuid4(), role="cell_pm")
    ctx = spec.Context(notes="cell scope complete; bubbling up to main pm")

    await runner.run_intent("submit_up", task, agent, ctx)
    assert calls == ["create_pr", "submit_for_review"], (
        f"create_pr must precede submit_for_review for submit_up; got {calls}"
    )


@pytest.mark.asyncio
async def test_create_pr_base_is_parent_task_branch_across_team() -> None:
    """#181: the cell→root PR base is the PARENT task's actual branch_name,
    not the team-preserving string derivation.

    The cell branch is feature/backend/ROOT--CELL but the root branch is
    feature/main_pm/ROOT (different team). parent_branch_for() would derive
    feature/backend/ROOT — a ref that doesn't exist on the remote, which
    GitHub rejects with base: invalid. The base must come from the parent
    task's branch_name.
    """
    task_svc = AsyncMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock())
    )
    task_svc.submit_pm_review = AsyncMock(
        return_value=MagicMock(status="awaiting_pm_review")
    )
    root_id = uuid4()
    # Parent (root) task lives under a DIFFERENT team prefix.
    task_svc.get = AsyncMock(
        return_value=MagicMock(branch_name="feature/main_pm/ROOT0001")
    )

    git_svc = AsyncMock()
    git_svc.create_pr = AsyncMock(return_value={"pr_number": 32})
    runner = VerbRunner(task_service=task_svc, git_service=git_svc)

    task = MagicMock(
        id=uuid4(),
        status="in_progress",
        parent_task_id=root_id,
        branch_name="feature/backend/ROOT0001--CELL0001",
    )
    agent = MagicMock(id=uuid4(), role="cell_pm")
    ctx = spec.Context(notes="cell scope complete; bubbling up to main pm")

    await runner.run_intent("submit_up", task, agent, ctx)

    task_svc.get.assert_awaited_once_with(root_id)
    _, kwargs = git_svc.create_pr.call_args
    assert kwargs["parent"] == "feature/main_pm/ROOT0001", (
        "cell→root PR base must be the parent task's real branch, not the "
        f"team-derived name; got parent={kwargs['parent']!r}"
    )


def test_submit_up_spec_pins_pr_before_transition() -> None:
    """The submit_up spec declares create_pr as a pre_side_effect, not a
    trailing side_effect — the ordering fix lives in the spec."""
    submit_up = spec._INTENT_VERBS["submit_up"]
    assert submit_up.pre_side_effects == ("create_pr",)
    assert "create_pr" not in submit_up.side_effects


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
