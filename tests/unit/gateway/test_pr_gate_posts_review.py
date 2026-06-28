"""The in-path gate posts its verdict on the assembled PR.

pr_pass / pr_fail leave a review on the PR the PM (or CEO) eventually merges,
so the gate decision is visible on the PR itself — never a silent transition.
The root→master PR only ever gets a COMMENT (only the CEO acts on master).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_choreographer(git: AsyncMock) -> Any:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": git,
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    c: Any = Choreographer(ChoreographerDeps(**base))
    # _project_slug_for hits the project service; stub it for the unit.
    c._project_slug_for = AsyncMock(return_value="proj")
    return c


def _task(*, parent_task_id: Any, pr_number: int | None = 77) -> MagicMock:
    return MagicMock(id=uuid4(), parent_task_id=parent_task_id, pr_number=pr_number)


@pytest.mark.asyncio
async def test_cell_pass_posts_approve() -> None:
    git = AsyncMock()
    c = _make_choreographer(git)
    await c._post_gate_review_to_pr(
        _task(parent_task_id=uuid4()), "pr_pass", "be-pr-reviewer", "looks good"
    )
    git.post_pr_review.assert_awaited_once()
    _slug, _pr, body = git.post_pr_review.await_args.args
    assert git.post_pr_review.await_args.kwargs["event"] == "APPROVE"
    assert "PASSED" in body
    assert "be-pr-reviewer" in body


@pytest.mark.asyncio
async def test_cell_fail_posts_request_changes() -> None:
    git = AsyncMock()
    c = _make_choreographer(git)
    await c._post_gate_review_to_pr(
        _task(parent_task_id=uuid4()),
        "pr_fail",
        "be-pr-reviewer",
        "Issues:\n- seam mismatch",
    )
    assert git.post_pr_review.await_args.kwargs["event"] == "REQUEST_CHANGES"
    assert "CHANGES REQUESTED" in git.post_pr_review.await_args.args[2]


@pytest.mark.asyncio
async def test_root_pass_posts_comment_never_approve() -> None:
    """The root→master PR must never get an APPROVE — only the CEO merges it."""
    git = AsyncMock()
    c = _make_choreographer(git)
    await c._post_gate_review_to_pr(
        _task(parent_task_id=None), "pr_pass", "pr-reviewer-1", "root scope clean"
    )
    assert git.post_pr_review.await_args.kwargs["event"] == "COMMENT"
    assert "master" in git.post_pr_review.await_args.args[2]


@pytest.mark.asyncio
async def test_root_fail_posts_comment_never_blocks_master() -> None:
    git = AsyncMock()
    c = _make_choreographer(git)
    await c._post_gate_review_to_pr(
        _task(parent_task_id=None), "pr_fail", "pr-reviewer-1", "Issues:\n- x"
    )
    assert git.post_pr_review.await_args.kwargs["event"] == "COMMENT"


@pytest.mark.asyncio
async def test_no_pr_number_skips_post() -> None:
    git = AsyncMock()
    c = _make_choreographer(git)
    await c._post_gate_review_to_pr(
        _task(parent_task_id=uuid4(), pr_number=None), "pr_pass", "r", "n"
    )
    git.post_pr_review.assert_not_awaited()


@pytest.mark.asyncio
async def test_github_failure_is_swallowed() -> None:
    """A posting failure must not propagate — the gate transition already ran."""
    git = AsyncMock()
    git.post_pr_review.side_effect = RuntimeError("github down")
    c = _make_choreographer(git)
    # Must not raise.
    await c._post_gate_review_to_pr(_task(parent_task_id=uuid4()), "pr_pass", "r", "n")
