"""GitService PR base falls back to the default branch when the resolved base
is missing on origin (PR #121).

An ancestor task's branch can be absent on origin (parent claimed but never
pushed — e.g. a PM paused before any commit), which makes GitHub reject PR
creation with 422 "base field invalid" and strands every child at PR time.
``_pr_base_on_remote`` retargets the PR to the default branch instead of
hard-failing, mirroring the branch-cutting fallback in ``create_branch``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.git import GitService


def _git_service() -> GitService:
    svc = GitService.__new__(GitService)
    svc.log = MagicMock()  # type: ignore[attr-defined]
    return svc


def _result(stdout: str) -> Any:
    return type("R", (), {"returncode": 0, "stdout": stdout})()


@pytest.mark.asyncio
async def test_pr_base_kept_when_present_on_remote() -> None:
    """ls-remote finds the base on origin → keep it (the normal case)."""
    svc = _git_service()
    svc._run_git = AsyncMock(  # type: ignore[method-assign]
        return_value=_result("abc123\trefs/heads/feature/backend/root--parent\n")
    )
    base = await svc._pr_base_on_remote(
        Path("/tmp/ws"), "feature/backend/root--parent", "main", "tok", uuid4()
    )
    assert base == "feature/backend/root--parent"


@pytest.mark.asyncio
async def test_pr_base_retargets_to_default_when_absent_on_remote() -> None:
    """ls-remote returns nothing (parent branch never pushed) → retarget the
    PR to the default branch rather than letting GitHub 422."""
    svc = _git_service()
    svc._run_git = AsyncMock(return_value=_result(""))  # type: ignore[method-assign]
    base = await svc._pr_base_on_remote(
        Path("/tmp/ws"), "feature/backend/root--orphan", "main", "tok", uuid4()
    )
    assert base == "main"
    svc.log.warning.assert_called_once()


@pytest.mark.asyncio
async def test_pr_base_skips_remote_check_when_target_is_default() -> None:
    """When the base already IS the default branch, no ls-remote is run — the
    default branch is assumed present."""
    svc = _git_service()
    run = AsyncMock()
    svc._run_git = run  # type: ignore[method-assign]
    base = await svc._pr_base_on_remote(Path("/tmp/ws"), "main", "main", "tok", uuid4())
    assert base == "main"
    run.assert_not_awaited()
