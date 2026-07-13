"""In-path PR-review gate: the assembled diff must use the REAL parent branch.

``_build_gate_review_evidence`` (claim_gate_review) and ``_pr_pass_blocked``
(pr_pass's conventions guard) used to call ``git.diff`` / the conventions
check with no base, which derives the parent via the same-team string
surgery ``parent_branch_for`` — wrong for every cross-team cell→root hop
(the cell task's own team segment can't derive the ``main_pm`` root's
branch). Both now resolve ``preferred_parent`` via
``merge_chain.resolve_parent_branch`` (reads the parent TASK's own
``branch_name``) and thread it through, falling back exactly like the
pre-fix derivation for a root / branchless-parent / parentless task.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_choreographer(*, task_service: AsyncMock, git: AsyncMock) -> Choreographer:
    return Choreographer(
        ChoreographerDeps(
            task=task_service,
            work_session=AsyncMock(),
            git=git,
            a2a=AsyncMock(),
            journal=AsyncMock(),
            audit=AsyncMock(),
            evidence_repo=AsyncMock(),
        )
    )


def _gate_task(*, branch_name: str, parent_task_id: Any) -> Any:
    return MagicMock(
        branch_name=branch_name,
        parent_task_id=parent_task_id,
        pr_number=139,
        pr_url="https://example/pr/139",
        acceptance_criteria=[],
    )


class TestGateDiffParent:
    """``_gate_diff_parent`` mirrors ``resolve_parent_branch``'s three cases."""

    @pytest.mark.asyncio
    async def test_cross_team_child_uses_parent_task_branch(self) -> None:
        parent_id = uuid4()
        t = _gate_task(
            branch_name="feature/frontend/f7d0a61a--e56e6543--e2b50b06",
            parent_task_id=parent_id,
        )
        task_service = AsyncMock()
        task_service.get.return_value = MagicMock(
            branch_name="feature/main_pm/f7d0a61a--e56e6543"
        )
        c = _make_choreographer(task_service=task_service, git=AsyncMock())

        parent = await c._gate_diff_parent(t)
        assert parent == "feature/main_pm/f7d0a61a--e56e6543"
        task_service.get.assert_awaited_once_with(parent_id)

    @pytest.mark.asyncio
    async def test_root_subtask_with_branchless_umbrella_uses_project_default(
        self,
    ) -> None:
        parent_id = uuid4()
        t = _gate_task(
            branch_name="feature/main_pm/f7d0a61a--e56e6543", parent_task_id=parent_id
        )
        task_service = AsyncMock()
        task_service.get.return_value = MagicMock(branch_name=None)
        task_service.project_default_branch_for_task = AsyncMock(return_value="master")
        c = _make_choreographer(task_service=task_service, git=AsyncMock())

        parent = await c._gate_diff_parent(t)
        assert parent == "master"

    @pytest.mark.asyncio
    async def test_parentless_root_falls_back_to_string_derivation(self) -> None:
        t = _gate_task(branch_name="feature/main_pm/f7d0a61a", parent_task_id=None)
        task_service = AsyncMock()
        c = _make_choreographer(task_service=task_service, git=AsyncMock())

        parent = await c._gate_diff_parent(t)
        assert parent == "master"
        task_service.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_branchless_task_returns_none(self) -> None:
        t = _gate_task(branch_name="", parent_task_id=uuid4())
        task_service = AsyncMock()
        c = _make_choreographer(task_service=task_service, git=AsyncMock())

        assert await c._gate_diff_parent(t) is None
        task_service.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_fails_open_on_parent_lookup_error(self) -> None:
        t = _gate_task(
            branch_name="feature/frontend/f7d0a61a--e56e6543--e2b50b06",
            parent_task_id=uuid4(),
        )
        task_service = AsyncMock()
        task_service.get.side_effect = RuntimeError("db connection reset")
        c = _make_choreographer(task_service=task_service, git=AsyncMock())

        assert await c._gate_diff_parent(t) is None


class TestBuildGateReviewEvidence:
    @pytest.mark.asyncio
    async def test_diff_called_with_resolved_cross_team_parent(self) -> None:
        parent_id = uuid4()
        t = _gate_task(
            branch_name="feature/frontend/f7d0a61a--e56e6543--e2b50b06",
            parent_task_id=parent_id,
        )
        task_service = AsyncMock()
        task_service.get.return_value = MagicMock(
            branch_name="feature/main_pm/f7d0a61a--e56e6543"
        )
        git = AsyncMock()
        git.diff.return_value = "diff body"
        c = _make_choreographer(task_service=task_service, git=git)

        evidence = await c._build_gate_review_evidence(t)

        git.diff.assert_awaited_once_with(
            branch_name=t.branch_name,
            preferred_parent="feature/main_pm/f7d0a61a--e56e6543",
        )
        assert evidence["pr_diff"] == "diff body"

    @pytest.mark.asyncio
    async def test_diff_skipped_for_branchless_task(self) -> None:
        t = _gate_task(branch_name="", parent_task_id=None)
        git = AsyncMock()
        c = _make_choreographer(task_service=AsyncMock(), git=git)

        evidence = await c._build_gate_review_evidence(t)

        git.diff.assert_not_awaited()
        assert evidence["pr_diff"] == ""

    @pytest.mark.asyncio
    async def test_diff_falls_back_when_parent_lookup_fails(self) -> None:
        t = _gate_task(
            branch_name="feature/frontend/f7d0a61a--e56e6543--e2b50b06",
            parent_task_id=uuid4(),
        )
        task_service = AsyncMock()
        task_service.get.side_effect = RuntimeError("db connection reset")
        git = AsyncMock()
        git.diff.return_value = "diff body"
        c = _make_choreographer(task_service=task_service, git=git)

        evidence = await c._build_gate_review_evidence(t)

        git.diff.assert_awaited_once_with(
            branch_name=t.branch_name, preferred_parent=None
        )
        assert evidence["pr_diff"] == "diff body"


class TestPrPassBlockedThreadsParent:
    """``_pr_pass_blocked`` resolves the parent ONCE and hands it to the
    conventions guard, so a reviewer's block-level finding is never raised
    against inherited base-branch content on a cross-team assembled PR."""

    @pytest.mark.asyncio
    async def test_conventions_guard_receives_resolved_parent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "conventions_enabled", True)
        parent_id = uuid4()
        t = _gate_task(
            branch_name="feature/frontend/f7d0a61a--e56e6543--e2b50b06",
            parent_task_id=parent_id,
        )
        task_service = AsyncMock()
        task_service.get.return_value = MagicMock(
            branch_name="feature/main_pm/f7d0a61a--e56e6543"
        )
        c = _make_choreographer(task_service=task_service, git=AsyncMock())
        cc: Any = c
        cc._toolchain_broken_guard = AsyncMock(return_value=None)
        cc._conventions_guard = AsyncMock(return_value=None)
        reviewer_id = uuid4()

        rejection, _ci_note = await c._pr_pass_blocked(
            reviewer_id, uuid4(), t, "pr_reviewer", {}
        )

        assert rejection is None
        cc._conventions_guard.assert_awaited_once_with(
            reviewer_id,
            t,
            {},
            preferred_parent="feature/main_pm/f7d0a61a--e56e6543",
        )

    @pytest.mark.asyncio
    async def test_parent_lookup_skipped_when_conventions_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "conventions_enabled", False)
        t = _gate_task(
            branch_name="feature/frontend/f7d0a61a--e56e6543--e2b50b06",
            parent_task_id=uuid4(),
        )
        task_service = AsyncMock()
        c = _make_choreographer(task_service=task_service, git=AsyncMock())
        cc: Any = c
        cc._toolchain_broken_guard = AsyncMock(return_value=None)
        cc._conventions_guard = AsyncMock(return_value=None)

        rejection, _ci_note = await c._pr_pass_blocked(
            uuid4(), uuid4(), t, "pr_reviewer", {}
        )

        assert rejection is None
        task_service.get.assert_not_called()
        cc._conventions_guard.assert_awaited_once()
        assert cc._conventions_guard.await_args.kwargs.get("preferred_parent") is None
