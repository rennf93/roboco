"""Real-git regression tests for the read-path stale-local-ref fix.

Live incident (2026-07-24): a reviewer's clone (QA/PM/documenter/PR-gate,
never the branch's own author) held a local ref for the task branch that
had DIVERGED from origin after a routine rebase force-push. ``diff()`` and
``read_file_at_branch()`` both resolve their head ref through
``_resolve_head_ref``, which used to keep local priority on ANY divergence
(real or rewritten-history) — QA's review evidence stayed frozen at a
stale commit for five straight review rounds while origin held every fix.

These run against a REAL bare origin + real clones (no mocked ``_run_git``)
mirroring ``test_git_rebase_reconcile.py`` — only the workspace/token
resolution (``_workspace_for_branch`` / ``_token_for_branch``, both DB-
backed) is mocked so the test needs no database.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.services.git import GitService

if TYPE_CHECKING:
    from pathlib import Path

_BRANCH = "feature/backend/task"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )


def _init_bare(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=master", str(path)],
        check=True,
        capture_output=True,
    )


def _configure(repo: Path) -> None:
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "commit.gpgsign", "false")


def _clone(origin: Path, dest: Path) -> None:
    subprocess.run(
        ["git", "clone", str(origin), str(dest)], check=True, capture_output=True
    )
    _configure(dest)


def _commit(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content)
    _git(repo, "add", name)
    _git(repo, "commit", "-m", f"add {name}")


def _service() -> Any:
    svc = GitService.__new__(GitService)
    svc.log = MagicMock()
    svc.session = MagicMock()
    return svc


def _wire_for_reviewer(svc: Any, reviewer: Path) -> None:
    """Bypass the DB-backed workspace/token lookup: point every call at the
    given clone, unauthenticated (local bare-repo remotes need no token)."""
    svc._workspace_for_branch = AsyncMock(return_value=reviewer)
    svc._token_for_branch = AsyncMock(return_value=None)


@pytest.fixture
def repo_pair(tmp_path: Path) -> tuple[Path, Path]:
    """A bare origin plus a dev clone, both carrying a pushed task branch."""
    origin = tmp_path / "origin.git"
    _init_bare(origin)
    dev = tmp_path / "dev"
    _clone(origin, dev)
    _commit(dev, "README.md", "root\n")
    _git(dev, "push", "origin", "master")
    _git(dev, "checkout", "-b", _BRANCH)
    _commit(dev, "feature.py", "v1\n")
    _git(dev, "push", "origin", _BRANCH)
    return origin, dev


@pytest.mark.asyncio
async def test_diverged_reviewer_clone_reads_origin(
    repo_pair: tuple[Path, Path],
) -> None:
    """A reviewer clone checked out the branch during an earlier round; the
    dev then rebased (force-pushed) it onto an advanced base. The reviewer's
    local ref and origin now diverge by raw SHA — the fix must still serve
    origin's rewritten content, not the frozen local checkout."""
    origin, dev = repo_pair
    root_sha = _git(dev, "rev-parse", "master").stdout.strip()
    reviewer = origin.parent / "reviewer"
    _clone(origin, reviewer)
    _git(reviewer, "checkout", _BRANCH)  # local ref pinned at "v1"

    # Advance master, then rebase the dev's branch onto it and force-push —
    # this rewrites the branch's commit SHAs (routine force-push rebase).
    _commit(dev, "base2.py", "advance master\n")
    _git(dev, "push", "origin", "master")
    _git(dev, "checkout", "master")
    _git(dev, "pull")
    _git(dev, "checkout", _BRANCH)
    _git(dev, "rebase", "master")
    _commit(dev, "fix.py", "the real fix\n")
    _git(dev, "push", "--force", "origin", _BRANCH)

    svc = _service()
    _wire_for_reviewer(svc, reviewer)

    # Literal root SHA as the diff base — an ancestor of both the pre- and
    # post-rebase branch, so this is purely a probe of the HEAD side.
    diff_out = await svc.diff(branch_name=_BRANCH, base=root_sha)
    assert "fix.py" in diff_out
    assert "the real fix" in diff_out

    content = await svc.read_file_at_branch(branch_name=_BRANCH, path="fix.py")
    assert content == "the real fix\n"


@pytest.mark.asyncio
async def test_author_ahead_reads_local(repo_pair: tuple[Path, Path]) -> None:
    """The branch's own author (committed but not yet pushed) still reads
    their own local content — origin has nothing local lacks."""
    _origin, dev = repo_pair
    _commit(dev, "unpushed.py", "not yet on origin\n")

    svc = _service()
    _wire_for_reviewer(svc, dev)

    content = await svc.read_file_at_branch(branch_name=_BRANCH, path="unpushed.py")
    assert content == "not yet on origin\n"


@pytest.mark.asyncio
async def test_absent_local_reads_origin(repo_pair: tuple[Path, Path]) -> None:
    """A clone that only fetched (never checked out) the branch — no local
    ref at all — still resolves cleanly to origin's content."""
    origin, _dev = repo_pair
    fresh = origin.parent / "fresh"
    _clone(origin, fresh)  # only master checked out; _BRANCH is origin-only

    svc = _service()
    _wire_for_reviewer(svc, fresh)

    content = await svc.read_file_at_branch(branch_name=_BRANCH, path="feature.py")
    assert content == "v1\n"
