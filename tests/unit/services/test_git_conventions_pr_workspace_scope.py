"""``open_conventions_pr`` refuses a ``project.workspace_path`` outside the
project's own workspace tree.

``workspace_path`` is settable through the PM-gated ``POST
/projects/{id}/workspace`` route with no path validation, so a steered PM
agent could point it at an arbitrary orchestrator directory (or another
project's clone) and have the conventions flow write + commit there. Only a
path under ``{workspaces_root}/{project.slug}`` may receive the scaffold
commit; anything else is treated as "no usable workspace" (returns None).
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


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("# r\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")


def _svc(
    monkeypatch: pytest.MonkeyPatch, workspace_path: Path, root: Path
) -> GitService:
    svc = GitService.__new__(GitService)
    svc.session = AsyncMock()
    monkeypatch.setattr(git_module.settings, "workspaces_root", str(root))
    project = MagicMock()
    project.slug = "g-proj"
    project.workspace_path = str(workspace_path)
    project.default_branch = "master"
    project_service = MagicMock()
    project_service.get_by_slug = AsyncMock(return_value=project)
    monkeypatch.setattr(git_module, "get_project_service", lambda _s: project_service)
    monkeypatch.setattr(svc, "_token_for_project", AsyncMock(return_value=None))
    return svc


def _branch_exists(repo: Path, branch: str) -> bool:
    res = subprocess.run(
        ["git", "rev-parse", "--verify", branch],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    return res.returncode == 0


@pytest.mark.asyncio
async def test_workspace_path_outside_root_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace_path outside workspaces_root entirely is refused and never
    receives a scaffold branch or file."""
    root = tmp_path / "workspaces"
    root.mkdir()
    outside = tmp_path / "outside-repo"
    _init_repo(outside)
    svc = _svc(monkeypatch, outside, root)

    result = await svc.open_conventions_pr(
        "g-proj", content="version: 1\n", title="scaffold", body="b"
    )

    assert result is None
    assert not _branch_exists(outside, _SCAFFOLD_BRANCH)
    assert not (outside / ".roboco" / "conventions.yml").exists()


@pytest.mark.asyncio
async def test_workspace_path_in_other_projects_tree_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace_path under workspaces_root but inside ANOTHER project's
    tree is refused — cross-project commits are not allowed."""
    other = tmp_path / "other-proj" / "backend" / "be-dev-1"
    _init_repo(other)
    svc = _svc(monkeypatch, other, tmp_path)

    result = await svc.open_conventions_pr(
        "g-proj", content="version: 1\n", title="scaffold", body="b"
    )

    assert result is None
    assert not _branch_exists(other, _SCAFFOLD_BRANCH)


@pytest.mark.asyncio
async def test_explicit_workspace_argument_bypasses_db_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The explicit ``workspace`` argument (internally constructed by callers
    from the validated workspace layout) is honored as before — the scope
    guard applies to the API-settable DB field, not the trusted argument."""
    repo = tmp_path / "elsewhere" / "clone"
    _init_repo(repo)
    # DB field points somewhere invalid; explicit arg wins.
    svc = _svc(monkeypatch, tmp_path / "bogus", tmp_path / "workspaces")

    result = await svc.open_conventions_pr(
        "g-proj",
        content="version: 1\n",
        title="scaffold",
        body="b",
        workspace=repo,
    )

    assert result is not None
    assert result["branch"] == _SCAFFOLD_BRANCH
    assert _branch_exists(repo, _SCAFFOLD_BRANCH)
