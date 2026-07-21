"""Communications foundation — A2A urgency + notification rules.

Single source of truth for:
  - Notification sender allowlist (replaces _NOTIFY_ALLOWED_ROLES +
    NOTIFICATION_PERMISSIONS)
  - NotificationType -> requires_ack mapping (replaces 7 hand-set callsites in
    services/notification_delivery.py)
  - Priority enum (re-exports models.base.NotificationPriority for SQLAlchemy compat)
"""

from __future__ import annotations

from roboco.foundation.identity import Role
from roboco.models.base import NotificationPriority, NotificationType

# Re-export the enum so consumers can import a single name.
Priority = NotificationPriority


def parse_priority(
    raw_priority: object | None,
    legacy_urgent_flag: bool = False,
) -> NotificationPriority:
    """Resolve a Priority value from mixed-source A2A inputs.

    Precedence (A2A urgency tristate):
      1. ``raw_priority`` — string matching the Priority enum
         ("normal" | "high" | "urgent"). Unknown values fall back to NORMAL.
      2. ``legacy_urgent_flag`` — legacy bool from
         ``SendMessageConfiguration.urgent`` or ``metadata['urgent']``;
         True maps to URGENT.
      3. Default: NORMAL.

    Centralizing here keeps the tristate single-sourced and the call site
    branch-light.
    """
    if raw_priority is not None:
        try:
            return Priority(str(raw_priority))
        except ValueError:
            return Priority.NORMAL
    if legacy_urgent_flag:
        return Priority.URGENT
    return Priority.NORMAL


# Roles permitted to call notify() (PM/Board/CEO; auditor is silent observer).
NOTIFY_SENDER_ROLES: frozenset[Role] = frozenset(
    {
        Role.CELL_PM,
        Role.MAIN_PM,
        Role.PRODUCT_OWNER,
        Role.HEAD_MARKETING,
        Role.CEO,
    }
)


# Roles with no agent-comms surface at all: the human-only prompter/secretary
# (note + evidence only — they own dedicated chat pages, not agent A2A). A DM
# to either is a black hole — nothing on the other end can read or answer it.
# Auditor and pr_reviewer are NOT here: both now carry dm/read_a2a so the CEO
# can reach a mid-flight one and it can reply in-thread, but neither gains a
# peer-initiation surface — the auditor stays silent by the can_a2a_direct
# rule (agents_config.can_a2a_direct), the pr_reviewer stays scoped to its
# owning PM (_check_pr_reviewer_a2a). Canonical set consumed by both the dm()
# sender-side guard (services.gateway.content_actions) and the CEO's
# asymmetric target check (agents_config.can_a2a_direct) so the two never
# drift apart.
NO_COMMS_ROLES: frozenset[Role] = frozenset(
    {
        Role.PROMPTER,
        Role.SECRETARY,
    }
)


# NotificationType -> requires_ack mapping.
# Convention from spec §5.5:
#   - Action-required (CEO/PM acks needed) -> True
#   - Informational (no ack) -> False
ACK_REQUIRED_BY_TYPE: dict[NotificationType, bool] = {
    # Action-required: recipient must explicitly ack.
    NotificationType.PRIORITY_CHANGE: True,  # priority shift demands acknowledgment
    NotificationType.BLOCKER_ESCALATION: True,  # escalations need confirmation
    NotificationType.APPROVAL: True,  # approval requests must be answered
    NotificationType.ALERT: True,  # alerts demand attention
    # Informational: no ack required.
    NotificationType.TASK_ASSIGNMENT: False,  # claim flow proves receipt
    NotificationType.REVIEW_REQUEST: False,  # QA pickup proves receipt
    NotificationType.DOCUMENTATION_REQUEST: False,  # doc pickup proves receipt
    NotificationType.BROADCAST: False,  # one-to-many announcement
    NotificationType.KNOWLEDGE_SHARE: False,  # cross-agent learning, no ack
    NotificationType.MENTION: False,  # chat @mention, no ack
    NotificationType.A2A_REQUEST: False,  # request/reply lives at message layer
}
