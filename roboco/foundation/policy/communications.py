"""Communications foundation — A2A urgency + notification rules.

Single source of truth for:
  - Notification sender allowlist (replaces _NOTIFY_ALLOWED_ROLES +
    NOTIFICATION_PERMISSIONS)
  - NotificationType -> requires_ack mapping (replaces 7 hand-set callsites in
    services/notification_delivery.py)
  - Priority enum (re-exports models.base.NotificationPriority for SQLAlchemy compat)
  - Re-escalation backoff schedule (pure; consumed by
    services/notification_delivery.py's sweep)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

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


# Doubling-schedule ceiling: no re-escalation ever waits longer than this
# between attempts, however high `notification_max_reescalations` is set.
REESCALATION_INTERVAL_CAP_SECONDS = 24 * 3600

ReescalationDecision = Literal["due", "wait", "capped"]


@dataclass(frozen=True)
class ReescalationPolicy:
    """The two `settings.notification_*` knobs the backoff schedule reads,
    bundled so `reescalation_decision` stays under the 5-arg lint ceiling —
    they always travel together (both come straight from `settings`),
    unlike the per-row facts (`now`/`expires_at`/`count`/`last_reescalated_at`)."""

    base_seconds: int
    max_reescalations: int


def reescalation_decision(
    *,
    now: datetime,
    expires_at: datetime,
    count: int,
    last_reescalated_at: datetime | None,
    policy: ReescalationPolicy,
) -> ReescalationDecision:
    """Pure per-notification re-escalation backoff decision.

    Schedule: the first re-escalation (``count == 0``) is due at
    ``expires_at`` itself. Each one after that doubles the wait from
    ``policy.base_seconds`` (1h, 2h, 4h, 8h, ...) measured from
    ``last_reescalated_at``, capped at ``REESCALATION_INTERVAL_CAP_SECONDS``.
    Past ``policy.max_reescalations``, always "capped" — the caller must
    never act on it again. A legacy row with no backoff state reads as
    ``count=0``, preserving the original first-fire-at-expiry behaviour.
    """
    if count >= policy.max_reescalations:
        return "capped"
    if count == 0:
        due_at = expires_at
    else:
        interval = min(
            policy.base_seconds * (2 ** (count - 1)), REESCALATION_INTERVAL_CAP_SECONDS
        )
        due_at = (last_reescalated_at or expires_at) + timedelta(seconds=interval)
    return "due" if now >= due_at else "wait"
