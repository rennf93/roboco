"""Make a task worktree import its own source, not the clone root's.

Every per-task worktree's ``.venv`` is a symlink to the clone root's shared
venv (``WorkspaceService._link_shared_venv``), and that venv holds the
project's editable install: a ``.pth`` line naming the clone root's checkout
(``uv sync`` from the clone root). Any interpreter started in a worktree
therefore imported the clone root's code, which sits on whatever branch the
clone root was last parked on: ``make codegen`` in be-dev-1's worktree
imported an Aug 7 ``roboco`` (``Role has no attribute DEVOPS``, 2026-11-01).

``WorkspaceService`` copies this file into the shared venv's site-packages as
``_roboco_worktree_imports.py`` with a ``.pth`` line that imports it, sorted
after the editable install's own ``.pth``. An interpreter started through a
worktree's ``.venv`` keeps that path as ``sys.prefix``, so the hook can tell
which worktree it serves and points every ``sys.path`` entry inside the clone
root at the same place inside that worktree. Started from the clone root, it
changes nothing.

Standard library only: it runs inside every project's venv, where RoboCo is
not installed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_WORKTREES_DIR = ".worktrees"
_CLONE_ROOT_ONLY = frozenset({".venv", _WORKTREES_DIR, ".uv-python"})


def point_clone_root_paths_at_worktree(prefix: str, path: list[str]) -> None:
    """Rewrite, in place, each ``path`` entry inside the clone root to the same
    place inside the worktree whose ``.venv`` is ``prefix``.

    ``prefix`` outside ``<clone root>/.worktrees/<id>/.venv`` leaves ``path``
    alone, and so do entries under the clone root's own ``.venv``,
    ``.worktrees`` and ``.uv-python``.
    """
    worktree = _worktree_whose_venv_is(prefix)
    if worktree is None:
        return
    for index, entry in enumerate(path):
        path[index] = _same_place_inside_worktree(entry, worktree)


def _worktree_whose_venv_is(prefix: str) -> Path | None:
    """``<clone root>/.worktrees/<id>`` when ``prefix`` is its ``.venv``."""
    worktree = Path(os.path.normpath(prefix)).parent
    return worktree if worktree.parent.name == _WORKTREES_DIR else None


def _same_place_inside_worktree(entry: str, worktree: Path) -> str:
    """``entry`` moved from the clone root into ``worktree``; unchanged when it
    is relative, outside the clone root, or under a clone-root-only directory."""
    try:
        relative = Path(os.path.normpath(entry)).relative_to(worktree.parent.parent)
    except ValueError:
        return entry
    if relative.parts and relative.parts[0] in _CLONE_ROOT_ONLY:
        return entry
    return str(worktree / relative)


if __name__ == "_roboco_worktree_imports":
    point_clone_root_paths_at_worktree(sys.prefix, sys.path)
