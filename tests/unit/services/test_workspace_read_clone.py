"""WorkspaceService._sync_read_clone — the conventions read-clone refresh.

The read clone is hard-reset to the default branch on every refresh. The bug it
fixes: the old refresh used a token-less fetch, so a private repo's clone stayed
frozen at clone-time and never saw commits merged afterwards. The refresh now
goes through the project's relay (which holds the token), and this proves it
actually advances the clone to a post-clone commit — and that the token stays
in the relay's process, never the read clone's.
"""

from __future__ import annotations

import base64
import subprocess
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from roboco.config import settings
from roboco.services import git_relay
from roboco.services import workspace as ws_mod
from roboco.services.git_relay import GitRelay, read_clone_path
from roboco.services.workspace import WorkspaceService

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _private_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "private_data_root", str(tmp_path / "private"))


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _relay(origin: Path | str) -> GitRelay:
    return GitRelay("p", f"file://{origin}", None, default_branch="master")


def test_sync_read_clone_advances_to_a_post_clone_commit(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "master")
    (origin / "README.md").write_text("v1\n")
    _commit(origin, "first")

    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    first = _git(clone, "rev-parse", "HEAD").stdout.strip()

    # A new commit lands on origin AFTER the clone — the frozen-clone scenario.
    (origin / "NEW.txt").write_text("added later\n")
    second = _commit(origin, "second")
    assert first != second

    WorkspaceService._sync_read_clone(clone, _relay(origin), "master")

    assert _git(clone, "rev-parse", "HEAD").stdout.strip() == second
    assert (clone / "NEW.txt").is_file()


def test_sync_read_clone_fetches_tags(tmp_path: Path) -> None:
    """The read clone must carry tags: the release manager derives "commits
    since last release" from the newest tag, and a tagless clone makes
    ``git describe`` fail so it walks the entire history (the 729-commit bug)."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "master")
    (origin / "README.md").write_text("v1\n")
    _commit(origin, "first")

    # The clone itself is tagless (--no-tags, as agent clones are) — the tag
    # lands on origin and the read-clone refresh must pull it in.
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", "--no-tags", str(origin), str(clone))
    assert _git(clone, "tag").stdout.strip() == ""

    # -c tag.gpgsign=false: create a lightweight tag regardless of a global
    # signing config that would otherwise force an annotated (signed) tag.
    _git(origin, "-c", "tag.gpgsign=false", "tag", "v0.17.0")

    WorkspaceService._sync_read_clone(clone, _relay(origin), "master")

    assert "v0.17.0" in _git(clone, "tag").stdout.split()
    assert _git(clone, "describe", "--tags", "--abbrev=0").stdout.strip() == "v0.17.0"


def test_sync_read_clone_is_best_effort_on_unreachable_origin(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "master")
    (origin / "README.md").write_text("v1\n")
    _commit(origin, "first")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    head = _git(clone, "rev-parse", "HEAD").stdout.strip()

    # A bogus origin must not raise — the refresh logs and leaves the clone as-is.
    WorkspaceService._sync_read_clone(clone, _relay("/nonexistent/repo"), "master")
    assert _git(clone, "rev-parse", "HEAD").stdout.strip() == head


def test_a_private_refresh_keeps_the_token_in_the_relay(tmp_path: Path) -> None:
    """A private-repo refresh authenticates in the relay's own process (a
    host-scoped header in its environment); the read clone's fetch — from
    the relay, on the local file transport — never carries the token in its
    argv or environment."""
    clone = tmp_path / "clone"
    clone.mkdir()
    token = "ghp_SECRETARGV"
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    relay = GitRelay("p", "https://github.com/o/r.git", token)
    relay.path.mkdir(parents=True)
    (relay.path / "HEAD").write_text("ref: refs/heads/master\n")
    calls: list[dict[str, Any]] = []

    def _fake_run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": list(argv), **kw})
        return subprocess.CompletedProcess(argv, 0, "", "")

    with patch.object(git_relay.subprocess, "run", side_effect=_fake_run):
        WorkspaceService._sync_read_clone(clone, relay, "master")

    def _carries_token(call: dict[str, Any]) -> bool:
        blob = " ".join([*call["argv"], *(call.get("env") or {}).values()])
        return token in blob or basic in blob

    relay_fetches = [c for c in calls if c.get("cwd") == str(relay.path)]
    clone_calls = [c for c in calls if c.get("cwd") == str(clone)]
    assert relay_fetches
    assert clone_calls
    assert all(not any(token in a or basic in a for a in c["argv"]) for c in calls)
    assert any(_carries_token(c) for c in relay_fetches)
    assert not any(_carries_token(c) for c in clone_calls)


# --- ensure_read_clone force-refetch (M45) -------------------------------------
# The 30s refresh TTL on the conventions read clone means a conventions commit
# that merges to the default branch within 30s leaves the clone's HEAD stale,
# so the sha-keyed conventions cache serves a stale map. `force=True` bypasses
# the TTL so conventions reads always see the current default-branch HEAD.


def _read_clone_service() -> WorkspaceService:
    # AsyncMock, not MagicMock: ensure_read_clone's fetch/clone branches now
    # call session.commit() (_release_pool_connection, the pool-hold fix) -
    # a MagicMock session's .commit() isn't awaitable and would TypeError.
    svc = WorkspaceService(AsyncMock())
    object.__setattr__(svc, "_relay_for", AsyncMock(return_value=object()))
    return svc


def _read_clone_workspace(slug: str) -> Path:
    workspace = read_clone_path(slug)
    (workspace / ".git" / "objects").mkdir(parents=True)
    (workspace / ".git" / "HEAD").write_text("ref: refs/heads/master\n")
    return workspace


@pytest.mark.asyncio
async def test_ensure_read_clone_force_bypasses_ttl() -> None:
    """force=True fetches even when the workspace was just synced (within TTL)."""
    svc = _read_clone_service()
    slug = "p"
    workspace = _read_clone_workspace(slug)
    project = SimpleNamespace(
        slug=slug, default_branch="master", git_url="https://example/o/r"
    )
    project_service = AsyncMock()
    project_service.get_by_slug = AsyncMock(return_value=project)
    fetched: list[Path] = []

    def _fake_sync(root: Path, *_a: object, **_k: object) -> None:
        fetched.append(root)

    # Pre-seed the synced map to "just now" so the TTL branch would skip.
    ws_mod._read_clone_synced[str(workspace)] = ws_mod._monotonic()
    try:
        with (
            patch(
                "roboco.services.project.get_project_service",
                return_value=project_service,
            ),
            patch.object(WorkspaceService, "_is_workspace_healthy", return_value=True),
            patch.object(WorkspaceService, "_sync_read_clone", side_effect=_fake_sync),
        ):
            await svc.ensure_read_clone(slug, force=True)
    finally:
        ws_mod._read_clone_synced.pop(str(workspace), None)

    assert fetched, "force=True must fetch even within the 30s TTL"


@pytest.mark.asyncio
async def test_ensure_read_clone_default_respects_ttl() -> None:
    """Default force=False skips the fetch when the workspace was just synced."""
    svc = _read_clone_service()
    slug = "p"
    workspace = _read_clone_workspace(slug)
    project = SimpleNamespace(
        slug=slug, default_branch="master", git_url="https://example/o/r"
    )
    project_service = AsyncMock()
    project_service.get_by_slug = AsyncMock(return_value=project)
    fetched: list[Path] = []

    def _fake_sync(root: Path, *_a: object, **_k: object) -> None:
        fetched.append(root)

    ws_mod._read_clone_synced[str(workspace)] = ws_mod._monotonic()
    try:
        with (
            patch(
                "roboco.services.project.get_project_service",
                return_value=project_service,
            ),
            patch.object(WorkspaceService, "_is_workspace_healthy", return_value=True),
            patch.object(WorkspaceService, "_sync_read_clone", side_effect=_fake_sync),
        ):
            await svc.ensure_read_clone(slug)
    finally:
        ws_mod._read_clone_synced.pop(str(workspace), None)

    assert not fetched, "default force=False must skip the fetch within the TTL"
