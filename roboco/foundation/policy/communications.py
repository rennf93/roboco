"""Communications foundation — channel topology + A2A urgency + notification rules.

Single source of truth for:
  - Notification sender allowlist (replaces _NOTIFY_ALLOWED_ROLES +
    NOTIFICATION_PERMISSIONS)
  - NotificationType -> requires_ack mapping (replaces 7 hand-set callsites in
    services/notification_delivery.py)
  - Priority enum (re-exports models.base.NotificationPriority for SQLAlchemy compat)
  - CHANNELS catalog: channel topology (slug -> role-keyed read/write/silent)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from roboco.foundation.identity import Role, Team
from roboco.models.base import ChannelType, NotificationPriority, NotificationType

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


# =============================================================================
# CHANNELS catalog
# =============================================================================
#
# The single source of truth for channel topology. Each entry binds a slug
# (the durable channel identifier) to the roles permitted to read/write,
# the roles that read silently (no write), the channel display type, and
# whether the channel is read-only for roles outside `write_roles` (the
# spec §5.5 "announcements" pattern).
#
# This catalog is the canonical replacement for the legacy data in
# `roboco.agents_config.CHANNEL_ACCESS` (agent-id keyed) and the display
# metadata in `roboco.seeds.initial_data.DEFAULT_CHANNELS`. Subsequent
# foundation tasks derive both from this dict.


@dataclass(frozen=True)
class ChannelSpec:
    slug: str
    description: str
    type: ChannelType
    read_roles: frozenset[Role]
    write_roles: frozenset[Role]
    silent_roles: frozenset[Role] = field(default_factory=frozenset)
    read_only_for_others: bool = False
    # When set, cell-member roles (DEVELOPER/QA/DOCUMENTER/CELL_PM) are
    # constrained to agents on this team. Cross-cell roles (MAIN_PM, AUDITOR,
    # CEO, board) are NOT filtered — they participate regardless of team.
    # Required for cell channels (backend-cell etc.) to derive correct slug
    # membership without leaking other cells' members.
    team_scope: Team | None = None


# -- Helper sets (DRY across multiple channels) -------------------------------

# Roles present in every cell channel: cell members + main-pm.
_CELL_READ: frozenset[Role] = frozenset(
    {
        Role.DEVELOPER,
        Role.QA,
        Role.DOCUMENTER,
        Role.CELL_PM,
        Role.MAIN_PM,
    }
)
_CELL_WRITE: frozenset[Role] = _CELL_READ

# Roles present in cross-cell role channels (dev-all/qa-all read-side):
# every cell member role plus the PM coordination layer.
_CROSS_CELL_READ_ALL: frozenset[Role] = frozenset(
    {
        Role.DEVELOPER,
        Role.QA,
        Role.DOCUMENTER,
        Role.CELL_PM,
        Role.MAIN_PM,
    }
)

# Auditor is the silent observer on every cell + cross-cell channel.
_AUDITOR_ONLY: frozenset[Role] = frozenset({Role.AUDITOR})

# All non-system roles (legacy ALL_AGENTS in role-terms): every Role except
# the SYSTEM sentinel. Used for company-wide channels (announcements,
# all-hands).
_ALL_ROLES: frozenset[Role] = frozenset(r for r in Role if r is not Role.SYSTEM)


CHANNELS: dict[str, ChannelSpec] = {
    # -- Cell channels (members + main-pm read/write, auditor silent) --------
    "backend-cell": ChannelSpec(
        slug="backend-cell",
        description="Backend development team channel",
        type=ChannelType.CELL,
        read_roles=_CELL_READ | _AUDITOR_ONLY,
        write_roles=_CELL_WRITE,
        silent_roles=_AUDITOR_ONLY,
        team_scope=Team.BACKEND,
    ),
    "frontend-cell": ChannelSpec(
        slug="frontend-cell",
        description="Frontend development team channel",
        type=ChannelType.CELL,
        read_roles=_CELL_READ | _AUDITOR_ONLY,
        write_roles=_CELL_WRITE,
        silent_roles=_AUDITOR_ONLY,
        team_scope=Team.FRONTEND,
    ),
    "uxui-cell": ChannelSpec(
        slug="uxui-cell",
        description="UX/UI design team channel",
        type=ChannelType.CELL,
        read_roles=_CELL_READ | _AUDITOR_ONLY,
        write_roles=_CELL_WRITE,
        silent_roles=_AUDITOR_ONLY,
        team_scope=Team.UX_UI,
    ),
    # -- Cross-cell role channels --------------------------------------------
    # dev-all: all cell-member roles read; only DEVELOPER + CELL_PM + MAIN_PM
    # write. Auditor silent.
    "dev-all": ChannelSpec(
        slug="dev-all",
        description="Cross-cell developer discussion",
        type=ChannelType.CROSS_CELL,
        read_roles=_CROSS_CELL_READ_ALL | _AUDITOR_ONLY,
        write_roles=frozenset({Role.DEVELOPER, Role.CELL_PM, Role.MAIN_PM}),
        silent_roles=_AUDITOR_ONLY,
    ),
    # qa-all: same read fan-out as dev-all; QA + CELL_PM write (no main-pm
    # write per legacy CHANNEL_ACCESS).
    "qa-all": ChannelSpec(
        slug="qa-all",
        description="Cross-cell QA discussion",
        type=ChannelType.CROSS_CELL,
        read_roles=_CROSS_CELL_READ_ALL | _AUDITOR_ONLY,
        write_roles=frozenset({Role.QA, Role.CELL_PM}),
        silent_roles=_AUDITOR_ONLY,
    ),
    # pm-all: PM-only coordination — cell PMs + main-pm.
    "pm-all": ChannelSpec(
        slug="pm-all",
        description="Cross-cell PM coordination",
        type=ChannelType.CROSS_CELL,
        read_roles=frozenset({Role.CELL_PM, Role.MAIN_PM}) | _AUDITOR_ONLY,
        write_roles=frozenset({Role.CELL_PM, Role.MAIN_PM}),
        silent_roles=_AUDITOR_ONLY,
    ),
    # doc-all: documenters + cell PMs + main-pm read; documenters + cell PMs
    # write.
    "doc-all": ChannelSpec(
        slug="doc-all",
        description="Cross-cell documentation discussion",
        type=ChannelType.CROSS_CELL,
        read_roles=frozenset({Role.DOCUMENTER, Role.CELL_PM, Role.MAIN_PM})
        | _AUDITOR_ONLY,
        write_roles=frozenset({Role.DOCUMENTER, Role.CELL_PM}),
        silent_roles=_AUDITOR_ONLY,
    ),
    # -- Management channels --------------------------------------------------
    # Legacy CHANNEL_ACCESS lists auditor as both read AND write here. We
    # preserve that behaviour for parity; the runtime guard that downgrades
    # auditor to silent lives in services.
    "main-pm-board": ChannelSpec(
        slug="main-pm-board",
        description="Main PM and Board communication",
        type=ChannelType.MANAGEMENT,
        read_roles=frozenset(
            {Role.MAIN_PM, Role.PRODUCT_OWNER, Role.HEAD_MARKETING, Role.AUDITOR}
        ),
        write_roles=frozenset(
            {Role.MAIN_PM, Role.PRODUCT_OWNER, Role.HEAD_MARKETING, Role.AUDITOR}
        ),
        silent_roles=frozenset(),
    ),
    "board-private": ChannelSpec(
        slug="board-private",
        description="Board-only discussions",
        type=ChannelType.MANAGEMENT,
        read_roles=frozenset(
            {
                Role.PRODUCT_OWNER,
                Role.HEAD_MARKETING,
                Role.AUDITOR,
                Role.CEO,
                Role.MAIN_PM,
            }
        ),
        write_roles=frozenset(
            {Role.PRODUCT_OWNER, Role.HEAD_MARKETING, Role.AUDITOR, Role.CEO}
        ),
        silent_roles=frozenset(),
    ),
    # -- Special / broadcast channels -----------------------------------------
    # announcements: company-wide read; only main-pm/board/ceo write.
    # Spec §5.5 marks this as the canonical read-only channel.
    "announcements": ChannelSpec(
        slug="announcements",
        description="Company-wide announcements (read-only for most)",
        type=ChannelType.SPECIAL,
        read_roles=_ALL_ROLES,
        write_roles=frozenset(
            {Role.MAIN_PM, Role.PRODUCT_OWNER, Role.HEAD_MARKETING, Role.CEO}
        ),
        silent_roles=frozenset(),
        read_only_for_others=True,
    ),
    # all-hands: company-wide open discussion — every role reads and writes.
    "all-hands": ChannelSpec(
        slug="all-hands",
        description="Company-wide open discussion",
        type=ChannelType.SPECIAL,
        read_roles=_ALL_ROLES,
        write_roles=_ALL_ROLES,
        silent_roles=frozenset(),
    ),
}
