"""Live contract of Entire's commit hooks around RoboCo's commits (real Docker,
the real Entire CLI the orchestrator image ships).

Env-gated: run as root on a host whose Docker daemon this process can reach,
with ROBOCO_ENTIRE_LIVE=1 and ROBOCO_WORKSPACE_CONTAINER_IMAGE=<the
orchestrator image>. An agent's container is played by a container of the same
image running Entire's session hooks as the agent uid (no ``stop`` before the
commit: RoboCo commits in the middle of an agent's turn); the commit and push
go through ``GitService`` exactly as in production.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from roboco.config import settings
from roboco.services import entire_hooks
from roboco.services.entire_hooks import ENTIRE_CLI
from roboco.services.git import GitService

from tests.unit.local_origin_relay import serve_origin_through_relay

pytestmark = pytest.mark.skipif(
    os.environ.get("ROBOCO_ENTIRE_LIVE") != "1"
    or not os.environ.get("ROBOCO_WORKSPACE_CONTAINER_IMAGE")
    or shutil.which("docker") is None
    or os.geteuid() != 0,
    reason="live Entire contract (opt-in: root, Docker, the orchestrator image)",
)

_REPO = Path(__file__).parents[2]
_AGENT_UID = 1000
_GIT_USER = "1500:1000"
_SESSION = "11111111-2222-3333-4444-555555555555"
_MARKER = "TRANSCRIPT-MARKER-7f3a"
_SECRET = "ORCHESTRATOR-ONLY-SECRET-91c2"
_BRANCH = "feature/backend/t1"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "safe.directory=*", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _folder(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


@pytest.fixture
def worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An agent's task worktree, its clone owned like on the NAS, an Entire-
    enabled project behind the relay, and the agent's transcript folder."""
    for directory in (tmp_path, tmp_path.parent, tmp_path.parent.parent):
        directory.chmod(directory.stat().st_mode | 0o055)
    image = os.environ["ROBOCO_WORKSPACE_CONTAINER_IMAGE"]
    monkeypatch.setattr(settings, "workspace_container_image", image)
    monkeypatch.setattr(settings, "host_data_dir", "")
    monkeypatch.setattr(settings, "host_claude_dir", "")
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path / "workspaces"))
    monkeypatch.setattr(settings, "entire_enabled", True)
    monkeypatch.setattr(entire_hooks, "_claude_dir", lambda: tmp_path / "claude")
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True
    )
    seed = tmp_path / "seed"
    (seed / ".entire" / "redactors").mkdir(parents=True)
    for name in ("settings.json", ".gitignore", "redactors/roboco.yaml"):
        shutil.copy(_REPO / ".entire" / name, seed / ".entire" / name)
    (seed / ".gitignore").write_text(".worktrees/\n")
    (seed / "README").write_text("seed\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    _git(seed, "add", "-A")
    _git(seed, "-c", "user.name=s", "-c", "user.email=s@s", "commit", "-qm", "seed")
    _git(seed, "push", "-q", str(origin), "main")
    serve_origin_through_relay(monkeypatch, tmp_path, origin)
    clone = tmp_path / "workspaces" / "proj" / "backend" / "be-dev-1"
    clone.parent.mkdir(parents=True)
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    _git(clone, "config", "user.name", "be-dev-1")
    _git(clone, "config", "user.email", "be-dev-1@roboco.tech")
    tree = clone / ".worktrees" / "t1"
    _git(clone, "worktree", "add", "-q", "-b", _BRANCH, str(tree))
    workspaces = tmp_path / "workspaces"
    subprocess.run(["chown", "-R", _GIT_USER, str(workspaces)], check=True)
    subprocess.run(["chmod", "-R", "g+w", str(workspaces)], check=True)
    transcripts = tmp_path / "claude" / "projects" / _folder(tree)
    transcripts.mkdir(parents=True)
    subprocess.run(
        ["chown", "-R", f"{_AGENT_UID}:{_AGENT_UID}", str(tmp_path / "claude")],
        check=True,
    )
    return tree


def _agent_session(tree: Path, tmp_path: Path) -> Path:
    """What an agent's container leaves: Entire's session hooks fired for a
    turn in progress, the transcript so far, and the file it wrote."""
    folder = _folder(tree)
    transcript = f"/home/agent/.claude/projects/{folder}/{_SESSION}.jsonl"
    host_transcript = tmp_path / "claude" / "projects" / folder / f"{_SESSION}.jsonl"
    lines = [
        {
            "type": "user",
            "sessionId": _SESSION,
            "uuid": "u1",
            "cwd": str(tree),
            "timestamp": "2026-10-29T00:00:00Z",
            "message": {"role": "user", "content": f"write hello.txt {_MARKER}"},
        },
        {
            "type": "assistant",
            "sessionId": _SESSION,
            "uuid": "a1",
            "parentUuid": "u1",
            "cwd": str(tree),
            "timestamp": "2026-10-29T00:00:01Z",
            "message": {
                "id": "m1",
                "role": "assistant",
                "model": "claude-x",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Write",
                        "input": {
                            "file_path": str(tree / "hello.txt"),
                            "content": "hi",
                        },
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        },
    ]
    host_transcript.write_text("".join(json.dumps(line) + "\n" for line in lines))
    os.chown(host_transcript, _AGENT_UID, _AGENT_UID)
    clone_root = tree.parents[1]
    for event in ("session-start", "user-prompt-submit"):
        payload = {
            "session_id": _SESSION,
            "transcript_path": transcript,
            "cwd": str(tree),
            "prompt": "write hello.txt",
        }
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-i",
                "--network",
                "none",
                "--user",
                f"{_AGENT_UID}:{_AGENT_UID}",
                "--tmpfs",
                f"/home/agent:uid={_AGENT_UID},gid={_AGENT_UID}",
                "-v",
                f"{clone_root}:{clone_root}",
                "-v",
                f"{host_transcript.parent}:/home/agent/.claude/projects/{folder}",
                "-w",
                str(tree),
                "-e",
                "HOME=/home/agent",
                "-e",
                "GIT_CONFIG_PARAMETERS='safe.directory=*'",
                "-e",
                "ENTIRE_TELEMETRY_OPTOUT=1",
                "--entrypoint",
                ENTIRE_CLI,
                settings.workspace_container_image or "",
                "hooks",
                "claude-code",
                event,
            ],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=True,
        )
    hello = tree / "hello.txt"
    hello.write_text("hi\n")
    os.chown(hello, _AGENT_UID, _AGENT_UID)
    return clone_root / ".git" / "entire-sessions" / f"{_SESSION}.json"


def _checkpoint_text(clone: Path) -> str:
    """Every blob reachable from the clone's Entire checkpoint refs."""
    refs = _git(clone, "for-each-ref", "--format=%(refname)", "refs/entire/")
    blobs = []
    for ref in refs.split():
        for line in _git(clone, "ls-tree", "-r", ref).splitlines():
            blobs.append(_git(clone, "cat-file", "-p", line.split()[2]))
    return "\n".join(blobs)


async def test_an_agent_commit_gets_a_checkpoint_that_reaches_origin(
    worktree: Path, tmp_path: Path
) -> None:
    _agent_session(worktree, tmp_path)
    git = GitService(MagicMock())

    await git._run_git(worktree, ["add", "-A"])
    await git._run_git(worktree, ["commit", "-m", "feat: hello"])

    message = _git(worktree, "log", "-1", "--format=%B")
    assert message.splitlines()[0] == "feat: hello"
    found = re.search(r"^Entire-Checkpoint: ([0-9A-Z]{26})$", message, re.MULTILINE)
    assert found, message
    ref = f"refs/entire/checkpoints/{found[1][-2:]}/{found[1]}"
    clone = worktree.parents[1]
    assert _MARKER in _checkpoint_text(clone)
    # Entire writes its objects owner-only; the git user's upload-pack hands
    # them to the relay on push, so they must be group-readable by now.
    unreadable = subprocess.run(
        [
            "find",
            f"{clone}/.git/objects",
            f"{clone}/.git/refs/entire",
            "!",
            "-perm",
            "-g=r",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert unreadable == ""

    await git._run_git(worktree, ["push", "-u", "origin", _BRANCH])

    origin = tmp_path / "origin.git"
    assert _git(origin, "rev-parse", ref) == _git(clone, "rev-parse", ref)
    assert _git(origin, "rev-parse", _BRANCH) == _git(worktree, "rev-parse", "HEAD")


async def test_a_session_pointed_at_an_orchestrator_file_cannot_reach_it(
    worktree: Path, tmp_path: Path
) -> None:
    record = _agent_session(worktree, tmp_path)
    secret = tmp_path / "private" / "secret.txt"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text(f"{_SECRET}\n")
    session = json.loads(record.read_text())
    session["transcript_path"] = str(secret)
    record.write_text(json.dumps(session))
    git = GitService(MagicMock())

    await git._run_git(worktree, ["add", "-A"])
    await git._run_git(worktree, ["commit", "-m", "feat: hello"])

    assert _git(worktree, "log", "-1", "--format=%s") == "feat: hello"
    assert _SECRET not in _checkpoint_text(worktree.parents[1])
