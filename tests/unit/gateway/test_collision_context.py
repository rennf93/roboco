"""W5 collision map: the pure ``build_collision_context`` builder.

Pins the truth table — no parent → None, no surfaced siblings → None,
file-overlap sibling shown, both-migration shown without file overlap,
shared-only-without-overlap NOT shown, declared-vs-actual drift computed,
and the caps respected. Pure (no DB, no IO): duck-typed task/sibling rows.

Also covers the two choreographer-level wrappers around it
(``_gate_collision_evidence`` / ``_collision_context_for``): a raise from the
builder itself — not just the sibling fetch — must still degrade to
``None``/omitted, never break the caller (``claim_gate_review`` evidence /
the planning briefing).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.gateway.choreographer.collision import (
    COLLISION_GLOB_CAP,
    COLLISION_SIBLING_CAP,
    build_collision_context,
)


def _task(
    *,
    parent_task_id: str | None = "p1",
    project_id: str = "proj",
    intends_to_touch: list[str] | None = None,
    adds_migration: bool = False,
    touches_shared: bool = False,
    priority: int = 2,
    sequence: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=str(uuid4()),
        parent_task_id=parent_task_id,
        project_id=project_id,
        intends_to_touch=intends_to_touch,
        adds_migration=adds_migration,
        touches_shared=touches_shared,
        priority=priority,
        sequence=sequence,
    )


def _sib(
    *,
    project_id: str = "proj",
    intends_to_touch: list[str] | None = None,
    adds_migration: bool = False,
    touches_shared: bool = False,
    priority: int = 2,
    sequence: int = 1,
    status: str = "in_progress",
    branch_name: str | None = "feature/x",
    pr_number: int | None = 7,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=str(uuid4()),
        project_id=project_id,
        intends_to_touch=intends_to_touch,
        adds_migration=adds_migration,
        touches_shared=touches_shared,
        priority=priority,
        sequence=sequence,
        status=status,
        branch_name=branch_name,
        pr_number=pr_number,
        title="sibling",
    )


def test_no_parent_returns_none() -> None:
    task = _task(parent_task_id=None, intends_to_touch=["a.py"])
    assert build_collision_context(task=task, siblings=[_sib()]) is None


def test_no_surfaced_siblings_returns_none() -> None:
    task = _task(intends_to_touch=["a.py"])
    # sibling with no collision surface at all
    assert (
        build_collision_context(task=task, siblings=[_sib(intends_to_touch=None)])
        is None
    )


def test_file_overlap_sibling_shown() -> None:
    task = _task(intends_to_touch=["roboco/services/git.py"])
    sib = _sib(intends_to_touch=["roboco/services/git.py", "roboco/services/x.py"])
    ctx = build_collision_context(task=task, siblings=[sib])
    assert ctx is not None
    assert len(ctx) == 1
    assert "roboco/services/git.py" in ctx[0]["overlap"]


def test_no_overlap_no_migration_not_shown() -> None:
    task = _task(intends_to_touch=["roboco/a.py"])
    sib = _sib(intends_to_touch=["roboco/b.py"])
    assert build_collision_context(task=task, siblings=[sib]) is None


def test_both_migration_shown_without_file_overlap() -> None:
    task = _task(intends_to_touch=["roboco/a.py"], adds_migration=True)
    sib = _sib(intends_to_touch=["roboco/b.py"], adds_migration=True)
    ctx = build_collision_context(task=task, siblings=[sib])
    assert ctx is not None
    assert ctx[0]["adds_migration"] is True
    assert ctx[0]["overlap"] == []


def test_one_migration_only_not_shown() -> None:
    # migration-chain needs BOTH adders; one alone is parallel.
    task = _task(intends_to_touch=["roboco/a.py"], adds_migration=True)
    sib = _sib(intends_to_touch=["roboco/b.py"], adds_migration=False)
    assert build_collision_context(task=task, siblings=[sib]) is None


def test_shared_only_without_overlap_not_shown() -> None:
    # touches_shared alone is too broad; it rides the file-overlap path.
    task = _task(intends_to_touch=["roboco/a.py"], touches_shared=True)
    sib = _sib(intends_to_touch=["roboco/b.py"])
    assert build_collision_context(task=task, siblings=[sib]) is None


def test_shared_with_overlap_shown_and_flagged() -> None:
    task = _task(intends_to_touch=["roboco/a.py"], touches_shared=True)
    sib = _sib(intends_to_touch=["roboco/a.py"], touches_shared=True)
    ctx = build_collision_context(task=task, siblings=[sib])
    assert ctx is not None
    assert ctx[0]["touches_shared"] is True


def test_cross_project_sibling_not_shown() -> None:
    # collisions are repo-scoped; a sibling in another repo can't collide.
    task = _task(intends_to_touch=["a.py"], project_id="proj")
    sib = _sib(intends_to_touch=["a.py"], project_id="other")
    assert build_collision_context(task=task, siblings=[sib]) is None


def test_self_excluded_from_siblings() -> None:
    task = _task(intends_to_touch=["a.py"])
    self_sib = _sib(intends_to_touch=["a.py"])
    self_sib.id = task.id
    assert build_collision_context(task=task, siblings=[self_sib]) is None


def test_drift_undeclared_computed() -> None:
    task = _task(intends_to_touch=["roboco/a.py"])
    sib = _sib(intends_to_touch=["roboco/a.py"])
    ctx = build_collision_context(
        task=task,
        siblings=[sib],
        actual_files=["roboco/a.py", "roboco/secret.py"],
    )
    assert ctx is not None
    assert ctx[0]["undeclared"] == ["roboco/secret.py"]


def test_drift_omitted_without_actual_files() -> None:
    task = _task(intends_to_touch=["roboco/a.py"])
    sib = _sib(intends_to_touch=["roboco/a.py"])
    ctx = build_collision_context(task=task, siblings=[sib])
    assert ctx is not None
    assert "undeclared" not in ctx[0]


def test_sibling_cap() -> None:
    task = _task(intends_to_touch=["a.py"])
    sibs = [
        _sib(intends_to_touch=["a.py"], sequence=i)
        for i in range(COLLISION_SIBLING_CAP + 5)
    ]
    ctx = build_collision_context(task=task, siblings=sibs)
    assert ctx is not None
    assert len(ctx) == COLLISION_SIBLING_CAP


def test_glob_cap() -> None:
    task = _task(intends_to_touch=[f"f{i}.py" for i in range(COLLISION_GLOB_CAP + 5)])
    sib = _sib(intends_to_touch=[f"f{i}.py" for i in range(COLLISION_GLOB_CAP + 5)])
    ctx = build_collision_context(task=task, siblings=[sib])
    assert ctx is not None
    assert len(ctx[0]["overlap"]) <= COLLISION_GLOB_CAP
    assert len(ctx[0]["intends_to_touch"]) <= COLLISION_GLOB_CAP


def test_sort_key_orders_by_priority_then_sequence() -> None:
    task = _task(intends_to_touch=["a.py"])
    low_prio = _sib(intends_to_touch=["a.py"], priority=3, sequence=0)
    high_prio = _sib(intends_to_touch=["a.py"], priority=1, sequence=5)
    mid = _sib(intends_to_touch=["a.py"], priority=2, sequence=1)
    ctx = build_collision_context(task=task, siblings=[low_prio, high_prio, mid])
    assert ctx is not None
    assert [e["sequence"] for e in ctx] == [5, 1, 0]


def _make_choreographer(*, task_service: AsyncMock) -> Choreographer:
    return Choreographer(
        ChoreographerDeps(
            task=task_service,
            work_session=AsyncMock(),
            git=AsyncMock(),
            a2a=AsyncMock(),
            journal=AsyncMock(),
            audit=AsyncMock(),
            evidence_repo=AsyncMock(),
        )
    )


class TestGateCollisionEvidenceDegradesOnBuilderFailure:
    """``_gate_collision_evidence`` (claim_gate_review evidence)."""

    @pytest.mark.asyncio
    async def test_builder_raise_omits_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        t = _task()
        task_service = AsyncMock()
        task_service.get_subtasks.return_value = [_sib()]
        c = _make_choreographer(task_service=task_service)
        monkeypatch.setattr(
            "roboco.services.gateway.choreographer.pr_gate.build_collision_context",
            lambda **_kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        result = await c._gate_collision_evidence(t, [])

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_raise_still_omits_block(self) -> None:
        t = _task()
        task_service = AsyncMock()
        task_service.get_subtasks.side_effect = RuntimeError("db down")
        c = _make_choreographer(task_service=task_service)

        result = await c._gate_collision_evidence(t, [])

        assert result is None


class TestCollisionContextForDegradesOnBuilderFailure:
    """``_collision_context_for`` (the planning/claim briefing helper)."""

    @pytest.mark.asyncio
    async def test_builder_raise_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        t = _task()
        task_service = AsyncMock()
        task_service.get_subtasks.return_value = [_sib()]
        c = _make_choreographer(task_service=task_service)
        monkeypatch.setattr(
            "roboco.services.gateway.choreographer._impl.build_collision_context",
            lambda **_kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        assert await c._collision_context_for(t) is None

    @pytest.mark.asyncio
    async def test_fetch_raise_returns_none(self) -> None:
        t = _task()
        task_service = AsyncMock()
        task_service.get_subtasks.side_effect = RuntimeError("db down")
        c = _make_choreographer(task_service=task_service)

        assert await c._collision_context_for(t) is None


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-q"])
