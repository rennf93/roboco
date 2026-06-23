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
        migs = sorted(
            (s for s in surfaces if s.adds_migration),
            key=lambda s: (s.priority, s.idx),
        )
        return [(prev.idx, cur.idx) for prev, cur in pairwise(migs)]

    # --- rule 3: shared-last -------------------------------------------------
    def _shared_last_edges(self, surfaces: list[DraftSurface]) -> list[tuple[int, int]]:
        edges: list[tuple[int, int]] = []
        for s in surfaces:
            if not s.touches_shared:
                continue
            for other in surfaces:
                if other.idx == s.idx or other.touches_shared:
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
