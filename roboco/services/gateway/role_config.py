"""Per-role allowed verbs and tool manifests.

Source of truth for which verbs and content tools each role gets at spawn
time. The spawn manifest builder reads from here. The MCP servers (Phase 1+)
also reference this catalog to scope their tool registration per role.

Flow-tool tuples (`_DEV_FLOW`, `_QA_FLOW`, ...) are derived from
`roboco.foundation.policy.lifecycle.intents_for_role`. The spec is canon
— adding or removing a role from an `IntentSpec.allowed_roles`
automatically updates the MCP manifest. This module is a thin shim that
adds the do-tool / write / subagent / description metadata the spec does
not carry.
"""

from __future__ import annotations

from dataclasses import dataclass

from roboco.foundation.policy import lifecycle as spec


@dataclass(frozen=True)
class RoleConfig:
    """Static config describing what a role can do."""

    role: str
    flow_tools: tuple[str, ...]  # roboco-flow verbs
    do_tools: tuple[str, ...]  # roboco-do content tools
    allows_write: bool  # Edit, Write to workspace
    allows_subagent: bool  # `Agent` tool (parallel research)
    description: str


# Wave 1 receivers — every role with inbox access gets notify_list/get/ack
# so `i_am_idle()` doesn't soft-block forever on unread notifications.
_NOTIFY_RECEIVER = ("notify_list", "notify_get", "notify_ack")
# Wave 2 — channel discovery. Every role gets `channels()` so the LLM stops
# inventing slugs ("backend-dev", "backend") that don't exist.
_CHANNEL_DISCOVERY = ("channels",)

_DEV_FLOW = spec.intents_for_role(spec.Role.DEVELOPER)
_DEV_DO = (
    "commit",
    "note",
    "say",
    "dm",
    "evidence",
    "progress",
    "pr_update",
    *_NOTIFY_RECEIVER,
    *_CHANNEL_DISCOVERY,
)

_QA_FLOW = spec.intents_for_role(spec.Role.QA)
_QA_DO = ("note", "say", "dm", "evidence", *_NOTIFY_RECEIVER, *_CHANNEL_DISCOVERY)

_DOC_FLOW = spec.intents_for_role(spec.Role.DOCUMENTER)
_DOC_DO = (
    "commit",
    "note",
    "say",
    "dm",
    "evidence",
    "progress",
    "pr_update",
    *_NOTIFY_RECEIVER,
    *_CHANNEL_DISCOVERY,
)

_CELL_PM_FLOW = spec.intents_for_role(spec.Role.CELL_PM)
_CELL_PM_DO = (
    "note",
    "say",
    "dm",
    "notify",
    "evidence",
    "open_session",
    "link_session",
    "pr_update",
    *_NOTIFY_RECEIVER,
    *_CHANNEL_DISCOVERY,
)

_MAIN_PM_FLOW = spec.intents_for_role(spec.Role.MAIN_PM)
_MAIN_PM_DO = (
    "note",
    "say",
    "dm",
    "notify",
    "evidence",
    "open_session",
    "link_session",
    "pr_update",
    *_NOTIFY_RECEIVER,
    *_CHANNEL_DISCOVERY,
)

_PRODUCT_OWNER_FLOW = spec.intents_for_role(spec.Role.PRODUCT_OWNER)
_HEAD_MARKETING_FLOW = spec.intents_for_role(spec.Role.HEAD_MARKETING)
_BOARD_DO = (
    "note",
    "say",
    "dm",
    "notify",
    "evidence",
    "open_session",  # Board can open strategic sessions but not link arbitrary
    *_NOTIFY_RECEIVER,
    *_CHANNEL_DISCOVERY,
)

_AUDITOR_FLOW = spec.intents_for_role(spec.Role.AUDITOR)
# Auditor reads, does not chat or escalate. notify_list/get for inbox visibility;
# no ack (silent observer — wouldn't ack notifications). channels for read map.
_AUDITOR_DO = ("note", "evidence", "notify_list", "notify_get", *_CHANNEL_DISCOVERY)


ROLE_CONFIGS: dict[str, RoleConfig] = {
    "developer": RoleConfig(
        role="developer",
        flow_tools=_DEV_FLOW,
        do_tools=_DEV_DO,
        allows_write=True,
        allows_subagent=False,
        description="Implements features and fixes; commits + pushes; never merges.",
    ),
    "qa": RoleConfig(
        role="qa",
        flow_tools=_QA_FLOW,
        do_tools=_QA_DO,
        allows_write=False,
        allows_subagent=False,
        description="Reviews code via PR diff and structured evidence; pass or fail.",
    ),
    "documenter": RoleConfig(
        role="documenter",
        flow_tools=_DOC_FLOW,
        do_tools=_DOC_DO,
        allows_write=True,
        allows_subagent=False,
        description="Writes documentation for completed work; commits doc files.",
    ),
    "cell_pm": RoleConfig(
        role="cell_pm",
        flow_tools=_CELL_PM_FLOW,
        do_tools=_CELL_PM_DO,
        allows_write=False,
        allows_subagent=True,
        description="Triages, unblocks, and completes cell tasks; merges leaf PRs.",
    ),
    "main_pm": RoleConfig(
        role="main_pm",
        flow_tools=_MAIN_PM_FLOW,
        do_tools=_MAIN_PM_DO,
        allows_write=False,
        allows_subagent=True,
        description="Coordinates across cells; opens master PR; escalates to CEO.",
    ),
    "product_owner": RoleConfig(
        role="product_owner",
        flow_tools=_PRODUCT_OWNER_FLOW,
        do_tools=_BOARD_DO,
        allows_write=False,
        allows_subagent=True,
        description="Product oversight; escalates strategic decisions to CEO.",
    ),
    "head_marketing": RoleConfig(
        role="head_marketing",
        flow_tools=_HEAD_MARKETING_FLOW,
        do_tools=_BOARD_DO,
        allows_write=False,
        allows_subagent=True,
        description="Marketing oversight; escalates to CEO.",
    ),
    "auditor": RoleConfig(
        role="auditor",
        flow_tools=_AUDITOR_FLOW,
        do_tools=_AUDITOR_DO,
        allows_write=False,
        allows_subagent=False,
        description="Silent observer; reads but never communicates outwardly.",
    ),
}


def get_role_config(role: str) -> RoleConfig:
    """Lookup a role config; raises KeyError on unknown role."""
    if role not in ROLE_CONFIGS:
        raise KeyError(f"unknown role: {role!r} (known: {sorted(ROLE_CONFIGS)})")
    return ROLE_CONFIGS[role]
