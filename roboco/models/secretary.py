"""Secretary domain models — the CEO's chief-of-staff directives.

A directive is an action the Secretary takes on the CEO's command. Low-risk
kinds execute directly; high-impact kinds (the gate list) bounce back for the
CEO's explicit confirmation before they run.
"""

from __future__ import annotations

from enum import StrEnum


class DirectiveKind(StrEnum):
    """What the Secretary was told to do."""

    RELAY_MESSAGE = "relay_message"  # direct: post a CEO-dictated message
    UPDATE_CHARTER = "update_charter"  # gated: edit company goals
    CONTROL_TASK = "control_task"  # gated: start / cancel / override a task
    APPROVE_PITCH = "approve_pitch"  # gated: approve a pitch (provision + spend)
    ANNOUNCE = "announce"  # gated: post to #announcements


class DirectiveStatus(StrEnum):
    """Lifecycle of a directive."""

    PENDING = "pending"  # gated, awaiting CEO confirmation
    EXECUTED = "executed"  # ran successfully
    REJECTED = "rejected"  # CEO declined
    FAILED = "failed"  # ran but errored


# High-impact kinds bounce back for the CEO's explicit confirmation; everything
# else the Secretary executes directly on the CEO's command.
GATED_KINDS: frozenset[DirectiveKind] = frozenset(
    {
        DirectiveKind.UPDATE_CHARTER,
        DirectiveKind.CONTROL_TASK,
        DirectiveKind.APPROVE_PITCH,
        DirectiveKind.ANNOUNCE,
    }
)
