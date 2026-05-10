"""Re-export shim for roboco.lifecycle.spec → roboco.foundation.policy.lifecycle.

The canonical lifecycle spec was relocated to
``roboco.foundation.policy.lifecycle`` in Phase 4 housekeeping so it sits next
to its policy siblings (task_completeness, tracing, journaling, communications,
agent_loop). This module remains as an explicit re-export shim so existing
consumers (``from roboco.lifecycle.spec import X``) keep working while the
import sites are migrated in batches.

Removed in Phase 4 Task 8 once every consumer has been migrated. See
docs/superpowers/plans/2026-05-11-foundation-phase4-housekeeping.md.

New consumers must import from ``roboco.foundation.policy.lifecycle`` directly.
"""

from __future__ import annotations

# Re-export the canonical Role enum at its historical location so existing
# imports keep working. Role itself lives in roboco.foundation.identity; the
# foundation lifecycle module also re-exports it.
from roboco.foundation.identity import Role

# Every name the original spec.py exposed is re-exported verbatim below. The
# uppercase / public names are declared in ``__all__``; the underscore-prefixed
# names (``_ATOMIC_ACTIONS``, ``_INTENT_VERBS``, ``_KNOWN_UNMIGRATED_CONSUMERS``,
# ``_STATUS_TRANSITIONS``) remain intentionally private — they exist solely for
# the internal ``roboco.lifecycle._validate`` and ``roboco.lifecycle._generators``
# modules, which Tasks 9 and 10 move into foundation. The single ``noqa: F401``
# covers those private entries; ``__all__`` covers the public ones.
from roboco.foundation.policy.lifecycle import (  # noqa: F401
    _ATOMIC_ACTIONS,
    _INTENT_VERBS,
    _KNOWN_UNMIGRATED_CONSUMERS,
    _STATUS_TRANSITIONS,
    CLAIM_RULES,
    PRECONDITION_COMMITS,
    PRECONDITION_NO_PR,
    PRECONDITION_OWNERSHIP,
    PRECONDITION_PLAN,
    ROLE_TEAM_RULES,
    STATUS_GRAPH,
    UNMIGRATED,
    ActionSpec,
    Context,
    Decision,
    IntentSpec,
    Precondition,
    RejectionKind,
    Status,
    StatusTransition,
    TaskType,
    can_claim,
    can_invoke_action,
    can_invoke_intent,
    composed_actions_for,
    intents_for_role,
    status_after,
    valid_next_verbs,
)

__all__ = [
    "CLAIM_RULES",
    "PRECONDITION_COMMITS",
    "PRECONDITION_NO_PR",
    "PRECONDITION_OWNERSHIP",
    "PRECONDITION_PLAN",
    "ROLE_TEAM_RULES",
    "STATUS_GRAPH",
    "UNMIGRATED",
    "ActionSpec",
    "Context",
    "Decision",
    "IntentSpec",
    "Precondition",
    "RejectionKind",
    "Role",
    "Status",
    "StatusTransition",
    "TaskType",
    "can_claim",
    "can_invoke_action",
    "can_invoke_intent",
    "composed_actions_for",
    "intents_for_role",
    "status_after",
    "valid_next_verbs",
]
