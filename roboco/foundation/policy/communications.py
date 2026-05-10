"""Communications foundation — channel topology + A2A urgency + notification rules.

Single source of truth for:
  - Notification sender allowlist (replaces _NOTIFY_ALLOWED_ROLES +
    NOTIFICATION_PERMISSIONS)
  - NotificationType -> requires_ack mapping (replaces 7 hand-set callsites in
    services/notification_delivery.py)
  - Priority enum (re-exports models.base.NotificationPriority for SQLAlchemy compat)

Subsequent tasks add the CHANNELS catalog (channel topology). For now this
module owns the simple policy rules.
"""

from __future__ import annotations

from roboco.foundation.identity import Role
from roboco.models.base import NotificationPriority, NotificationType

# Re-export the enum so consumers can import a single name.
Priority = NotificationPriority


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
