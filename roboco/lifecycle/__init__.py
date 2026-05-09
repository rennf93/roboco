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

# Validate the spec at import time. A LifecycleSpecError here means the
# orchestrator container will fail to start — by design.
from roboco.lifecycle._validate import run_all_validators

run_all_validators()
