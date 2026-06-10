"""Concurrency-invariant claim-time predicates.

These guards run BEFORE any task-status mutation in the claim verbs
(``i_will_work_on``, ``i_will_plan``, ``claim_review``, ``claim_doc_task``).
Each predicate returns a rejection ``Envelope`` if it fires; ``None`` if it
passes. The first non-None return short-circuits the claim.

Scope: only system-level concurrency invariants the lifecycle spec does
NOT model live here. Role/state/task_type checks (the former
``role_typed_claim_guard`` and ``pm_cannot_execute_code_guard``) now route
through ``spec.can_invoke_action``'s CLAIM_RULES + ``ActionSpec
.allowed_task_types`` and have been deleted.

Pre-gateway location at commit 0c3d15a:
    roboco/mcp/tasks/handlers/_helpers.py:124-204
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from roboco.services.gateway.envelope import Envelope

if TYPE_CHECKING:
    from uuid import UUID

# Statuses that count as "still actively worked" — pre-gateway
# _helpers.py:check_blocking_tasks 134-152.
_ACTIVE_BLOCKING_STATUSES: frozenset[str] = frozenset(
    {"claimed", "in_progress", "verifying"}
)


def already_active_guard(
    in_progress_tasks: list[Any], target_task_id: UUID
) -> Envelope | None:
    """Refuse claim if agent has any in_progress task other than this one.

    Pre-gateway: _helpers.py:check_blocking_tasks 134-152.
    """
    blocking = [
        t
        for t in in_progress_tasks
        if str(t.status) in _ACTIVE_BLOCKING_STATUSES and t.id != target_task_id
    ]
    if not blocking:
        return None
    blocker = blocking[0]
    return Envelope.invalid_state(
        message=(
            f"You have a {blocker.status} task ({blocker.id}); "
            "finish or pause it before claiming new work."
        ),
        remediate=(
            f"finish or pause {blocker.id} first via i_am_done(...) or i_am_idle()"
        ),
    )


def paused_tasks_guard(paused_tasks: list[Any]) -> Envelope | None:
    """Refuse claim if agent has any paused tasks.

    Pre-gateway: _helpers.py:check_paused_tasks 154-165.
    """
    if not paused_tasks:
        return None
    paused = paused_tasks[0]
    return Envelope.invalid_state(
        message=(
            f"You have {len(paused_tasks)} paused task(s); resume before "
            "claiming new work."
        ),
        remediate=(
            f"resume {paused.id} (call i_will_work_on again) before starting new work"
        ),
    )


def unmet_dependency_guard(
    target_task: Any, unmet_dependency_ids: list[UUID]
) -> Envelope | None:
    """Refuse claim while the task has non-terminal dependencies.

    A task may not be claimed until every task it ``depends_on`` reaches a
    terminal state (completed/cancelled). This holds the pre-assigned dev
    that arrives via the claim verb directly — the dependency filter on the
    unassigned claim pool (``list_pending(filter_by_dependencies=True)``)
    never sees a pre-assigned task. ``unmet_dependency_ids`` is resolved by
    the caller (it requires a DB read) so this predicate stays pure.
    """
    if not unmet_dependency_ids:
        return None
    blockers = ", ".join(str(dep_id) for dep_id in unmet_dependency_ids)
    return Envelope.invalid_state(
        message=(
            f"task {target_task.id} depends on unfinished work; "
            f"{len(unmet_dependency_ids)} dependency(ies) not yet "
            "completed/cancelled."
        ),
        remediate=(
            f"wait for dependency task(s) {blockers} to reach "
            "completed/cancelled before claiming this task"
        ),
    )
