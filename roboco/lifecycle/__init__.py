"""Public API for the canonical lifecycle spec."""

from roboco.lifecycle import spec
from roboco.lifecycle.spec import (
    CLAIM_RULES,
    PRECONDITION_COMMITS,
    PRECONDITION_NO_PR,
    PRECONDITION_OWNERSHIP,
    PRECONDITION_PLAN,
    ROLE_TEAM_RULES,
    STATUS_GRAPH,
    Context,
    Decision,
    Role,
    Status,
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
    "Context",
    "Decision",
    "Role",
    "Status",
    "TaskType",
    "can_claim",
    "can_invoke_action",
    "can_invoke_intent",
    "composed_actions_for",
    "intents_for_role",
    "spec",
    "status_after",
    "valid_next_verbs",
]

# Validation runs at import time of roboco.foundation.policy.lifecycle
# itself (see the trailing ``_run_all_lifecycle_validators()`` call in
# that module). Importing the canonical spec via the shim above therefore
# already validates it; no extra call needed here. This shim is removed
# in Phase 4 Task 8.
