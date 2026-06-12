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
    ``/`` with ``-``, so those dirs are ``-app`` and anything starting with the
    encoded workspaces root (e.g. ``-data-workspaces``). The operator's own
    sessions live under their real cwd (``-Users-…``, ``-home-…``) and are never
    matched.
    """
    if dir_name == "-app":
        return True
    encoded_root = workspaces_root.rstrip("/").replace("/", "-")
    return bool(encoded_root) and dir_name.startswith(encoded_root)


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
        if not child.is_dir() or not is_agent_owned_dir(child.name, workspaces_root):
            continue
        for transcript in child.glob("*.jsonl"):
            try:
                if transcript.is_file() and transcript.stat().st_mtime < cutoff_epoch:
                    prunable.append(transcript)
            except OSError:
                continue
    return prunable
