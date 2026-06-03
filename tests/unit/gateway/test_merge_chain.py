"""Tests for PR merge target resolution by task scope."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.merge_chain import (
    branch_depth,
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
    async def test_falls_back_when_no_parent(self) -> None:
        task = MagicMock(parent_task_id=None, branch_name="feature/backend/ROOT0001")
        task_service = AsyncMock()
        # No parent → root→master.
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
