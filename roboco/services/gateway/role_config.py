"""Per-role allowed verbs and tool manifests.

Source of truth for which verbs and content tools each role gets at spawn
time. The spawn manifest builder reads from here. The MCP servers (Phase 1+)
also reference this catalog to scope their tool registration per role.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoleConfig:
    """Static config describing what a role can do."""

    role: str
    flow_tools: tuple[str, ...]  # roboco-flow verbs
    do_tools: tuple[str, ...]  # roboco-do content tools
    allows_write: bool  # Edit, Write to workspace
    allows_subagent: bool  # `Agent` tool (parallel research)
    description: str


_DEV_FLOW = (
    "give_me_work",
    "i_will_work_on",
    "i_have_committed",
    "submit_for_qa",
    "i_am_done",
    "i_am_blocked",
    "unclaim",
    "i_am_idle",
)
_DEV_DO = ("commit", "note", "say", "dm", "evidence")

_QA_FLOW = (
    "give_me_work",
    "claim_review",
    "pass",
    "fail",
    "unclaim",
    "i_am_idle",
)
_QA_DO = ("note", "say", "dm", "evidence")

_DOC_FLOW = (
    "give_me_work",
    "claim_doc_task",
    "i_documented",
    "unclaim",
    "i_am_idle",
)
_DOC_DO = ("commit", "note", "say", "dm", "evidence")

_CELL_PM_FLOW = (
    "give_me_work",
    "i_will_plan",
    "delegate",
    "submit_up",
    "triage",
    "unblock",
    "complete",
    "escalate_up",
    "unclaim",
    "i_am_idle",
)
_CELL_PM_DO = ("note", "say", "dm", "evidence")

_MAIN_PM_FLOW = (
    "give_me_work",
    "i_will_plan",
    "delegate",
    "triage_all",
    "unblock",
    "complete",
    "escalate_up",
    "escalate_to_ceo",
    "unclaim",
    "i_am_idle",
)
_MAIN_PM_DO = ("note", "say", "dm", "evidence")

_BOARD_FLOW = (
    "triage",
    "escalate_to_ceo",
    "i_am_idle",
)
_BOARD_DO = ("note", "say", "dm", "evidence")

_AUDITOR_FLOW = (
    "triage",
    "i_am_idle",
)
_AUDITOR_DO = ("note", "evidence")  # auditor reads, does not chat or escalate


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
        flow_tools=_BOARD_FLOW,
        do_tools=_BOARD_DO,
        allows_write=False,
        allows_subagent=True,
        description="Product oversight; escalates strategic decisions to CEO.",
    ),
    "head_marketing": RoleConfig(
        role="head_marketing",
        flow_tools=_BOARD_FLOW,
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
