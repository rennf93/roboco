"""GitService.open_conventions_pr commits the file locally; PR is best-effort."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from uuid import uuid4

from roboco.db.tables import AgentTable, ProjectTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.services.git import _ConventionsPr, get_git_service

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

_SCAFFOLD_BRANCH = "chore/roboco-conventions-scaffold"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


async def _seed_project(db: AsyncSession, workspace_path: str) -> ProjectTable:
    agent = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"be-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db.add(agent)
    await db.flush()
    project = ProjectTable(
        id=uuid4(),
        name="G-Proj",
        slug=f"g-proj-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        default_branch="master",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
        workspace_path=workspace_path,
    )
    db.add(project)
    await db.flush()
    return project


async def test_open_conventions_pr_commits_locally_without_remote(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("# r\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")

    project = await _seed_project(db_session, str(repo))
    git = get_git_service(db_session)
    result = await git.open_conventions_pr(
        project.slug,
        content="version: 1\n",
        title="scaffold",
        body="b",
    )

    assert result is not None
    assert result["pr_number"] is None  # no git token / remote → PR not opened
    show = subprocess.run(
        ["git", "show", f"{_SCAFFOLD_BRANCH}:.roboco/conventions.yml"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert show.returncode == 0
    assert show.stdout == "version: 1\n"


async def test_open_conventions_pr_returns_none_without_workspace(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    project = await _seed_project(db_session, str(tmp_path / "does-not-exist"))
    git = get_git_service(db_session)
    result = await git.open_conventions_pr(
        project.slug,
        content="version: 1\n",
        title="t",
        body="b",
    )
    assert result is None


async def test_open_conventions_pr_force_pushes_scaffold_branch(
    db_session: AsyncSession, tmp_path: Path, monkeypatch
) -> None:
    # Second call: scaffold branch already on remote (non-fast-forward).
    # Must force-push (force=True) and return a real pr_number, not the
    # unopened {"pr_number": None} dict.
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("# r\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")

    project = await _seed_project(db_session, str(repo))
    git = get_git_service(db_session)

    pushed: list[bool] = []

    async def _fake_push(_workspace, force=False, _branch=None):
        pushed.append(force)
        return (_SCAFFOLD_BRANCH, 1)

    async def _fake_token(_slug):
        return "tok"

    monkeypatch.setattr(git, "_token_for_project", _fake_token)
    monkeypatch.setattr(git, "push", _fake_push)
    monkeypatch.setattr(git, "_parse_github_remote", lambda _ws: ("owner", "repo"))

    _pr_number = 42
    _pr_url = "https://github.com/owner/repo/pull/42"

    class _Resp:
        is_success = True

        def json(self):
            return {"number": _pr_number, "html_url": _pr_url}

    async def _fake_post_pr(_owner, _repo, _token, _body):
        return _Resp()

    monkeypatch.setattr(git, "_post_pr", _fake_post_pr)

    spec = _ConventionsPr(
        content="version: 1\n",
        branch=_SCAFFOLD_BRANCH,
        title="scaffold",
        body="b",
    )
    result = await git._push_and_open_conventions_pr(project.slug, repo, "master", spec)

    assert pushed == [True], "second call must force-push (force=True)"
    assert result["pr_number"] == _pr_number
    assert result["pr_url"] == _pr_url
