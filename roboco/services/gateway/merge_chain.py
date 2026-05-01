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
    """Return the merge target for `branch`."""
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
