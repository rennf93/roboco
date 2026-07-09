"""Scoped post-op ownership repair.

Every git op used to pay a recursive full-workspace chown after `_run_git`
regardless of whether it wrote anything — live NAS logs showed a plain
`rev-parse --verify` costing 5165ms of pure chown, and `fetch origin`
10534ms; `i_am_done` alone chains ~a dozen ops. `_git_ownership_scope`
classifies each invocation so a read-only op skips the repair entirely and a
`.git`-only-writing op (add/commit/fetch/push, the SET forms of
branch/symbolic-ref) repairs only `.git/` instead of walking the whole
working tree.
"""

from __future__ import annotations

import asyncio
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from roboco.services.git import GitService, _git_ownership_scope

if TYPE_CHECKING:
    from pathlib import Path


def _svc() -> GitService:
    return GitService(MagicMock())


# ---------------------------------------------------------------------------
# Pure classification — every verb shape actually used across git.py's call
# sites (86 sites, hand-enumerated).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["status", "--porcelain"],
        ["log", "-1", "--format=%H|%s"],
        ["diff", "--stat", "HEAD~1..HEAD"],
        ["diff", "--name-only", "--diff-filter=U"],
        ["rev-parse", "--verify", "--quiet", "refs/heads/x"],
        ["rev-list", "--left-right", "--count", "a...b"],
        ["rev-list", "--count", "a..b"],
        ["ls-remote", "--heads", "origin", "x"],
        ["merge-base", "--is-ancestor", "a", "b"],
        ["show", "HEAD:path/to/file"],
        ["cherry", "origin/main", "child-ref"],
    ],
)
def test_read_only_verbs_classify_none(args: list[str]) -> None:
    assert _git_ownership_scope(args) == "none"


@pytest.mark.parametrize(
    "args",
    [
        ["add", "file.py"],
        ["add", "-A"],
        ["commit", "-m", "msg", "--author", "a <a@b>"],
        ["fetch", "origin", "branch"],
        ["fetch", "origin"],
        ["push", "-u", "origin", "branch"],
        ["push", "--force-with-lease", "origin", "HEAD:branch"],
    ],
)
def test_git_scoped_verbs_classify_git(args: list[str]) -> None:
    assert _git_ownership_scope(args) == "git"


@pytest.mark.parametrize(
    "args",
    [
        ["checkout", "branch"],
        ["checkout", "-b", "branch", "origin/branch"],
        ["checkout", "-B", "branch"],
        ["checkout", "--detach"],
        ["reset", "--hard", "origin/branch"],
        ["pull"],
        ["pull", "--ff-only"],
        ["rebase", "target"],
        ["rebase", "--abort"],
    ],
)
def test_full_scope_verbs_classify_full(args: list[str]) -> None:
    assert _git_ownership_scope(args) == "full"


def test_branch_query_form_classifies_none() -> None:
    # `git branch --show-current` — the only branch-query shape used.
    assert _git_ownership_scope(["branch", "--show-current"]) == "none"


def test_branch_set_form_classifies_git() -> None:
    # `git branch <name> <start-point>` creates a local ref — writes .git/.
    assert (
        _git_ownership_scope(["branch", "task-branch", "origin/task-branch"]) == "git"
    )


def test_symbolic_ref_query_form_classifies_none() -> None:
    # `git symbolic-ref [-q] <name>` reads what <name> points to.
    assert (
        _git_ownership_scope(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"])
        == "none"
    )


def test_symbolic_ref_set_form_classifies_git() -> None:
    # `git symbolic-ref <name> <ref>` writes — 2 positional args.
    assert _git_ownership_scope(["symbolic-ref", "HEAD", "refs/heads/main"]) == "git"


def test_empty_args_classifies_full() -> None:
    """Safe default: nothing to classify never under-repairs."""
    assert _git_ownership_scope([]) == "full"


def test_unrecognized_verb_classifies_full() -> None:
    """Safe default: an unclassified verb never under-repairs."""
    assert _git_ownership_scope(["worktree", "add", "x", "-b", "y", "z"]) == "full"


# ---------------------------------------------------------------------------
# `_run_git` wiring — the classifier must actually gate which repair runs.
# ---------------------------------------------------------------------------


def _ok(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["git", *args], returncode=0, stdout="", stderr=""
    )


@pytest.mark.asyncio
async def test_read_only_op_skips_chown_entirely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read-only op must never call either repair function."""
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        "roboco.services.git.subprocess.run", lambda *_a, **_k: _ok(["status"])
    )
    full_repair = MagicMock()
    git_repair = MagicMock()
    monkeypatch.setattr("roboco.services.workspace._ensure_agent_owned", full_repair)
    monkeypatch.setattr("roboco.services.workspace._ensure_git_dir_owned", git_repair)

    await _svc()._run_git(tmp_path, ["status", "--porcelain"])

    full_repair.assert_not_called()
    git_repair.assert_not_called()


@pytest.mark.asyncio
async def test_git_scoped_op_calls_git_repair_not_full_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """add/commit/fetch/push call the .git-only repair, never the full walk."""
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        "roboco.services.git.subprocess.run", lambda *_a, **_k: _ok(["commit"])
    )
    full_repair = MagicMock()
    git_repair = MagicMock()
    monkeypatch.setattr("roboco.services.workspace._ensure_agent_owned", full_repair)
    monkeypatch.setattr("roboco.services.workspace._ensure_git_dir_owned", git_repair)

    await _svc()._run_git(tmp_path, ["commit", "-m", "msg"])

    git_repair.assert_called_once_with(tmp_path)
    full_repair.assert_not_called()


@pytest.mark.asyncio
async def test_full_scope_op_calls_full_repair_not_git_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """checkout/reset/rebase/pull keep the unchanged full-workspace repair."""
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        "roboco.services.git.subprocess.run", lambda *_a, **_k: _ok(["checkout"])
    )
    full_repair = MagicMock()
    git_repair = MagicMock()
    monkeypatch.setattr("roboco.services.workspace._ensure_agent_owned", full_repair)
    monkeypatch.setattr("roboco.services.workspace._ensure_git_dir_owned", git_repair)

    await _svc()._run_git(tmp_path, ["checkout", "some-branch"])

    full_repair.assert_called_once_with(tmp_path)
    git_repair.assert_not_called()


@pytest.mark.asyncio
async def test_reown_after_git_op_returns_zero_ms_when_skipped() -> None:
    """The instrumentation must see a true near-zero cost for a skipped repair,
    not a stale/garbage value."""
    loop = asyncio.get_running_loop()
    ms = await GitService._reown_after_git_op(loop, MagicMock(), ["status"])
    assert ms == 0.0


# ---------------------------------------------------------------------------
# `.git`-scoped repair actually targets clone_root/.git — for a plain clone
# AND for a per-task worktree path (the worktree-awareness requirement).
# ---------------------------------------------------------------------------


@pytest.fixture
def _record_touched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every path the real ownership-repair primitives touch."""
    touched: list[str] = []

    def _record(entry: str) -> int:
        touched.append(entry)
        return 0

    monkeypatch.setattr("roboco.services.workspace._own_and_grant_rw", _record)
    return touched


@pytest.mark.asyncio
async def test_git_scoped_repair_targets_clone_git_for_plain_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _record_touched: list[str]
) -> None:
    """A plain clone (not a worktree): `.git` scoped repair touches clone/.git."""
    clone = tmp_path / "clone"
    (clone / ".git" / "objects").mkdir(parents=True)
    (clone / ".git" / "config").write_text("[core]\n")
    monkeypatch.setattr(
        "roboco.services.git.subprocess.run", lambda *_a, **_k: _ok(["push"])
    )

    await _svc()._run_git(clone, ["push", "-u", "origin", "branch"])

    touched = set(_record_touched)
    assert str(clone / ".git") in touched
    assert str(clone / ".git" / "config") in touched


@pytest.mark.asyncio
async def test_git_scoped_repair_targets_shared_clone_git_for_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _record_touched: list[str]
) -> None:
    """An op run inside a `.worktrees/<id>` checkout repairs the SHARED
    clone_root/.git (the real object store) — NOT the worktree's own `.git`,
    which is just a small gitlink file, not a directory to walk."""
    clone_root = tmp_path / "clone"
    (clone_root / ".git" / "worktrees" / "abc123").mkdir(parents=True)
    (clone_root / ".git" / "refs" / "heads").mkdir(parents=True)

    worktree = clone_root / ".worktrees" / "abc123"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text(
        f"gitdir: {clone_root / '.git' / 'worktrees' / 'abc123'}\n"
    )

    monkeypatch.setattr(
        "roboco.services.git.subprocess.run", lambda *_a, **_k: _ok(["commit"])
    )

    await _svc()._run_git(worktree, ["commit", "-m", "msg"])

    touched = set(_record_touched)
    assert str(clone_root / ".git") in touched
    assert str(clone_root / ".git" / "worktrees" / "abc123") in touched
    # Never touches the worktree's own gitlink file as a directory walk root.
    assert str(worktree / ".git") not in touched
