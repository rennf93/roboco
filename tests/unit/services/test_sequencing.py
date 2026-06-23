"""SequencingService — the deterministic collision-sequencing analyzer.

The unit tests pin each rule in isolation; the golden test asserts the analyzer
reproduces the CEO's own hand-sequencing of the 11-item guard-core-app batch
(the effort that motivated the feature, and whose hand-coordination deadlocked
the Main PM): S6 alone last, the R1/R3/R4 migration chain, R2/R3/S8 serialized on
the shared threat service, and S1/S2/S7 in one parallel wave.
"""

from __future__ import annotations

import pytest
from roboco.foundation.policy.sequencing.models import (
    DraftSurface,
    SequencingError,
)
from roboco.services.sequencing import SequencingService


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
