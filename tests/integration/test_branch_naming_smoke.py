"""Branch naming smoke tests.

Validates that the branch naming convention is correctly enforced:
- Format: {type}/{team}/{root_short}[--{sub_short}[--{subsub_short}]]
- Types: feature, bug, chore, docs, hotfix
- Max depth: 3 levels (root → subtask → sub-subtask)
- Uses '--' separator for task hierarchy
- Team name must be lowercase

See docs/rag/workflows/git-branch-naming.md
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.templates.git.constants import (
    BRANCH_TYPES,
    MAX_TASK_DEPTH,
)
from roboco.templates.git.branch import (
    BranchNameError,
    build_branch_name,
    get_root_task_id,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from roboco.services.task import TaskService


# Regex patterns for branch name validation
# {8} matches exactly 8 hex chars, {0,N} matches 0 to N repetitions of (--xxxx)
# Team name must be lowercase alphanumeric with hyphens (following team slug convention)
_MAX_SUBTASKS = MAX_TASK_DEPTH - 1
BRANCH_PATTERN = re.compile(
    rf"^(?P<type>{'|'.join(BRANCH_TYPES)})"
    rf"/(?P<team>[a-z][a-z0-9-]*)"
    rf"/(?P<path>[\da-f]{{8}}(?:--[\da-f]{{8}}){{0,{_MAX_SUBTASKS}}})$"
)


class TestBranchNamingSmoke:
    """Smoke tests for branch naming convention compliance."""

    def test_branch_types_constant_is_valid(self) -> None:
        """BRANCH_TYPES must contain all expected branch types."""
        expected = {"feature", "bug", "chore", "docs", "hotfix"}
        assert BRANCH_TYPES == expected, (
            f"BRANCH_TYPES mismatch. Expected {expected}, got {BRANCH_TYPES}"
        )

    def test_max_task_depth_constant_is_valid(self) -> None:
        """MAX_TASK_DEPTH must be 3 for root→subtask→sub-subtask hierarchy."""
        assert MAX_TASK_DEPTH == 3, (
            f"MAX_TASK_DEPTH should be 3, got {MAX_TASK_DEPTH}"
        )

    def test_branch_pattern_matches_valid_names(self) -> None:
        """Valid branch names must match the documented format."""
        valid_names = [
            "feature/backend/550e8400",  # root only
            "bug/backend/550e8400--6ba7b810",  # root → subtask
            "chore/frontend/550e8400--6ba7b810--f47ac10b",  # root → subtask → sub-subtask
            "docs/backend/a1b2c3d4",  # docs type
            "hotfix/backend/ff00aa00",  # hotfix type
            "feature/be-dev-1/12345678--abcdef12",  # complex team name
        ]
        for name in valid_names:
            assert BRANCH_PATTERN.match(name), f"Valid branch name failed: {name}"

    def test_branch_pattern_rejects_invalid_names(self) -> None:
        """Invalid branch names must be rejected."""
        invalid_names = [
            "feature",  # missing parts
            "feature/backend",  # missing path
            "feature/backend/",  # empty path
            "ghost/backend/550e8400",  # invalid type
            "feature//550e8400",  # empty team
            "Feature/backend/550e8400",  # wrong case type
            "feature/Backend/550e8400",  # uppercase team
            "feature/backend/550e8400/",  # trailing separator
            "feature/backend/550e8400/extra",  # too many parts (uses / not --)
            "feature/backend/550e84000",  # wrong length (9 chars)
            "feature/backend/550e840g",  # non-hex char
        ]
        for name in invalid_names:
            assert not BRANCH_PATTERN.match(name), f"Invalid branch name passed: {name}"


@pytest.mark.asyncio
async def test_build_branch_name_single_level() -> None:
    """Root task only: feature/backend/{8-hex}"""
    fake_svc = AsyncMock()
    task_id = uuid4()
    # Single level: task has no parent
    fake_svc.get.return_value = SimpleNamespace(id=task_id, parent_task_id=None)

    branch = await build_branch_name(task_id, "feature", "backend", fake_svc)

    # Should be just the 8-char hex prefix
    expected_suffix = str(task_id.hex[:8])
    assert branch == f"feature/backend/{expected_suffix}", f"Got: {branch}"
    assert BRANCH_PATTERN.match(branch)

    # Verify only one call to get()
    assert fake_svc.get.call_count == 1


@pytest.mark.asyncio
async def test_build_branch_name_two_levels() -> None:
    """Root → subtask: feature/backend/{root_short}--{sub_short}"""
    fake_svc = AsyncMock()
    root_id = uuid4()
    child_id = uuid4()

    # Walk UP from child to root:
    # 1. get(child_id) -> has parent_task_id=root_id
    # 2. get(root_id) -> has parent_task_id=None
    fake_svc.get.side_effect = [
        SimpleNamespace(id=child_id, parent_task_id=root_id),
        SimpleNamespace(id=root_id, parent_task_id=None),
    ]

    branch = await build_branch_name(child_id, "bug", "backend", fake_svc)

    # Format: {type}/{team}/{root_short}--{child_short}
    path = branch.split("/")[-1]
    assert "--" in path, f"Two-level path must use '--' separator. Got: {path}"
    parts = path.split("--")
    assert len(parts) == 2, f"Two-level path must have exactly 2 segments. Got: {path}"
    assert BRANCH_PATTERN.match(branch), f"Pattern match failed for: {branch}"

    # Verify correct order: root first, then child
    root_hex = root_id.hex[:8]
    child_hex = child_id.hex[:8]
    expected_path = f"{root_hex}--{child_hex}"
    assert path == expected_path, f"Expected {expected_path}, got {path}"

    assert fake_svc.get.call_count == 2


@pytest.mark.asyncio
async def test_build_branch_name_three_levels() -> None:
    """Root → subtask → sub-subtask: feature/backend/{root}--{sub}--{subsub}"""
    fake_svc = AsyncMock()
    root_id = uuid4()
    child_id = uuid4()
    grandchild_id = uuid4()

    # Walk UP from grandchild to root:
    fake_svc.get.side_effect = [
        SimpleNamespace(id=grandchild_id, parent_task_id=child_id),
        SimpleNamespace(id=child_id, parent_task_id=root_id),
        SimpleNamespace(id=root_id, parent_task_id=None),
    ]

    branch = await build_branch_name(grandchild_id, "chore", "backend", fake_svc)

    path = branch.split("/")[-1]
    assert path.count("--") == 2, f"Three-level path must have 2 separators. Got: {path}"
    parts = path.split("--")
    assert len(parts) == 3, f"Three-level path must have 3 segments. Got: {path}"
    assert BRANCH_PATTERN.match(branch), f"Pattern match failed for: {branch}"

    # Verify correct order: root first
    expected_path = f"{root_id.hex[:8]}--{child_id.hex[:8]}--{grandchild_id.hex[:8]}"
    assert path == expected_path, f"Expected {expected_path}, got {path}"

    assert fake_svc.get.call_count == 3


@pytest.mark.asyncio
async def test_build_branch_name_invalid_type_raises() -> None:
    """Invalid branch type must raise BranchNameError."""
    fake_svc = AsyncMock()
    task_id = uuid4()
    fake_svc.get.return_value = SimpleNamespace(id=task_id, parent_task_id=None)

    with pytest.raises(BranchNameError, match="Invalid branch type"):
        await build_branch_name(task_id, "invalid_type", "backend", fake_svc)


@pytest.mark.asyncio
async def test_build_branch_name_task_not_found_raises() -> None:
    """Unknown task ID must raise BranchNameError."""
    fake_svc = AsyncMock()
    fake_svc.get.return_value = None

    with pytest.raises(BranchNameError, match="Task not found"):
        await build_branch_name(uuid4(), "feature", "backend", fake_svc)


@pytest.mark.asyncio
async def test_build_branch_name_too_deep_raises() -> None:
    """Hierarchy deeper than MAX_TASK_DEPTH must raise BranchNameError."""
    fake_svc = AsyncMock()
    # Create a chain that exceeds MAX_TASK_DEPTH (3 levels)
    ids = [uuid4() for _ in range(MAX_TASK_DEPTH + 2)]  # 5 IDs
    # Walk up: last ID has parent of second-to-last, etc.
    fake_svc.get.side_effect = [
        SimpleNamespace(id=ids[i], parent_task_id=ids[i + 1])
        for i in range(len(ids) - 1)
    ]

    with pytest.raises(BranchNameError, match="too deep"):
        await build_branch_name(ids[-1], "feature", "backend", fake_svc)


@pytest.mark.asyncio
async def test_all_branch_types_valid() -> None:
    """All BRANCH_TYPES must be accepted by build_branch_name."""
    fake_svc = AsyncMock()
    task_id = uuid4()
    fake_svc.get.return_value = SimpleNamespace(id=task_id, parent_task_id=None)

    for branch_type in BRANCH_TYPES:
        branch = await build_branch_name(task_id, branch_type, "backend", fake_svc)
        assert branch.startswith(f"{branch_type}/backend/"), (
            f"Branch type '{branch_type}' not reflected in output: {branch}"
        )


@pytest.mark.asyncio
async def test_get_root_task_id_direct() -> None:
    """Root task should return itself."""
    fake_svc = AsyncMock()
    task_id = uuid4()
    fake_svc.get.return_value = SimpleNamespace(id=task_id, parent_task_id=None)

    result = await get_root_task_id(task_id, fake_svc)
    assert result == task_id


@pytest.mark.asyncio
async def test_get_root_task_id_walks_up_chain() -> None:
    """Should walk up parent chain to find root."""
    fake_svc = AsyncMock()
    root_id = uuid4()
    child_id = uuid4()
    grandchild_id = uuid4()

    # Walk up from grandchild
    fake_svc.get.side_effect = [
        SimpleNamespace(id=grandchild_id, parent_task_id=child_id),
        SimpleNamespace(id=child_id, parent_task_id=root_id),
        SimpleNamespace(id=root_id, parent_task_id=None),
    ]

    result = await get_root_task_id(grandchild_id, fake_svc)
    assert result == root_id


@pytest.mark.asyncio
async def test_get_root_task_id_unknown_raises() -> None:
    """Unknown task must raise BranchNameError."""
    fake_svc = AsyncMock()
    fake_svc.get.return_value = None

    with pytest.raises(BranchNameError, match="Task not found"):
        await get_root_task_id(uuid4(), fake_svc)


@pytest.mark.asyncio
async def test_get_root_task_id_too_deep_raises() -> None:
    """Hierarchy deeper than MAX_TASK_DEPTH must raise."""
    fake_svc = AsyncMock()
    ids = [uuid4() for _ in range(MAX_TASK_DEPTH + 2)]
    fake_svc.get.side_effect = [
        SimpleNamespace(id=ids[i], parent_task_id=ids[i + 1])
        for i in range(len(ids) - 1)
    ]

    with pytest.raises(BranchNameError, match="too deep"):
        await get_root_task_id(ids[-1], fake_svc)
