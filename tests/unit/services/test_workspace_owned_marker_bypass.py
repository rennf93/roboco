"""The ownership-sentinel marker (`_ensure_agent_owned`'s root short-circuit,
see test_workspace_ensure_agent_owned_scope.py) must be invalidated by EVERY
root-side git-write path, not just `GitService._run_git`
(test_git_ownership_scope.py covers that one).

Adversarial review found a deterministic hole: `WorkspaceService`'s raw-
subprocess git helpers — `_worktree_git`, `_fetch_branch_ref`,
`_fetch_origin_best_effort` — never invalidated the marker, and the most
common spawn path hits them on (nearly) every respawn
(`ensure_worktree_self_heal` -> `_refresh_present_worktree` ->
`_fetch_branch_ref` + `_worktree_git(["reset", "--hard", ...])`). A stale
marker then let `_ensure_agent_owned` skip the walk that would have repaired
the root-owned files those calls had just created — a live Permission
denied for the agent. These tests cover the bypass paths directly.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.services.workspace import WorkspaceService, _ensure_agent_owned
from tests.unit.services.test_workspace_ensure_agent_owned_scope import (
    _build_workspace,
)

if TYPE_CHECKING:
    from pathlib import Path


def _svc() -> WorkspaceService:
    return WorkspaceService(MagicMock())


def _ok(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["git", *args], returncode=0, stdout="", stderr=""
    )


# ---------------------------------------------------------------------------
# `_worktree_git`: mutating verbs invalidate, read-only verbs don't.
# ---------------------------------------------------------------------------


def test_worktree_git_reset_hard_invalidates_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "roboco.services.workspace.subprocess.run", lambda *_a, **_k: _ok(["reset"])
    )
    invalidate = MagicMock()
    monkeypatch.setattr("roboco.services.workspace.invalidate_owned_marker", invalidate)

    WorkspaceService._worktree_git(tmp_path, ["reset", "--hard", "origin/x"])

    invalidate.assert_called_once_with(tmp_path)


def test_worktree_git_rev_parse_does_not_invalidate_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "roboco.services.workspace.subprocess.run",
        lambda *_a, **_k: _ok(["rev-parse"]),
    )
    invalidate = MagicMock()
    monkeypatch.setattr("roboco.services.workspace.invalidate_owned_marker", invalidate)

    WorkspaceService._worktree_git(
        tmp_path, ["rev-parse", "--verify", "--quiet", "refs/heads/x"], check=False
    )

    invalidate.assert_not_called()


def test_worktree_git_branch_show_current_does_not_invalidate_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ambiguous verb's query form: `branch --show-current` only reads."""
    monkeypatch.setattr(
        "roboco.services.workspace.subprocess.run", lambda *_a, **_k: _ok(["branch"])
    )
    invalidate = MagicMock()
    monkeypatch.setattr("roboco.services.workspace.invalidate_owned_marker", invalidate)

    WorkspaceService._worktree_git(tmp_path, ["branch", "--show-current"], check=False)

    invalidate.assert_not_called()


def test_worktree_git_branch_delete_invalidates_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ambiguous verb's write forms: `branch -d/-D <name>` writes."""
    monkeypatch.setattr(
        "roboco.services.workspace.subprocess.run", lambda *_a, **_k: _ok(["branch"])
    )
    invalidate = MagicMock()
    monkeypatch.setattr("roboco.services.workspace.invalidate_owned_marker", invalidate)

    WorkspaceService._worktree_git(
        tmp_path, ["branch", "-D", "task-branch"], check=False
    )

    invalidate.assert_called_once_with(tmp_path)


# ---------------------------------------------------------------------------
# `_fetch_branch_ref` — always mutating (fetch writes .git/objects + refs).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_branch_ref_invalidates_marker_before_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []

    def _run_subprocess(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        order.append("subprocess.run")
        return _ok(["fetch"])

    monkeypatch.setattr("roboco.services.workspace.subprocess.run", _run_subprocess)
    monkeypatch.setattr(
        "roboco.services.workspace.invalidate_owned_marker",
        lambda _ws: order.append("invalidate_owned_marker"),
    )
    mock_project_service = MagicMock()
    mock_project_service.get_by_slug = AsyncMock(return_value=None)

    with patch(
        "roboco.services.project.get_project_service",
        return_value=mock_project_service,
    ):
        await _svc()._fetch_branch_ref(tmp_path, "task-branch", "roboco-api")

    assert order == ["invalidate_owned_marker", "subprocess.run"]


# ---------------------------------------------------------------------------
# `_fetch_origin_best_effort` — same shape, scoped multi-ref fetch.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_origin_best_effort_invalidates_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "roboco.services.workspace.subprocess.run", lambda *_a, **_k: _ok(["fetch"])
    )
    invalidate = MagicMock()
    monkeypatch.setattr("roboco.services.workspace.invalidate_owned_marker", invalidate)

    await WorkspaceService._fetch_origin_best_effort(tmp_path, "roboco-api")

    invalidate.assert_called_once_with(tmp_path)


# ---------------------------------------------------------------------------
# End-to-end-shaped repro: a full zero-failure pass writes the marker; a
# bypass-path root write (through the now-fixed helper) invalidates it; the
# NEXT _ensure_agent_owned call walks again instead of trusting stale state.
# ---------------------------------------------------------------------------


def test_bypass_path_write_forces_next_ensure_agent_owned_to_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_workspace(tmp_path)
    touched_pass_1: list[str] = []
    touched_pass_2: list[str] = []

    def make_fakes(sink: list[str]) -> tuple[object, object]:
        def fake_chown_entry(entry: str, _st: object) -> bool:
            sink.append(entry)
            return True

        def fake_make_rw(entry: str, _st: object) -> None:
            sink.append(entry)

        return fake_chown_entry, fake_make_rw

    # Pass 1: a normal zero-failure ensure_agent_owned — writes the marker.
    fake_chown, fake_rw = make_fakes(touched_pass_1)
    monkeypatch.setattr("roboco.services.workspace._chown_entry", fake_chown)
    monkeypatch.setattr("roboco.services.workspace._make_owner_and_group_rw", fake_rw)
    _ensure_agent_owned(tmp_path)
    marker = tmp_path / ".git" / "roboco-owned"
    assert marker.is_file()
    assert touched_pass_1  # the walk actually ran

    # Bypass-path root write: a mutating _worktree_git call (the exact class
    # of call ensure_worktree_self_heal's self-heal makes on nearly every
    # respawn) — with subprocess mocked so no real git repo is needed, but
    # invalidate_owned_marker running for real.
    monkeypatch.setattr(
        "roboco.services.workspace.subprocess.run", lambda *_a, **_k: _ok(["reset"])
    )
    WorkspaceService._worktree_git(tmp_path, ["reset", "--hard", "origin/x"])
    assert not marker.exists()  # the fix: the bypass path invalidated it

    # Pass 2: _ensure_agent_owned must walk again (marker gone), not trust
    # the stale "fully owned" state from before the bypass-path write.
    fake_chown_2, fake_rw_2 = make_fakes(touched_pass_2)
    monkeypatch.setattr("roboco.services.workspace._chown_entry", fake_chown_2)
    monkeypatch.setattr("roboco.services.workspace._make_owner_and_group_rw", fake_rw_2)
    _ensure_agent_owned(tmp_path)
    assert touched_pass_2  # the walk ran again — nothing was silently skipped
