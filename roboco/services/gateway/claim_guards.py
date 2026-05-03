"""Claim-time predicates restored from pre-gateway _helpers.py:124-204.

These guards run BEFORE any task-status mutation in the claim verbs
(``i_will_work_on``, ``i_will_plan``, ``claim_review``, ``claim_doc_task``).
Each predicate returns a rejection ``Envelope`` if it fires; ``None`` if it
passes. The first non-None return short-circuits the claim.

Pre-gateway location at commit 0c3d15a:
    roboco/mcp/tasks/handlers/_helpers.py:124-204
    roboco/mcp/tasks/handlers/claim.py:121-180  (sibling sequence)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from roboco.services.gateway.envelope import Envelope

if TYPE_CHECKING:
    from uuid import UUID

# Roles that may NOT claim a code task — pre-gateway _helpers.py:181-204.
_PM_ROLES: frozenset[str] = frozenset({"cell_pm", "main_pm"})

# Statuses that count as "still actively worked" — pre-gateway
# _helpers.py:check_blocking_tasks 134-152.
_ACTIVE_BLOCKING_STATUSES: frozenset[str] = frozenset(
    {"claimed", "in_progress", "verifying"}
)

# Terminal statuses that satisfy the sibling-sequence check —
# pre-gateway claim.py:153.
_TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "cancelled"})

# Allowed task_types per role for the claim verb. Mirrors the role-typed
# claim policy: developers do code-like work; QA reviews; documenters
# document.  PMs cannot claim code (see pm_cannot_execute_code).
_ROLE_TASK_TYPE_ALLOW: dict[str, frozenset[str]] = {
    "developer": frozenset({"code", "research", "design"}),
    "qa": frozenset(),  # QA never enters via i_will_work_on
    "documenter": frozenset(),  # Doc never enters via i_will_work_on
}


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


def pm_cannot_execute_code_guard(role: str, task_type: str) -> Envelope | None:
    """Refuse cell_pm/main_pm from claiming a code task.

    Pre-gateway: _helpers.py:_guard_pm_from_code_tasks 181-204.
    """
    if role not in _PM_ROLES:
        return None
    if task_type != "code":
        return None
    nice_role = role.replace("_", " ").title()
    return Envelope.not_authorized(
        message=(
            f"{nice_role} cannot claim code tasks. PMs coordinate, never execute code."
        ),
        remediate=(
            "PMs coordinate, never execute code. Delegate this to a "
            "developer in your cell via delegate(parent_task_id, "
            "title=..., description=..., assigned_to='be-dev-1', "
            "team='backend')."
        ),
    )


def role_typed_claim_guard(role: str, task_type: str) -> Envelope | None:
    """Refuse cross-role claim attempts (developer claiming doc/qa, etc).

    Pre-gateway: _helpers.py:_CLAIMABLE_STATUSES + the per-role status mapping
    at lines 144-150 plus the implicit task_type cohesion.  The pre-gateway
    code routed by status; here we route by ``task_type`` because the verbs
    already split by status (claim_review, claim_doc_task vs i_will_work_on).

    Only runs for non-PM roles; PMs route through pm_cannot_execute_code_guard
    and i_will_plan instead.
    """
    if role in _PM_ROLES:
        return None
    if role not in _ROLE_TASK_TYPE_ALLOW:
        # Unknown roles default to developer-like — silently allowed; the
        # service-layer enforcement catches misuse downstream.
        return None
    allowed = _ROLE_TASK_TYPE_ALLOW[role]
    if task_type in allowed:
        return None
    return Envelope.not_authorized(
        message=(f"role {role!r} cannot claim a {task_type!r} task via i_will_work_on"),
        remediate=(
            "developer claims code/research/design; qa uses claim_review; "
            "documenter uses claim_doc_task"
        ),
    )


def sibling_sequence_guard(target_task: Any, siblings: list[Any]) -> Envelope | None:
    """Refuse claim if any earlier-sequence sibling is non-terminal.

    Pre-gateway: claim.py:_validate_sibling_sequence 121-180.

    A task with sequence=N is blocked while any sibling with sequence<N is
    not in (completed, cancelled). Tasks without a parent_task_id (root) or
    sequence==0 (first in line) are always allowed.
    """
    parent_id = getattr(target_task, "parent_task_id", None)
    if parent_id is None:
        return None
    my_sequence = getattr(target_task, "sequence", 0) or 0
    if my_sequence == 0:
        return None
    for sib in siblings:
        if sib.id == target_task.id:
            continue
        sib_seq = getattr(sib, "sequence", 0) or 0
        sib_status = str(getattr(sib, "status", ""))
        if sib_seq < my_sequence and sib_status not in _TERMINAL_STATUSES:
            return Envelope.invalid_state(
                message=(
                    f"sequence {my_sequence} blocked: earlier sibling "
                    f"{sib.id} (sequence {sib_seq}) is in {sib_status}"
                ),
                remediate=(
                    f"wait for sibling {sib.id} (sequence {sib_seq}) to "
                    "reach completed/cancelled before claiming this task"
                ),
            )
    return None
