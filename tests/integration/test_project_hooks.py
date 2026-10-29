"""A project's own git hooks gate RoboCo's commits and pushes, run in the
throwaway container — never in the orchestrator.

Real git end to end (a bare origin served through the relay, a clone under
the workspaces root, ``GitService._run_git``); only the container is swapped
for a local runner, since starting one needs Docker (the live suite runs the
real container). Each test hook leaves a record of how it was called.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from roboco.config import settings
from roboco.exceptions import GitCommandError
from roboco.services import project_hooks
from roboco.services.git import GitService
from roboco.services.workspace_container import (
    WorkspaceContainerError,
    WorkspaceContainerResult,
)

from tests.unit.local_origin_relay import serve_origin_through_relay

_SUPPORT = Path(__file__).parents[2] / "docker" / "git-support"
_ZERO_SHA = "0" * 40


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


class _Runner:
    """Stands in for the throwaway container: runs the hook here, records it."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        cwd: Path,
        argv: list[str],
        *,
        timeout: float,
        env: dict[str, str] | None = None,
        merge_stderr: bool = False,
    ) -> WorkspaceContainerResult:
        self.calls.append({"cwd": cwd, "argv": argv, "env": env or {}})
        done = subprocess.run(
            argv,
            cwd=cwd,
            env={**os.environ, **(env or {})},
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        out = done.stdout + (done.stderr if merge_stderr else "")
        return WorkspaceContainerResult(done.returncode, out, "")


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> _Runner:
    fake = _Runner()
    monkeypatch.setattr(project_hooks, "run_in_workspace_container", fake)
    return fake


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An agent clone under the workspaces root, its origin behind the relay."""
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path / "workspaces"))
    support = tmp_path / "support"
    shutil.copytree(_SUPPORT, support)
    monkeypatch.setattr(settings, "git_support_dir", str(support))
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True
    )
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    (seed / "README").write_text("seed\n")
    _git(seed, "add", ".")
    _git(seed, "-c", "user.name=s", "-c", "user.email=s@s", "commit", "-qm", "seed")
    _git(seed, "push", "-q", str(origin), "main")
    serve_origin_through_relay(monkeypatch, tmp_path, origin)
    clone = tmp_path / "workspaces" / "proj" / "backend" / "be-dev-1"
    clone.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    _git(clone, "config", "user.name", "be-dev-1")
    _git(clone, "config", "user.email", "be-dev-1@roboco.tech")
    return clone


def _hook(ws: Path, name: str, body: str, *, hooks_dir: str = ".githooks") -> Path:
    """A project hook in ``hooks_dir`` (wired through core.hooksPath, as
    husky and plain .githooks setups do) that records how it was called."""
    hook = ws / hooks_dir / name
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(
        "#!/bin/sh\n"
        f'{{ echo "args=$*"; echo "pwd=$(pwd)"; }} > "{ws}/.{name}.called"\n'
        f"{body}\n"
    )
    hook.chmod(0o755)
    _git(ws, "config", "core.hooksPath", hooks_dir)
    return hook


async def _commit(ws: Path, message: str = "work", *extra: str) -> None:
    (ws / "work.txt").write_text(message)
    git = GitService(MagicMock())
    await git._run_git(ws, ["add", "-A"])
    await git._run_git(ws, ["commit", "-m", message, *extra])


@pytest.mark.asyncio
async def test_a_failing_pre_commit_refuses_the_commit_with_its_output(
    ws: Path, runner: _Runner
) -> None:
    _hook(ws, "pre-commit", 'echo "lint: 3 errors"; exit 1')
    head = _git(ws, "rev-parse", "HEAD")

    with pytest.raises(GitCommandError, match="lint: 3 errors"):
        await _commit(ws)

    assert _git(ws, "rev-parse", "HEAD") == head
    (call,) = runner.calls
    assert call["argv"] == [str(ws / ".githooks" / "pre-commit")]
    assert call["cwd"] == ws


@pytest.mark.asyncio
async def test_a_passing_pre_commit_lets_the_commit_through(
    ws: Path, runner: _Runner
) -> None:
    _hook(ws, "pre-commit", "exit 0")
    await _commit(ws, "ship it")
    assert _git(ws, "log", "-1", "--format=%s") == "ship it"
    assert (ws / ".pre-commit.called").exists()
    assert len(runner.calls) == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("runner")
async def test_commit_msg_may_rewrite_the_message(ws: Path) -> None:
    _hook(ws, "commit-msg", 'printf "\\nReviewed-by: hook\\n" >> "$1"')
    await _commit(ws, "subject")
    message = _git(ws, "log", "-1", "--format=%B")
    assert message.splitlines()[0] == "subject"
    assert "Reviewed-by: hook" in message
    assert (
        "args=" + str(ws / ".git" / "COMMIT_EDITMSG")
        in (ws / ".commit-msg.called").read_text()
    )


@pytest.mark.asyncio
async def test_no_verify_skips_the_hooks(ws: Path, runner: _Runner) -> None:
    _hook(ws, "pre-commit", "exit 1")
    await _commit(ws, "skip", "--no-verify")
    assert _git(ws, "log", "-1", "--format=%s") == "skip"
    assert runner.calls == []


@pytest.mark.asyncio
async def test_husky_style_hooks_are_found_where_git_looks(
    ws: Path, runner: _Runner
) -> None:
    _hook(ws, "pre-commit", "exit 1", hooks_dir=".husky/_")
    with pytest.raises(GitCommandError, match="pre-commit hook"):
        await _commit(ws)
    assert runner.calls[0]["argv"] == [str(ws / ".husky" / "_" / "pre-commit")]


@pytest.mark.asyncio
async def test_a_clone_without_hooks_starts_no_container(
    ws: Path, runner: _Runner
) -> None:
    await _commit(ws, "plain")
    await GitService(MagicMock())._run_git(ws, ["push", "-u", "origin", "main"])
    assert runner.calls == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("runner")
async def test_pre_push_gets_gits_arguments_and_input(ws: Path, tmp_path: Path) -> None:
    _hook(ws, "pre-push", 'cat > "$(pwd)/.pre-push.stdin"')
    _git(ws, "checkout", "-q", "-b", "feature/x")
    await _commit(ws, "feature")
    local_sha = _git(ws, "rev-parse", "HEAD")

    await GitService(MagicMock())._run_git(ws, ["push", "-u", "origin", "feature/x"])

    called = (ws / ".pre-push.called").read_text()
    assert f"args=origin file://{tmp_path / 'origin.git'}" in called
    assert (ws / ".pre-push.stdin").read_text() == (
        f"refs/heads/feature/x {local_sha} refs/heads/feature/x {_ZERO_SHA}\n"
    )
    assert _git(tmp_path / "origin.git", "rev-parse", "feature/x") == local_sha
    assert not list((ws / ".git").glob("roboco-*.stdin"))


@pytest.mark.asyncio
@pytest.mark.usefixtures("runner")
async def test_a_failing_pre_push_keeps_the_commit_off_origin(
    ws: Path, tmp_path: Path
) -> None:
    _hook(ws, "pre-push", 'echo "gates red"; exit 1')
    _git(ws, "checkout", "-q", "-b", "feature/y")
    await _commit(ws, "feature")

    with pytest.raises(GitCommandError, match="gates red"):
        await GitService(MagicMock())._run_git(
            ws, ["push", "-u", "origin", "feature/y"]
        )

    listed = subprocess.run(
        ["git", "-C", str(tmp_path / "origin.git"), "branch", "--list", "feature/y"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert listed.stdout == ""


@pytest.mark.asyncio
async def test_a_hook_that_cannot_run_refuses_rather_than_passes(
    ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _cannot_start(*_args: Any, **_kwargs: Any) -> WorkspaceContainerResult:
        raise WorkspaceContainerError("docker run failed: no daemon")

    monkeypatch.setattr(project_hooks, "run_in_workspace_container", _cannot_start)
    _hook(ws, "pre-commit", "exit 0")
    with pytest.raises(GitCommandError, match="could not run"):
        await _commit(ws)
