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

    Relies on the creation-time invariant ``is_valid_batch_shape`` that a
    ``batch_id``-bearing top-level task carries no project/product — so a real
    umbrella is genuinely branchless and a normal task cannot spoof the exemption
    by attaching a ``batch_id``.
    """
    if project_id is None and product_id is not None:
        return True
    return is_batch_umbrella(batch_id=batch_id, parent_task_id=parent_task_id)


def is_valid_batch_shape(
    *,
    batch_id: object | None,
    parent_task_id: object | None,
    project_id: object | None,
    product_id: object | None,
) -> bool:
    """Guardrail: a ``batch_id`` is only valid on a well-formed MegaTask member.

    A ``batch_id`` is permitted on exactly two shapes:

    - an **umbrella** (no ``parent_task_id``) — which must target NEITHER a
      project nor a product (it is branchless, grouping root-subtasks that each
      carry their own repo);
    - a **root-subtask** (has a ``parent_task_id``) — which must target exactly
      one of project / product (it does its own git).

    A task without a ``batch_id`` is unconstrained here (the normal targeting
    rule applies). Denying every other ``batch_id`` shape stops a normal task
    from spoofing the umbrella's branch-gate / no-PR exemption by attaching a
    stray ``batch_id`` — the reason :func:`is_branchless_coordination` can trust
    that a ``batch_id``-bearing top-level task really is a branchless umbrella.
    """
    if batch_id is None:
        return True
    if parent_task_id is None:  # umbrella
        return project_id is None and product_id is None
    # root-subtask: exactly one target
    return (project_id is None) != (product_id is None)
