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
    has_cell_projects: bool = False,
) -> bool:
    """True for a task that does no git of its own (no branch, no PR).

    Three shapes qualify: a product fan-out coordination root (no ``project_id``,
    carries a ``product_id``), an ad-hoc per-cell map coordination root (no
    ``project_id``, no ``product_id``, carries a ``task_cell_projects`` map), and a
    MegaTask umbrella (``batch_id`` set, top-level). All three are Main-PM
    coordination points whose children do the git — the map/product root still
    cuts ``feature/main_pm/{root}`` per repo and opens a root->master PR per repo,
    but the *claim branch gate* skips the single-branch requirement for it exactly
    as it does for a product root.

    Relies on the creation-time invariant ``is_valid_batch_shape`` that a
    ``batch_id``-bearing top-level task carries no project/product/map — so a real
    umbrella is genuinely branchless and a normal task cannot spoof the exemption
    by attaching a ``batch_id``.
    """
    if project_id is None and product_id is not None:
        return True
    if project_id is None and product_id is None and has_cell_projects:
        return True
    return is_batch_umbrella(batch_id=batch_id, parent_task_id=parent_task_id)


def is_valid_batch_shape(
    *,
    batch_id: object | None,
    parent_task_id: object | None,
    project_id: object | None,
    product_id: object | None,
    has_cell_projects: bool = False,
) -> bool:
    """Guardrail: a ``batch_id`` is only valid on a well-formed MegaTask member.

    A ``batch_id`` is permitted on exactly two shapes:

    - an **umbrella** (no ``parent_task_id``) — which must target NEITHER a
      project, a product, nor carry a cell map (it is branchless, grouping
      root-subtasks that each carry their own repo);
    - a **root-subtask** (has a ``parent_task_id``) — which must target exactly
      one of project / product / ad-hoc cell map (it does its own git).

    A task without a ``batch_id`` is unconstrained here (the normal targeting
    rule applies). Denying every other ``batch_id`` shape stops a normal task
    from spoofing the umbrella's branch-gate / no-PR exemption by attaching a
    stray ``batch_id`` — the reason :func:`is_branchless_coordination` can trust
    that a ``batch_id``-bearing top-level task really is a branchless umbrella.
    """
    if batch_id is None:
        return True
    targets = bool(project_id) + bool(product_id) + bool(has_cell_projects)
    if parent_task_id is None:  # umbrella
        return targets == 0
    # root-subtask: exactly one target
    return targets == 1
