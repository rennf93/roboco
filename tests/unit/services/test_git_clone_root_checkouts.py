"""No task branch is checked out in a clone root any more (F123).

Two paths still did it:

- ``POST /api/git/checkout`` (``checkout_branch_for_agent``) ran ``git
  checkout <branch>`` in the caller's clone root. For the caller's own task
  the branch is checked out in its worktree, so git refused ("already
  checked out") and the fallback ``checkout -b`` refused again ("already
  exists"): a 500, every time. For a branch without a worktree it moved the
  clone root onto a task branch that origin later deletes, the state behind
  the ``couldn't find remote ref`` refresh failures.
- the supersede cut (``create_branch_from_pr_head``) checked the fork PR's
  head out in the system clone root before pushing it, so a retry after a
  failed push could not update the branch (git refuses to fetch into the
  checked-out branch), and the system clone root sat on contributor code.

Real git end to end: a bare origin through a real relay, clones under the
workspaces root.
"""

from __future__ import annotations

import shutil
import subprocess
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.api.schemas.git import GitCheckoutRequest
from roboco.config import settings
from roboco.services.base import ValidationError
from roboco.services.git import GitService
from roboco.services.workspace import WorkspaceService
from tests.unit.local_origin_relay import serve_origin_through_relay

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git CLI required")

_BRANCH = "feature/backend/abcd1234"
_AGENT = uuid4()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """origin with main and a task branch; be-dev-1's clone root on main."""
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path / "workspaces"))
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True
    )
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True)
    _git(seed, "commit", "-q", "--allow-empty", "-m", "base")
    _git(seed, "push", "-q", "origin", "main")
    _git(seed, "checkout", "-q", "-b", _BRANCH)
    _git(seed, "commit", "-q", "--allow-empty", "-m", "[abcd1234] work")
    _git(seed, "push", "-q", "origin", _BRANCH)
    clone = tmp_path / "workspaces" / "proj" / "backend" / "be-dev-1"
    clone.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    relay = serve_origin_through_relay(monkeypatch, tmp_path, origin)
    monkeypatch.setattr(WorkspaceService, "_relay_for", AsyncMock(return_value=relay))
    return origin, clone


def _service(clone: Path, task: object | None) -> GitService:
    svc = GitService(MagicMock())
    object.__setattr__(svc, "get_workspace", AsyncMock(return_value=clone))
    object.__setattr__(
        svc,
        "_allowed_checkout_branches",
        AsyncMock(return_value={"main", _BRANCH}),
    )
    object.__setattr__(svc, "_task_for_branch", AsyncMock(return_value=task))
    object.__setattr__(
        svc, "_project_for_task", AsyncMock(return_value=SimpleNamespace(slug="proj"))
    )
    return svc


def _checkout(branch: str) -> GitCheckoutRequest:
    return GitCheckoutRequest(project_slug="proj", branch=branch)


async def test_checking_out_your_own_task_attaches_its_worktree(
    setup: tuple[Path, Path],
) -> None:
    _, clone = setup
    task = SimpleNamespace(id=uuid4(), branch_name=_BRANCH, assigned_to=_AGENT)
    worktree = clone / ".worktrees" / str(task.id)[:8]
    _git(clone, "worktree", "add", "-q", str(worktree), _BRANCH)

    with patch("roboco.services.git.get_workspace_service", WorkspaceService):
        await _service(clone, task).checkout_branch_for_agent(
            _AGENT, _checkout(_BRANCH)
        )

    assert _git(clone, "branch", "--show-current") == "main"
    assert _git(worktree, "branch", "--show-current") == _BRANCH


async def test_another_agents_task_branch_never_lands_in_your_clone_root(
    setup: tuple[Path, Path],
) -> None:
    _, clone = setup
    task = SimpleNamespace(id=uuid4(), branch_name=_BRANCH, assigned_to=uuid4())

    with pytest.raises(ValidationError, match="CHECKOUT_OTHER_TASK"):
        await _service(clone, task).checkout_branch_for_agent(
            _AGENT, _checkout(_BRANCH)
        )

    assert _git(clone, "branch", "--show-current") == "main"


async def test_the_default_branch_is_still_checked_out_in_the_clone_root(
    setup: tuple[Path, Path],
) -> None:
    _, clone = setup
    _git(clone, "checkout", "-q", "-b", "scratch")

    await _service(clone, None).checkout_branch_for_agent(_AGENT, _checkout("main"))

    assert _git(clone, "branch", "--show-current") == "main"


async def test_a_supersede_cut_can_be_retried_and_leaves_the_clone_root_alone(
    setup: tuple[Path, Path],
) -> None:
    """The second cut is the retry after a push that failed: it must update
    the branch, and the system clone root stays on its default branch."""
    origin, clone = setup
    pr_head = _git(origin, "rev-parse", _BRANCH)
    _git(origin, "update-ref", "refs/pull/7/head", pr_head)
    svc = GitService(MagicMock())
    branch = "feature/backend/supersede-pr-7"

    await svc.create_branch_from_pr_head(clone, 7, branch)
    await svc.create_branch_from_pr_head(clone, 7, branch)

    assert _git(origin, "rev-parse", branch) == pr_head
    assert _git(clone, "branch", "--show-current") == "main"
