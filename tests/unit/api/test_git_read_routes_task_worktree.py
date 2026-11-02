"""``/api/git/status`` and ``/api/git/diff`` read the agent's task worktree.

Under F123 an agent works in ``<clone>/.worktrees/<task id[:8]>``; its clone
root holds another branch's tree. The agents' ``roboco_git_status`` /
``roboco_git_diff`` tools (and the panel's git browser) read these routes,
which reported the clone root: an agent asking "what have I changed?" was
shown somebody else's checkout. Both routes now read the worktree of the
task named by ``task_id`` (the legacy ``_task_id`` still works), else of the
caller's active task (``TaskService.get_active_task_for_agent``, as the
gateway's ``commit`` resolves it), and fall back to the clone root only when
there is none; the response names the task whose worktree it read. Real git;
the database is stubbed at the service seams.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from http import HTTPStatus
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
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

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from pathlib import Path

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git CLI required")

_BRANCH = "feature/backend/abcd1234"
_AGENT_ID = uuid4()


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
    ).stdout.strip()


@pytest.fixture
def clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """be-dev-1's clone root on main; its task's worktree carries an
    uncommitted edit the clone root does not have."""
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path / "workspaces"))
    clone = tmp_path / "workspaces" / "proj" / "backend" / "be-dev-1"
    clone.mkdir(parents=True)
    _git(clone, "init", "-q", "-b", "main")
    (clone / "app.py").write_text("x = 1\n")
    _git(clone, "add", "app.py")
    _git(clone, "commit", "-qm", "base")
    return clone


def _task(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "id": uuid4(),
        "branch_name": _BRANCH,
        "assigned_to": _AGENT_ID,
        "project_id": uuid4(),
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _worktree_with_edit(clone: Path, task: SimpleNamespace) -> Path:
    worktree = clone / ".worktrees" / str(task.id)[:8]
    _git(clone, "worktree", "add", "-q", "-b", _BRANCH, str(worktree), "main")
    (worktree / "app.py").write_text("x = 2\n")
    return worktree


@pytest.fixture
def services(clone: Path) -> Iterator[MagicMock]:
    """A real GitService over the clone; the task lookups are the stub."""
    tasks = MagicMock(get=AsyncMock(), get_active_task_for_agent=AsyncMock())
    git = GitService(MagicMock())
    object.__setattr__(git, "get_workspace", AsyncMock(return_value=clone))
    object.__setattr__(
        git, "_project_for_task", AsyncMock(return_value=SimpleNamespace(slug="proj"))
    )
    with (
        patch.object(git_routes, "get_git_service", return_value=git),
        patch.object(
            git_routes, "_resolve_project_slug", AsyncMock(return_value="proj")
        ),
        patch("roboco.services.git.get_task_service", return_value=tasks),
        patch("roboco.services.git.get_workspace_service", WorkspaceService),
    ):
        yield tasks


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(git_routes.router, prefix="/api/git")

    async def _db() -> AsyncIterator[MagicMock]:
        yield MagicMock()

    async def _agent() -> AgentContext:
        return AgentContext(
            agent_id=_AGENT_ID, role=AgentRole.DEVELOPER, team=Team.BACKEND
        )

    app.dependency_overrides[get_db_committed] = _db
    app.dependency_overrides[get_agent_context] = _agent
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def test_status_reads_the_active_tasks_worktree(
    clone: Path, services: MagicMock, client: AsyncClient
) -> None:
    task = _task()
    _worktree_with_edit(clone, task)
    services.get_active_task_for_agent.return_value = task

    response = await client.get("/api/git/status", params={"project_slug": "proj"})

    body = response.json()
    assert response.status_code == HTTPStatus.OK, body
    assert body["current_branch"] == _BRANCH
    assert body["unstaged_files"] == ["app.py"]
    assert body["task_id"] == str(task.id)


async def test_diff_reads_the_active_tasks_worktree(
    clone: Path, services: MagicMock, client: AsyncClient
) -> None:
    task = _task()
    _worktree_with_edit(clone, task)
    services.get_active_task_for_agent.return_value = task

    response = await client.get("/api/git/diff", params={"project_slug": "proj"})

    body = response.json()
    assert "+x = 2" in body["diff"]
    assert body["task_id"] == str(task.id)


async def test_without_an_active_task_the_clone_root_is_read(
    services: MagicMock, client: AsyncClient
) -> None:
    services.get_active_task_for_agent.return_value = None

    response = await client.get("/api/git/status", params={"project_slug": "proj"})

    body = response.json()
    assert body["current_branch"] == "main"
    assert body["task_id"] is None


async def test_a_named_task_wins_and_the_legacy_parameter_still_works(
    clone: Path, services: MagicMock, client: AsyncClient
) -> None:
    task = _task()
    _worktree_with_edit(clone, task)
    services.get.return_value = task
    services.get_active_task_for_agent.return_value = None

    for name in ("task_id", "_task_id"):
        response = await client.get(
            "/api/git/status", params={"project_slug": "proj", name: str(task.id)}
        )
        assert response.json()["current_branch"] == _BRANCH, name


async def test_someone_elses_task_never_gets_a_worktree_in_the_callers_clone(
    clone: Path, services: MagicMock, client: AsyncClient
) -> None:
    """A read never adds a worktree for a task the caller does not own (the
    panel browsing a developer's task from the CEO's clone): it reads that
    worktree only when the clone already has it, else the clone root."""
    services.get.return_value = _task(assigned_to=uuid4())

    response = await client.get(
        "/api/git/status",
        params={"project_slug": "proj", "task_id": str(services.get.return_value.id)},
    )

    assert response.json()["current_branch"] == "main"
    assert response.json()["task_id"] is None
    assert not (clone / ".worktrees").exists()
