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


def _task(
    *,
    parent_task_id: Any,
    pr_number: int | None = 77,
    batch_id: Any = None,
) -> MagicMock:
    return MagicMock(
        id=uuid4(),
        parent_task_id=parent_task_id,
        pr_number=pr_number,
        batch_id=batch_id,
    )


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
async def test_batch_root_subtask_pass_posts_comment_never_approve() -> None:
    """#608: a MegaTask root-subtask opens a root→master PR (parent='master')
    but carries parent_task_id=umbrella, so parentage alone misclassifies it as
    a cell→root PR and leaves an APPROVE that could satisfy master branch
    protection. is_batch_root_subtask must re-classify it as root → COMMENT."""
    git = AsyncMock()
    c = _make_choreographer(git)
    await c._post_gate_review_to_pr(
        _task(parent_task_id=uuid4(), batch_id=uuid4()),
        "pr_pass",
        "pr-reviewer-1",
        "root scope clean",
    )
    assert git.post_pr_review.await_args.kwargs["event"] == "COMMENT"
    assert "master" in git.post_pr_review.await_args.args[2]


@pytest.mark.asyncio
async def test_batch_root_subtask_fail_posts_comment_never_blocks_master() -> None:
    """#608: a MegaTask root-subtask's root→master PR must never get a blocking
    REQUEST_CHANGES either — only the CEO acts on master."""
    git = AsyncMock()
    c = _make_choreographer(git)
    await c._post_gate_review_to_pr(
        _task(parent_task_id=uuid4(), batch_id=uuid4()),
        "pr_fail",
        "pr-reviewer-1",
        "Issues:\n- x",
    )
    assert git.post_pr_review.await_args.kwargs["event"] == "COMMENT"


@pytest.mark.asyncio
async def test_batch_cell_task_still_gets_approve() -> None:
    """A batch cell task (under a root-subtask) is a cell→root PR — it must keep
    APPROVE/REQUEST_CHANGES. is_valid_batch_shape rejects batch_id on cell tasks,
    so a well-formed batch cell task carries batch_id=None → not a root-subtask."""
    git = AsyncMock()
    c = _make_choreographer(git)
    await c._post_gate_review_to_pr(
        _task(parent_task_id=uuid4(), batch_id=None),
        "pr_pass",
        "be-pr-reviewer",
        "looks good",
    )
    assert git.post_pr_review.await_args.kwargs["event"] == "APPROVE"


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


@pytest.mark.asyncio
async def test_slug_resolver_failure_is_swallowed() -> None:
    """#82: ``_post_gate_review_to_pr`` resolves the project slug via
    ``_project_slug_for`` (which walks ``resolve_task_project_slug`` and sorts a
    ``cell_projects`` map by ``m.team.value``). A malformed cell_map entry (a
    mapping missing ``.team``) makes that sort raise ``AttributeError``; the
    gate verdict must still be best-effort — the DB transition already
    committed, so a slug error must NOT propagate and 500 the reviewer (which
    would also skip the pr_fail a2a delivery that runs right after this).
    Mirror ``_capture_pr_head_sha``: catch + log + skip the post. The post
    itself is not awaited (no slug to post to); the call simply returns."""
    git = AsyncMock()
    c = _make_choreographer(git)
    # A malformed cell_map sort key — the resolver blows up mid-resolve.
    c._project_slug_for = AsyncMock(side_effect=AttributeError("team"))
    # Must not raise.
    await c._post_gate_review_to_pr(
        _task(parent_task_id=uuid4()), "pr_fail", "be-pr-reviewer", "Issues:\n- x"
    )
    git.post_pr_review.assert_not_awaited()
