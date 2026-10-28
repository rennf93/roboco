"""Pure path rules of the agent workspace layout.

    {workspaces_root}/{project}/{team}/{agent}/                 clone root
    {workspaces_root}/{project}/{team}/{agent}/.worktrees/{id}/ per-task worktree
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def clone_root_of(workspace: Path) -> Path:
    """The clone root for a workspace or one of its linked worktrees.

    ``.venv`` and ``.uv-python`` live at the clone root and are shared by every
    worktree under ``{clone_root}/.worktrees/{id}/``. Given a worktree path,
    return its clone root; given the clone root itself, return it unchanged.
    Pure path logic keyed on the ``.worktrees`` layout — no git call needed.
    """
    if workspace.parent.name == ".worktrees":
        return workspace.parent.parent
    return workspace
