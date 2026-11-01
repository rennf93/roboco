"""The pre-QA quality gate judges the submitted task's code, in its worktree.

Live 2026-10-28 / 2026-11-01: every developer ``i_am_done`` was refused with
the same 69 mypy errors (``Module "roboco.api.schemas.tasks" has no attribute
"TaskGovernanceReportResponse"``) on unrelated tasks (11a83407, f5d66db6), and
be-dev-2's ruff output named ``.worktrees/91758921/...`` paths: the gate ran in
the developer's clone root, parked on an old branch, instead of the per-task
worktree holding the work being submitted (F123). Real git, no mocks of git:
the clone root and the task worktree carry different trees, and the gate
command can only pass in the worktree.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.services.base import ValidationError
from roboco.services.gateway import quality_gate
from roboco.services.git import GitService
from roboco.services.workspace import WorkspaceService
from tests.unit.local_workspace_container import run_in_local_shell

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git CLI required")

_TASK_ONLY_FILE = "only_on_the_task_branch.txt"


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout


def _clone_with_task_branch(clone: Path, branch: str) -> None:
    """A clone root parked on ``main`` and a task branch that adds a file the
    clone root's checkout does not have (no worktree yet)."""
    clone.mkdir(parents=True)
    _git(clone, "init", "-b", "main")
    (clone / "README.md").write_text("clone root\n")
    _git(clone, "add", "README.md")
    _git(clone, "commit", "-m", "init")
    _git(clone, "checkout", "-b", branch)
    (clone / _TASK_ONLY_FILE).write_text("the submitted work\n")
    _git(clone, "add", _TASK_ONLY_FILE)
    _git(clone, "commit", "-m", "task work")
    _git(clone, "checkout", "main")


def _service(clone: Path) -> GitService:
    svc = GitService.__new__(GitService)
    svc.log = MagicMock()
    svc.session = MagicMock()
    object.__setattr__(svc, "get_workspace", AsyncMock(return_value=clone))
    project = SimpleNamespace(
        slug="roboco-api", quality_command=f"test -f {_TASK_ONLY_FILE}"
    )
    object.__setattr__(svc, "_project_for_task", AsyncMock(return_value=project))
    return svc


@pytest.fixture(autouse=True)
def _gate_runs_in_a_local_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(quality_gate, "run_in_workspace_container", run_in_local_shell)


@pytest.fixture
def real_worktrees() -> Iterator[None]:
    """``GitService`` reaches the worktree primitives through the workspace
    service factory; serve the real ones over a mock session."""
    with patch(
        "roboco.services.git.get_workspace_service",
        WorkspaceService,
    ):
        yield


@pytest.mark.usefixtures("real_worktrees")
async def test_the_gate_runs_in_the_task_worktree_not_the_clone_root(
    tmp_path: Path,
) -> None:
    clone = tmp_path / "roboco-api" / "backend" / "be-dev-1"
    task = SimpleNamespace(id=uuid4(), branch_name="feature/backend/abc12345")
    _clone_with_task_branch(clone, task.branch_name)
    worktree = clone / ".worktrees" / str(task.id)[:8]
    _git(clone, "worktree", "add", str(worktree), task.branch_name)

    result = await _service(clone).run_pre_submit_quality_gate(uuid4(), task)

    assert result.passed, result.output


@pytest.mark.usefixtures("real_worktrees")
async def test_a_pruned_task_worktree_is_re_attached_before_the_gate(
    tmp_path: Path,
) -> None:
    """The worktree can be evicted mid-task (disk pressure, reaper); the gate
    re-attaches it from the task branch the way the commit path does."""
    clone = tmp_path / "roboco-api" / "backend" / "be-dev-1"
    task = SimpleNamespace(id=uuid4(), branch_name="feature/backend/def67890")
    _clone_with_task_branch(clone, task.branch_name)

    result = await _service(clone).run_pre_submit_quality_gate(uuid4(), task)

    assert result.passed, result.output
    worktree = clone / ".worktrees" / str(task.id)[:8]
    assert (worktree / _TASK_ONLY_FILE).is_file()


async def test_a_task_without_a_branch_has_no_code_to_gate(tmp_path: Path) -> None:
    """No branch, no worktree: the gate refuses to judge the clone root
    instead (the submit verb treats that as an infrastructure error)."""
    clone = tmp_path / "roboco-api" / "backend" / "be-dev-1"
    task = SimpleNamespace(id=uuid4(), branch_name=None)
    with pytest.raises(ValidationError, match="NO_BRANCH"):
        await _service(clone).run_pre_submit_quality_gate(uuid4(), task)
