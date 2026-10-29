"""The old layout's read and release clones under the workspaces root go
once, and only when they are still exactly that layout."""

from __future__ import annotations

import fcntl
from typing import TYPE_CHECKING

import pytest
from roboco.config import settings
from roboco.services import git_relay
from roboco.services.git_relay import remove_pre_move_copies

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    workspaces = tmp_path / "workspaces"
    private = tmp_path / "private"
    workspaces.mkdir()
    monkeypatch.setattr(settings, "workspaces_root", str(workspaces))
    monkeypatch.setattr(settings, "private_data_root", str(private))
    return workspaces, private


def _clone(path: Path) -> Path:
    (path / ".git").mkdir(parents=True)
    (path / "README").write_text("x\n")
    return path


def test_the_old_copies_go_and_agent_clones_stay(roots: tuple[Path, Path]) -> None:
    workspaces, private = roots
    _clone(workspaces / "proj" / "_meta" / "conventions")
    _clone(workspaces / "_release" / "proj")
    agent = _clone(workspaces / "proj" / "backend" / "be-dev-1")

    remove_pre_move_copies()

    assert not (workspaces / "proj" / "_meta").exists()
    assert not (workspaces / "_release").exists()
    assert (agent / "README").exists()
    assert (private / "pre-move-copies-removed").exists()


def test_anything_but_the_old_layout_is_left_alone(
    roots: tuple[Path, Path], tmp_path: Path
) -> None:
    workspaces, private = roots
    extra = workspaces / "a" / "_meta"
    _clone(extra / "conventions")
    (extra / "notes.md").write_text("mine\n")
    not_a_clone = workspaces / "b" / "_meta" / "conventions"
    not_a_clone.mkdir(parents=True)
    (workspaces / "_release").mkdir()
    (workspaces / "_release" / "readme.txt").write_text("x\n")
    elsewhere = _clone(tmp_path / "elsewhere" / "_meta" / "conventions").parent.parent
    (workspaces / "c").symlink_to(elsewhere)

    remove_pre_move_copies()

    assert (extra / "notes.md").exists()
    assert not_a_clone.exists()
    assert (workspaces / "_release" / "readme.txt").exists()
    assert (elsewhere / "_meta" / "conventions" / "README").exists()
    assert (private / "pre-move-copies-removed").exists()


def test_it_runs_once(roots: tuple[Path, Path]) -> None:
    workspaces, _private = roots
    remove_pre_move_copies()
    later = _clone(workspaces / "proj" / "_meta" / "conventions")

    remove_pre_move_copies()

    assert (later / "README").exists()


def test_a_copy_it_could_not_remove_is_retried_next_start(
    roots: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workspaces, private = roots
    meta = _clone(workspaces / "proj" / "_meta" / "conventions").parent

    def _refuse(path: Path) -> None:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(git_relay, "remove_workspace_tree", _refuse)
    remove_pre_move_copies()
    assert meta.exists()
    assert not (private / "pre-move-copies-removed").exists()

    monkeypatch.undo()
    monkeypatch.setattr(settings, "workspaces_root", str(workspaces))
    monkeypatch.setattr(settings, "private_data_root", str(private))
    remove_pre_move_copies()
    assert not meta.exists()
    assert (private / "pre-move-copies-removed").exists()


def test_a_pass_already_running_elsewhere_is_left_to_finish(
    roots: tuple[Path, Path],
) -> None:
    workspaces, private = roots
    meta = _clone(workspaces / "proj" / "_meta" / "conventions").parent
    private.mkdir()
    with (private / "pre-move-copies-removed.lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        remove_pre_move_copies()

    assert meta.exists()
    assert not (private / "pre-move-copies-removed").exists()


def test_a_pass_that_cannot_start_is_logged_not_raised(
    roots: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspaces, _private = roots
    meta = _clone(workspaces / "proj" / "_meta" / "conventions").parent
    (tmp_path / "a-file").write_text("x\n")
    monkeypatch.setattr(settings, "private_data_root", str(tmp_path / "a-file" / "p"))

    remove_pre_move_copies()

    assert meta.exists()
