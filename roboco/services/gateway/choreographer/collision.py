"""Pure assembly of the reviewer/PM "collision map" block.

The collision surface (``intends_to_touch`` / ``adds_migration`` /
``touches_shared``) is authored at delegate time and consumed once by
``SequencingService`` to wire dependency edges, then never shown to a
reviewer again. This module surfaces it: for a task under review, the
siblings that share its parent and would collide — file-overlap siblings
plus migration-chain siblings (both ``adds_migration`` in the same repo) —
with the overlapping globs and a declared-vs-actual drift check.

Pure (no DB, no IO): callers fetch the task + its siblings (one indexed
``get_subtasks(parent_task_id)`` query, mig 069) and the task's actual
touched files (from git, where available), then hand them here. The same
builder feeds the QA ``claim_review`` evidence, the PR-gate
``claim_gate_review`` evidence, the PM planning briefing, and the panel's
``GET /api/tasks/{id}/collision-map`` endpoint.

A sibling is shown when the analyzer would wire an edge between it and the
task under review: file globs overlap, OR both add a migration (the
Alembic-head collision needs no file overlap). ``touches_shared`` alone is
too broad — a shared surface only collides with a sibling that also touches
the same files (rule 3), so it rides the file-overlap path as a flag, not a
standalone trigger.
"""

from __future__ import annotations

from fnmatch import fnmatch
from typing import Any

# Caps — the roadmap's risk budget. Siblings capped so a 20-child umbrella
# doesn't bury the review; globs capped so a glob like ``roboco/**/*.py``.
COLLISION_SIBLING_CAP = 10
COLLISION_GLOB_CAP = 5


def _globs_overlap(a: list[str], b: list[str]) -> bool:
    """True if any path in ``a`` overlaps any in ``b``.

    Mirrors ``SequencingService._globs_overlap`` without the private-method
    dependency: equality, fnmatch in either direction, or directory-prefix
    containment (``a/`` contains ``a/b.py``).
    """
    for pa in a:
        for pb in b:
            if pa == pb or fnmatch(pa, pb) or fnmatch(pb, pa):
                return True
            if pa.startswith(pb.rstrip("/") + "/") or pb.startswith(
                pa.rstrip("/") + "/"
            ):
                return True
    return False


def _glob_matches_file(glob: str, path: str) -> bool:
    """True if a declared ``glob`` covers an actual touched ``path`` (fnmatch
    either direction, or directory containment). Drift = an actual file that
    no declared glob covers — collision risk the reviewer should flag."""
    if glob == path or fnmatch(path, glob) or fnmatch(glob, path):
        return True
    return path.startswith(glob.rstrip("/") + "/") or glob.startswith(
        path.rstrip("/") + "/"
    )


def _glob_overlaps_any(glob: str, others: list[str]) -> bool:
    """True if ``glob`` overlaps any glob in ``others`` (the sibling-glob /
    task-glob intersection that produces the entry's ``overlap`` list)."""
    return any(_glob_overlaps(glob, other) for other in others)


def _glob_overlaps(a: str, b: str) -> bool:
    if a == b or fnmatch(a, b) or fnmatch(b, a):
        return True
    return a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/")


def _surfaced(sib: Any) -> bool:
    """A sibling carries a collision surface: a project to collide within
    and at least one of the three collision signals. Mirrors
    ``sequencing._surfaced_siblings`` so the map shows exactly the siblings
    the analyzer considered."""
    return bool(getattr(sib, "project_id", None)) and (
        bool(getattr(sib, "intends_to_touch", None))
        or bool(getattr(sib, "adds_migration", False))
        or bool(getattr(sib, "touches_shared", False))
    )


def _sort_key(sib: Any) -> tuple[int, int, str]:
    """Stable sibling ordering — ``(priority, sequence, id8)`` — so the map's
    order matches the analyzer's edge order (a re-run only adds entries)."""
    return (
        int(getattr(sib, "priority", 2)),
        int(getattr(sib, "sequence", 0)),
        str(getattr(sib, "id", ""))[:8],
    )


def _drift(task_intends: list[str], actual_files: list[str]) -> list[str]:
    """Declared-vs-actual drift: actual files the task touched that no declared
    glob covers — collision risk the reviewer should flag. Capped at
    ``COLLISION_GLOB_CAP``."""
    return [
        f
        for f in actual_files
        if not any(_glob_matches_file(g, f) for g in task_intends)
        and f not in task_intends
    ][:COLLISION_GLOB_CAP]


def _sibling_entry(
    sib: Any,
    *,
    task_intends: list[str],
    actual_files: list[str] | None,
) -> dict[str, Any]:
    """One colliding sibling → its collision-map entry. The caller has already
    decided the sibling collides (file-overlap or both-add-migration); this
    renders it: status/branch/PR/sequence, the sibling's declared globs, the
    overlap globs, and — when the caller handed real touched files — the
    task's declared-vs-actual drift."""
    sib_intends = list(getattr(sib, "intends_to_touch", None) or [])
    overlap = [g for g in sib_intends if _glob_overlaps_any(g, task_intends)][
        :COLLISION_GLOB_CAP
    ]
    entry: dict[str, Any] = {
        "id": str(getattr(sib, "id", ""))[:8],
        "title": getattr(sib, "title", None),
        "status": str(getattr(sib, "status", "")),
        "branch_name": getattr(sib, "branch_name", None),
        "pr_number": getattr(sib, "pr_number", None),
        "sequence": getattr(sib, "sequence", None),
        "intends_to_touch": sib_intends[:COLLISION_GLOB_CAP],
        "adds_migration": bool(getattr(sib, "adds_migration", False)),
        "touches_shared": bool(getattr(sib, "touches_shared", False)),
        "overlap": overlap,
    }
    # Only when the caller handed real touched files (QA / gate; not the PM
    # planning path, which has no work yet).
    if actual_files:
        undeclared = _drift(task_intends, actual_files)
        if undeclared:
            entry["undeclared"] = undeclared
    return entry


def _candidates(task: Any, siblings: list[Any]) -> list[Any]:
    """Surfaced, not-self, same-project siblings — the pool the analyzer
    would consider for an edge against ``task``."""
    task_project = getattr(task, "project_id", None)
    task_id = getattr(task, "id", None)
    return [
        s
        for s in siblings
        if _surfaced(s)
        and getattr(s, "id", None) != task_id
        and getattr(s, "project_id", None) == task_project
    ]


def _collides_with(task_intends: list[str], task_migrates: bool, sib: Any) -> bool:
    """File-overlap OR both-add-migration — the analyzer's edge predicate.
    The Alembic-head collision (both add a migration) needs no file overlap."""
    if _globs_overlap(task_intends, list(getattr(sib, "intends_to_touch", None) or [])):
        return True
    return task_migrates and bool(getattr(sib, "adds_migration", False))


def build_collision_context(
    *,
    task: Any,
    siblings: list[Any],
    actual_files: list[str] | None = None,
) -> list[dict[str, Any]] | None:
    """The collision map for ``task`` against its surfaced siblings.

    Returns ``None`` (block omitted, zero token cost) when the task has no
    parent (a root — no siblings to collide with) or no surfaced siblings.
    Otherwise one entry per colliding sibling, capped at
    ``COLLISION_SIBLING_CAP``, ordered by the analyzer's stable key.

    ``actual_files`` is the task's real touched-file list (from git, where
    available). When present, each entry carries ``undeclared`` — actual
    files not matched by the task's declared globs. The PM planning path
    passes ``None`` (no work yet), so drift is omitted there by design.
    """
    parent_id = getattr(task, "parent_task_id", None)
    if not parent_id:
        return None
    task_intends = list(getattr(task, "intends_to_touch", None) or [])
    task_migrates = bool(getattr(task, "adds_migration", False))
    actual = list(actual_files) if actual_files else None

    candidates = _candidates(task, siblings)
    if not candidates:
        return None

    entries: list[dict[str, Any]] = []
    for sib in sorted(candidates, key=_sort_key):
        if not _collides_with(task_intends, task_migrates, sib):
            continue
        entries.append(
            _sibling_entry(sib, task_intends=task_intends, actual_files=actual)
        )
        if len(entries) >= COLLISION_SIBLING_CAP:
            break
    return entries or None
