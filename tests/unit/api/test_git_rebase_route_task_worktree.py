"""``POST /api/git/rebase`` with a task rebases that task's branch in its worktree.

The route rebased "the current branch" of the caller's clone root, whatever
the ``task_id``: under F123 that is not the task's branch (the clone root
holds another, usually the default, which the route then refused as
protected). The panel's git browser sends the task it is showing. With a
``task_id`` the route now runs the gateway's sync path
(``GitService.sync_task_branch``: the task's worktree, fetch, rebase,
``--force-with-lease`` push); without one it is unchanged. Real git through
the real router and a real relay over a local bare origin; the database is
stubbed at the service seams.
"""

from __future__ import annotations

import shutil
import subprocess
from http import HTTPStatus
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db_committed
from roboco.api.routes import git as git_routes
from roboco.config import settings
from roboco.models import AgentRole, Team
from roboco.models.permissions import AgentContext
from roboco.services.git import GitService
from roboco.services.workspace import WorkspaceService
from tests.unit.local_origin_relay import serve_origin_through_relay

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git CLI required")

_BASE = "feature/backend/root1234"
_BRANCH = "feature/backend/root1234--abcd5678"
_PM_ID = uuid4()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def origin_and_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    """The task branch was cut from its base, which then moved on origin;
    be-pm's clone root sits on main."""
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path / "workspaces"))
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True
    )
    dev = tmp_path / "dev"
    subprocess.run(["git", "clone", "-q", str(origin), str(dev)], check=True)
    _git(dev, "commit", "-q", "--allow-empty", "-m", "base")
    _git(dev, "push", "-q", "origin", "main")
    _git(dev, "checkout", "-q", "-b", _BASE)
    _git(dev, "push", "-q", "origin", _BASE)
    _git(dev, "checkout", "-q", "-b", _BRANCH)
    (dev / "task.txt").write_text("task\n")
    _git(dev, "add", ".")
    _git(dev, "commit", "-q", "-m", "[abcd5678] task work")
    _git(dev, "push", "-q", "origin", _BRANCH)
    _git(dev, "checkout", "-q", _BASE)
    (dev / "base.txt").write_text("sibling landed\n")
    _git(dev, "add", ".")
    _git(dev, "commit", "-q", "-m", "sibling merged into the base")
    _git(dev, "push", "-q", "origin", _BASE)
    clone = tmp_path / "workspaces" / "proj" / "backend" / "be-pm"
    clone.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    serve_origin_through_relay(monkeypatch, tmp_path, origin)
    return origin, clone


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(git_routes.router, prefix="/api/git")

    async def _db() -> AsyncIterator[MagicMock]:
        yield MagicMock()

    async def _pm() -> AgentContext:
        return AgentContext(agent_id=_PM_ID, role=AgentRole.CELL_PM, team=Team.BACKEND)

    app.dependency_overrides[get_db_committed] = _db
    app.dependency_overrides[get_agent_context] = _pm
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def test_a_task_rebase_rebases_the_task_branch_in_its_worktree(
    origin_and_clone: tuple[Path, Path], client: AsyncClient
) -> None:
    origin, clone = origin_and_clone
    task = SimpleNamespace(
        id=uuid4(), branch_name=_BRANCH, assigned_to=_PM_ID, project_id=uuid4()
    )
    git = GitService(MagicMock())
    project = SimpleNamespace(slug="proj")
    object.__setattr__(git, "_project_for_task", AsyncMock(return_value=project))
    object.__setattr__(git, "get_workspace", AsyncMock(return_value=clone))
    object.__setattr__(git, "_protected_branches_for", AsyncMock(return_value=set()))
    object.__setattr__(git, "_get_project_token_or_raise", AsyncMock())
    tasks = MagicMock(get=AsyncMock(return_value=task))

    with (
        patch.object(git_routes, "get_git_service", return_value=git),
        patch.object(git_routes, "get_task_service", return_value=tasks),
        patch.object(
            git_routes, "_resolve_project_slug", AsyncMock(return_value="proj")
        ),
        patch("roboco.services.git.get_workspace_service", WorkspaceService),
    ):
        response = await client.post(
            "/api/git/rebase",
            json={
                "project_slug": "proj",
                "task_id": str(task.id),
                "target_branch": _BASE,
            },
        )

    body = response.json()
    assert response.status_code == HTTPStatus.OK, body
    assert body["conflict"] is False
    assert body["status"] == "rebased"
    base_tip = _git(origin, "rev-parse", _BASE)
    _git(origin, "merge-base", "--is-ancestor", base_tip, _BRANCH)
    assert _git(clone, "branch", "--show-current") == "main"
    worktree = clone / ".worktrees" / str(task.id)[:8]
    assert _git(worktree, "branch", "--show-current") == _BRANCH
