"""`prune_remote_best_effort` TTL.

The branch-list route (`/api/git/branches?include_remote=true`) ran this
self-heal on EVERY call, a panel poll every couple minutes turned a
read-only listing into a repeated `git remote prune` (+ ownership repair)
per poll. `_REMOTE_PRUNE_TTL_SECONDS` caps it to at most once per workspace
per window, process-wide.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.services import git as git_module
from roboco.services.git import GitService


def _bind(svc: GitService, name: str, value: object) -> None:
    object.__setattr__(svc, name, value)


_EXPECTED_RETRY_ATTEMPTS = 2


@pytest.mark.asyncio
async def test_prune_remote_skips_git_call_within_ttl_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(git_module, "_remote_pruned_at", {})
    svc = GitService(MagicMock())
    run_git = AsyncMock()
    _bind(svc, "_run_git", run_git)
    _bind(svc, "_token_for_workspace", AsyncMock(return_value="tok"))
    workspace = Path("/tmp/ws-ttl-a")

    await svc.prune_remote_best_effort(workspace)
    await svc.prune_remote_best_effort(workspace)

    run_git.assert_awaited_once()


@pytest.mark.asyncio
async def test_prune_remote_runs_again_once_ttl_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = Path("/tmp/ws-ttl-b")
    stale = time.monotonic() - (git_module._REMOTE_PRUNE_TTL_SECONDS + 5.0)
    monkeypatch.setattr(git_module, "_remote_pruned_at", {str(workspace): stale})
    svc = GitService(MagicMock())
    run_git = AsyncMock()
    _bind(svc, "_run_git", run_git)
    _bind(svc, "_token_for_workspace", AsyncMock(return_value="tok"))

    await svc.prune_remote_best_effort(workspace)

    run_git.assert_awaited_once()


@pytest.mark.asyncio
async def test_prune_remote_records_timestamp_only_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed prune must not poison the TTL cache, the next call should
    still retry rather than silently going quiet for a whole window."""
    monkeypatch.setattr(git_module, "_remote_pruned_at", {})
    svc = GitService(MagicMock())
    _bind(svc, "log", MagicMock())
    run_git = AsyncMock(side_effect=RuntimeError("boom"))
    _bind(svc, "_run_git", run_git)
    _bind(svc, "_token_for_workspace", AsyncMock(return_value="tok"))
    workspace = Path("/tmp/ws-ttl-c")

    await svc.prune_remote_best_effort(workspace)
    await svc.prune_remote_best_effort(workspace)

    assert run_git.await_count == _EXPECTED_RETRY_ATTEMPTS
