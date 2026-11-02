"""A documenter's claim attaches the task's worktree; its clone root stays put.

``claim_doc_task`` used to check the task branch out in the documenter's
clone root (``git checkout -b <branch> origin/<branch>``), the pre-F123
way. The documenter writes and commits in the task's worktree, so that
checkout either failed ("already checked out" at the worktree, swallowed)
or moved the clone root onto a task branch that origin deletes once it is
merged: the clone-root state behind ``couldn't find remote ref`` on every
refresh fetch (2026-10-28, 2026-11-01). Real git end to end: a bare origin
through the relay, the developer's branch pushed, the documenter's clone on
main.
"""

from __future__ import annotations

import shutil
import subprocess
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.services.git import GitService
from roboco.services.workspace import WorkspaceService
from tests.unit.local_origin_relay import serve_origin_through_relay

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git CLI required")

_BRANCH = "feature/backend/root1234--dev78901"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


async def test_the_doc_claim_attaches_the_task_worktree_not_the_clone_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path / "workspaces"))
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True
    )
    dev = tmp_path / "dev"
    subprocess.run(["git", "clone", "-q", str(origin), str(dev)], check=True)
    _git(dev, "commit", "-q", "--allow-empty", "-m", "base")
    _git(dev, "push", "-q", "origin", "main")
    _git(dev, "checkout", "-q", "-b", _BRANCH)
    _git(dev, "commit", "-q", "--allow-empty", "-m", "[dev78901] the work")
    _git(dev, "push", "-q", "origin", _BRANCH)
    doc_clone = tmp_path / "workspaces" / "proj" / "backend" / "be-doc"
    doc_clone.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(doc_clone)], check=True)
    relay = serve_origin_through_relay(monkeypatch, tmp_path, origin)
    task = SimpleNamespace(
        id=uuid4(), branch_name=_BRANCH, project_id=uuid4(), assigned_to=None
    )
    svc = GitService(MagicMock())
    object.__setattr__(svc, "_task_for_branch", AsyncMock(return_value=task))
    object.__setattr__(
        svc, "_project_for_task", AsyncMock(return_value=SimpleNamespace(slug="proj"))
    )
    object.__setattr__(svc, "get_workspace", AsyncMock(return_value=doc_clone))

    with (
        patch("roboco.services.git.get_workspace_service", WorkspaceService),
        patch.object(WorkspaceService, "_relay_for", AsyncMock(return_value=relay)),
    ):
        await svc.ensure_branch_worktree_for_agent(_BRANCH, actor_agent_id=uuid4())

    worktree = doc_clone / ".worktrees" / str(task.id)[:8]
    assert _git(worktree, "branch", "--show-current") == _BRANCH
    assert _git(worktree, "rev-parse", "HEAD") == _git(dev, "rev-parse", "HEAD")
    assert _git(doc_clone, "branch", "--show-current") == "main"
