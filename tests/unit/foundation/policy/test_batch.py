"""MegaTask identity + branchless-coordination predicates (the single source of
truth the orchestrator / git-gate / branch-creation / reject-routing consult)."""

from __future__ import annotations

from uuid import uuid4

from roboco.foundation.policy.batch import (
    is_batch_root_subtask,
    is_batch_umbrella,
    is_branchless_coordination,
    is_valid_batch_shape,
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


def test_valid_batch_shape_allows_umbrella_and_root_subtask() -> None:
    bid = uuid4()
    # umbrella: batch_id, no parent, NO target
    assert is_valid_batch_shape(
        batch_id=bid, parent_task_id=None, project_id=None, product_id=None
    )
    # root-subtask: batch_id, a parent, exactly one target (project)
    assert is_valid_batch_shape(
        batch_id=bid, parent_task_id=uuid4(), project_id=uuid4(), product_id=None
    )
    # root-subtask targeting a product instead is also well-formed
    assert is_valid_batch_shape(
        batch_id=bid, parent_task_id=uuid4(), project_id=None, product_id=uuid4()
    )
    # no batch_id → unconstrained here
    assert is_valid_batch_shape(
        batch_id=None, parent_task_id=None, project_id=uuid4(), product_id=None
    )


def test_valid_batch_shape_denies_stray_batch_id() -> None:
    bid = uuid4()
    # an umbrella-shaped task (batch_id, no parent) that ALSO targets a project —
    # the spoof that would otherwise get the branchless exemption — is refused.
    assert not is_valid_batch_shape(
        batch_id=bid, parent_task_id=None, project_id=uuid4(), product_id=None
    )
    # umbrella with a product is equally malformed
    assert not is_valid_batch_shape(
        batch_id=bid, parent_task_id=None, project_id=None, product_id=uuid4()
    )
    # a root-subtask (has a parent) with NO target is malformed
    assert not is_valid_batch_shape(
        batch_id=bid, parent_task_id=uuid4(), project_id=None, product_id=None
    )
    # a root-subtask with BOTH targets is malformed
    assert not is_valid_batch_shape(
        batch_id=bid, parent_task_id=uuid4(), project_id=uuid4(), product_id=uuid4()
    )
