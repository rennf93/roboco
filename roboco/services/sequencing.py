"""Deterministic collision-sequencing analyzer for sequenced batch intake.

Turns a batch's per-task collision surfaces into a dependency DAG + execution
waves. Correctness lives in CODE, not agent judgment: the analyzer *guarantees*
the safety serializations (file overlap, migration chain, shared-last) so a weak
Prompter can only ever over-serialize, never miss a declared collision. Pure —
no DB, no services; consumed by the batch-create path.

Rules (evaluated in order; an edge ``(a, b)`` means *b depends on a*, a first):
  1. File overlap   — overlapping surfaces serialize, more-important (lower
                      ``(priority, idx)``) first. Mixed shared/non-shared pairs
                      are left to rule 3 so a high-priority shared task can't
                      invert shared-last into a cycle.
  2. Migration chain — all ``adds_migration`` drafts run serially, ordered by
                      ``(priority, idx)`` (Alembic cannot have concurrent heads).
  3. Shared-last     — every non-shared draft that overlaps a ``touches_shared``
                      draft runs before it.
  4. Cell contention — when a wave puts more drafts on a cell than its capacity,
                      warn (never serialize — that is the orchestrator's job).

Then: dedupe edges, existence + cycle check, Kahn topological layering.
"""

from __future__ import annotations

from collections import defaultdict
from fnmatch import fnmatch
from itertools import pairwise
from typing import TYPE_CHECKING

from roboco.foundation.policy.sequencing.models import (
    DraftSurface,
    SequencePlan,
    SequencingError,
)

if TYPE_CHECKING:
    from collections.abc import Callable


class SequencingService:
    """Pure analyzer: ``analyze`` is the only public entry point."""

    def analyze(
        self,
        surfaces: list[DraftSurface],
        cell_of: Callable[[int], str],
        cell_capacity: dict[str, int],
    ) -> SequencePlan:
        """Compute the dependency edges + execution waves for a batch."""
        edges = self._dedupe(
            self._file_overlap_edges(surfaces)
            + self._migration_chain_edges(surfaces)
            + self._shared_last_edges(surfaces)
        )
        waves = self._toposort(edges, len(surfaces))
        warnings = self._contention_warnings(waves, cell_of, cell_capacity)
        return SequencePlan(edges=edges, waves=waves, warnings=warnings)

    # --- rule 1: file overlap ------------------------------------------------
    def _file_overlap_edges(
        self, surfaces: list[DraftSurface]
    ) -> list[tuple[int, int]]:
        edges: list[tuple[int, int]] = []
        for i, a in enumerate(surfaces):
            for b in surfaces[i + 1 :]:
                # Different repos can't share a working-tree path — no collision.
                if a.project_id != b.project_id:
                    continue
                # Mixed shared/non-shared overlaps are owned by rule 3.
                if a.touches_shared != b.touches_shared:
                    continue
                if self._globs_overlap(a.intends_to_touch, b.intends_to_touch):
                    edges.append(self._order_edge(a, b))
        return edges

    # --- rule 2: migration chain ---------------------------------------------
    @staticmethod
    def _migration_chain_edges(
        surfaces: list[DraftSurface],
    ) -> list[tuple[int, int]]:
        # Migrations serialize per project (each repo has its own alembic chain);
        # two repos' migrations are independent. Within a project, non-shared
        # migrations chain BEFORE shared ones (the ``touches_shared`` sort key) so
        # this never contradicts rule 3's shared-last ordering into a cycle.
        by_project: dict[object, list[DraftSurface]] = defaultdict(list)
        for s in surfaces:
            if s.adds_migration:
                by_project[s.project_id].append(s)
        edges: list[tuple[int, int]] = []
        for group in by_project.values():
            migs = sorted(group, key=lambda s: (s.touches_shared, s.priority, s.idx))
            edges.extend((prev.idx, cur.idx) for prev, cur in pairwise(migs))
        return edges

    # --- rule 3: shared-last -------------------------------------------------
    def _shared_last_edges(self, surfaces: list[DraftSurface]) -> list[tuple[int, int]]:
        edges: list[tuple[int, int]] = []
        for s in surfaces:
            if not s.touches_shared:
                continue
            for other in surfaces:
                if other.idx == s.idx or other.touches_shared:
                    continue
                # A shared edit only runs after a NON-shared task in the SAME repo.
                if other.project_id != s.project_id:
                    continue
                if self._globs_overlap(other.intends_to_touch, s.intends_to_touch):
                    edges.append((other.idx, s.idx))
        return edges

    # --- rule 4: cell contention (warnings only) -----------------------------
    @staticmethod
    def _contention_warnings(
        waves: list[list[int]],
        cell_of: Callable[[int], str],
        cell_capacity: dict[str, int],
    ) -> list[str]:
        warnings: list[str] = []
        for wave_no, wave in enumerate(waves):
            counts: dict[str, int] = defaultdict(int)
            for idx in wave:
                counts[cell_of(idx)] += 1
            for cell, n in sorted(counts.items()):
                cap = cell_capacity.get(cell)
                if cap is not None and n > cap:
                    warnings.append(
                        f"wave {wave_no}: {n} tasks target {cell} (capacity {cap})"
                    )
        return warnings

    # --- topological layering (existence + cycle check) ----------------------
    def _toposort(self, edges: list[tuple[int, int]], n: int) -> list[list[int]]:
        """Layer the DAG into waves; raise on an out-of-range edge or a cycle."""
        self._check_edges_in_range(edges, n)
        indeg, adj = self._build_graph(edges, n)
        return self._kahn_layers(indeg, adj, n)

    @staticmethod
    def _check_edges_in_range(edges: list[tuple[int, int]], n: int) -> None:
        for a, b in edges:
            if not (0 <= a < n and 0 <= b < n):
                raise SequencingError(
                    f"edge ({a}, {b}) references a draft outside 0..{n - 1}"
                )

    @staticmethod
    def _build_graph(
        edges: list[tuple[int, int]], n: int
    ) -> tuple[list[int], dict[int, list[int]]]:
        indeg = [0] * n
        adj: dict[int, list[int]] = defaultdict(list)
        for a, b in edges:
            adj[a].append(b)
            indeg[b] += 1
        return indeg, adj

    def _kahn_layers(
        self, indeg: list[int], adj: dict[int, list[int]], n: int
    ) -> list[list[int]]:
        remaining = set(range(n))
        waves: list[list[int]] = []
        while remaining:
            ready = sorted(i for i in remaining if indeg[i] == 0)
            if not ready:
                raise SequencingError("collision graph has a cycle")
            waves.append(ready)
            remaining -= set(ready)
            self._relax(indeg, adj, ready)
        return waves

    @staticmethod
    def _relax(indeg: list[int], adj: dict[int, list[int]], ready: list[int]) -> None:
        for i in ready:
            for nbr in adj[i]:
                indeg[nbr] -= 1

    # --- helpers -------------------------------------------------------------
    @staticmethod
    def _order_edge(a: DraftSurface, b: DraftSurface) -> tuple[int, int]:
        """Edge from the more-important draft (lower ``(priority, idx)``) first."""
        first, second = (a, b) if (a.priority, a.idx) <= (b.priority, b.idx) else (b, a)
        return (first.idx, second.idx)

    @staticmethod
    def _globs_overlap(a: list[str], b: list[str]) -> bool:
        """True if any path in ``a`` overlaps any in ``b`` (equality, fnmatch in
        either direction, or directory-prefix containment)."""
        for pa in a:
            for pb in b:
                if pa == pb or fnmatch(pa, pb) or fnmatch(pb, pa):
                    return True
                if pa.startswith(pb.rstrip("/") + "/") or pb.startswith(
                    pa.rstrip("/") + "/"
                ):
                    return True
        return False

    @staticmethod
    def _dedupe(edges: list[tuple[int, int]]) -> list[tuple[int, int]]:
        seen: set[tuple[int, int]] = set()
        out: list[tuple[int, int]] = []
        for edge in edges:
            if edge not in seen:
                seen.add(edge)
                out.append(edge)
        return out


# ---------------------------------------------------------------------------
# dev_task_collision_edges — the dev-task collision DAG (edge kind 3).
# ---------------------------------------------------------------------------

# A collision needs at least two surfaced siblings to produce an edge.
_MIN_COLLISION_PAIR = 2


def _surfaced_siblings(siblings: list) -> list:
    """Siblings carrying a collision surface: a project to collide within and at
    least one of intends_to_touch / adds_migration / touches_shared."""
    return [
        s
        for s in siblings
        if getattr(s, "project_id", None)
        and (
            getattr(s, "intends_to_touch", None)
            or getattr(s, "adds_migration", False)
            or getattr(s, "touches_shared", False)
        )
    ]


def dev_task_collision_edges(siblings: list) -> list[tuple[object, object]]:
    """Wire the dev-task collision DAG for a parent's surfaced siblings.

    Pure glue over :class:`SequencingService`: turns each surfaced sibling
    (a task row with ``id`` / ``priority`` / ``sequence`` / ``intends_to_touch``
    / ``adds_migration`` / ``touches_shared`` / ``project_id``) into a
    :class:`DraftSurface`, runs the analyzer, and returns
    ``(depends_on_id, task_id)`` pairs — *task depends-on depends_on* — ready
    for ``TaskService.add_dependency``.

    Incremental + idempotent by construction: a sibling with no collision
    surface (empty ``intends_to_touch`` and not ``adds_migration`` /
    ``touches_shared``) is parallel to everything and contributes no edges; a
    sibling without a ``project_id`` cannot collide (collisions are scoped to
    a repo) and is skipped. Siblings are ordered by ``(priority, sequence)``
    before indexing so the analyzer's edge order is stable across re-runs
    (a newly-delegated sibling takes a fresh ``sequence``; existing siblings
    keep theirs), so re-running after each delegate only ADDS edges for the
    new sibling's collisions — never flips an existing pair's order into a
    reverse edge (which would cycle). ``add_dependency`` dedupes, so repeated
    wiring is a no-op on already-wired pairs.
    """
    # Collision edges from DECLARED surfaces. Fewer than two surfaced siblings
    # -> no collision path (edges stays empty); the undeclared-surface fallback
    # below may still chain a same-assignee lane, so it must run regardless.
    surfaced = _surfaced_siblings(siblings)
    edges: list[tuple[object, object]] = []
    if len(surfaced) >= _MIN_COLLISION_PAIR:
        # Stable order across incremental re-runs: priority is set at creation,
        # sequence is append-only (existing siblings keep theirs).
        surfaced.sort(
            key=lambda s: (
                int(getattr(s, "priority", 2)),
                int(getattr(s, "sequence", 0)),
            )
        )
        surfaces = [
            DraftSurface(
                idx=i,
                priority=int(getattr(s, "priority", 2)),
                intends_to_touch=list(getattr(s, "intends_to_touch", None) or []),
                adds_migration=bool(getattr(s, "adds_migration", False)),
                touches_shared=bool(getattr(s, "touches_shared", False)),
                project_id=str(s.project_id) if s.project_id is not None else None,
            )
            for i, s in enumerate(surfaced)
        ]
        # cell_of / cell_capacity are advisory (contention warnings only); dev
        # tasks under one cell-task share the parent's cell, so a constant keeps
        # any warning attributable. Empty capacity -> no warnings emitted.
        plan = SequencingService().analyze(surfaces, lambda _idx: "", {})
        edges = [(surfaced[a].id, surfaced[b].id) for a, b in plan.edges]
    if edges:
        return edges

    # Undeclared-surface fallback: same-assignee same-repo siblings share a
    # working tree, so chain each (project, assignee) lane by (priority,
    # sequence) to avoid an out-of-order merge conflict. Same-assignee scoped so
    # cross-dev parallel work is untouched; the edge survives reassignment.
    # Only fires with zero collision edges; same stable sort -> re-runs only add.
    lanes: dict[tuple[str, object], list] = defaultdict(list)
    for s in siblings:
        proj = getattr(s, "project_id", None)
        owner = getattr(s, "assigned_to", None)
        if proj is not None and owner is not None:
            lanes[(str(proj), owner)].append(s)
    fallback: list[tuple[object, object]] = []
    for members in lanes.values():
        members.sort(
            key=lambda s: (
                int(getattr(s, "priority", 2)),
                int(getattr(s, "sequence", 0)),
            )
        )
        for prev, cur in pairwise(members):
            fallback.append((prev.id, cur.id))
    return fallback


# ---------------------------------------------------------------------------
# Multi-level sequencing — edge kinds 2 + 4 (cell-task wave chain + by-osmosis).
# Pure glue (no DB): the choreographer's TaskService wrappers walk the tree and
# hand the gathered objects to these helpers, which return the IDs to
# add_dependency. Pure so the edge logic is unit-testable without a database.
# ---------------------------------------------------------------------------


def cell_task_wave_chain_depends_on(
    predecessor_root_ids: list,
    cell_tasks_by_root: dict,
) -> list:
    """Kind 2: the cell-task IDs a new cell-task should depend on.

    For each predecessor root-subtask (the kind-1 wave-chain edges on the new
    cell-task's root-subtask — i.e. ``root.dependency_ids``), EVERY cell-task
    under it. The new cell-task waits for the whole previous wave's cell work so
    its branch carries the merged tail; a root-subtask may fan to several
    cell-tasks (different cells), so the previous wave's "cell-task" is a SET,
    not a single task. Idempotent by construction (``add_dependency`` dedupes);
    over-serializes safely (a predecessor cell-task already terminal is a no-op
    gate). A predecessor root with no cell tasks contributes nothing.
    """
    deps: list = []
    for rid in predecessor_root_ids:
        for ct in cell_tasks_by_root.get(rid, []):
            deps.append(getattr(ct, "id", ct))
    return deps


def by_osmosis_tail_dev_tasks(
    is_first_dev_task: bool,
    predecessor_dev_task_groups: list,
) -> list:
    """Kind 4: the tail dev-task IDs a new dev task should depend on.

    Only the FIRST dev task (``sequence == 0``) under a cell-task carries the
    by-osmosis edge — its branch is cut first and must carry the previous wave's
    fully-merged tail. Subsequent dev tasks inherit the tail via the kind-3
    collision DAG (they depend on earlier siblings) or share the cell branch's
    already-merged base, so they need no explicit edge. "Tail" = the
    highest-``sequence`` dev task under each predecessor cell-task; a
    predecessor with no dev tasks contributes no edge. Idempotent + best-effort
    (a tail already terminal is a no-op gate).
    """
    if not is_first_dev_task:
        return []
    tails: list = []
    for group in predecessor_dev_task_groups:
        if not group:
            continue
        tail = max(group, key=lambda t: int(getattr(t, "sequence", 0)))
        tails.append(getattr(tail, "id", tail))
    return tails
