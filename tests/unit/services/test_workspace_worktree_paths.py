"""Per-task git-worktree path model (F123 fix, Phase A prep).

The coordinator PM exemption lets a PM hold multiple in_progress roots, but the
clone is one checkout — so switching roots' branches clobbers the working tree
(live on NAS: main-pm ping-ponged 03f80432 <-> c80e19ff on one clone). The fix:
each task gets its own working tree under ``{clone_root}/.worktrees/{task-short}/``
via ``git worktree add``. These tests pin the path layout BEFORE the helpers are
wired into the claim/spawn flow (Phase B).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.models.base import Team
from roboco.services.workspace import WorkspaceError, WorkspaceService

if TYPE_CHECKING:
    from pathlib import Path


def _service(root: Path) -> WorkspaceService:
    svc = WorkspaceService(MagicMock())
    svc.session = AsyncMock()
    svc.root = root
    return svc


def test_get_clone_root_path_equals_get_workspace_path(tmp_path: Path) -> None:
    # The clone root IS the existing per-agent workspace path; the new helper
    # is a named alias so call sites can express intent (clone-root vs worktree).
    svc = _service(tmp_path)
    clone = svc.get_clone_root_path("guard-core", Team.BACKEND, "be-dev-1")
    assert clone == svc.get_workspace_path("guard-core", Team.BACKEND, "be-dev-1")
    assert clone == tmp_path / "guard-core" / "backend" / "be-dev-1"


def test_get_worktree_path_lays_out_under_clone_root(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    wt = svc.get_worktree_path("guard-core", Team.BACKEND, "be-dev-1", "a3c40fe7")
    clone = svc.get_clone_root_path("guard-core", Team.BACKEND, "be-dev-1")
    assert wt == clone / ".worktrees" / "a3c40fe7"
    # And expressed from the workspaces root:
    assert (
        wt
        == tmp_path / "guard-core" / "backend" / "be-dev-1" / ".worktrees" / "a3c40fe7"
    )


def test_get_worktree_path_rejects_none_team(tmp_path: Path) -> None:
    # Mirrors get_workspace_path's guard: a None team would produce a literal
    # "None" segment and a broken path.
    svc = _service(tmp_path)
    with pytest.raises(WorkspaceError):
        svc.get_worktree_path(
            "guard-core", cast("Team | str", None), "be-dev-1", "a3c40fe7"
        )


def test_get_worktree_path_accepts_string_team(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    wt = svc.get_worktree_path("guard-core", "backend", "be-dev-1", "a3c40fe7")
    assert (
        wt
        == tmp_path / "guard-core" / "backend" / "be-dev-1" / ".worktrees" / "a3c40fe7"
    )


def test_get_worktree_path_per_task_isolation(tmp_path: Path) -> None:
    # Two tasks of the same agent get DISTINCT worktree dirs (the F123 point:
    # each root its own checkout, never shared).
    svc = _service(tmp_path)
    a = svc.get_worktree_path("guard-core", Team.BACKEND, "be-dev-1", "a3c40fe7")
    b = svc.get_worktree_path("guard-core", Team.BACKEND, "be-dev-1", "8e460893")
    assert a != b
    assert (
        a.parent
        == b.parent
        == tmp_path / "guard-core" / "backend" / "be-dev-1" / ".worktrees"
    )
