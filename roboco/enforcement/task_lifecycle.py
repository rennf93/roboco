"""Backwards-compatibility shim — view of roboco.foundation.policy.lifecycle.

The canonical lifecycle / permissions tables live in
:mod:`roboco.foundation.policy.lifecycle`. This module is a thin view
over that data for legacy callers that still import
``VALID_TRANSITIONS`` or ``ROLE_RESTRICTED_TRANSITIONS`` by name. New
code should import from :mod:`roboco.foundation.policy.lifecycle`
directly.

Symbols still owned by this module (not yet absorbed into the spec):
    * Git-workflow gates (:class:`GitContext`,
      :class:`GitRequirementError`, :func:`validate_git_requirements`,
      :func:`check_parallel_completion`).
    * SLA tables (:data:`ROLE_STATE_SLA_KEYS`, :func:`sla_seconds_for`).

Everything else is derived from the spec.
"""

from __future__ import annotations

from dataclasses import dataclass

from roboco.config import settings
from roboco.exceptions import TaskLifecycleError
from roboco.foundation.policy.lifecycle import _STATUS_TRANSITIONS, STATUS_GRAPH, Status

__all__ = [
    "ROLE_RESTRICTED_TRANSITIONS",
    "ROLE_STATE_SLA_KEYS",
    "VALID_TRANSITIONS",
    "GitContext",
    "GitRequirementError",
    "TaskLifecycleError",
    "can_agent_transition",
    "check_parallel_completion",
    "get_valid_transitions",
    "is_active_state",
    "is_terminal_state",
    "is_waiting_state",
    "sla_seconds_for",
    "validate_git_requirements",
    "validate_task_transition",
]


# =============================================================================
# SPEC-DERIVED LEGACY VIEWS
# =============================================================================
#
# `VALID_TRANSITIONS` mirrors the legacy `dict[str, list[str]]` shape
# (str keys, str-list values) so existing callers keep working without
# import churn. The data flows from `STATUS_GRAPH` (which is the
# canonical source of truth in `roboco.foundation.policy.lifecycle`).
#
# `_LEGACY_OPERATIONAL_EDGES` are transitions the runtime exercises today
# that the spec has not yet absorbed (unclaim / reaper sweep / PM-direct
# completes / parallel-doc-PR developer trigger). They are out-of-scope
# for the canonical spec — the gateway intent verbs do not compose them
# — but TaskService still calls `_validate_and_set_status` for these
# edges. They live here, clearly fenced, until those callers are
# rewritten to dispatch via the spec; at that point this constant
# becomes empty and the file collapses to a pure view.
#
# `ROLE_RESTRICTED_TRANSITIONS` is the subset of `_STATUS_TRANSITIONS`
# that explicitly pin a role gate at the transition level. Transitions
# whose `role_constraint is None` defer to the action's `allowed_roles`
# table (handled by the gateway, not the legacy enforcement helpers),
# so they are NOT part of this view. Legacy operational edges are not
# role-gated here either — their authority lives in the calling
# service method.

_LEGACY_OPERATIONAL_EDGES: dict[Status, frozenset[Status]] = {
    # Voluntary unclaim + reaper sweep (TaskService.unclaim*,
    # AgentOrchestrator._reconcile_with_service): an agent or the
    # reaper releases a task back to the pool.
    Status.CLAIMED: frozenset({Status.PENDING}),
    Status.IN_PROGRESS: frozenset(
        {
            Status.PENDING,  # reaper sweep / voluntary unclaim
            Status.COMPLETED,  # PM completing their own (non-PR) task
            # QA acting via direct assignment (no awaiting_qa hop):
            Status.AWAITING_DOCUMENTATION,
            Status.NEEDS_REVISION,
        }
    ),
    # Self-fail out of verifying (QA / PM only — role gate enforced
    # in ROLE_RESTRICTED_TRANSITIONS below). The canonical exit is submit_qa
    # -> awaiting_qa -> (qa_pass) -> awaiting_documentation; a direct
    # verifying->awaiting_documentation edge would bypass the QA review hop.
    Status.VERIFYING: frozenset({Status.NEEDS_REVISION}),
    # QA can park a task as blocked while waiting on dev clarification.
    Status.AWAITING_QA: frozenset({Status.BLOCKED}),
    # PM claim + PM reject path on review queue.
    Status.AWAITING_PM_REVIEW: frozenset({Status.CLAIMED, Status.NEEDS_REVISION}),
    # Re-entry from revision back into active dev work (without re-claim).
    Status.NEEDS_REVISION: frozenset({Status.IN_PROGRESS}),
}

# Role pins for legacy operational edges. Same shape as the spec-derived
# ROLE_RESTRICTED_TRANSITIONS table; merged in below.
_LEGACY_ROLE_GATES: dict[tuple[Status, Status], tuple[str, ...]] = {
    # Direct QA when assigned without going through awaiting_qa.
    (Status.IN_PROGRESS, Status.AWAITING_DOCUMENTATION): ("qa",),
    (Status.IN_PROGRESS, Status.NEEDS_REVISION): ("qa",),
    # PM completing their own work.
    (Status.IN_PROGRESS, Status.COMPLETED): (
        "cell_pm",
        "head_marketing",
        "main_pm",
        "product_owner",
    ),
    # PM claim of review queue.
    (Status.AWAITING_PM_REVIEW, Status.CLAIMED): (
        "cell_pm",
        "head_marketing",
        "main_pm",
        "product_owner",
    ),
    # PM reject back to dev (needs_revision).
    (Status.AWAITING_PM_REVIEW, Status.NEEDS_REVISION): (
        "cell_pm",
        "head_marketing",
        "main_pm",
        "product_owner",
    ),
    # Verifying self-fail — QA + PM only, dev cannot self-route to revision.
    (Status.VERIFYING, Status.NEEDS_REVISION): (
        "cell_pm",
        "head_marketing",
        "main_pm",
        "product_owner",
        "qa",
    ),
    # Parallel doc/PR completion: spec pins this to DOCUMENTER (the
    # canonical "docs_complete" trigger), but the runtime also calls it
    # with role="developer" via TaskService.mark_pr_created when the dev
    # creates the PR last. Both are legitimate triggers in the
    # parallel-completion phase; override the spec gate here to keep the
    # runtime path open until the developer trigger is folded into the
    # spec as a sibling action.
    (Status.AWAITING_DOCUMENTATION, Status.AWAITING_PM_REVIEW): (
        "developer",
        "documenter",
    ),
}


def _build_valid_transitions() -> dict[str, list[str]]:
    merged: dict[str, set[str]] = {
        src.value: {t.value for t in STATUS_GRAPH.get(src, frozenset())}
        for src in Status
    }
    for src, extras in _LEGACY_OPERATIONAL_EDGES.items():
        merged[src.value].update(t.value for t in extras)
    return {src: sorted(targets) for src, targets in merged.items()}


def _build_role_restricted_transitions() -> dict[tuple[str, str], tuple[str, ...]]:
    out: dict[tuple[str, str], tuple[str, ...]] = {
        (t.source.value, t.target.value): tuple(
            sorted(r.value for r in t.role_constraint)
        )
        for t in _STATUS_TRANSITIONS
        if t.role_constraint is not None
    }
    for (src, tgt), roles in _LEGACY_ROLE_GATES.items():
        # UNION (not overwrite): legacy gates ADD operational roles to an edge;
        # overwriting silently dropped any spec-derived roles that share the
        # same edge. pr_review_done puts pr_reviewer on (in_progress, completed)
        # — the same edge the legacy PM-self-complete gate pins — so an
        # overwrite erased pr_reviewer and the review task could never complete.
        existing = out.get((src.value, tgt.value), ())
        out[(src.value, tgt.value)] = tuple(sorted(set(existing) | set(roles)))
    return out


VALID_TRANSITIONS: dict[str, list[str]] = _build_valid_transitions()

ROLE_RESTRICTED_TRANSITIONS: dict[tuple[str, str], tuple[str, ...]] = (
    _build_role_restricted_transitions()
)


# =============================================================================
# LIFECYCLE PREDICATES (DERIVED FROM SPEC)
# =============================================================================


def validate_task_transition(
    current_status: str,
    target_status: str,
    agent_role: str | None = None,
) -> bool:
    """Validate a task state transition against the spec-derived view.

    Mirrors the legacy contract: returns ``True`` on success, raises
    :class:`TaskLifecycleError` on rejection. Role gates are checked
    only against transitions explicitly pinned in
    :data:`ROLE_RESTRICTED_TRANSITIONS` — gates that derive from the
    triggering action's ``allowed_roles`` are enforced upstream by the
    gateway / choreographer, not here.
    """
    valid = VALID_TRANSITIONS.get(current_status, [])

    if target_status not in valid:
        raise TaskLifecycleError(
            current_status=current_status,
            target_status=target_status,
            valid_transitions=valid,
        )

    if agent_role:
        allowed_roles = ROLE_RESTRICTED_TRANSITIONS.get((current_status, target_status))
        if allowed_roles and agent_role not in allowed_roles:
            raise TaskLifecycleError(
                current_status=current_status,
                target_status=target_status,
                message=(
                    f"Role '{agent_role}' cannot perform this transition. "
                    f"Allowed roles: {list(allowed_roles)}"
                ),
            )

    return True


def can_agent_transition(
    current_status: str,
    target_status: str,
    agent_role: str,
) -> bool:
    """Non-raising variant of :func:`validate_task_transition`."""
    try:
        return validate_task_transition(current_status, target_status, agent_role)
    except TaskLifecycleError:
        return False


def get_valid_transitions(current_status: str) -> list[str]:
    """Return the list of valid target statuses from ``current_status``."""
    return VALID_TRANSITIONS.get(current_status, [])


def is_terminal_state(status: str) -> bool:
    """True if ``status`` is a terminal state (no outgoing transitions)."""
    return status in ("completed", "cancelled")


def is_waiting_state(status: str) -> bool:
    """True if ``status`` parks an agent waiting on someone else."""
    return status in (
        "blocked",
        "paused",
        "awaiting_qa",
        "awaiting_documentation",
        "awaiting_pr_review",
        "awaiting_pm_review",
        "awaiting_ceo_approval",
    )


def is_active_state(status: str) -> bool:
    """True if ``status`` is an active working state."""
    return status in ("claimed", "in_progress", "verifying", "needs_revision")


# =============================================================================
# TIME-IN-STATE SLAs (soft guardrail for stuck-task sweep)
# =============================================================================
#
# Keys are (role, status) tuples. Values are setting names on
# :data:`roboco.config.settings`. The orchestrator's stuck-task sweep
# reads this table and auto-escalates / auto-releases tasks that exceed
# the SLA for their current owner's role. Absence from the table means
# "no per-role SLA; only the generic 10-minute pending-task sweep
# applies." Defaults live in :class:`roboco.config.Settings` so they're
# overridable per environment without a code change.

ROLE_STATE_SLA_KEYS: dict[tuple[str, str], str] = {
    ("developer", "in_progress"): "agent_sla_developer_in_progress",
    ("developer", "verifying"): "agent_sla_developer_verifying",
    ("qa", "claimed"): "agent_sla_qa_claimed",
    ("documenter", "claimed"): "agent_sla_documenter_claimed",
    ("cell_pm", "claimed"): "agent_sla_cell_pm_claimed",
}


def sla_seconds_for(role: str | None, status: str) -> int | None:
    """Return the configured SLA for ``(role, status)``, or ``None``."""
    if not role:
        return None
    key = ROLE_STATE_SLA_KEYS.get((role, status))
    if key is None:
        return None
    value = getattr(settings, key, None)
    return int(value) if isinstance(value, int) else None


# =============================================================================
# GIT INTEGRATION VALIDATION
# =============================================================================


class GitRequirementError(Exception):
    """Raised when git requirements are not met for a transition."""

    def __init__(
        self,
        transition: tuple[str, str],
        requirement: str,
        message: str | None = None,
    ) -> None:
        self.transition = transition
        self.requirement = requirement
        self.message = message or f"Git requirement not met: {requirement}"
        super().__init__(self.message)


@dataclass
class GitContext:
    """Git-related task state for validation (all tasks follow git workflow)."""

    docs_complete: bool = False
    pr_created: bool = False
    pr_number: int | None = None
    branch_name: str | None = None
    # A coordination/fan-out task carries a product (a cell->project map) but no
    # repo of its own, so it does no git work and never gets a branch. It must
    # be able to transition claimed->in_progress without one. Mirrors
    # `_is_coordination_task` in the orchestrator and the `_ensure_branch_for_task`
    # short-circuit in TaskService.
    is_coordination: bool = False
    # An inbound external-PR review task reviews someone else's PR read-only; it
    # does no git work of its own and never gets a branch, so it is exempt from
    # the claimed->in_progress branch gate (same rationale as is_coordination).
    is_external_review: bool = False
    # A MegaTask umbrella assembles no PR of its own (each root-subtask carries
    # its own), so it escalates to the CEO with no pr_number — exempt from the
    # awaiting_pm_review->awaiting_ceo_approval pr_number gate. A product fan-out
    # root is is_coordination too but DOES get a pr_number (via submit_root), so
    # this is umbrella-specific, not all-coordination.
    is_umbrella: bool = False


def validate_git_requirements(
    current_status: str,
    target_status: str,
    git_ctx: GitContext | None = None,
) -> bool:
    """Validate git-related preconditions for a task transition.

    Enforced gates:

    * ``awaiting_documentation -> awaiting_pm_review`` requires both
      ``docs_complete=True`` and ``pr_created=True`` (the documenter and
      developer work in parallel; both must finish).
    * ``awaiting_pm_review -> awaiting_ceo_approval`` requires
      ``pr_number`` to be set (the PR must exist for CEO review).
    * ``claimed -> in_progress`` requires ``branch_name`` (auto-created
      on claim).

    Passing ``git_ctx=None`` short-circuits the check (no validation).
    """
    if git_ctx is None:
        return True

    transition = (current_status, target_status)
    _check_doc_phase_gate(transition, git_ctx)
    _check_ceo_escalation_gate(transition, git_ctx)
    _check_claim_branch_gate(transition, git_ctx)
    return True


def _check_doc_phase_gate(transition: tuple[str, str], git_ctx: GitContext) -> None:
    """awaiting_documentation -> awaiting_pm_review needs docs_complete + pr_created."""
    if transition != ("awaiting_documentation", "awaiting_pm_review"):
        return
    if not git_ctx.docs_complete:
        raise GitRequirementError(
            transition=transition,
            requirement="docs_complete",
            message=(
                "Blocked: documentation not yet complete. "
                "In awaiting_documentation, Documenter and Developer work in "
                "parallel. Wait for Documenter to call i_documented()."
            ),
        )
    if not git_ctx.pr_created:
        raise GitRequirementError(
            transition=transition,
            requirement="pr_created",
            message=(
                "Blocked: PR not yet created. "
                "In awaiting_documentation, Documenter and Developer work in "
                "parallel. Wait for the Developer's submit_for_qa(task_id) "
                "call to complete — the choreographer opens the PR as part "
                "of that transition."
            ),
        )


def _check_ceo_escalation_gate(
    transition: tuple[str, str], git_ctx: GitContext
) -> None:
    """awaiting_pm_review -> awaiting_ceo_approval needs a recorded pr_number,
    EXCEPT a MegaTask umbrella, which is branchless and assembles no PR."""
    if (
        transition == ("awaiting_pm_review", "awaiting_ceo_approval")
        and git_ctx.pr_number is None
        and not git_ctx.is_umbrella
    ):
        raise GitRequirementError(
            transition=transition,
            requirement="pr_number",
            message=(
                "Cannot escalate to CEO: task has no PR number recorded. "
                "This may indicate the task reached PM review without going through "
                "the git workflow. Check task.pr_number field."
            ),
        )


def _check_claim_branch_gate(transition: tuple[str, str], git_ctx: GitContext) -> None:
    """claimed -> in_progress needs a branch (coordination/external review exempt)."""
    if (
        transition == ("claimed", "in_progress")
        and not git_ctx.branch_name
        and not git_ctx.is_coordination
        and not git_ctx.is_external_review
    ):
        raise GitRequirementError(
            transition=transition,
            requirement="branch_name",
            message=(
                "Cannot start work: no branch assigned to this task. "
                "Branches are auto-created on claim. If missing, either "
                "re-claim the task or check if parent task needs claiming first."
            ),
        )


def check_parallel_completion(docs_complete: bool, pr_created: bool) -> bool:
    """Return True iff the parallel doc+PR phase is fully complete."""
    return docs_complete and pr_created
