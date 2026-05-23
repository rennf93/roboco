"""PR merge target resolution by task scope.

Branch convention (from CLAUDE.md):
  {feature|bug|chore|docs|hotfix}/{team}/{root-id}[--{sub-id}[--{subsub-id}]]

Merge chain:
  - leaf branch (depth >= 2) merges into its immediate parent (drop last `--seg`)
  - root branch (depth == 1) merges into master
  - master is its own target (no-op)
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

_TYPES = ("feature", "bug", "chore", "docs", "hotfix")
_TYPE_PATTERN = "|".join(_TYPES)
_BRANCH_RE = re.compile(
    rf"^(?P<type>{_TYPE_PATTERN})/"
    r"(?P<team>[a-z_]+)/"
    r"(?P<segments>[a-zA-Z0-9_-]+(?:--[a-zA-Z0-9_-]+)*)$"
)


def branch_depth(branch: str) -> int:
    """Number of `--`-separated segments in the task hierarchy."""
    if branch == "master":
        return 0
    m = _BRANCH_RE.match(branch)
    if not m:
        raise ValueError(f"invalid branch: {branch!r}")
    return len(m.group("segments").split("--"))


def parent_branch_for(branch: str) -> str:
    """Return the merge target for `branch` by string surgery.

    NOTE: this REUSES ``branch``'s own team segment for the parent, so it is
    only correct within a single team. Across a team boundary — every
    cell→root hop, where the cell is ``feature/backend/…`` but the root is
    ``feature/main_pm/…`` — it yields a non-existent ref. For PR base/target
    resolution prefer :func:`resolve_parent_branch`, which reads the parent
    task's real branch_name. This stays as the fallback for the
    rootless / same-team / diff-base cases.
    """
    if branch == "master":
        return "master"
    m = _BRANCH_RE.match(branch)
    if not m:
        raise ValueError(f"invalid branch: {branch!r}")
    type_ = m.group("type")
    team = m.group("team")
    segments = m.group("segments").split("--")
    if len(segments) == 1:
        return "master"
    parent_segments = "--".join(segments[:-1])
    return f"{type_}/{team}/{parent_segments}"


async def resolve_parent_branch(task: Any, task_service: Any) -> str:
    """Base/target branch for a child→parent PR: the parent task's own
    branch_name.

    The parent task's stored ``branch_name`` is authoritative — branch
    creation already cuts and pushes each child from it
    (``TaskService._resolve_parent_branch``). Unlike :func:`parent_branch_for`
    it is correct across a team boundary (#181). Falls back to string
    derivation only when there is no parent or the parent has no branch yet.
    """
    parent_id = getattr(task, "parent_task_id", None)
    if parent_id is not None:
        parent = await task_service.get(UUID(str(parent_id)))
        if parent is not None and parent.branch_name:
            return str(parent.branch_name)
    return parent_branch_for(task.branch_name)
