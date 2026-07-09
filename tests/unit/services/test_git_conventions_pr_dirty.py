"""``open_conventions_pr`` refuses a dirty working tree up front (returns
None, no checkout) so an active agent workspace is never swept into a
project-level conventions commit.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.services import git as git_module
from roboco.services.git import GitService

if TYPE_CHECKING:
    from pathlib import Path

_SCAFFOLD_BRANCH = "chore/roboco-conventions-scaffold"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("# r\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")


def _svc(monkeypatch: pytest.MonkeyPatch, repo: Path, root: Path) -> GitService:
    svc = GitService.__new__(GitService)
    svc.session = AsyncMock()

    # The workspace-scope guard requires workspace_path under
    # {workspaces_root}/{project.slug}; anchor the root at the test dir.
    monkeypatch.setattr(git_module.settings, "workspaces_root", str(root))
    project = MagicMock()
    project.slug = "g-proj"
    project.workspace_path = str(repo)
    project.default_branch = "master"
    project_service = MagicMock()
    project_service.get_by_slug = AsyncMock(return_value=project)
    monkeypatch.setattr(git_module, "get_project_service", lambda _s: project_service)
    # No remote token → push/PR skipped; we only exercise the local commit path.
    monkeypatch.setattr(svc, "_token_for_project", AsyncMock(return_value=None))
    return svc


def _branch_exists(repo: Path, branch: str) -> bool:
    res = subprocess.run(
        ["git", "rev-parse", "--verify", branch],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return res.returncode == 0


@pytest.mark.asyncio
async def test_dirty_tree_refused_before_any_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dirty working tree is the agent's active workspace — refuse, return
    None, and leave the tree exactly as it was (no scaffold branch, dirty
    change still uncommitted in the working tree)."""
    repo = tmp_path / "g-proj" / "repo"
    _init_repo(repo)
    # Dirty the tree: modify a tracked file (the agent's in-progress work).
    (repo / "README.md").write_text("# dirty work in progress\n")

    svc = _svc(monkeypatch, repo, tmp_path)

    result = await svc.open_conventions_pr(
        "g-proj", content="version: 1\n", title="scaffold", body="b"
    )

    # Refused.
    assert result is None
    # The scaffold branch was never created (no commit landed anywhere).
    assert not _branch_exists(repo, _SCAFFOLD_BRANCH)
    # The agent's dirty change is still in the working tree, uncommitted.
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "README.md" in status
    # master's history is untouched (still just the init commit).
    log = subprocess.run(
        ["git", "log", "--oneline", "master"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert log.count("\n") == 0  # exactly one commit on master


@pytest.mark.asyncio
async def test_clean_tree_proceeds_and_commits_on_scaffold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean tree proceeds: the conventions file is committed on the
    scaffold branch cut from master, and master itself is untouched
    (regression guard for the fix not over-rejecting the clean case)."""
    repo = tmp_path / "g-proj" / "repo"
    _init_repo(repo)

    svc = _svc(monkeypatch, repo, tmp_path)

    result = await svc.open_conventions_pr(
        "g-proj", content="version: 1\n", title="scaffold", body="b"
    )

    assert result is not None
    assert result["branch"] == _SCAFFOLD_BRANCH
    assert result["pr_number"] is None  # no token → no remote PR
    assert _branch_exists(repo, _SCAFFOLD_BRANCH)
    show = subprocess.run(
        ["git", "show", f"{_SCAFFOLD_BRANCH}:.roboco/conventions.yml"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert show.returncode == 0
    assert show.stdout == "version: 1\n"
    # The working tree ends back on master, clean.
    assert (
        subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        == "master"
    )


@pytest.mark.asyncio
async def test_missing_base_branch_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ``checkout <base>`` fails (base ref missing locally) the code must
    not fall through to ``checkout -B <scaffold>`` from the current branch —
    that would base the scaffold on the agent's task branch. Refuse when the
    checkout doesn't actually land on base."""
    repo = tmp_path / "g-proj" / "repo"
    _init_repo(repo)

    svc = _svc(monkeypatch, repo, tmp_path)
    # Override the project default to a branch that doesn't exist locally.
    project = MagicMock()
    project.slug = "g-proj"
    project.workspace_path = str(repo)
    project.default_branch = "nonexistent-base"
    project_service = MagicMock()
    project_service.get_by_slug = AsyncMock(return_value=project)
    monkeypatch.setattr(git_module, "get_project_service", lambda _s: project_service)

    result = await svc.open_conventions_pr(
        "g-proj", content="version: 1\n", title="scaffold", body="b"
    )

    # Refused — no scaffold branch fabricated on top of master.
    assert result is None
    assert not _branch_exists(repo, _SCAFFOLD_BRANCH)
