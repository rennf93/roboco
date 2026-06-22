"""WorkspaceService._sync_read_clone — the conventions read-clone refresh.

The read clone is hard-reset to the default branch on every refresh. The bug it
fixes: the old refresh used a token-less fetch, so a private repo's clone stayed
frozen at clone-time and never saw commits merged afterwards. This proves the
refresh actually advances the clone to a post-clone commit.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from roboco.services.workspace import WorkspaceService

if TYPE_CHECKING:
    from pathlib import Path


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


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

    # token=None: a file:// fetch needs no auth (mirrors a public-repo refresh);
    # for a private https repo the token would be injected into the fetch URL.
    WorkspaceService._sync_read_clone(clone, f"file://{origin}", "master", None)

    assert _git(clone, "rev-parse", "HEAD").stdout.strip() == second
    assert (clone / "NEW.txt").is_file()


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
    WorkspaceService._sync_read_clone(clone, "file:///nonexistent/repo", "master", None)
    assert _git(clone, "rev-parse", "HEAD").stdout.strip() == head
