"""An agent clone is made through the relay: no token ever touches it, and a
clone that fails part-way leaves nothing behind.

The relay fetches origin (with the project token, in its own root-owned
repository); the workspace git user then clones the relay. The workspace's
``remote.origin.url`` is the project's URL from the database, display only.
A failure anywhere (relay fetch, clone, configuration) or a timeout removes
the partial workspace so the next ``ensure_workspace`` re-clones from scratch
instead of short-circuiting past it on a valid-looking ``.git``.
"""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.config import settings
from roboco.services import workspace as workspace_module
from roboco.services.git_relay import GitRelay
from roboco.services.workspace import WorkspaceError, WorkspaceService

if TYPE_CHECKING:
    from pathlib import Path

_TOKEN = "ghp_NEVERINTHECLONE"


@pytest.fixture
def origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "private_data_root", str(tmp_path / "private"))
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path / "workspaces"))
    monkeypatch.setattr(settings, "workspace_install_dev_deps", False)
    (tmp_path / "workspaces").mkdir()
    if os.geteuid() == 0:
        # As root, workspace git runs as the git user: let it traverse
        # pytest's 0700 temp dirs, as /data is traversable in production.
        for directory in (tmp_path, tmp_path.parent, tmp_path.parent.parent):
            directory.chmod(directory.stat().st_mode | 0o055)
    bare = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    git = ["git", "-c", "user.name=s", "-c", "user.email=s@s"]
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "master", str(bare)], check=True
    )
    subprocess.run(["git", "init", "-q", "-b", "master", str(seed)], check=True)
    (seed / "README").write_text("seed\n")
    subprocess.run([*git, "-C", str(seed), "add", "."], check=True)
    subprocess.run([*git, "-C", str(seed), "commit", "-qm", "seed"], check=True)
    subprocess.run(
        [*git, "-C", str(seed), "push", "-q", str(bare), "master"], check=True
    )
    return bare


def _service(
    monkeypatch: pytest.MonkeyPatch, relay: GitRelay | None
) -> WorkspaceService:
    svc = WorkspaceService(MagicMock())
    monkeypatch.setattr(svc, "_relay_for", AsyncMock(return_value=relay))
    return svc


def _workspace(tmp_path: Path) -> Path:
    return tmp_path / "workspaces" / "proj" / "backend" / "be-dev-1"


async def _clone(svc: WorkspaceService, workspace: Path) -> None:
    await svc._clone_repo(
        workspace,
        git_url="https://github.com/o/r.git",
        default_branch="master",
        project_slug="proj",
        agent=MagicMock(name="dev", slug="be-dev-1"),
    )


@pytest.mark.asyncio
async def test_a_clone_comes_from_the_relay_and_holds_no_credential(
    tmp_path: Path, origin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path)
    relay = GitRelay("proj", f"file://{origin}", _TOKEN, default_branch="master")

    await _clone(_service(monkeypatch, relay), workspace)

    config = (workspace / ".git" / "config").read_text()
    assert "url = https://github.com/o/r.git" in config  # the DB URL, display only
    for path in (workspace / ".git").rglob("*"):
        if path.is_file():
            assert _TOKEN.encode() not in path.read_bytes(), path
    # Through the workspace runner: as root, plain git refuses a clone the
    # git user owns (root keeps git's dubious-ownership check).
    head = workspace_module.run_workspace_git(
        workspace, ["rev-parse", "HEAD"], timeout=30
    ).stdout
    assert (
        head
        == subprocess.run(
            ["git", "-C", str(origin), "rev-parse", "master"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )


@pytest.mark.asyncio
async def test_a_failure_while_configuring_removes_the_workspace(
    tmp_path: Path, origin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path)
    relay = GitRelay("proj", f"file://{origin}", None, default_branch="master")
    real = workspace_module.run_workspace_git

    def _fail_on_config(cwd: Path, args: list[str], **kw: Any) -> Any:
        if args and args[0] == "config":
            raise subprocess.CalledProcessError(128, ["git", *args], "", "disk error")
        return real(cwd, args, **kw)

    with (
        patch.object(
            workspace_module, "run_workspace_git", side_effect=_fail_on_config
        ),
        pytest.raises(WorkspaceError, match="disk error"),
    ):
        await _clone(_service(monkeypatch, relay), workspace)

    assert not workspace.exists()


@pytest.mark.asyncio
async def test_a_timeout_removes_the_partial_workspace(
    tmp_path: Path, origin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path)
    relay = GitRelay("proj", f"file://{origin}", None, default_branch="master")

    def _hang(_cwd: Path, args: list[str], **_kw: Any) -> Any:
        (workspace / ".git").mkdir(parents=True, exist_ok=True)
        raise subprocess.TimeoutExpired(cmd=["git", *args], timeout=1)

    with (
        patch.object(workspace_module, "run_workspace_git", side_effect=_hang),
        pytest.raises(WorkspaceError, match="timed out"),
    ):
        await _clone(_service(monkeypatch, relay), workspace)

    assert not workspace.exists()


@pytest.mark.asyncio
async def test_an_unreachable_origin_never_creates_the_workspace(
    tmp_path: Path, origin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path)
    relay = GitRelay("proj", f"file://{tmp_path / 'missing.git'}", None)

    with pytest.raises(WorkspaceError):
        await _clone(_service(monkeypatch, relay), workspace)

    assert not workspace.exists()
    assert origin.exists()


@pytest.mark.asyncio
async def test_an_unknown_project_is_refused_before_any_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(WorkspaceError, match="Project not found"):
        await _clone(_service(monkeypatch, None), _workspace(tmp_path))
