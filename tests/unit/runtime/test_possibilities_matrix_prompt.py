"""The possibilities-matrix proxy rewrites a dev prompt to WORK_ALREADY_DONE when
the flag is armed and the task already carries commits + an open PR.

The proxy is a cheap sync read — it does NOT re-check the async DB gates
(AC coverage, open findings); the server fast path ``_i_am_done_fast_path`` is
the authority. The prompt just collapses a 3-5 turn re-derivation to one
``i_am_done`` call. With the flag off (or the precondition unmet) the existing
``WORKFLOW STATE`` mapping is byte-for-byte unchanged.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.runtime.orchestrator import AgentOrchestrator

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _orch() -> AgentOrchestrator:
    orch = object.__new__(AgentOrchestrator)
    orch._instances = {}
    return orch


def _task(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": str(uuid4()),
        "title": "Ship it",
        "status": "in_progress",
        "plan": "did the thing",
        "pr_created": True,
        "commits": [{"sha": "abc"}],
    }
    base.update(over)
    return base


def _no_findings_db() -> tuple[Any, Any]:
    """Patch the findings fetch out so a needs_revision prompt builds no DB."""

    @asynccontextmanager
    async def _fake_ctx() -> AsyncIterator[AsyncMock]:
        yield AsyncMock()

    repo = AsyncMock()
    repo.list_for_task = AsyncMock(return_value=[])
    return (
        patch("roboco.db.base.get_db_context", _fake_ctx),
        patch(
            "roboco.services.repositories.review_findings.ReviewFindingsRepository",
            return_value=repo,
        ),
    )


@pytest.mark.asyncio
async def test_armed_in_progress_with_pr_and_commits_is_work_already_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "possibilities_matrix_enabled", True)
    prompt = await _orch()._build_dev_prompt(_task(status="in_progress"))
    assert "WORK ALREADY DONE" in prompt
    assert "i_am_done(task_id=" in prompt


@pytest.mark.asyncio
async def test_armed_claimed_with_pr_and_commits_is_work_already_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "possibilities_matrix_enabled", True)
    prompt = await _orch()._build_dev_prompt(_task(status="claimed"))
    assert "WORK ALREADY DONE" in prompt


@pytest.mark.asyncio
async def test_armed_verifying_with_pr_and_commits_is_not_work_already_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # verifying is excluded from the proxy's trigger set: _i_am_done_pre_gate_dispatch
    # routes an owned `verifying` task to the resume path before the fast path is
    # ever reachable, so steering the prompt there would be a dead-end promise.
    monkeypatch.setattr(settings, "possibilities_matrix_enabled", True)
    prompt = await _orch()._build_dev_prompt(_task(status="verifying"))
    assert "WORK ALREADY DONE" not in prompt
    assert "WORKFLOW STATE: VERIFYING" in prompt


@pytest.mark.asyncio
async def test_flag_off_leaves_in_progress_as_executing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "possibilities_matrix_enabled", False)
    prompt = await _orch()._build_dev_prompt(_task(status="in_progress"))
    assert "WORK ALREADY DONE" not in prompt
    assert "WORKFLOW STATE: EXECUTING" in prompt


@pytest.mark.asyncio
async def test_armed_without_pr_stays_on_standard_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "possibilities_matrix_enabled", True)
    prompt = await _orch()._build_dev_prompt(
        _task(status="in_progress", pr_created=False)
    )
    assert "WORK ALREADY DONE" not in prompt
    assert "WORKFLOW STATE: EXECUTING" in prompt


@pytest.mark.asyncio
async def test_armed_without_commits_stays_on_standard_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "possibilities_matrix_enabled", True)
    prompt = await _orch()._build_dev_prompt(_task(status="in_progress", commits=[]))
    assert "WORK ALREADY DONE" not in prompt
    assert "WORKFLOW STATE: EXECUTING" in prompt


@pytest.mark.asyncio
async def test_armed_needs_revision_is_unaffected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # needs_revision is not in the proxy's status set — REVISION_REQUIRED wins.
    monkeypatch.setattr(settings, "possibilities_matrix_enabled", True)
    db_ctx, repo_ctx = _no_findings_db()
    with db_ctx, repo_ctx:
        prompt = await _orch()._build_dev_prompt(_task(status="needs_revision"))
    assert "WORK ALREADY DONE" not in prompt
    assert "REVISION REQUESTED" in prompt


@pytest.mark.asyncio
async def test_flag_off_claimed_no_plan_is_needs_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "possibilities_matrix_enabled", False)
    prompt = await _orch()._build_dev_prompt(
        _task(status="claimed", plan=None, pr_created=True, commits=[{"sha": "x"}])
    )
    assert "WORK ALREADY DONE" not in prompt
    assert "WORKFLOW STATE: NEEDS_PLAN" in prompt


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
