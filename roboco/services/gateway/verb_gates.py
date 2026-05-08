"""Single declarative table mapping (role, task_status) -> valid verbs.

Used by:
  1. Envelope introspection - every envelope carries `valid_next_verbs`
     so agents know what's callable without trial-and-error.
  2. Role-check guards - instead of scattered `if role in PM_ROLES`
     checks, verbs ask `is_verb_allowed(role, verb, task)`.

Pre-2026-05-08, this logic lived in three places (role_config.py,
claim_guards.py, choreographer string constants) and could disagree
silently. This module is the single source of truth.
"""

from __future__ import annotations

from typing import Any

# Roles that PLAN and DELEGATE; never EXECUTE code.
_PM_ROLES: frozenset[str] = frozenset({"cell_pm", "main_pm"})

# Always-available verbs (don't depend on task state).
_ALWAYS_AVAILABLE: dict[str, frozenset[str]] = {
    "developer": frozenset({"i_am_idle", "give_me_work"}),
    "qa": frozenset({"i_am_idle", "give_me_work"}),
    "documenter": frozenset({"i_am_idle", "give_me_work"}),
    "cell_pm": frozenset({"i_am_idle", "give_me_work", "triage"}),
    "main_pm": frozenset({"i_am_idle", "give_me_work", "triage_all"}),
    "product_owner": frozenset({"i_am_idle", "triage"}),
    "head_marketing": frozenset({"i_am_idle", "triage"}),
    "auditor": frozenset({"i_am_idle", "triage"}),
}

# (role, status) -> tuple of additional verbs valid in that state.
# Verbs in _ALWAYS_AVAILABLE are added on top.
_STATE_VERBS: dict[tuple[str, str], tuple[str, ...]] = {
    # Developer
    ("developer", "pending"): ("i_will_work_on", "unclaim"),
    ("developer", "needs_revision"): ("i_will_work_on", "unclaim"),
    ("developer", "claimed"): (
        "commit",
        "submit_for_qa",
        "i_am_done",
        "i_am_blocked",
        "unclaim",
    ),
    ("developer", "in_progress"): (
        "commit",
        "submit_for_qa",
        "i_am_done",
        "i_am_blocked",
        "unclaim",
    ),
    ("developer", "verifying"): (
        "commit",
        "submit_for_qa",
        "i_am_done",
        "i_am_blocked",
    ),
    ("developer", "blocked"): ("resume", "i_am_blocked", "unclaim"),
    ("developer", "paused"): ("resume",),
    # QA
    ("qa", "awaiting_qa"): ("claim_review",),
    ("qa", "claimed"): ("pass", "fail", "unclaim"),
    ("qa", "in_progress"): ("pass", "fail", "unclaim"),
    # Documenter
    ("documenter", "awaiting_documentation"): ("claim_doc_task",),
    ("documenter", "claimed"): ("commit", "i_documented", "unclaim"),
    ("documenter", "in_progress"): ("commit", "i_documented", "unclaim"),
    # Cell PM - PMs PLAN code-typed parents; they don't EXECUTE.
    ("cell_pm", "pending"): ("i_will_plan", "unclaim"),
    ("cell_pm", "claimed"): ("delegate", "unblock", "complete", "escalate_up"),
    ("cell_pm", "in_progress"): ("delegate", "unblock", "complete", "escalate_up"),
    ("cell_pm", "blocked"): ("unblock", "resume"),
    ("cell_pm", "awaiting_pm_review"): ("complete", "submit_up", "escalate_up"),
    # Main PM
    ("main_pm", "pending"): ("i_will_plan", "unclaim"),
    ("main_pm", "claimed"): (
        "delegate",
        "unblock",
        "complete",
        "escalate_to_ceo",
    ),
    ("main_pm", "in_progress"): (
        "delegate",
        "unblock",
        "complete",
        "escalate_to_ceo",
    ),
    ("main_pm", "blocked"): ("unblock", "resume"),
    ("main_pm", "awaiting_pm_review"): ("complete", "escalate_to_ceo"),
}


def valid_next_verbs(role: str, task: Any) -> list[str]:
    """Return the verbs `role` can usefully call on `task` right now.

    `task` must expose `.status` (str) and `.task_type` (str). For a
    PM-role caller against a code-typed task in pending status the
    result includes `i_will_plan` (PMs plan any task type), but never
    `i_will_work_on` (which is the developer execution verb).

    Returns [] for an unknown role. Returns the always-available
    subset for an unknown status.
    """
    always = _ALWAYS_AVAILABLE.get(role)
    if always is None:
        return []
    status = str(getattr(task, "status", ""))
    state_verbs = _STATE_VERBS.get((role, status), ())
    return sorted(set(always) | set(state_verbs))


def is_verb_allowed(role: str, verb: str, task: Any) -> bool:
    """Quick check: can `role` call `verb` on `task` right now?"""
    return verb in valid_next_verbs(role, task)
