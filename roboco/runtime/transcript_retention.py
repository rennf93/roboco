"""Agent transcript retention — select old Claude Code transcripts to prune.

Agents write a ``{session-id}.jsonl`` transcript per spawn under
``~/.claude/projects/<encoded-cwd>/``. Review/coordinate roles share the
``-app`` dir; authoring roles get a per-workspace dir under the workspaces
root. Nothing ever deleted them, so the host ``~/.claude`` grows unbounded — and
it is the operator's *real* ``~/.claude`` via the agent bind mount. This module
selects agent-owned transcripts older than the retention window, and ONLY
agent-owned dirs, never the operator's own Claude sessions.

The selection is a pure function so the deletion target is unit-testable against
a temp dir before it ever runs on a real home directory.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def is_agent_owned_dir(dir_name: str, workspaces_root: str) -> bool:
    """True if a ``~/.claude/projects`` subdir was written by a spawned agent.

    Agents run either at the image WORKDIR ``/app`` (review/coordinate roles →
    the shared ``-app`` dir) or in a per-agent clone under the workspaces root
    (authoring roles). Claude Code encodes the cwd into the dir name by replacing
    ``/`` with ``-``, so those dirs are ``-app`` and either the encoded
    workspaces root (e.g. ``-data-workspaces``) or one of its descendants
    (``-data-workspaces-<agent>``). Sibling cwds like ``/data/workspaces-old``
    or ``/data/workspaces2`` encode to ``-data-workspaces-old`` and
    ``-data-workspaces2``; a raw ``startswith(encoded_root)`` would treat those
    as agent-owned and prune unrelated operator transcripts.

    We require either an exact match against the encoded root or a path-boundary
    prefix (``encoded_root + "-"``) — that boundary character comes from the
    original ``/`` separator and is the encoding's only signal that the next
    component is a descendant rather than a sibling.
    """
    if dir_name == "-app":
        return True
    encoded_root = workspaces_root.rstrip("/").replace("/", "-")
    if not encoded_root:
        return False
    return dir_name == encoded_root or dir_name.startswith(encoded_root + "-")


def _is_old_transcript(path: Path, cutoff_epoch: float) -> bool:
    """True if ``path`` is a regular .jsonl file last modified before the cutoff."""
    try:
        return path.is_file() and path.stat().st_mtime < cutoff_epoch
    except OSError:
        return False


def _old_transcripts_in(directory: Path, cutoff_epoch: float) -> list[Path]:
    """Old ``*.jsonl`` transcripts directly inside one agent-owned dir."""
    return [t for t in directory.glob("*.jsonl") if _is_old_transcript(t, cutoff_epoch)]


def select_prunable_transcripts(
    projects_root: Path, workspaces_root: str, cutoff_epoch: float
) -> list[Path]:
    """Agent-owned ``*.jsonl`` transcripts last modified before ``cutoff_epoch``.

    Only files inside agent-owned project dirs are considered; directories and
    the operator's own session dirs are never returned. Missing/unreadable
    entries are skipped rather than raising.
    """
    if not projects_root.is_dir():
        return []
    prunable: list[Path] = []
    for child in sorted(projects_root.iterdir()):
        if child.is_dir() and is_agent_owned_dir(child.name, workspaces_root):
            prunable.extend(_old_transcripts_in(child, cutoff_epoch))
    return prunable
