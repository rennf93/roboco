"""Real-git regression tests for the committed-work-preserving reconciliation
in ``rebase_onto_base``.

The historical bug (live, struck repeatedly): the preamble ran an
unconditional ``git reset --hard origin/<head_branch>`` right after checkout,
which rewound the local branch to the last-PUSHED tip — silently discarding
every committed-but-unpushed commit, since the ``commit`` do-verb never
pushes. The subsequent ``push --force-with-lease`` then succeeded (the lease
matches the freshly-fetched origin ref, which never moved) and republished
the truncated branch as authoritative.

These run against a REAL bare origin + real clones (no mocked ``_run_git``)
— the bug lived exactly in real-git ref semantics, and a mock can't
reproduce a fast-forward/divergence classification bug like this one.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from roboco.exceptions import GitCommandError
from roboco.services.git import GitService

if TYPE_CHECKING:
    from pathlib import Path

_HEAD = "feature/backend/task"
_BASE = "master"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )


def _git_ok(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Non-raising variant for existence probes."""
    return subprocess.run(
        ["git", *args], cwd=repo, check=False, capture_output=True, text=True
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


def _log_subjects(repo: Path, ref: str) -> list[str]:
    out = _git(repo, "log", "--format=%s", ref)
    return out.stdout.splitlines()


def _rev(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", ref).stdout.strip()


def _service() -> GitService:
    svc = GitService.__new__(GitService)
    svc.log = MagicMock()
    svc.session = MagicMock()
    return svc


@pytest.fixture
def repo_pair(tmp_path: Path) -> tuple[Path, Path]:
    """A bare origin plus a working clone, both carrying a pushed task branch
    (``master`` with one root commit, ``_HEAD`` branched off it with one more)."""
    origin = tmp_path / "origin.git"
    _init_bare(origin)
    seed = tmp_path / "seed"
    _clone(origin, seed)
    _commit(seed, "README.md", "root\n")
    _git(seed, "push", "origin", "master")
    _git(seed, "checkout", "-b", _HEAD)
    _commit(seed, "feature.py", "v1\n")
    _git(seed, "push", "origin", _HEAD)

    work = tmp_path / "work"
    _clone(origin, work)
    _git(work, "checkout", _HEAD)
    return origin, work


@pytest.mark.asyncio
async def test_local_ahead_survives_sync_and_reaches_origin(
    repo_pair: tuple[Path, Path],
) -> None:
    """(a) Committed-but-unpushed local work is a superset of origin — the
    reset is skipped and the rebase + force-push PUBLISHES it instead of
    discarding it (the historical data-loss bug)."""
    origin, work = repo_pair
    _commit(work, "fix.py", "unpushed fix\n")  # committed locally, never pushed

    svc = _service()
    result = await svc.rebase_onto_base(
        work, head_branch=_HEAD, base_branch=_BASE, git_token=""
    )

    assert result["status"] == "rebased"
    assert "add fix.py" in _log_subjects(origin, _HEAD)


@pytest.mark.asyncio
async def test_local_behind_adopts_origin_unchanged(
    repo_pair: tuple[Path, Path],
) -> None:
    """(b) Local has nothing origin lacks (a sibling pushed while this clone
    sat idle) — resets to origin exactly as before the fix."""
    origin, work = repo_pair
    other = origin.parent / "other"
    _clone(origin, other)
    _git(other, "checkout", _HEAD)
    _commit(other, "sibling.py", "from another clone\n")
    _git(other, "push", "origin", _HEAD)

    svc = _service()
    result = await svc.rebase_onto_base(
        work, head_branch=_HEAD, base_branch=_BASE, git_token=""
    )

    assert result["status"] == "rebased"
    assert "add sibling.py" in _log_subjects(work, _HEAD)
    assert _rev(work, _HEAD) == _rev(work, f"origin/{_HEAD}")


@pytest.mark.asyncio
async def test_diverged_refuses_and_touches_neither_side(
    repo_pair: tuple[Path, Path],
) -> None:
    """(c) Both local and origin carry unique commits — refuse outright;
    neither branch nor origin is touched, nothing is pushed."""
    origin, work = repo_pair
    other = origin.parent / "other"
    _clone(origin, other)
    _git(other, "checkout", _HEAD)
    _commit(other, "sibling.py", "from another clone\n")
    _git(other, "push", "origin", _HEAD)

    _commit(work, "local.py", "local-only work\n")  # unpushed local commit

    local_before = _rev(work, _HEAD)
    origin_before = _rev(origin, _HEAD)

    svc = _service()
    result = await svc.rebase_onto_base(
        work, head_branch=_HEAD, base_branch=_BASE, git_token=""
    )

    assert result == {"status": "diverged", "local_only": 1, "origin_only": 1}
    assert _rev(work, _HEAD) == local_before
    assert _rev(origin, _HEAD) == origin_before


@pytest.mark.asyncio
async def test_local_ref_absent_recovers_from_origin(
    repo_pair: tuple[Path, Path],
) -> None:
    """A workspace whose local ref for ``head_branch`` doesn't exist yet
    (only fetched/tracked from origin, e.g. a bare clone-root caller) is
    recovered — checkout, never reset, since there's nothing local to lose."""
    origin, _work = repo_pair
    fresh = origin.parent / "fresh"
    _clone(origin, fresh)  # only master is checked out; _HEAD is origin-only

    assert _git_ok(fresh, "rev-parse", "--verify", "--quiet", _HEAD).returncode != 0

    svc = _service()
    result = await svc.rebase_onto_base(
        fresh, head_branch=_HEAD, base_branch=_BASE, git_token=""
    )

    assert result["status"] == "rebased"
    assert _git_ok(fresh, "rev-parse", "--verify", "--quiet", _HEAD).returncode == 0


@pytest.mark.asyncio
async def test_superseded_still_returns_superseded(
    repo_pair: tuple[Path, Path],
) -> None:
    """(d) A head whose work is already in base is still 'superseded' —
    byte-for-byte unchanged by the reconciliation step."""
    origin, work = repo_pair

    # Fast-forward base past the branch's own tip so the rebase leaves it
    # with zero unique commits over base.
    other = origin.parent / "other-base"
    _clone(origin, other)
    _git(other, "merge", "--no-ff", "-m", "merge feature", f"origin/{_HEAD}")
    _git(other, "push", "origin", "master")

    svc = _service()
    result = await svc.rebase_onto_base(
        work, head_branch=_HEAD, base_branch=_BASE, git_token=""
    )
    assert result == {"status": "superseded"}


def _install_rejecting_hook(bare: Path) -> None:
    """Make every push to ``bare`` fail, simulating a push-side failure
    (network blip, flow-verb timeout kill, container reap) after a rebase
    already succeeded locally."""
    hook = bare / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)


def _remove_rejecting_hook(bare: Path) -> None:
    (bare / "hooks" / "pre-receive").unlink()


@pytest.mark.asyncio
async def test_self_inflicted_wedge_self_heals_on_retry(
    repo_pair: tuple[Path, Path],
) -> None:
    """(e) A prior rebase that succeeded locally but whose force-push then
    failed leaves local=rebased-history, origin=old-history — both rev-list
    counts positive by raw SHA. The patch-equivalence probe recognizes
    origin's tip as fully rewritten into local's exclusive commits, so the
    retry self-heals as 'rebased' instead of refusing as 'diverged' forever."""
    origin, work = repo_pair

    # Advance master so the rebase actually rewrites HEAD's commits' SHAs.
    other = origin.parent / "other-base"
    _clone(origin, other)
    _commit(other, "base2.py", "advance master\n")
    _git(other, "push", "origin", "master")

    _commit(work, "fix.py", "unpushed fix\n")  # local commit to be rebased

    svc = _service()
    _install_rejecting_hook(origin)
    with pytest.raises(GitCommandError):
        await svc.rebase_onto_base(
            work, head_branch=_HEAD, base_branch=_BASE, git_token=""
        )
    _remove_rejecting_hook(origin)

    result = await svc.rebase_onto_base(
        work, head_branch=_HEAD, base_branch=_BASE, git_token=""
    )

    assert result["status"] == "rebased"
    assert "add fix.py" in _log_subjects(origin, _HEAD)


@pytest.mark.asyncio
async def test_conflicts_still_refuses_and_reports_files(
    repo_pair: tuple[Path, Path],
) -> None:
    """(d) A genuine rebase conflict is still reported exactly as before —
    aborted, files listed, nothing pushed."""
    origin, _work = repo_pair

    # Branch off the ORIGINAL master and edit README.md.
    conflict_head = "feature/backend/conflict"
    conflicting = origin.parent / "conflicting"
    _clone(origin, conflicting)
    _git(conflicting, "checkout", "-b", conflict_head)
    (conflicting / "README.md").write_text("branch change\n")
    _git(conflicting, "add", "README.md")
    _git(conflicting, "commit", "-m", "branch edits README")
    _git(conflicting, "push", "origin", conflict_head)

    # Move master with a COMPETING edit to the same line, so rebasing the
    # branch onto the new master tip collides.
    other = origin.parent / "other-base"
    _clone(origin, other)
    (other / "README.md").write_text("master change\n")
    _git(other, "add", "README.md")
    _git(other, "commit", "-m", "master edits README")
    _git(other, "push", "origin", "master")

    svc = _service()
    result = await svc.rebase_onto_base(
        conflicting, head_branch=conflict_head, base_branch=_BASE, git_token=""
    )
    assert result["status"] == "conflicts"
    assert result["files"] == ["README.md"]
