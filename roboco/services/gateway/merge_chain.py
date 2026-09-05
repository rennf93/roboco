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
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from roboco.models.env_branches import head_branch

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
    cut from. A PARENTLESS root resolves the same way: its branch was cut
    from the project's head rung (panel-configured env ladder), so that rung
    is the root PR base / merge target — never a literal ``master``, which
    on a ``main``-default repo silently targets a branch the project doesn't
    use. Only when there is genuinely no project to consult do we fall back
    to pure string derivation.
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
    else:
        default_branch = await _project_default_branch(task, task_service)
        if default_branch is not None:
            return default_branch
    return parent_branch_for(task.branch_name)


async def _project_default_branch(task: Any, task_service: Any) -> str | None:
    """Resolve a task's project head rung, or None if unavailable.

    Prefers a dedicated TaskService resolver when present; otherwise reads the
    eager-loaded ``task.project`` row through ``head_branch`` (the env-ladder
    head rung). Returns None when no project can be resolved so the caller can
    fall back to string derivation.
    """
    resolver = getattr(task_service, "project_default_branch_for_task", None)
    if resolver is not None:
        branch = await resolver(task)
        if branch:
            return str(branch)
        return None
    project = getattr(task, "project", None)
    return head_branch(project) if project else None


@dataclass(frozen=True)
class TopologyIssue:
    """A detected PR-base / parent-topology mismatch (see :func:`find_topology_issue`).

    ``shape`` is one of:
      - ``"parented_base_is_head"``: the task has a parent that owns its own
        branch, but the task's actual recorded base is the project's
        integration/head branch instead of that parent's branch — the
        5612b225/PR #856 incident class (a task re-parented after its
        branch/PR base was already cut against the head branch).
      - ``"parentless_in_coordination_tree"``: the task is parentless (a
        root), but its own branch name encodes a nested hierarchy
        (``branch_depth`` > 1) — the signature of a task detached from a
        coordination ancestor during a restructure, not a genuinely
        standalone root (whose branch is always depth 1).
    """

    shape: str
    expected_base: str
    actual_base: str | None
    message: str
    repair: str


async def find_topology_issue(task: Any, task_service: Any) -> TopologyIssue | None:
    """Detect a stale/incoherent PR-base <-> parent-topology mismatch, or None.

    Reused at two catch points: ``open_pr`` preflight (before a PR/branch
    goes any further) and the ``parent_task_id`` re-parent write-through —
    so branch/parent topology is validated at the earliest points instead of
    only at the terminal verbs (``complete``/``submit_root``), where the
    5612b225 incident class (PR #856 bounced 3+ times over two days after
    all work and review had already been spent) only ever surfaced.
    """
    parent_id = getattr(task, "parent_task_id", None)
    if parent_id is not None:
        return await _parented_topology_issue(task, task_service, parent_id)
    return await _parentless_topology_issue(task, task_service)


async def _parented_topology_issue(
    task: Any, task_service: Any, parent_id: Any
) -> TopologyIssue | None:
    """Shape (a): a real parent branch exists, but the recorded base isn't it.

    Skips entirely when the parent is missing or owns no branch of its own
    (a legitimate branchless-coordination parent, or one not yet claimed) —
    ``resolve_parent_branch`` already falls back correctly for those, and
    the terminal-verb CEO_ONLY head-branch routing already handles the
    legitimate branchless-parent case.
    """
    parent = await task_service.get(UUID(str(parent_id)))
    parent_branch = getattr(parent, "branch_name", None) if parent else None
    if not parent_branch:
        return None
    actual = await task_service.recorded_pr_base(task)
    if actual is None or actual == parent_branch:
        return None
    head = await _project_default_branch(task, task_service)
    if head is None or actual != head:
        return None
    return TopologyIssue(
        shape="parented_base_is_head",
        expected_base=parent_branch,
        actual_base=actual,
        message=(
            f"task {task.id} has parent {parent_id} with its own branch "
            f"'{parent_branch}', but the task's branch/PR is based on the "
            f"integration branch '{actual}' instead"
        ),
        repair=(
            f"retarget/rebase this task's branch and PR onto '{parent_branch}' "
            "(its parent's branch) — it was likely re-parented after its "
            "branch/PR base was already cut against the integration branch"
        ),
    )


async def _parentless_topology_issue(
    task: Any, task_service: Any
) -> TopologyIssue | None:
    """Shape (b): a parentless root whose branch encodes a nested hierarchy.

    A genuinely standalone root's branch is always depth 1 (just its own
    root segment) and never fires this.
    """
    branch_name = getattr(task, "branch_name", None)
    if not branch_name:
        return None
    try:
        depth = branch_depth(branch_name)
    except ValueError:
        return None
    if depth <= 1:
        return None
    expected = await resolve_parent_branch(task, task_service)
    return TopologyIssue(
        shape="parentless_in_coordination_tree",
        expected_base=expected,
        actual_base=await task_service.recorded_pr_base(task),
        message=(
            f"task {task.id} is parentless (a root) but its branch "
            f"'{branch_name}' encodes a nested hierarchy (depth {depth}) — "
            "it looks detached from a coordination ancestor during a "
            "restructure rather than being a genuinely standalone root"
        ),
        repair=(
            "set parent_task_id to the real coordination ancestor this task "
            "belongs under, or if it is genuinely standalone, recreate its "
            f"branch at depth 1 off '{expected}'"
        ),
    )
