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
    allows_subagent: bool  # `Agent` tool — fleet-wide False (CEO ban, 2026-07-09)
    description: str


# Wave 1 receivers — every role with inbox access gets notify_list/get/ack for
# notifications and read_messages for A2A, so `i_am_idle()`'s unread soft-block
# is satisfiable rather than a permanent dead-end.
_NOTIFY_RECEIVER = (
    "notify_list",
    "notify_get",
    "notify_ack",
    "read_messages",
    "read_a2a",
)

_DEV_FLOW = spec.intents_for_role(spec.Role.DEVELOPER)
_DEV_DO = (
    "commit",
    "note",
    "dm",
    "evidence",
    "progress",
    "pr_update",
    "draft_playbook",
    # Every dev's manifest carries this (Role.DEVELOPER doesn't distinguish
    # ux-dev from be-dev/fe-dev) — harmless, since only UX/UI devs are ever
    # assigned a video-authoring task. The real gate is propose_video's
    # runtime _caller_team check.
    "propose_video",
    # On-demand sandbox DB/Redis/Mongo — carried unconditionally (declarative
    # manifest), gated for real by request_sandbox's project opt-in check.
    "request_sandbox",
    # Render the video composition to preview frames (dev's own working
    # tree); manifest carries it unconditionally, gated for real by
    # request_render's active-video-task + flag checks.
    "request_render",
    *_NOTIFY_RECEIVER,
)

_QA_FLOW = spec.intents_for_role(spec.Role.QA)
_QA_DO = (
    "note",
    "dm",
    "evidence",
    "draft_playbook",
    "request_sandbox",
    # QA's render source is a read-only branch export, never a working tree
    # (see request_render/_render_qa_source); gated the same way as above.
    "request_render",
    *_NOTIFY_RECEIVER,
)

_DOC_FLOW = spec.intents_for_role(spec.Role.DOCUMENTER)
_DOC_DO = (
    "commit",
    "note",
    "dm",
    "evidence",
    "progress",
    "pr_update",
    "draft_playbook",
    *_NOTIFY_RECEIVER,
)

_CELL_PM_FLOW = spec.intents_for_role(spec.Role.CELL_PM)
_CELL_PM_DO = (
    "note",
    "dm",
    "notify",
    "evidence",
    "pr_update",
    "draft_playbook",
    *_NOTIFY_RECEIVER,
)

_MAIN_PM_FLOW = spec.intents_for_role(spec.Role.MAIN_PM)
_MAIN_PM_DO = (
    "note",
    "dm",
    "notify",
    "evidence",
    "pr_update",
    "draft_playbook",
    *_NOTIFY_RECEIVER,
)

_PRODUCT_OWNER_FLOW = spec.intents_for_role(spec.Role.PRODUCT_OWNER)
_HEAD_MARKETING_FLOW = spec.intents_for_role(spec.Role.HEAD_MARKETING)
_BOARD_DO = (
    "note",
    "pitch",
    "dm",
    "notify",
    "evidence",
    # Registry-driven "this cycle found nothing worth proposing" exit for
    # any Board Program exploration task — every board explorer role gets
    # it (Product Owner / Head of Marketing here; the Auditor separately
    # below), the runtime role-vs-program check in content_actions.py does
    # the real gating.
    "nothing_to_propose",
    *_NOTIFY_RECEIVER,
)

# Product Owner only (v1 — HoM stays a reviewer via the normal board gate when
# an approved roadmap item later ships as real work; see the roadmap spec's
# non-goals). ``propose_bug_hunt`` (Pest Control), ``propose_gap_fill``
# (Spackle), and ``propose_rebalance`` (Scales) are likewise
# Product-Owner-only. ``propose_friction_fixes`` (Dogfood) is likewise
# Product-Owner-only — the one program whose spawn also gets the playwright
# MCP (task-scoped, not role-blanket — see AgentOrchestrator._is_dogfood_spawn).
_PRODUCT_OWNER_DO = (
    *_BOARD_DO,
    "propose_roadmap",
    "propose_bug_hunt",
    "propose_gap_fill",
    "propose_rebalance",
    "propose_friction_fixes",
)

# Head of Marketing only (mirrors _PRODUCT_OWNER_DO's propose_roadmap grant).
# ``propose_market_brief`` (Periscope) is likewise Head-of-Marketing-only.
# ``propose_messaging_fixes`` (Mirror) is likewise Head-of-Marketing-only —
# the mirror image of the Product Owner's ``propose_gap_fill`` (Spackle).
# ``propose_editorial_post`` (Megaphone) is likewise Head-of-Marketing-only.
# ``propose_campaign`` (War Room) is the same bounded expansion, one call per
# campaign-planning cycle. ``propose_conversation_replies`` (Barfly) is
# likewise Head-of-Marketing-only.
_HEAD_MARKETING_DO = (
    *_BOARD_DO,
    "propose_feature_spotlight",
    "propose_market_brief",
    "propose_messaging_fixes",
    "propose_editorial_post",
    "propose_campaign",
    "propose_conversation_replies",
)

_AUDITOR_FLOW = spec.intents_for_role(spec.Role.AUDITOR)
# Auditor reads, does not chat or escalate. notify_list/get for inbox visibility;
# no ack (silent observer — wouldn't ack notifications). It now carries
# dm/read_a2a so the CEO can open a DM with a mid-flight auditor and it can
# reply in-thread, but it still never INITIATES peer A2A — that's enforced in
# agents_config.can_a2a_direct, not by omitting the tool.
# The Auditor is the playbook quality gate — a deliberate, bounded expansion of
# its surface (approve/reject/archive are KB curation actions, not agent comms).
# ``propose_postmortem`` is the Coroner (Board Program) grant (spec §4): the
# ONE program the Auditor originates content for. It does NOT also carry
# ``draft_playbook`` — a coroner-authored playbook-kind process change is
# drafted by calling PlaybookService directly inside propose_postmortem
# (content_actions.py), never through this do-verb; "auditor curates but
# does not draft" stays an invariant (test_playbook_verbs.py). Librarian
# (below) is the program the spec flagged as the one that would deliberately
# revisit that invariant — it does NOT: propose_playbook_drafts follows the
# exact same precedent, drafting directly via PlaybookService inside the
# verb (content_actions.py), so draft_playbook itself still never appears
# here.
_AUDITOR_DO = (
    "note",
    "evidence",
    "dm",
    "read_a2a",
    "approve_playbook",
    "reject_playbook",
    "archive_playbook",
    "curate_vault",
    "propose_postmortem",
    # See _BOARD_DO's comment — the Auditor owns Coroner/Sentinel/Librarian,
    # so it needs the same "nothing to propose" exit.
    "nothing_to_propose",
    "notify_list",
    "notify_get",
    # Sentinel (Board Program): the Auditor's weekly org-wide drift report,
    # a bounded expansion mirroring _PRODUCT_OWNER_DO's propose_roadmap /
    # _HEAD_MARKETING_DO's propose_market_brief grants.
    "propose_quality_report",
    # Librarian (Board Program): proactive playbook mining — drafts 1-3
    # playbooks directly via PlaybookService (see the comment above), never
    # via draft_playbook.
    "propose_playbook_drafts",
)

# PR reviewer: a read-only reviewer of inbound external/fork PRs. Flow verbs come
# from the lifecycle spec (a dedicated review trio, not QA's). It reads diffs and
# records findings (note/evidence); the change-request is posted server-side. It
# now carries dm/read_a2a so the CEO can reach one mid-review and it can reply
# in-thread; its only INITIATION target stays its owning cell_pm/main_pm
# (agents_config._check_pr_reviewer_a2a).
_PR_REVIEWER_FLOW = spec.intents_for_role(spec.Role.PR_REVIEWER)
_PR_REVIEWER_DO = ("note", "evidence", "dm", "read_a2a", "notify_list", "notify_get")

_PROMPTER_FLOW = spec.intents_for_role(
    spec.Role.PROMPTER
)  # none — not a lifecycle role
# Intake interviewer: human-only. It journals (note) and cites sources
# (evidence) but has NO outward agent comms — no dm/notify (agents).
# Its conversation with the human runs over the
# live-session bridge, not these gateway tools.
_PROMPTER_DO = ("note", "evidence")

# Secretary: the CEO's chief-of-staff. Foundation tools mirror the prompter
# (note + evidence; its conversation with the CEO runs over the live-session
# bridge). Its gated CEO-authority directive tools are layered on separately by
# the secretary authority surface, not registered as generic do-tools here.
_SECRETARY_FLOW = spec.intents_for_role(spec.Role.SECRETARY)  # none — human-only
_SECRETARY_DO = ("note", "evidence")


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
        allows_subagent=False,
        description="Triages, unblocks, and completes cell tasks; merges leaf PRs.",
    ),
    "main_pm": RoleConfig(
        role="main_pm",
        flow_tools=_MAIN_PM_FLOW,
        do_tools=_MAIN_PM_DO,
        allows_write=False,
        allows_subagent=False,
        description="Coordinates across cells; opens master PR; escalates to CEO.",
    ),
    "product_owner": RoleConfig(
        role="product_owner",
        flow_tools=_PRODUCT_OWNER_FLOW,
        do_tools=_PRODUCT_OWNER_DO,
        allows_write=False,
        allows_subagent=False,
        description="Product oversight; escalates strategic decisions to CEO.",
    ),
    "head_marketing": RoleConfig(
        role="head_marketing",
        flow_tools=_HEAD_MARKETING_FLOW,
        do_tools=_HEAD_MARKETING_DO,
        allows_write=False,
        allows_subagent=False,
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
    "pr_reviewer": RoleConfig(
        role="pr_reviewer",
        flow_tools=_PR_REVIEWER_FLOW,
        do_tools=_PR_REVIEWER_DO,
        allows_write=False,
        allows_subagent=False,
        description=(
            "Reviews inbound external/fork PRs and posts one change-request. "
            "Read-only; never writes code or merges."
        ),
    ),
    "prompter": RoleConfig(
        role="prompter",
        flow_tools=_PROMPTER_FLOW,
        do_tools=_PROMPTER_DO,
        allows_write=False,
        allows_subagent=False,
        description=(
            "Intake interviewer; chats only with the human, reads the codebase, "
            "and drafts a task. No outward agent comms; never writes or merges."
        ),
    ),
    "secretary": RoleConfig(
        role="secretary",
        flow_tools=_SECRETARY_FLOW,
        do_tools=_SECRETARY_DO,
        allows_write=False,
        allows_subagent=False,
        description=(
            "CEO's chief-of-staff; chats only with the CEO and carries gated CEO "
            "authority. Reads company state and executes the CEO's directives, "
            "bouncing high-impact ones back for explicit confirmation."
        ),
    ),
}


def get_role_config(role: str) -> RoleConfig:
    """Lookup a role config; raises KeyError on unknown role."""
    if role not in ROLE_CONFIGS:
        raise KeyError(f"unknown role: {role!r} (known: {sorted(ROLE_CONFIGS)})")
    return ROLE_CONFIGS[role]


def role_carries_notify_ack(role: str) -> bool:
    """True when `role`'s do-tool manifest includes ``notify_ack``.

    The single chokepoint for any gate that expects the caller to clear an
    ack-required notification before proceeding: a role without this tool
    (auditor, pr_reviewer, prompter, secretary; see their do-tool tuples
    above) can never satisfy such a gate, so blocking one of them would be a
    permanent dead-end rather than a fixable condition. An unknown role
    reads as False (fail toward not blocking).
    """
    try:
        return "notify_ack" in get_role_config(role).do_tools
    except KeyError:
        return False
