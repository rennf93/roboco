"""MegaTask (sequenced batch intake) — pure identity + git-exemption predicates.

A MegaTask is an *umbrella* task grouping N *root-subtasks*, each a real
coordination root with its own project / branch / PR. The umbrella is the
batch's identity — the board-review, CEO-approve, and Main-PM-coordinate unit —
and it does no git of its own: branchless, never assembles a PR, completes only
when every root-subtask is terminal.

These predicates are the single source of truth every layer consults (the
orchestrator's coordination check, the git-requirement gate, the branch-creation
short-circuit, the CEO-reject routing) so the umbrella's exemptions cannot drift
between sites. Inputs are typed ``object | None`` because callers pass either ORM
``UUID | None`` columns or ``dict.get(...)`` values.
"""

from __future__ import annotations


def is_batch_umbrella(
    *, batch_id: object | None, parent_task_id: object | None
) -> bool:
    """True for a MegaTask umbrella: carries a ``batch_id`` and is top-level.

    The umbrella and its root-subtasks share a ``batch_id``; only the umbrella is
    parentless (a root-subtask's ``parent_task_id`` is the umbrella), so parentage
    alone distinguishes them.
    """
    return batch_id is not None and parent_task_id is None


def is_batch_root_subtask(
    *, batch_id: object | None, parent_task_id: object | None
) -> bool:
    """True for a MegaTask root-subtask: a batch item under an umbrella."""
    return batch_id is not None and parent_task_id is not None


def is_branchless_coordination(
    *,
    project_id: object | None,
    product_id: object | None,
    batch_id: object | None = None,
    parent_task_id: object | None = None,
) -> bool:
    """True for a task that does no git of its own (no branch, no PR).

    Two shapes qualify: a product fan-out coordination root (no ``project_id``,
    carries a ``product_id``), and a MegaTask umbrella (``batch_id`` set,
    top-level). Both are Main-PM coordination points whose children do the git.
    """
    if project_id is None and product_id is not None:
        return True
    return is_batch_umbrella(batch_id=batch_id, parent_task_id=parent_task_id)
