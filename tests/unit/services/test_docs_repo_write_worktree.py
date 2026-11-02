"""A documenter's repo write lands in the task's worktree, where it commits.

Live 2026-10-28 (be-doc, task 5db9054b): ``Could not commit documentation to
repo`` — ``git add docs/architecture/learning-notification-queue.md`` failed
with "pathspec ... did not match any files". ``_commit_doc_to_repo`` wrote the
file into the documenter's clone root while ``GitService.commit`` stages and
commits in the task's per-task worktree (F123), so the doc never reached the
task branch. Real git; the git service is a stand-in whose ``commit`` stages
the file in the worktree exactly as the real one does.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.services.docs import DocsService, WriteDocInput

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git CLI required")


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


class _WorktreeGit:
    """The two GitService calls the repo write makes, over real git."""

    def __init__(self, clone_root: Path, worktree: Path) -> None:
        self.clone_root = clone_root
        self.worktree = worktree

    async def get_workspace(self, *_args: Any, **_kwargs: Any) -> Path:
        return self.clone_root

    async def ensure_task_worktree(self, *_args: Any, **_kwargs: Any) -> Path:
        return self.worktree

    async def commit(self, *, files: list[str], message: str, **_kw: Any) -> dict:
        _git(self.worktree, "add", *files)
        _git(self.worktree, "commit", "-m", message)
        return {"sha": _git(self.worktree, "rev-parse", "HEAD").strip()}


def _docs_service(task: Any) -> DocsService:
    result = MagicMock()
    result.scalar_one_or_none.return_value = task
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    return DocsService(session)


async def test_the_doc_is_written_and_committed_in_the_task_worktree(
    tmp_path: Path,
) -> None:
    clone = tmp_path / "roboco-api" / "backend" / "be-doc"
    clone.mkdir(parents=True)
    _git(clone, "init", "-b", "main")
    (clone / "README.md").write_text("x\n")
    _git(clone, "add", "README.md")
    _git(clone, "commit", "-m", "init")
    branch = "feature/backend/5db9054b"
    worktree = clone / ".worktrees" / "5db9054b"
    _git(clone, "worktree", "add", "-b", branch, str(worktree), "main")
    task = SimpleNamespace(id=uuid4(), branch_name=branch, project_id=uuid4())
    projects = MagicMock(get=AsyncMock(return_value=SimpleNamespace(slug="roboco")))
    git = _WorktreeGit(clone, worktree)

    with (
        patch("roboco.services.git.get_git_service", return_value=git),
        patch("roboco.services.project.get_project_service", return_value=projects),
    ):
        status = await _docs_service(task)._commit_doc_to_repo(
            str(uuid4()),
            WriteDocInput(
                task_id=task.id,
                filename="learning-notification-queue.md",
                doc_type="architecture",
                title="Learning notification queue",
                content="# Queue\n",
            ),
            "architecture",
        )

    assert status == "committed"
    committed = _git(worktree, "show", "--name-only", "--format=", branch).split()
    assert committed == ["docs/architecture/learning-notification-queue.md"]
    assert not (clone / "docs").exists(), "nothing is written into the clone root"
