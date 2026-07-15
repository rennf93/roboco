"""Pure derivation matrix for ``derive_pr_labels`` — no DB, no I/O."""

from __future__ import annotations

from uuid import uuid4

from roboco.foundation.identity import Team
from roboco.foundation.policy.pr_labels import (
    CONVENTIONS_PR_LABELS,
    derive_pr_labels,
)


def test_root_master_megatask_main_pm() -> None:
    # submit_root on a MegaTask root-subtask: root->master, main_pm, batch member.
    labels = derive_pr_labels(
        is_root_pr=True,
        task_team=Team.MAIN_PM,
        batch_id=uuid4(),
        has_children=True,
    )
    assert labels == ["to master", "root", "MegaTask", "main-pm"]


def test_root_master_main_pm_no_batch() -> None:
    labels = derive_pr_labels(
        is_root_pr=True,
        task_team=Team.MAIN_PM,
        batch_id=None,
        has_children=True,
    )
    assert labels == ["to master", "root", "main-pm"]


def test_cell_to_root_assembled() -> None:
    # submit_up: cell->root PR, base is the integration branch (not default).
    labels = derive_pr_labels(
        is_root_pr=False,
        task_team=Team.BACKEND,
        batch_id=None,
        has_children=True,
    )
    assert labels == ["to slave", "cell/backend"]


def test_leaf_dev_pr() -> None:
    labels = derive_pr_labels(
        is_root_pr=False,
        task_team=Team.FRONTEND,
        batch_id=None,
        has_children=False,
    )
    assert labels == ["to slave", "subtask/frontend"]


def test_freeform_pr_no_task() -> None:
    # task_id None: no team, no batch — just the tree + root flags.
    labels = derive_pr_labels(
        is_root_pr=False,
        task_team=None,
        batch_id=None,
        has_children=False,
    )
    assert labels == ["to slave"]


def test_freeform_root_pr_no_task() -> None:
    labels = derive_pr_labels(
        is_root_pr=True,
        task_team=None,
        batch_id=None,
        has_children=False,
    )
    assert labels == ["to master", "root"]


def test_accepts_string_team_value() -> None:
    # callers pass ORM enum members OR their .value strings (mirrors batch.py).
    labels = derive_pr_labels(
        is_root_pr=False,
        task_team="main_pm",
        batch_id=None,
        has_children=True,
    )
    assert labels == ["to slave", "main-pm"]


def test_conventions_pr_labels_static() -> None:
    assert CONVENTIONS_PR_LABELS == ["chore"]


def test_no_duplicates() -> None:
    # a shape that could repeat a label still yields a unique list.
    labels = derive_pr_labels(
        is_root_pr=True,
        task_team=Team.MAIN_PM,
        batch_id=uuid4(),
        has_children=True,
    )
    assert len(labels) == len(set(labels))
