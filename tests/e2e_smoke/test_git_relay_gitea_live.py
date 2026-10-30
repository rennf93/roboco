"""Live-Gitea round-trip of the git relay: the token lives only in the relay.

Drives exactly what RoboCo runs for an agent against a real Gitea over HTTP,
on a PRIVATE repository so nothing works without the token: the relay fetches
origin (token), the workspace git user clones the relay, commits, and every
``origin`` command (push, force-with-lease push, ls-remote, fetch of a branch
someone else pushed) goes through ``run_origin_command``. Proves the relay's
host-scoped header authenticates for real, the commit lands on the forge, and
no form of the token is ever written into the workspace.

Skipped unless both env vars are set (same instance as test_gitea_live.py):

    ROBOCO_GITEA_E2E_URL    e.g. http://localhost:3310
    ROBOCO_GITEA_E2E_TOKEN  an admin PAT (scopes: all)
"""

from __future__ import annotations

import base64
import os
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from roboco.config import settings
from roboco.services.git_origin import (
    OriginContext,
    push_checkpoint_refs,
    run_origin_command,
)
from roboco.services.git_relay import GitRelay
from roboco.services.workspace_git import prepare_clone_parent, run_workspace_git

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Iterator, Sequence
    from pathlib import Path

_URL = os.environ.get("ROBOCO_GITEA_E2E_URL", "")
_TOKEN = os.environ.get("ROBOCO_GITEA_E2E_TOKEN", "")
_TIMEOUT = 60

pytestmark = pytest.mark.skipif(
    not (_URL and _TOKEN),
    reason="ROBOCO_GITEA_E2E_URL / ROBOCO_GITEA_E2E_TOKEN not set",
)


def _api(
    method: str, path: str, json: dict[str, object] | None = None
) -> httpx.Response:
    parts = urlsplit(_URL)
    return httpx.request(
        method,
        f"{parts.scheme or 'http'}://{parts.netloc}/api/v1{path}",
        headers={"Authorization": f"token {_TOKEN}"},
        timeout=15.0,
        json=json,
    )


class _Project:
    def __init__(self, root: Path, owner: str, repo: str) -> None:
        parts = urlsplit(_URL)
        self.owner, self.repo = owner, repo
        self.url = f"{parts.scheme or 'http'}://{parts.netloc}/{owner}/{repo}.git"
        self.relay = GitRelay(repo, self.url, _TOKEN, default_branch="main")
        self.ws = root / "workspaces" / repo / "backend" / "be-dev-1"

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return run_workspace_git(self.ws, list(args), timeout=_TIMEOUT)

    def context(self) -> OriginContext:
        def _local(local: Sequence[str]) -> subprocess.CompletedProcess[str]:
            return run_workspace_git(self.ws, local, timeout=_TIMEOUT, check=False)

        return OriginContext(
            relay=self.relay,
            repo=self.ws,
            local_git=_local,
            from_workspace=True,
            timeout=_TIMEOUT,
        )

    def origin(self, *args: str) -> subprocess.CompletedProcess[str]:
        return run_origin_command(self.context(), list(args))

    def clone(self) -> None:
        """The agent's clone, made from the relay as RoboCo makes it."""
        fetched = self.relay.fetch_from_origin(
            ["+refs/heads/*:refs/heads/*"], prune=True, timeout=_TIMEOUT
        )
        assert fetched.returncode == 0, fetched.stderr
        prepare_clone_parent(self.ws)
        run_workspace_git(
            self.ws.parent,
            [
                "clone",
                "-q",
                "--no-local",
                "--no-tags",
                "--branch",
                "main",
                str(self.relay.path),
                str(self.ws),
            ],
            timeout=_TIMEOUT,
        )
        self.git("remote", "set-url", "origin", self.url)
        self.git("config", "user.name", "be-dev-1")
        self.git("config", "user.email", "be-dev-1@roboco.tech")

    def branch_sha(self, branch: str) -> str:
        resp = _api("get", f"/repos/{self.owner}/{self.repo}/branches/{branch}")
        assert resp.status_code == httpx.codes.OK, resp.text
        return str(resp.json()["commit"]["id"])


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Project]:
    monkeypatch.setattr(settings, "private_data_root", str(tmp_path / "private"))
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path / "workspaces"))
    (tmp_path / "workspaces").mkdir()
    if settings.workspace_git_drop == "required":
        # With the drop required, workspace git runs as the git user: let it
        # traverse pytest's 0700 temp dirs, as /data is in production.
        for directory in (tmp_path, tmp_path.parent, tmp_path.parent.parent):
            directory.chmod(directory.stat().st_mode | 0o055)
    repo = f"relay-e2e-{uuid4().hex[:8]}"
    created = _api(
        "post",
        "/user/repos",
        json={
            "name": repo,
            "auto_init": True,
            "default_branch": "main",
            "private": True,
        },
    )
    assert created.status_code == httpx.codes.CREATED, created.text
    owner = _api("get", "/user").json()["username"]
    try:
        yield _Project(tmp_path, owner, repo)
    finally:
        _api("delete", f"/repos/{owner}/{repo}")


def test_a_private_repo_is_unreachable_without_the_relays_token(
    project: _Project,
) -> None:
    anonymous = GitRelay("anon", project.url, None, default_branch="main")
    fetched = anonymous.fetch_from_origin(
        ["+refs/heads/*:refs/heads/*"], timeout=_TIMEOUT
    )
    assert fetched.returncode != 0


def test_an_agent_round_trip_goes_through_the_relay(project: _Project) -> None:
    project.clone()

    # Push a new branch.
    project.git("checkout", "-q", "-b", "feature/relay")
    (project.ws / "relay.txt").write_text("v1\n")
    project.git("add", "relay.txt")
    project.git("commit", "-qm", "relay v1")
    pushed = project.origin("push", "-u", "origin", "feature/relay")
    assert pushed.returncode == 0, pushed.stderr
    first = project.git("rev-parse", "HEAD").stdout.strip()
    assert project.branch_sha("feature/relay") == first

    # Rewrite it and force-with-lease push (what rebase_onto_base does).
    (project.ws / "relay.txt").write_text("v2\n")
    project.git("commit", "-qa", "--amend", "-m", "relay v2")
    leased = project.origin("push", "--force-with-lease", "origin", "feature/relay")
    assert leased.returncode == 0, leased.stderr
    second = project.git("rev-parse", "HEAD").stdout.strip()
    assert second != first
    assert project.branch_sha("feature/relay") == second

    listed = project.origin("ls-remote", "--heads", "origin", "feature/relay")
    assert listed.returncode == 0, listed.stderr
    assert second in listed.stdout

    # A branch someone else pushed reaches the clone through the relay.
    other = _api(
        "post",
        f"/repos/{project.owner}/{project.repo}/contents/peer.txt",
        json={
            "content": base64.b64encode(b"peer\n").decode(),
            "message": "peer work",
            "branch": "main",
            "new_branch": "feature/peer",
        },
    )
    assert other.status_code == httpx.codes.CREATED, other.text
    peer = project.origin("fetch", "--no-tags", "origin", "feature/peer")
    assert peer.returncode == 0, peer.stderr
    tracked = project.git("rev-parse", "refs/remotes/origin/feature/peer").stdout
    assert tracked.strip() == project.branch_sha("feature/peer")

    basic = base64.b64encode(f"x-access-token:{_TOKEN}".encode()).decode()
    for path in (project.ws / ".git").rglob("*"):
        if path.is_file() and not path.is_symlink():
            data = path.read_bytes()
            assert _TOKEN.encode() not in data, path
            assert basic.encode() not in data, path


def test_an_entire_checkpoint_reaches_the_forge_through_the_relay(
    project: _Project,
) -> None:
    """Entire's checkpoint refs are not branches: the forge must take them
    as they are, pushed from the workspace through the relay."""
    project.clone()
    tree = project.git("write-tree").stdout.strip()
    checkpoint = project.git("commit-tree", tree, "-m", "checkpoint").stdout.strip()
    ref = "refs/entire/checkpoints/AW/01M620JW5RZ2G6QBKQAM0FZNAW"
    project.git("update-ref", ref, checkpoint)

    pushed = push_checkpoint_refs(project.context(), [ref])

    assert pushed.returncode == 0, pushed.stderr
    listed = project.relay.ls_remote([ref], timeout=_TIMEOUT)
    assert listed.stdout.split() == [checkpoint, ref]
