"""Tests for PR merge target resolution by task scope."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.merge_chain import (
    branch_depth,
    find_topology_issue,
    parent_branch_for,
    resolve_parent_branch,
)

_DEPTH_ROOT = 1
_DEPTH_ONE_SUBTASK = 2
_DEPTH_DEEP_SUBTASK = 3


class TestBranchDepth:
    def test_root_branch(self) -> None:
        assert branch_depth("feature/backend/abc12345") == _DEPTH_ROOT

    def test_one_subtask(self) -> None:
        assert branch_depth("feature/backend/abc12345--def67890") == _DEPTH_ONE_SUBTASK

    def test_deep_subtask(self) -> None:
        deep = "feature/backend/abc12345--def67890--ghi11111"
        assert branch_depth(deep) == _DEPTH_DEEP_SUBTASK


class TestParentBranchFor:
    def test_leaf_returns_immediate_parent(self) -> None:
        b = "feature/backend/abc12345--def67890--ghi11111"
        assert parent_branch_for(b) == "feature/backend/abc12345--def67890"

    def test_one_level_returns_root_task_branch(self) -> None:
        b = "feature/backend/abc12345--def67890"
        assert parent_branch_for(b) == "feature/backend/abc12345"

    def test_root_returns_master(self) -> None:
        b = "feature/backend/abc12345"
        assert parent_branch_for(b) == "master"

    def test_master_returns_master(self) -> None:
        # Edge case: should be a no-op
        assert parent_branch_for("master") == "master"

    def test_invalid_pattern_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid branch"):
            parent_branch_for("not-a-branch")


class TestResolveParentBranch:
    """#181/#182: base/target comes from the parent TASK's branch_name, which
    is correct across a team boundary; parent_branch_for is the fallback."""

    @pytest.mark.asyncio
    async def test_uses_parent_task_branch_across_team(self) -> None:
        root_id = uuid4()
        task = MagicMock(
            parent_task_id=root_id,
            branch_name="feature/backend/ROOT0001--CELL0001",
        )
        task_service = AsyncMock()
        # Root lives under a DIFFERENT team prefix than the cell.
        task_service.get = AsyncMock(
            return_value=MagicMock(branch_name="feature/main_pm/ROOT0001")
        )
        result = await resolve_parent_branch(task, task_service)
        assert result == "feature/main_pm/ROOT0001"
        task_service.get.assert_awaited_once_with(root_id)

    @pytest.mark.asyncio
    async def test_parentless_root_uses_project_head_rung(self) -> None:
        # A parentless root's branch was cut from the project's head rung
        # (panel-configured env ladder), so that rung is the PR base / merge
        # target — never a literal "master", which on a main-default repo
        # silently targets a branch the project doesn't use.
        task = MagicMock(parent_task_id=None, branch_name="feature/backend/ROOT0001")
        task_service = AsyncMock()
        task_service.project_default_branch_for_task = AsyncMock(return_value="main")
        assert await resolve_parent_branch(task, task_service) == "main"
        task_service.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_parentless_root_falls_back_to_string_when_no_project(self) -> None:
        task = MagicMock(parent_task_id=None, branch_name="feature/backend/ROOT0001")
        task_service = AsyncMock()
        task_service.project_default_branch_for_task = AsyncMock(return_value=None)
        # No project to consult → string derivation's master fallback.
        assert await resolve_parent_branch(task, task_service) == "master"
        task_service.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_branchless_parent_uses_project_default_branch(self) -> None:
        # #17: a branchless coordination parent never gets a branch. The child
        # was cut from its own project's default branch, so that is the real
        # merge target — NOT a string-derived ref the parent never created
        # (which would have no valid merge target and wedge the cell↔Main-PM
        # loop).
        task = MagicMock(
            parent_task_id=uuid4(),
            branch_name="feature/backend/ROOT0001--CELL0001",
        )
        task_service = AsyncMock()
        task_service.get = AsyncMock(return_value=MagicMock(branch_name=None))
        task_service.project_default_branch_for_task = AsyncMock(return_value="master")
        result = await resolve_parent_branch(task, task_service)
        assert result == "master"
        task_service.project_default_branch_for_task.assert_awaited_once_with(task)

    @pytest.mark.asyncio
    async def test_branchless_parent_falls_back_to_string_when_no_project(self) -> None:
        # No project to consult (resolver returns None) → string derivation
        # remains the last-resort fallback.
        task = MagicMock(
            parent_task_id=uuid4(),
            branch_name="feature/backend/ROOT0001--CELL0001",
        )
        task_service = AsyncMock()
        task_service.get = AsyncMock(return_value=MagicMock(branch_name=None))
        task_service.project_default_branch_for_task = AsyncMock(return_value=None)
        result = await resolve_parent_branch(task, task_service)
        assert result == "feature/backend/ROOT0001"


def test_branch_depth_master_is_zero() -> None:
    """Line 28: master returns 0."""
    assert branch_depth("master") == 0


def test_branch_depth_invalid_pattern_raises() -> None:
    """Line 31: invalid branch pattern raises."""
    with pytest.raises(ValueError, match="invalid branch"):
        branch_depth("garbage-branch-name")


class TestFindTopologyIssue:
    """Postmortem follow-up: the topology-comparison helper that moves
    PR-base/parent-topology validation from the terminal verbs to open_pr +
    the re-parent path. Fixtures are modeled on incident 5612b225/PR #856
    (a task restructured out of a parentless root left its PR based on
    'slave' instead of an intermediate root branch) and the same-class
    incidents on record (6866e888, 6b9e19aa, 7de89c6e)."""

    @pytest.mark.asyncio
    async def test_parented_task_stranded_on_head_branch_is_flagged(self) -> None:
        """Shape (a): the parent owns a real branch, but the task's
        recorded base is still the integration/head branch — the
        5612b225/PR #856 shape (re-parented after the PR/branch base was
        already cut against 'slave')."""
        parent_id = uuid4()
        task = MagicMock(
            id=uuid4(),
            parent_task_id=parent_id,
            branch_name="feature/backend/root0001--child0001",
        )
        task_service = AsyncMock()
        task_service.get = AsyncMock(
            return_value=MagicMock(branch_name="feature/main_pm/root0001")
        )
        task_service.recorded_pr_base = AsyncMock(return_value="slave")
        task_service.project_default_branch_for_task = AsyncMock(return_value="slave")

        issue = await find_topology_issue(task, task_service)

        assert issue is not None
        assert issue.shape == "parented_base_is_head"
        assert issue.expected_base == "feature/main_pm/root0001"
        assert issue.actual_base == "slave"
        assert "feature/main_pm/root0001" in issue.repair

    @pytest.mark.asyncio
    async def test_parentless_root_nested_in_coordination_tree_is_flagged(
        self,
    ) -> None:
        """Shape (b): parent_task_id is None, but the branch name encodes a
        nested hierarchy (depth > 1) — the task looks detached from a
        coordination ancestor during a restructure."""
        task = MagicMock(
            id=uuid4(),
            parent_task_id=None,
            branch_name="feature/backend/root0001--child0001",
        )
        task_service = AsyncMock()
        task_service.project_default_branch_for_task = AsyncMock(return_value="slave")
        task_service.recorded_pr_base = AsyncMock(return_value="slave")

        issue = await find_topology_issue(task, task_service)

        assert issue is not None
        assert issue.shape == "parentless_in_coordination_tree"
        assert issue.expected_base == "slave"
        task_service.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_standalone_parentless_root_is_not_flagged(self) -> None:
        """Allow-shape: a genuinely standalone root (depth-1 branch, no
        parent) must NOT fire — this is the legitimate, existing pattern."""
        task = MagicMock(
            id=uuid4(),
            parent_task_id=None,
            branch_name="feature/backend/root0001",
        )
        task_service = AsyncMock()
        task_service.project_default_branch_for_task = AsyncMock(return_value="slave")

        assert await find_topology_issue(task, task_service) is None

    @pytest.mark.asyncio
    async def test_parented_task_matching_parent_branch_is_not_flagged(self) -> None:
        """Allow-shape: the recorded base already matches the parent's own
        branch — no drift, nothing to refuse."""
        parent_id = uuid4()
        task = MagicMock(
            id=uuid4(),
            parent_task_id=parent_id,
            branch_name="feature/backend/root0001--child0001",
        )
        task_service = AsyncMock()
        task_service.get = AsyncMock(
            return_value=MagicMock(branch_name="feature/main_pm/root0001")
        )
        task_service.recorded_pr_base = AsyncMock(
            return_value="feature/main_pm/root0001"
        )

        assert await find_topology_issue(task, task_service) is None

    @pytest.mark.asyncio
    async def test_branchless_coordination_parent_is_not_flagged(self) -> None:
        """Allow-shape: a legitimate branchless coordination parent (no
        branch of its own) is the existing, intentional pattern — the
        terminal-verb CEO_ONLY head-branch routing handles it, not this
        check."""
        parent_id = uuid4()
        task = MagicMock(
            id=uuid4(),
            parent_task_id=parent_id,
            branch_name="feature/backend/root0001--child0001",
        )
        task_service = AsyncMock()
        task_service.get = AsyncMock(return_value=MagicMock(branch_name=None))

        assert await find_topology_issue(task, task_service) is None
        task_service.recorded_pr_base.assert_not_called()
