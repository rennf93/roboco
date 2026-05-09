"""Public API for the canonical lifecycle spec."""

from roboco.lifecycle import spec
from roboco.lifecycle.spec import (
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
