"""Entire's commit hooks around RoboCo's commits and pushes of agent work.

Real git end to end (a bare origin served through the relay, a clone under
the workspaces root, ``GitService._run_git``); only the sealed container is
swapped for a stand-in that does what Entire does there: ``prepare-commit-msg``
appends the checkpoint trailer, ``post-commit`` writes the checkpoint ref.
The live suite (``test_entire_hooks_live.py``) runs the real CLI in the real
container.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from roboco.config import settings
from roboco.services import entire_hooks, project_hooks
from roboco.services.entire_hooks import ENTIRE_CLI
from roboco.services.git import GitService
from roboco.services.workspace_container import (
    ReadOnlyMount,
    WorkspaceContainerError,
    WorkspaceContainerResult,
)

from tests.unit.local_origin_relay import serve_origin_through_relay

_SUPPORT = Path(__file__).parents[2] / "docker" / "git-support"
_CHECKPOINT_ID = "01M62HNXJKAZ085RFT8EHNPF3W"
_CHECKPOINT_REF = f"refs/entire/checkpoints/3W/{_CHECKPOINT_ID}"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


class _Entire:
    """Stands in for the sealed container running Entire's hooks."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        cwd: Path,
        argv: list[str],
        *,
        timeout: float,
        env: dict[str, str] | None = None,
        read_only_mount: ReadOnlyMount | None = None,
    ) -> WorkspaceContainerResult:
        _sh, _c, script, cli, path = argv
        assert cli == ENTIRE_CLI
        assert timeout > 0
        self.calls.append({"cwd": cwd, "script": script, "path": path, "env": env})
        self.calls[-1]["mount"] = read_only_mount
        if "prepare-commit-msg" in script:
            with Path(path).open("a") as message:
                message.write(f"\nEntire-Checkpoint: {_CHECKPOINT_ID}\n")
        if "post-commit" in script:
            tree = _git(cwd, "write-tree")
            checkpoint = _git(cwd, "commit-tree", tree, "-m", "checkpoint")
            _git(cwd, "update-ref", _CHECKPOINT_REF, checkpoint)
        return WorkspaceContainerResult(0, "", "")

    def steps(self) -> list[str]:
        return [
            "post-commit" if "post-commit" in c["script"] else "message"
            for c in self.calls
        ]


@pytest.fixture
def entire(monkeypatch: pytest.MonkeyPatch) -> _Entire:
    fake = _Entire()
    monkeypatch.setattr(entire_hooks, "run_offline_in_workspace_container", fake)
    monkeypatch.setattr(settings, "entire_enabled", True)
    return fake


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An agent clone whose agent's Entire session hooks recorded a session."""
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path / "workspaces"))
    support = tmp_path / "support"
    shutil.copytree(_SUPPORT, support)
    monkeypatch.setattr(settings, "git_support_dir", str(support))
    monkeypatch.setattr(entire_hooks, "_claude_dir", lambda: tmp_path / "claude")
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
    sessions = clone / ".git" / "entire-sessions"
    sessions.mkdir()
    (sessions / "11111111-2222-3333-4444-555555555555.json").write_text("{}")
    return clone


async def _commit(ws: Path, message: str = "feat: hello") -> None:
    (ws / "hello.txt").write_text(message)
    git = GitService(MagicMock())
    await git._run_git(ws, ["add", "-A"])
    await git._run_git(ws, ["commit", "-m", message])


@pytest.mark.asyncio
async def test_a_commit_gets_its_checkpoint_trailer_and_then_its_checkpoint(
    ws: Path, entire: _Entire
) -> None:
    await _commit(ws)

    message = _git(ws, "log", "-1", "--format=%B")
    assert message.splitlines()[0] == "feat: hello"
    assert f"Entire-Checkpoint: {_CHECKPOINT_ID}" in message
    assert entire.steps() == ["message", "post-commit"]
    message_hooks, post_commit = entire.calls
    assert message_hooks["path"] == str(ws / ".git" / "COMMIT_EDITMSG")
    assert "commit-msg" in message_hooks["script"]
    assert post_commit["path"] == str(ws / ".git")
    assert _git(ws, "rev-parse", "--verify", _CHECKPOINT_REF)


@pytest.mark.asyncio
@pytest.mark.usefixtures("entire")
async def test_a_push_sends_the_checkpoints_of_the_commits_it_pushes(
    ws: Path, tmp_path: Path
) -> None:
    _git(ws, "checkout", "-q", "-b", "feature/backend/t1")
    await _commit(ws)

    await GitService(MagicMock())._run_git(
        ws, ["push", "-u", "origin", "feature/backend/t1"]
    )

    origin = tmp_path / "origin.git"
    assert _git(origin, "rev-parse", _CHECKPOINT_REF) == _git(
        ws, "rev-parse", _CHECKPOINT_REF
    )
    assert _git(origin, "rev-parse", "feature/backend/t1") == _git(
        ws, "rev-parse", "HEAD"
    )


@pytest.mark.asyncio
async def test_a_trailer_that_is_not_a_checkpoint_id_pushes_nothing_extra(
    ws: Path, entire: _Entire, tmp_path: Path
) -> None:
    entire.calls.clear()
    _git(ws, "checkout", "-q", "-b", "feature/backend/t2")
    (ws / "x.txt").write_text("x")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "x\n\nEntire-Checkpoint: ../../heads/main")

    await GitService(MagicMock())._run_git(
        ws, ["push", "-u", "origin", "feature/backend/t2"]
    )

    refs = _git(tmp_path / "origin.git", "for-each-ref", "--format=%(refname)")
    assert refs.split() == ["refs/heads/feature/backend/t2", "refs/heads/main"]


@pytest.mark.asyncio
async def test_off_or_without_a_recorded_session_entire_is_left_out(
    ws: Path, entire: _Entire, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "entire_enabled", False)
    await _commit(ws, "feat: off")
    monkeypatch.setattr(settings, "entire_enabled", True)
    shutil.rmtree(ws / ".git" / "entire-sessions")
    await _commit(ws, "feat: no session")

    assert entire.calls == []
    assert "Entire-Checkpoint" not in _git(ws, "log", "-2", "--format=%B")


@pytest.mark.asyncio
@pytest.mark.usefixtures("entire")
async def test_entire_failing_never_refuses_the_commit(
    ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _cannot_start(*_args: Any, **_kwargs: Any) -> WorkspaceContainerResult:
        raise WorkspaceContainerError("docker run failed: no daemon")

    monkeypatch.setattr(
        entire_hooks, "run_offline_in_workspace_container", _cannot_start
    )
    await _commit(ws, "feat: still committed")
    assert _git(ws, "log", "-1", "--format=%s") == "feat: still committed"


@pytest.mark.asyncio
async def test_the_agents_transcript_folder_is_mounted_where_its_session_names_it(
    ws: Path, entire: _Entire, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = str(ws).replace("/", "-").replace("_", "-").replace(".", "-")
    (tmp_path / "claude" / "projects" / folder).mkdir(parents=True)
    monkeypatch.setattr(settings, "host_claude_dir", "/home/operator/.claude")

    await _commit(ws)

    mount = entire.calls[0]["mount"]
    assert mount == ReadOnlyMount(
        root=tmp_path / "claude",
        local=tmp_path / "claude" / "projects" / folder,
        host=f"/home/operator/.claude/projects/{folder}",
        target=f"/home/agent/.claude/projects/{folder}",
    )
    assert entire.calls[0]["env"]["ENTIRE_TELEMETRY_OPTOUT"] == "1"


@pytest.mark.asyncio
@pytest.mark.usefixtures("entire")
async def test_an_entire_wrapper_hook_stands_for_the_hook_it_moved_aside(
    ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[str] = []

    async def _runner(
        _cwd: Path, argv: list[str], **_kwargs: Any
    ) -> WorkspaceContainerResult:
        ran.append(Path(argv[0]).name)
        return WorkspaceContainerResult(0, "", "")

    monkeypatch.setattr(project_hooks, "run_in_workspace_container", _runner)
    hooks = ws / ".git" / "hooks"
    for name in ("commit-msg", "pre-commit", "pre-commit.pre-entire"):
        body = "#!/bin/sh\n# Entire CLI hooks\nexit 0\n"
        if name.endswith(".pre-entire"):
            body = "#!/bin/sh\nexit 0\n"
        (hooks / name).write_text(body)
        (hooks / name).chmod(0o755)

    await _commit(ws)

    assert ran == ["pre-commit.pre-entire"]
