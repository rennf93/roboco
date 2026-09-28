"""Per-dev lane ordering (Spec 3) — the transitive-dependents closure.

The per-dev dispatch barrier (``_blocked_by_earlier_lane_sibling``) and its
service-layer mirror (``has_earlier_incomplete_code_sibling``) must agree on
which siblings count as "earlier in the lane". Both exclude the siblings that
(transitively) depend on the task in question; this module is the single
implementation of that exclusion so the two sites cannot drift.
"""

from __future__ import annotations


def transitive_dependents(dep_map: dict[str, set[str]], task_id: str) -> set[str]:
    """Ids in ``dep_map`` that (transitively, within the map) depend on ``task_id``.

    A sibling that depends on this task is ordered AFTER it by the dependency
    guard, never before it in the lane. Counting such a sibling as "earlier"
    via the equal-sequence created-at tiebreak — which happens when a PM wires
    the dependency opposite to creation order — makes each guard wait on the
    other: the dependency guard holds the sibling on this task while the lane
    barrier holds this task on the sibling. A silent, permanent wedge
    (be-dev-2 starved for a day on exactly this shape, 2026-09-27). Dependency
    order outranks the created-at tiebreak.
    """
    dependents: set[str] = set()
    frontier = {task_id}
    while frontier:
        frontier = {
            sib_id
            for sib_id, deps in dep_map.items()
            if sib_id not in dependents and deps & frontier
        }
        dependents |= frontier
    return dependents
