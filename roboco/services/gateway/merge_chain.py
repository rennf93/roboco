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
    it is correct across a team boundary.

    When the parent is a branchless coordination/fan-out task (it carries a
    product but no repo of its own, so it never gets a branch), string
    derivation off the child's own branch would yield a ref the parent never
    created — the merge then has no valid target and the cell↔Main-PM loop
    wedges. In that case fall back to the child task's own project
    default branch (e.g. master), which is what the child branch was actually
    cut from. Only when there is genuinely no project to consult do we fall
    back to pure string derivation.
    """
    parent_id = getattr(task, "parent_task_id", None)
    if parent_id is not None:
        parent = await task_service.get(UUID(str(parent_id)))
        if parent is not None:
            if parent.branch_name:
                return str(parent.branch_name)
            # Parent exists but owns no branch: a branchless coordination
            # parent. The child was cut from its own project's default branch,
            # so that is the real merge target.
            default_branch = await _project_default_branch(task, task_service)
            if default_branch is not None:
                return default_branch
    return parent_branch_for(task.branch_name)


async def _project_default_branch(task: Any, task_service: Any) -> str | None:
    """Resolve a task's project default branch, or None if unavailable.

    Prefers a dedicated TaskService resolver when present; otherwise reads the
    eager-loaded ``task.project.default_branch`` relationship. Returns None when
    no project can be resolved so the caller can fall back to string derivation.
    """
    resolver = getattr(task_service, "project_default_branch_for_task", None)
    if resolver is not None:
        branch = await resolver(task)
        if branch:
            return str(branch)
        return None
    project = getattr(task, "project", None)
    default_branch = getattr(project, "default_branch", None) if project else None
    return str(default_branch) if default_branch else None
