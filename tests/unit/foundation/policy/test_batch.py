"""MegaTask identity + branchless-coordination predicates (the single source of
truth the orchestrator / git-gate / branch-creation / reject-routing consult)."""

from __future__ import annotations

from uuid import uuid4

from roboco.foundation.policy.batch import (
    is_batch_root_subtask,
    is_batch_umbrella,
    is_branchless_coordination,
)


def test_umbrella_is_batch_id_set_and_top_level() -> None:
    bid = uuid4()
    assert is_batch_umbrella(batch_id=bid, parent_task_id=None)
    assert not is_batch_umbrella(batch_id=bid, parent_task_id=uuid4())  # a child
    assert not is_batch_umbrella(batch_id=None, parent_task_id=None)  # a normal root


def test_root_subtask_is_batch_id_set_and_parented() -> None:
    bid = uuid4()
    assert is_batch_root_subtask(batch_id=bid, parent_task_id=uuid4())
    assert not is_batch_root_subtask(batch_id=bid, parent_task_id=None)  # the umbrella
    assert not is_batch_root_subtask(batch_id=None, parent_task_id=uuid4())


def test_branchless_coordination_covers_product_root_and_umbrella() -> None:
    # product fan-out coordination root: no project, carries a product
    assert is_branchless_coordination(project_id=None, product_id=uuid4())
    # MegaTask umbrella: batch_id set, top-level
    assert is_branchless_coordination(
        project_id=None, product_id=None, batch_id=uuid4(), parent_task_id=None
    )


def test_branchless_coordination_excludes_normal_and_root_subtasks() -> None:
    # a normal project task does its own git
    assert not is_branchless_coordination(project_id=uuid4(), product_id=None)
    # a root-subtask (has a parent + a project) is NOT the branchless umbrella
    assert not is_branchless_coordination(
        project_id=uuid4(),
        product_id=None,
        batch_id=uuid4(),
        parent_task_id=uuid4(),
    )
    # genuinely unroutable (none of project / product / batch) stays gated
    assert not is_branchless_coordination(project_id=None, product_id=None)
