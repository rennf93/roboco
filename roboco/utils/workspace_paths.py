"""Pure path rules of the agent workspace layout.

{workspaces_root}/{project}/{team}/{agent}/                 clone root
{workspaces_root}/{project}/{team}/{agent}/.worktrees/{id}/ per-task worktree
"""

from __future__ import annotations

import os
from pathlib import Path

WORKTREES_DIR = ".worktrees"


def clone_root_of(workspace: Path) -> Path:
    """The clone root for a workspace or one of its linked worktrees.

    ``.venv`` and ``.uv-python`` live at the clone root and are shared by every
    worktree under ``{clone_root}/.worktrees/{id}/``. Given a worktree path,
    return its clone root; given the clone root itself, return it unchanged.
    Pure path logic keyed on the ``.worktrees`` layout — no git call needed.
    """
    if workspace.parent.name == WORKTREES_DIR:
        return workspace.parent.parent
    return workspace


def worktree_path_of(clone_root: Path, task_short_id: str) -> Path:
    """A task's linked worktree: ``{clone_root}/.worktrees/{task_short_id}``.

    Normalized and refused (``ValueError``) unless it lies inside the clone's
    worktrees directory, so an id like ``..`` can never aim a worktree
    command, or the removal of one, at the clone itself or beyond it.
    """
    worktrees = os.path.normpath(clone_root / WORKTREES_DIR)
    path = os.path.normpath(Path(worktrees) / task_short_id)
    if not path.startswith(f"{worktrees}{os.sep}"):
        raise ValueError(f"unsafe task id for a worktree path: {task_short_id!r}")
    return Path(path)
