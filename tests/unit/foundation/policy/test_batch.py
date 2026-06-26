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
    # genuinely unroutable (none of project / product / batch / cell-map) stays gated
    assert not is_branchless_coordination(project_id=None, product_id=None)


def test_branchless_coordination_covers_ad_hoc_cell_map_root() -> None:
    # An ad-hoc per-cell project map (no project_id, no product_id, carries a
    # cell map) is a coordination root exactly like a Product fan-out root: it
    # cuts feature/main_pm/{root} per repo and opens a root->master PR per repo,
    # so the claim branch gate skips the single-branch requirement. Holds both
    # for a MegaTask root-subtask and a standalone coordination root.
    assert is_branchless_coordination(
        project_id=None, product_id=None, has_cell_projects=True
    )
    assert is_branchless_coordination(
        project_id=None,
        product_id=None,
        batch_id=uuid4(),
        parent_task_id=uuid4(),
        has_cell_projects=True,
    )
    # a cell map is NOT branchless if a project_id is also set (then it's a normal
    # project task that happens to carry a stray map — gated, not exempt).
    assert not is_branchless_coordination(
        project_id=uuid4(), product_id=None, has_cell_projects=True
    )


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


def test_valid_batch_shape_allows_ad_hoc_cell_map_root_subtask() -> None:
    bid = uuid4()
    # a root-subtask carrying an ad-hoc per-cell map (no project, no product) is a
    # well-formed third targeting shape — exactly one of {project, product, map}.
    assert is_valid_batch_shape(
        batch_id=bid,
        parent_task_id=uuid4(),
        project_id=None,
        product_id=None,
        has_cell_projects=True,
    )
    # a non-batch task carrying a cell map is unconstrained here (the normal
    # targeting rule applies; the map is a coordination-root shape in its own
    # right, not a batch-only construct).
    assert is_valid_batch_shape(
        batch_id=None,
        parent_task_id=None,
        project_id=None,
        product_id=None,
        has_cell_projects=True,
    )


def test_valid_batch_shape_denies_cell_map_alongside_another_target() -> None:
    bid = uuid4()
    # umbrella with a cell map: an umbrella must target NEITHER — a map is a target.
    assert not is_valid_batch_shape(
        batch_id=bid,
        parent_task_id=None,
        project_id=None,
        product_id=None,
        has_cell_projects=True,
    )
    # root-subtask with BOTH a project and a cell map: two targets, malformed.
    assert not is_valid_batch_shape(
        batch_id=bid,
        parent_task_id=uuid4(),
        project_id=uuid4(),
        product_id=None,
        has_cell_projects=True,
    )
    # root-subtask with BOTH a product and a cell map: two targets, malformed.
    assert not is_valid_batch_shape(
        batch_id=bid,
        parent_task_id=uuid4(),
        project_id=None,
        product_id=uuid4(),
        has_cell_projects=True,
    )
