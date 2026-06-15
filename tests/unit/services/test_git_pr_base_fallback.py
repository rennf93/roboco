"""GitService retargets a PR base to the default branch when it's missing on origin.

create_pull_request resolves the base from the parent task's branch. If that
branch was never pushed to origin (an ancestor claimed but paused before its
first commit), GitHub rejects creation with 422 "base field invalid" and every
child task strands at open_pr. _pr_base_on_remote mirrors the branch-cutting
fallback in create_branch: ls-remote the resolved base and retarget to the
default branch when it's absent.
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
    svc.log = MagicMock()
    return svc


def _result(stdout: str = "") -> Any:
    return type("R", (), {"stdout": stdout, "returncode": 0})()


@pytest.mark.asyncio
async def test_retargets_to_default_when_base_absent_on_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _git_service()
    monkeypatch.setattr(svc, "_run_git", AsyncMock(return_value=_result(stdout="")))

    base = await svc._pr_base_on_remote(
        Path("/tmp/ws"), "feature/backend/root--parent", "master", "tok", uuid4()
    )

    assert base == "master"


@pytest.mark.asyncio
async def test_keeps_base_when_present_on_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _git_service()
    monkeypatch.setattr(
        svc,
        "_run_git",
        AsyncMock(return_value=_result(stdout="abc123\trefs/heads/feature/x\n")),
    )

    base = await svc._pr_base_on_remote(
        Path("/tmp/ws"), "feature/x", "master", "tok", uuid4()
    )

    assert base == "feature/x"


@pytest.mark.asyncio
async def test_noop_when_base_is_default_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _git_service()
    run = AsyncMock(return_value=_result(stdout=""))
    monkeypatch.setattr(svc, "_run_git", run)

    base = await svc._pr_base_on_remote(
        Path("/tmp/ws"), "master", "master", "tok", uuid4()
    )

    assert base == "master"
    run.assert_not_awaited()  # no remote lookup needed when base is the default
