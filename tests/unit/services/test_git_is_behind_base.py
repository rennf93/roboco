"""Unit tests for ``GitService.is_behind_base`` (multi-level sequencing Phase B2).

Pins the rev-list ``--left-right --count`` parsing: ``"<left> <right>"`` where
left = commits only in the base (what the head is BEHIND by) and right = the
head's own ahead work. The behind-base submit gate keys off the ``behind``
count. Guards: requires ``branch_name``; raises when the project lookup misses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.base import NotFoundError
from roboco.services.git import GitService

_WORKSPACE = Path("/tmp/fake-ws")
_TOKEN = "ghp_fake"
_BASE = "feature/backend/parent12345"
_HEAD = "feature/backend/abc12345"


def _git_service() -> GitService:
    svc = GitService.__new__(GitService)
    svc.log = MagicMock()
    return svc


def _result(stdout: str = "", returncode: int = 0) -> Any:
    r = MagicMock()
    r.stdout = stdout
    r.returncode = returncode
    r.stderr = ""
    return r


def _task(branch: str | None = _HEAD) -> Any:
    return MagicMock(id=uuid4(), branch_name=branch)


def _project() -> Any:
    return MagicMock(slug="roboco")


async def _wire(svc: GitService, *, rev_list_stdout: str) -> AsyncMock:
    """Stub the workspace/token resolution + _run_git; return the run mock."""
    svc._project_for_task = AsyncMock(return_value=_project())  # type: ignore[method-assign]
    svc._resolve_workspace_agent_id = MagicMock(return_value=uuid4())  # type: ignore[method-assign]
    svc.get_workspace = AsyncMock(return_value=_WORKSPACE)  # type: ignore[method-assign]
    svc._get_project_token_or_raise = AsyncMock(return_value=_TOKEN)  # type: ignore[method-assign]
    run = AsyncMock(side_effect=[_result(), _result(stdout=rev_list_stdout)])
    svc._run_git = run  # type: ignore[method-assign]
    return run


@pytest.mark.asyncio
async def test_is_behind_base_parses_left_right_counts() -> None:
    """'3 2' → behind=3, ahead=2 (3 commits on base not on head)."""
    svc = _git_service()
    await _wire(svc, rev_list_stdout="3 2")

    behind, ahead = await svc.is_behind_base(_task(), base_branch=_BASE)

    assert (behind, ahead) == (3, 2)


@pytest.mark.asyncio
async def test_is_behind_base_up_to_date_returns_zeros() -> None:
    """'0 5' → not behind (0), 5 commits ahead."""
    svc = _git_service()
    await _wire(svc, rev_list_stdout="0 5")

    behind, ahead = await svc.is_behind_base(_task(), base_branch=_BASE)

    assert (behind, ahead) == (0, 5)


@pytest.mark.asyncio
async def test_is_behind_base_malformed_stdout_fails_open_to_zero() -> None:
    """A non-numeric stdout (git error text) must not crash — degrade to (0, 0)."""
    svc = _git_service()
    await _wire(svc, rev_list_stdout="fatal: bad rev")

    behind, ahead = await svc.is_behind_base(_task(), base_branch=_BASE)

    assert (behind, ahead) == (0, 0)


@pytest.mark.asyncio
async def test_is_behind_base_uses_left_right_triple_dot_form() -> None:
    """The rev-list call must use --left-right --count with triple-dot (symmetric
    difference) across origin/{base}...origin/{head}."""
    svc = _git_service()
    run = await _wire(svc, rev_list_stdout="1 4")

    await svc.is_behind_base(_task(), base_branch=_BASE)

    # second _run_git call is the rev-list; assert its argv shape.
    rev_list_call = run.call_args_list[1]
    argv = rev_list_call.args[1]
    assert argv[:3] == ["rev-list", "--left-right", "--count"]
    assert argv[3] == f"origin/{_BASE}...origin/{_HEAD}"


@pytest.mark.asyncio
async def test_is_behind_base_requires_branch_name() -> None:
    """A branchless task has nothing to compare — ValueError, not a silent (0,0)."""
    svc = _git_service()
    with pytest.raises(ValueError, match="branch_name"):
        await svc.is_behind_base(_task(branch=None), base_branch=_BASE)


@pytest.mark.asyncio
async def test_is_behind_base_raises_when_project_missing() -> None:
    svc = _git_service()
    svc._project_for_task = AsyncMock(return_value=None)  # type: ignore[method-assign]

    with pytest.raises(NotFoundError):
        await svc.is_behind_base(_task(), base_branch=_BASE)
