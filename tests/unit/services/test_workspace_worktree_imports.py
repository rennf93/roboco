"""A worktree's interpreter imports the worktree's source, not the clone root's.

The shared venv's editable install names the clone root's checkout; the hook
WorkspaceService writes into that venv rewrites those sys.path entries for an
interpreter started through a worktree's ``.venv`` symlink (2026-11-01:
``make codegen`` in be-dev-1's worktree imported the clone root's Aug 7 code).
"""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest
from roboco.services.workspace import WorkspaceService
from roboco.utils.worktree_imports import point_clone_root_paths_at_worktree

if TYPE_CHECKING:
    from pathlib import Path

_CLONE = "/data/workspaces/p/backend/be-dev-1"
_WORKTREE = f"{_CLONE}/.worktrees/abc12345"


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        (_CLONE, _WORKTREE),
        (f"{_CLONE}/src", f"{_WORKTREE}/src"),
        (f"{_CLONE}/.venv/lib/python3.13/site-packages", None),
        (f"{_CLONE}/.uv-python/cpython-3.13/lib", None),
        (f"{_CLONE}/.worktrees/other", None),
        (f"{_CLONE}-sibling", None),
        ("/usr/lib/python3.13", None),
        ("", None),
    ],
)
def test_entries_inside_the_clone_root_move_to_the_worktree(
    entry: str, expected: str | None
) -> None:
    path = [entry]
    point_clone_root_paths_at_worktree(f"{_WORKTREE}/.venv", path)
    assert path == [expected if expected is not None else entry]


def test_an_interpreter_started_from_the_clone_root_is_left_alone() -> None:
    path = [_CLONE, f"{_CLONE}/src"]
    point_clone_root_paths_at_worktree(f"{_CLONE}/.venv", path)
    assert path == [_CLONE, f"{_CLONE}/src"]


def _clone_with_editable_install(root: Path) -> tuple[Path, Path]:
    """A clone root whose venv carries an editable-style ``.pth`` naming the
    clone root, a package ``demo`` in both checkouts, and one worktree."""
    clone = root / "clone"
    worktree = clone / ".worktrees" / "abc12345"
    for checkout, where in ((clone, "clone root"), (worktree, "worktree")):
        (checkout / "demo").mkdir(parents=True)
        (checkout / "demo" / "__init__.py").write_text(f"WHERE = {where!r}\n")
        (checkout / "scripts").mkdir()
        (checkout / "scripts" / "show.py").write_text(
            "import demo\nprint(demo.WHERE)\n"
        )
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(clone / ".venv")],
        check=True,
    )
    (site_packages,) = (clone / ".venv" / "lib").glob("python*/site-packages")
    (site_packages / "_demo.pth").write_text(f"{clone}\n")
    return clone, worktree


def _imported_from(checkout: Path) -> str:
    done = subprocess.run(
        [str(checkout / ".venv" / "bin" / "python"), "scripts/show.py"],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def test_a_script_in_the_worktree_imports_the_worktrees_package(tmp_path: Path) -> None:
    clone, worktree = _clone_with_editable_install(tmp_path)

    WorkspaceService._link_shared_venv(worktree, clone)

    assert (worktree / ".venv").is_symlink()
    assert _imported_from(worktree) == "worktree"
    assert _imported_from(clone) == "clone root"


def test_the_hook_comes_back_into_a_venv_rebuilt_under_a_linked_worktree(
    tmp_path: Path,
) -> None:
    clone, worktree = _clone_with_editable_install(tmp_path)
    WorkspaceService._link_shared_venv(worktree, clone)
    (site_packages,) = (clone / ".venv" / "lib").glob("python*/site-packages")
    for hook in site_packages.glob("*roboco_worktree_imports*"):
        hook.unlink()
    assert _imported_from(worktree) == "clone root"

    WorkspaceService._link_shared_venv(worktree, clone)

    assert _imported_from(worktree) == "worktree"
