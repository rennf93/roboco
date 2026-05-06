"""Tests for PR merge target resolution by task scope."""

from __future__ import annotations

import pytest
from roboco.services.gateway.merge_chain import branch_depth, parent_branch_for

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


def test_branch_depth_master_is_zero() -> None:
    """Line 28: master returns 0."""
    assert branch_depth("master") == 0


def test_branch_depth_invalid_pattern_raises() -> None:
    """Line 31: invalid branch pattern raises."""
    with pytest.raises(ValueError, match="invalid branch"):
        branch_depth("garbage-branch-name")
