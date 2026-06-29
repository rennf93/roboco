"""SequencingService — the deterministic collision-sequencing analyzer.

The unit tests pin each rule in isolation; the golden test asserts the analyzer
reproduces the CEO's own hand-sequencing of the 11-item guard-core-app batch
(the effort that motivated the feature, and whose hand-coordination deadlocked
the Main PM): S6 alone last, the R1/R3/R4 migration chain, R2/R3/S8 serialized on
the shared threat service, and S1/S2/S7 in one parallel wave.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from roboco.foundation.policy.sequencing.models import (
    DraftSurface,
    SequencingError,
)
from roboco.services.sequencing import (
    SequencingService,
    by_osmosis_tail_dev_tasks,
    cell_task_wave_chain_depends_on,
    dev_task_collision_edges,
)


def _backend(_i: int) -> str:
    return "backend"


def _frontend(_i: int) -> str:
    return "frontend"


def _wave_of(waves: list[list[int]], idx: int) -> int:
    return next(w for w, wave in enumerate(waves) if idx in wave)


# ---------------------------------------------------------------------------
# Per-rule unit tests
# ---------------------------------------------------------------------------


def test_disjoint_surfaces_no_edges() -> None:
    s = [
        DraftSurface(0, 1, ["a/x.py"], False, False),
        DraftSurface(1, 1, ["b/y.py"], False, False),
    ]
    plan = SequencingService().analyze(s, _backend, {"backend": 2})
    assert plan.edges == []
    assert plan.waves == [[0, 1]]


def test_file_overlap_serializes_more_important_first() -> None:
    # idx 1 has the lower priority NUMBER (more important) → it runs first.
    s = [
        DraftSurface(0, 2, ["svc/threats.py"], False, False),
        DraftSurface(1, 1, ["svc/threats.py"], False, False),
    ]
    plan = SequencingService().analyze(s, _backend, {"backend": 2})
    assert (1, 0) in plan.edges  # more-important runs first, the other waits


def test_migrations_form_serial_chain() -> None:
    s = [
        DraftSurface(0, 1, ["a.py"], True, False),
        DraftSurface(1, 1, ["b.py"], True, False),
        DraftSurface(2, 1, ["c.py"], True, False),
    ]
    plan = SequencingService().analyze(s, _backend, {"backend": 2})
    assert (0, 1) in plan.edges  # no two migrations run in parallel
    assert (1, 2) in plan.edges


def test_touches_shared_runs_last() -> None:
    s = [
        DraftSurface(0, 1, ["page/a.tsx"], False, False),
        DraftSurface(1, 1, ["page/b.tsx"], False, False),
        DraftSurface(2, 1, ["page/a.tsx", "components/shared.tsx"], False, True),
    ]
    plan = SequencingService().analyze(s, _frontend, {"frontend": 2})
    assert plan.waves[-1] == [2]  # the shared task is the final wave


def test_cycle_is_rejected() -> None:
    with pytest.raises(SequencingError):
        SequencingService()._toposort([(0, 1), (1, 0)], 2)


def test_existence_check_rejects_out_of_range_edge() -> None:
    with pytest.raises(SequencingError):
        SequencingService()._toposort([(0, 5)], 2)


def test_shared_migration_chains_after_non_shared_no_cycle() -> None:
    # Regression: a draft that is BOTH touches_shared AND adds_migration,
    # overlapping a non-shared migration draft on the same file, used to fabricate
    # a cycle — rule 2 (migration chain) emitted shared->non-shared while rule 3
    # (shared-last) emitted non-shared->shared. The migration chain is now
    # shared-last-aware, so the shared draft is ordered LAST and there is no cycle.
    s = [
        DraftSurface(0, 1, ["svc/threats.py"], True, True),  # shared migration
        DraftSurface(1, 1, ["svc/threats.py"], True, False),  # non-shared migration
    ]
    plan = SequencingService().analyze(s, _backend, {"backend": 2})
    assert plan.waves == [[1], [0]]  # non-shared first, shared migration last


def test_cross_project_surfaces_do_not_collide() -> None:
    # A MegaTask spans repos that don't share a working tree — two migrations in
    # different projects run in PARALLEL, and a coincidentally-equal path across
    # repos is not a collision.
    s = [
        DraftSurface(0, 1, ["alembic/x.py"], True, False, project_id="proj-a"),
        DraftSurface(1, 1, ["alembic/x.py"], True, False, project_id="proj-b"),
    ]
    plan = SequencingService().analyze(s, _backend, {"backend": 2})
    assert plan.waves == [[0, 1]]  # independent repos → one parallel wave


def test_cell_contention_warns_not_serializes() -> None:
    s = [DraftSurface(i, 1, [f"page/{i}.tsx"], False, False) for i in range(3)]
    plan = SequencingService().analyze(s, _frontend, {"frontend": 2})
    assert plan.edges == []  # contention never adds an edge
    assert any("frontend" in w for w in plan.warnings)


# ---------------------------------------------------------------------------
# Golden test — reproduce the CEO's 4-wave plan for the 11-item batch
# ---------------------------------------------------------------------------

# Index map for the guard-core-app items (see obs: wave-based sequencing).
R1, R2, R3, R4 = 0, 1, 2, 3
S1, S2, S3, S5, S7, S8, S6 = 4, 5, 6, 7, 8, 9, 10


def _guard_core_app_batch() -> list[DraftSurface]:
    # (idx, priority, intends_to_touch, adds_migration, touches_shared)
    return [
        DraftSurface(R1, 1, ["be/services/project_service.py"], True, False),
        DraftSurface(R2, 1, ["be/services/threats_service.py"], False, False),
        DraftSurface(
            R3,
            1,
            ["be/services/threats_service.py", "be/services/behavioral_service.py"],
            True,
            False,
        ),
        DraftSurface(R4, 1, ["fe/app/rules/page.tsx"], True, False),
        DraftSurface(S1, 1, ["fe/app/metrics/page.tsx"], False, False),
        DraftSurface(S2, 1, ["fe/app/settings/page.tsx"], False, False),
        DraftSurface(S3, 1, ["be/services/dashboard_service.py"], False, False),
        DraftSurface(S5, 1, ["be/services/audit_service.py"], False, False),
        DraftSurface(S7, 1, ["fe/app/threats/page.tsx"], False, False),
        DraftSurface(S8, 1, ["be/services/threats_service.py"], False, False),
        DraftSurface(S6, 1, ["fe/components/", "fe/app/"], False, True),
    ]


def _cell_of(idx: int) -> str:
    return "backend" if idx in {R1, R2, R3, S3, S5, S8} else "frontend"


def test_golden_reproduces_ceo_waves() -> None:
    plan = SequencingService().analyze(
        _guard_core_app_batch(), _cell_of, {"backend": 2, "frontend": 2}
    )

    # EXACT partition — the CEO's own 4-wave hand-sequencing, locked. The bar is
    # "reproduce my exact waves or it's not done", so assert the full partition,
    # not just the properties below.
    assert plan.waves == [
        sorted([R1, R2, S1, S2, S3, S5, S7]),  # wave 1: everything unblocked
        [R3],  # wave 2: the shared+migration hinge
        sorted([R4, S8]),  # wave 3: after R3
        [S6],  # wave 4: the shared UI-consistency pass, alone, last
    ]

    # The properties that partition expresses (kept as documentation of WHY):
    # S6 (the shared UI-consistency pass) runs alone, last.
    assert plan.waves[-1] == [S6]
    # R1/R3/R4 form a serial migration chain (no concurrent Alembic heads).
    assert (R1, R3) in plan.edges
    assert (R3, R4) in plan.edges
    # R2/R3/S8 serialize on the shared threats service surface.
    assert (R2, R3) in plan.edges
    assert (R3, S8) in plan.edges
    # The page-isolated frontend work (S1/S2/S7) lands in one parallel wave.
    assert _wave_of(plan.waves, S1) == _wave_of(plan.waves, S2)
    assert _wave_of(plan.waves, S2) == _wave_of(plan.waves, S7)


# ---------------------------------------------------------------------------
# dev_task_collision_edges — the dev-task collision DAG (edge kind 3).
# Pure glue: a parent's surfaced siblings -> (depends_on_id, task_id) pairs.
# Wraps SequencingService so the choreographer can wire the DAG via add_dependency
# at cell-PM dev-delegation time (incremental, idempotent). See the multi-level
# sequencing design doc.
# ---------------------------------------------------------------------------


@dataclass
class _Sib:
    """Minimal sibling shape — the attributes dev_task_collision_edges reads."""

    id: object
    priority: int = 2
    sequence: int = 0
    intends_to_touch: list[str] = field(default_factory=list)
    adds_migration: bool = False
    touches_shared: bool = False
    project_id: str | None = "proj-backend"
    assigned_to: object | None = None


def _edge_set(pairs: list[tuple[object, object]]) -> set[tuple[object, object]]:
    return set(pairs)


def test_dev_collision_disjoint_surfaces_are_parallel() -> None:
    # Same project, disjoint files → no edge (the two dev tasks run together).
    a, b = (
        _Sib(uuid4(), sequence=0, intends_to_touch=["a.py"]),
        _Sib(uuid4(), sequence=1, intends_to_touch=["b.py"]),
    )
    assert dev_task_collision_edges([a, b]) == []


def test_dev_collision_overlap_serializes_more_important_first() -> None:
    # Both touch a.py → serialized; lower priority NUMBER runs first.
    first = _Sib(uuid4(), priority=1, sequence=0, intends_to_touch=["a.py"])
    second = _Sib(uuid4(), priority=2, sequence=1, intends_to_touch=["a.py"])
    edges = dev_task_collision_edges([second, first])  # passed out of order
    assert edges == [
        (first.id, second.id)
    ]  # first depends-on nothing; second depends-on first


def test_dev_collision_overlap_equal_priority_uses_sequence() -> None:
    # Equal priority → lower sequence runs first (stable across incremental re-runs).
    t1 = _Sib(uuid4(), sequence=0, intends_to_touch=["a.py"])
    t3 = _Sib(uuid4(), sequence=1, intends_to_touch=["a.py"])
    assert dev_task_collision_edges([t1, t3]) == [(t1.id, t3.id)]


def test_dev_collision_skips_unsurfaced_siblings() -> None:
    # A sibling with no surface is parallel to everything (no edges to/from it).
    surfaced = _Sib(uuid4(), sequence=0, intends_to_touch=["a.py"])
    bare = _Sib(uuid4(), sequence=1)  # no intends_to_touch / migration / shared
    other = _Sib(uuid4(), sequence=2, intends_to_touch=["a.py"])
    edges = _edge_set(dev_task_collision_edges([surfaced, bare, other]))
    assert edges == {(surfaced.id, other.id)}
    assert bare.id not in {e[0] for e in edges} and bare.id not in {e[1] for e in edges}


def test_dev_collision_skips_different_project() -> None:
    # Same path, different repo → no collision (different codebase).
    a = _Sib(uuid4(), sequence=0, intends_to_touch=["a.py"], project_id="proj-be")
    b = _Sib(uuid4(), sequence=1, intends_to_touch=["a.py"], project_id="proj-fe")
    assert dev_task_collision_edges([a, b]) == []


def test_dev_collision_migration_chain_serializes() -> None:
    # Two migration-adders in the same repo chain serially (alembic single-head).
    m1 = _Sib(uuid4(), sequence=0, adds_migration=True, intends_to_touch=["m1.py"])
    m2 = _Sib(uuid4(), sequence=1, adds_migration=True, intends_to_touch=["m2.py"])
    assert dev_task_collision_edges([m1, m2]) == [(m1.id, m2.id)]


def test_dev_collision_shared_last_after_non_shared_overlap() -> None:
    # A touches_shared edit runs after a non-shared task that overlaps it.
    base = _Sib(uuid4(), sequence=0, intends_to_touch=["svc/shared.py"])
    shared = _Sib(
        uuid4(), sequence=1, touches_shared=True, intends_to_touch=["svc/shared.py"]
    )
    assert dev_task_collision_edges([base, shared]) == [(base.id, shared.id)]


def test_dev_collision_single_surfaced_sibling_no_edge() -> None:
    solo = _Sib(uuid4(), sequence=0, intends_to_touch=["a.py"])
    assert dev_task_collision_edges([solo]) == []


def test_dev_collision_returns_depends_on_first_pairs() -> None:
    # Contract: each pair is (depends_on_id, task_id) — task depends-on depends_on.
    first = _Sib(uuid4(), sequence=0, intends_to_touch=["a.py"])
    second = _Sib(uuid4(), sequence=1, intends_to_touch=["a.py"])
    [(dep, task)] = dev_task_collision_edges([first, second])
    assert dep == first.id
    assert task == second.id


# ---------------------------------------------------------------------------
# dev_task_collision_edges — undeclared-surface fallback: same-assignee
# same-repo siblings chain by (priority, sequence); cross-dev stays parallel.
# ---------------------------------------------------------------------------


def test_dev_collision_fallback_chains_same_assignee_no_surface() -> None:
    # Same dev, same repo, no declared surface -> chain by sequence.
    a = _Sib(uuid4(), sequence=0, assigned_to="be-dev-1")
    b = _Sib(uuid4(), sequence=1, assigned_to="be-dev-1")
    assert dev_task_collision_edges([a, b]) == [(a.id, b.id)]


def test_dev_collision_fallback_skips_cross_assignee() -> None:
    # Two different devs on the same repo, no surface -> parallel.
    a = _Sib(uuid4(), sequence=0, assigned_to="be-dev-1")
    b = _Sib(uuid4(), sequence=1, assigned_to="be-dev-2")
    assert dev_task_collision_edges([a, b]) == []


def test_dev_collision_fallback_skips_unassigned() -> None:
    # No assignee -> can't determine a per-dev lane -> skip.
    a = _Sib(uuid4(), sequence=0)
    b = _Sib(uuid4(), sequence=1)
    assert dev_task_collision_edges([a, b]) == []


def test_dev_collision_fallback_skips_different_project() -> None:
    # Same dev, different repos -> no shared working tree -> no chain.
    a = _Sib(uuid4(), sequence=0, assigned_to="be-dev-1", project_id="proj-be")
    b = _Sib(uuid4(), sequence=1, assigned_to="be-dev-1", project_id="proj-fe")
    assert dev_task_collision_edges([a, b]) == []


def test_dev_collision_fallback_does_not_override_collision_edges() -> None:
    # Declared overlapping surface -> collision edge wins; no fallback chain.
    a = _Sib(uuid4(), sequence=0, assigned_to="be-dev-1", intends_to_touch=["a.py"])
    b = _Sib(uuid4(), sequence=1, assigned_to="be-dev-1", intends_to_touch=["a.py"])
    assert dev_task_collision_edges([a, b]) == [(a.id, b.id)]


def test_dev_collision_fallback_orders_by_priority_then_sequence() -> None:
    # Mixed priority/sequence -> chain in (priority, sequence) ascending order.
    p2s2 = _Sib(uuid4(), priority=2, sequence=2, assigned_to="be-dev-1")
    p1s5 = _Sib(uuid4(), priority=1, sequence=5, assigned_to="be-dev-1")
    p1s1 = _Sib(uuid4(), priority=1, sequence=1, assigned_to="be-dev-1")
    edges = dev_task_collision_edges([p2s2, p1s5, p1s1])  # passed out of order
    assert edges == [(p1s1.id, p1s5.id), (p1s5.id, p2s2.id)]


def test_dev_collision_fallback_single_sibling_no_edge() -> None:
    # A chain needs >= 2 same-assignee same-project siblings.
    solo = _Sib(uuid4(), sequence=0, assigned_to="be-dev-1")
    assert dev_task_collision_edges([solo]) == []


def test_dev_collision_fallback_idempotent_on_rerun() -> None:
    # Deterministic sort -> two calls return the same edge list.
    a = _Sib(uuid4(), sequence=0, assigned_to="be-dev-1")
    b = _Sib(uuid4(), sequence=1, assigned_to="be-dev-1")
    assert dev_task_collision_edges([a, b]) == dev_task_collision_edges([a, b])


# ---------------------------------------------------------------------------
# cell_task_wave_chain_depends_on — the cell-task wave chain (edge kind 2).
# Pure glue: a new cell-task under root-subtask UT_n depends on every cell-task
# under every root-subtask UT_n itself depends on (the kind-1 wave-chain edges).
# ---------------------------------------------------------------------------


def test_wave_chain_collects_all_predecessor_cell_tasks() -> None:
    # Two predecessor root-subtasks: one fans to two cell-tasks, the other to one.
    ct_a1, ct_a2, ct_b1 = _Sib(uuid4()), _Sib(uuid4()), _Sib(uuid4())
    root_a, root_b = object(), object()
    deps = cell_task_wave_chain_depends_on(
        [root_a, root_b], {root_a: [ct_a1, ct_a2], root_b: [ct_b1]}
    )
    assert set(deps) == {ct_a1.id, ct_a2.id, ct_b1.id}


def test_wave_chain_empty_when_no_predecessor_roots() -> None:
    assert cell_task_wave_chain_depends_on([], {}) == []


def test_wave_chain_skips_root_with_no_cell_tasks() -> None:
    root = object()
    assert cell_task_wave_chain_depends_on([root], {root: []}) == []
    # A predecessor root absent from the map contributes nothing (no KeyError).
    assert cell_task_wave_chain_depends_on([object()], {}) == []


def test_wave_chain_preserves_predecessor_order() -> None:
    # Edges are appended in predecessor-root order then cell-task order — stable
    # so add_dependency (which dedupes) sees a deterministic sequence.
    ct_a, ct_b = _Sib(uuid4()), _Sib(uuid4())
    root_a, root_b = object(), object()
    deps = cell_task_wave_chain_depends_on(
        [root_a, root_b], {root_a: [ct_a], root_b: [ct_b]}
    )
    assert deps == [ct_a.id, ct_b.id]


# ---------------------------------------------------------------------------
# by_osmosis_tail_dev_tasks — the by-osmosis edge (edge kind 4).
# Pure glue: the first dev task of a cell-task depends on each predecessor
# cell-task's tail (highest-sequence) dev task. Only sequence 0 carries it.
# ---------------------------------------------------------------------------


def test_by_osmosis_skips_non_first_dev_task() -> None:
    tail = _Sib(uuid4(), sequence=2)
    # is_first_dev_task=False -> no edges, regardless of predecessor groups.
    assert by_osmosis_tail_dev_tasks(False, [[tail]]) == []


def test_by_osmosis_picks_max_sequence_per_group() -> None:
    t0 = _Sib(uuid4(), sequence=0)
    t1 = _Sib(uuid4(), sequence=1)
    t2 = _Sib(uuid4(), sequence=2)
    assert by_osmosis_tail_dev_tasks(True, [[t0, t1, t2]]) == [t2.id]


def test_by_osmosis_one_tail_per_predecessor_group() -> None:
    a_tail = _Sib(uuid4(), sequence=2)
    b_tail = _Sib(uuid4(), sequence=4)
    a_group = [_Sib(uuid4(), sequence=0), _Sib(uuid4(), sequence=1), a_tail]
    b_group = [_Sib(uuid4(), sequence=3), b_tail]
    assert by_osmosis_tail_dev_tasks(True, [a_group, b_group]) == [a_tail.id, b_tail.id]


def test_by_osmosis_skips_empty_predecessor_group() -> None:
    # A predecessor cell-task with no dev tasks contributes no edge.
    tail = _Sib(uuid4(), sequence=1)
    assert by_osmosis_tail_dev_tasks(True, [[], [tail]]) == [tail.id]


def test_by_osmosis_no_edges_when_no_predecessor_groups() -> None:
    assert by_osmosis_tail_dev_tasks(True, []) == []
