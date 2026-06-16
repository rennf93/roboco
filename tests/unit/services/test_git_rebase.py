"""Unit tests for GitService rebase conflict-state handling.

Pins the three critical control-flow branches of ``rebase_onto_base``:

1. **Success** — the underlying ``git rebase`` exits 0 → method returns a
   non-conflict result dict and never calls ``git rebase --abort``.
2. **Conflict** — ``git rebase`` exits non-zero → method calls
   ``git diff --name-only --diff-filter=U`` to collect conflicted files,
   calls ``git rebase --abort`` to restore the workspace, and returns a
   conflict result dict.
3. **Resilience** — both ``git rebase`` and ``git rebase --abort`` exit
   non-zero (e.g. abort fails mid-stream).  The method must still return
   the conflict dict without propagating an exception, because both are
   invoked with ``check=False``.

All tests mock ``_run_git`` at the service-method level using
``AsyncMock`` with a ``side_effect`` list so each awaited call consumes
the next pre-configured result in order.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from roboco.services.git import GitService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEAD = "feature/backend/root--task"
_BASE = "feature/backend/root"
_WORKSPACE = Path("/tmp/fake-ws")
_TOKEN = "ghp_fake"


def _git_service() -> GitService:
    """Instantiate GitService without a real DB session."""
    svc = GitService.__new__(GitService)
    svc.log = MagicMock()  # silence warning/info calls
    return svc


def _result(returncode: int = 0, stdout: str = "") -> Any:
    """Minimal subprocess result stand-in."""
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    return r


# ---------------------------------------------------------------------------
# Test 1 — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_path_returns_rebased_and_does_not_call_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When git rebase exits 0 the method returns a non-conflict result and
    never invokes ``git rebase --abort``.

    Call sequence for the success path (rebase OK, 2 unique commits):
      [0] fetch origin
      [1] checkout HEAD branch
      [2] reset --hard origin/HEAD
      [3] rebase origin/BASE          ← exits 0
      [4] rev-list --count            ← returns "2"
      [5] push --force-with-lease     ← pushes the rebased branch
    """
    run = AsyncMock(
        side_effect=[
            _result(),  # [0] fetch
            _result(),  # [1] checkout
            _result(),  # [2] reset
            _result(),  # [3] rebase ← success
            _result(stdout="2\n"),  # [4] rev-list
            _result(),  # [5] push
        ]
    )
    monkeypatch.setattr(GitService, "_run_git", run)

    svc = _git_service()
    result = await svc.rebase_onto_base(
        _WORKSPACE,
        head_branch=_HEAD,
        base_branch=_BASE,
        git_token=_TOKEN,
    )

    assert result == {"status": "rebased", "unique_commits": 2}

    # Verify abort was never called
    abort_call = call(_WORKSPACE, ["rebase", "--abort"], check=False)
    assert abort_call not in run.call_args_list, (
        "git rebase --abort must NOT be called on a clean rebase"
    )


# ---------------------------------------------------------------------------
# Test 2 — conflict path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conflict_path_calls_diff_then_abort_and_returns_conflict_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When git rebase exits non-zero the method:

    * calls ``git diff --name-only --diff-filter=U`` to identify conflicted files,
    * calls ``git rebase --abort`` to restore the workspace,
    * returns ``{"status": "conflicts", "files": [<conflicted files>]}``.

    Call sequence:
      [0] fetch origin
      [1] checkout HEAD branch
      [2] reset --hard origin/HEAD
      [3] rebase origin/BASE          ← exits 1 (conflict)
      [4] diff --name-only            ← lists conflicted files
      [5] rebase --abort              ← exits 0
    """
    run = AsyncMock(
        side_effect=[
            _result(),  # [0] fetch
            _result(),  # [1] checkout
            _result(),  # [2] reset
            _result(returncode=1),  # [3] rebase ← conflict
            _result(stdout="src/a.py\nsrc/b.py\n"),  # [4] diff
            _result(),  # [5] rebase --abort
        ]
    )
    monkeypatch.setattr(GitService, "_run_git", run)

    svc = _git_service()
    result = await svc.rebase_onto_base(
        _WORKSPACE,
        head_branch=_HEAD,
        base_branch=_BASE,
        git_token=_TOKEN,
    )

    assert result == {"status": "conflicts", "files": ["src/a.py", "src/b.py"]}

    # Verify the diff call was made with the correct flags
    diff_call = call(
        _WORKSPACE,
        ["diff", "--name-only", "--diff-filter=U"],
        check=False,
    )
    assert diff_call in run.call_args_list, (
        "git diff --name-only --diff-filter=U must be called to collect conflicted"
        " files"
    )

    # Verify abort was called
    abort_call = call(_WORKSPACE, ["rebase", "--abort"], check=False)
    assert abort_call in run.call_args_list, (
        "git rebase --abort must be called to restore the workspace after a conflict"
    )


# ---------------------------------------------------------------------------
# Test 3 — resilience: both rebase and abort exit non-zero
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resilience_when_both_rebase_and_abort_fail_returns_conflict_no_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``git rebase`` exits non-zero AND ``git rebase --abort`` also
    exits non-zero, the method must still return a conflict result dict
    without raising an exception.

    Both are called with ``check=False`` so a non-zero exit code from
    either command produces a result object (not a raised exception).

    Call sequence:
      [0] fetch origin
      [1] checkout HEAD branch
      [2] reset --hard origin/HEAD
      [3] rebase origin/BASE          ← exits 1 (conflict)
      [4] diff --name-only            ← lists conflicted files
      [5] rebase --abort              ← exits 1 (abort also fails)
    """
    run = AsyncMock(
        side_effect=[
            _result(),  # [0] fetch
            _result(),  # [1] checkout
            _result(),  # [2] reset
            _result(returncode=1),  # [3] rebase ← conflict
            _result(stdout="src/conflict.py\n"),  # [4] diff
            _result(returncode=1),  # [5] rebase --abort ← also fails
        ]
    )
    monkeypatch.setattr(GitService, "_run_git", run)

    svc = _git_service()

    # Must not raise even though both rebase and abort return non-zero
    result = await svc.rebase_onto_base(
        _WORKSPACE,
        head_branch=_HEAD,
        base_branch=_BASE,
        git_token=_TOKEN,
    )

    assert result == {"status": "conflicts", "files": ["src/conflict.py"]}
