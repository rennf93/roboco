"""On resume, the branch-mismatch chokepoint recovers the clone instead of
hard-failing.

A dev/documenter/QA clone is shared across tasks; on a respawn/resume it can sit
on a sibling task's branch, or a re-provisioned clone can lack the task branch as
a local ref (commits only on origin). `_assert_on_task_branch` used to raise
BRANCH_MISMATCH in that state, so the agent's next commit failed and the task
wedged in a blocked respawn loop (the documented resume deadlock — e.g. the
documenter PR #102 case). It now fetches + checks out the task branch (recreating
a missing local ref from origin) and only raises if it genuinely cannot switch
(uncommitted changes block it). It NEVER discards local commits — checkout, not
reset.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.services.base import ValidationError
from roboco.services.git import GitService


def _service() -> GitService:
    session = MagicMock()
    session.execute = AsyncMock(return_value=None)
    return GitService(session)


def _bind(svc: GitService, name: str, value: object) -> None:
    object.__setattr__(svc, name, value)


def _run_git_mock(*, local_ref_rc: int = 0, checkout_rc: int = 0) -> AsyncMock:
    async def _run(_workspace: Path, args: list[str], **_kw: object) -> MagicMock:
        if args[:2] == ["rev-parse", "--verify"]:
            return MagicMock(returncode=local_ref_rc, stdout="")
        if args[0] == "checkout":
            return MagicMock(returncode=checkout_rc, stdout="")
        return MagicMock(returncode=0, stdout="")

    return AsyncMock(side_effect=_run)


@pytest.mark.asyncio
async def test_noop_when_already_on_task_branch() -> None:
    svc = _service()
    _bind(svc, "get_current_branch", AsyncMock(return_value="feature/main_pm/abc"))
    run = _run_git_mock()
    _bind(svc, "_run_git", run)
    await svc._assert_on_task_branch(Path("/ws"), "feature/main_pm/abc")
    run.assert_not_awaited()  # already on it → no git work, no raise


@pytest.mark.asyncio
async def test_noop_when_task_branch_none() -> None:
    svc = _service()
    gcb = AsyncMock(return_value="whatever")
    _bind(svc, "get_current_branch", gcb)
    await svc._assert_on_task_branch(Path("/ws"), None)
    gcb.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovers_by_checkout_when_local_ref_present() -> None:
    svc = _service()
    _bind(
        svc,
        "get_current_branch",
        AsyncMock(side_effect=["feature/other--leaf", "feature/main_pm/abc"]),
    )
    _bind(svc, "_token_for_workspace", AsyncMock(return_value="tok"))
    run = _run_git_mock(local_ref_rc=0, checkout_rc=0)
    _bind(svc, "_run_git", run)

    await svc._assert_on_task_branch(Path("/ws"), "feature/main_pm/abc")

    cmds = [c.args[1] for c in run.await_args_list]
    assert ["checkout", "feature/main_pm/abc"] in cmds
    # local ref present → no recovery fetch/branch-create
    assert not any(c[0] == "fetch" for c in cmds)


@pytest.mark.asyncio
async def test_recovers_missing_local_ref_from_origin() -> None:
    svc = _service()
    _bind(
        svc,
        "get_current_branch",
        AsyncMock(side_effect=["feature/other--leaf", "feature/main_pm/abc"]),
    )
    _bind(svc, "_token_for_workspace", AsyncMock(return_value="tok"))
    run = _run_git_mock(local_ref_rc=1, checkout_rc=0)  # local ref missing
    _bind(svc, "_run_git", run)

    await svc._assert_on_task_branch(Path("/ws"), "feature/main_pm/abc")

    cmds = [c.args[1] for c in run.await_args_list]
    assert ["fetch", "origin", "feature/main_pm/abc"] in cmds
    assert ["branch", "feature/main_pm/abc", "origin/feature/main_pm/abc"] in cmds
    assert ["checkout", "feature/main_pm/abc"] in cmds


@pytest.mark.asyncio
async def test_raises_when_cannot_switch() -> None:
    svc = _service()
    _bind(svc, "get_current_branch", AsyncMock(return_value="feature/other--leaf"))
    _bind(svc, "_token_for_workspace", AsyncMock(return_value="tok"))
    run = _run_git_mock(local_ref_rc=0, checkout_rc=1)  # checkout fails (dirty tree)
    _bind(svc, "_run_git", run)

    with pytest.raises(ValidationError, match="BRANCH_MISMATCH"):
        await svc._assert_on_task_branch(Path("/ws"), "feature/main_pm/abc")
