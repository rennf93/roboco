"""Smart-wrapped content tools — commit, note, dm, read_a2a, evidence.

Each method:
1. Validates input (e.g., commit_validator for commit messages).
2. Auto-injects task_id when the agent has an active claim and the param is missing.
3. Calls the underlying service.
4. Returns a standardized Envelope.

Pure orchestration; no DB writes outside what the underlying services do.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import re
import shutil
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast
from urllib.parse import urlparse

import structlog

from roboco.config import settings
from roboco.exceptions import GitError
from roboco.foundation.policy import communications as _comms
from roboco.foundation.policy.content import ContentValidationError, markers
from roboco.foundation.policy.content.validators import reject_trivial
from roboco.foundation.policy.injection_guard import screen_external_text
from roboco.foundation.policy.journaling import Scope as _Scope
from roboco.models.base import TaskStatus
from roboco.services.content_notes import content_type_for_role
from roboco.services.gateway.choreographer import findings as findings_lib
from roboco.services.gateway.choreographer.evidence_legs import (
    LegBudget,
    run_bounded_leg,
)
from roboco.services.gateway.commit_validator import validate_commit_message
from roboco.services.gateway.envelope import Envelope
from roboco.services.gateway.evidence_builder import build_evidence_for_task
from roboco.services.x_client import MAX_TWEET_CHARS

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from roboco.foundation.identity import Team
    from roboco.foundation.policy.board_programs import BoardProgram


logger = structlog.get_logger()


def _merge_resumption_fields(
    section: dict[str, Any] | None,
    *,
    done: str,
    next: str,
    where_to_look: list[str] | None,
) -> dict[str, Any] | None:
    """Fold the top-level resumption fields into the handoff ``section``.

    ``section: dict[str, Any]`` renders a tool schema with no visible
    sub-fields, so a weak model (minimax-m3) emits ``section={}`` and the
    resumption gate rejects ``done — Field required`` (the 2026-06-27 PM
    respawn-loop meltdown). The top-level ``done`` / ``next`` /
    ``where_to_look`` string fields are the LLM-facing contract — they show
    up in the tool schema as discrete fields the same model fills fine. Here
    they fill any keys the explicit ``section`` omits without overwriting
    keys the agent already supplied, so a capable model passing ``section``
    directly is unaffected. Returns ``None`` when nothing was supplied so the
    downstream ``{'summary': text}`` fallback + gate remediation still fire.
    """
    merged: dict[str, Any] = dict(section) if section else {}
    if done and "done" not in merged:
        merged["done"] = done
    if next and "next" not in merged:
        merged["next"] = next
    if where_to_look and "where_to_look" not in merged:
        merged["where_to_look"] = where_to_look
    return merged or None


# Scope catalog is canonical in foundation.policy.journaling.
# Derived here as a string frozenset for the existing call sites that
# compare strings rather than the Scope enum.
_VALID_NOTE_SCOPES: frozenset[str] = frozenset(s.value for s in _Scope)
_TASK_ID_PREFIX_RE = re.compile(r"^\s*\[[a-zA-Z0-9_-]+\]\s*")

# Content-tool RBAC. These are the same role sets that drive the spawn
# manifest in `role_config.py` (`_DEV_DO`/`_DOC_DO` include "commit";
# `_CELL_PM_DO`/`_MAIN_PM_DO`/`_BOARD_DO` include "notify"). Pre-2026-05-10
# this lookup went through `verb_gates.is_verb_allowed`; the verb-gates
# table has been folded into `roboco.foundation.policy.lifecycle`, but
# `commit` and `notify` are content tools (not lifecycle intents) so they
# live here as explicit role frozensets — not in `_INTENT_VERBS`.
#
# Notification sender + priority allowlists are canonical in
# foundation.policy.communications. Derived as string frozensets here so
# the existing call sites that compare strings keep working.
_COMMIT_ALLOWED_ROLES: frozenset[str] = frozenset({"developer", "documenter"})
_NOTIFY_ALLOWED_ROLES: frozenset[str] = frozenset(
    r.value for r in _comms.NOTIFY_SENDER_ROLES
)

# Roles with NO agent-comms surface (CLAUDE.md): the human-only prompter and
# secretary — restricted to note + evidence, no dm/notify, they own their own
# dedicated chat pages instead. (Auditor and pr_reviewer carry dm/read_a2a
# now — the CEO can DM either and it can reply in-thread — so they're no
# longer in this set; the auditor's silence toward PEERS is enforced
# separately in agents_config.can_a2a_direct.)
# The spawn manifest already omits dm from these roles' tool surfaces, but
# that is convention-only — this frozenset is the handler-level defence-in-depth
# that refuses any call that bypassed the manifest (direct verb dispatch, test
# harness, future routing change), so the no-comms invariant holds regardless of
# how the call arrived. Matches the explicit role-frozenset gates on commit /
# notify / pitch / playbook. Derived from the canonical set in
# foundation.policy.communications — agents_config.can_a2a_direct's CEO
# target-side check reuses the same source.
_NO_COMMS_ROLES: frozenset[str] = frozenset(r.value for r in _comms.NO_COMMS_ROLES)


_DECISION_SECTIONS: tuple[tuple[str, str], ...] = (
    ("context", "Context"),
    ("options", "Options Considered"),
    ("chosen", "Chosen"),
    ("rationale", "Rationale"),
    ("consequences", "Consequences"),
)

_REFLECT_SECTIONS: tuple[tuple[str, str], ...] = (
    ("what_done", "What Done"),
    ("what_learned", "What Learned"),
    ("what_struggled", "What Struggled"),
    ("next_steps", "Next Steps"),
)


def _render_option_block(option: dict[str, str] | str) -> str:
    """Render one decision option. Accepts dict or legacy string."""
    if isinstance(option, str):
        return f"- {option}"
    name = option.get("name", "").strip() or "(unnamed)"
    pros = option.get("pros", "").strip()
    cons = option.get("cons", "").strip()
    block = [f"### {name}"]
    if pros:
        block.append(f"- Pros: {pros}")
    if cons:
        block.append(f"- Cons: {cons}")
    return "\n".join(block)


_SCOPE_SECTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "decision": _DECISION_SECTIONS,
    "reflect": _REFLECT_SECTIONS,
}


def _render_section_value(key: str, value: Any) -> str | None:
    """Render one section value or return None to skip the section."""
    if key == "options" and isinstance(value, list):
        if not value:
            return None
        return "\n\n".join(_render_option_block(o) for o in value)
    if isinstance(value, list):
        if not value:
            return None
        return "\n".join(f"- {item}" for item in value)
    rendered = str(value).strip()
    return rendered or None


def _render_journal_content(scope: str, text: str, structured: dict[str, Any]) -> str:
    """Build the journal entry body. Pre-gateway parity for decision/reflect."""
    sections = _SCOPE_SECTIONS.get(scope, ())
    if not sections:
        return text
    body_parts: list[str] = [text.strip()] if text.strip() else []
    for key, label in sections:
        value = structured.get(key)
        if value is None:
            continue
        rendered = _render_section_value(key, value)
        if rendered is None:
            continue
        body_parts.append(f"## {label}\n{rendered}")
    return "\n\n".join(body_parts) if body_parts else text


# Narrative fields that decision/reflect scopes want filled. A
# missing or empty value used to hard-reject the note with `incomplete_input`
# — and since that kind counts toward the do-server circuit breaker, three
# well-intentioned-but-thin notes in a row tripped it. We now default the
# field instead so the note is always recorded (audit value preserved) and
# the breaker never fires on a note. The placeholder makes the gap visible in
# the panel rather than silently dropping the section.
_NARRATIVE_PLACEHOLDER = "(not provided)"

_DECISION_NARRATIVE_FIELDS: tuple[str, ...] = ("context", "chosen", "rationale")
_REFLECT_NARRATIVE_FIELDS: tuple[str, ...] = (
    "what_done",
    "what_learned",
    "what_struggled",
)

# List-typed structured fields. A lone scalar is wrapped into a one-element
# list here too (defense-in-depth — the route schema coerces first, but the
# service is called directly from the choreographer and tests).
_LIST_FIELDS: tuple[str, ...] = ("options", "consequences", "next_steps")

_SCOPE_NARRATIVE_FIELDS: dict[str, tuple[str, ...]] = {
    "decision": _DECISION_NARRATIVE_FIELDS,
    "reflect": _REFLECT_NARRATIVE_FIELDS,
}


def _scalar_field_missing(value: Any) -> bool:
    """True when a required scalar/text field is empty/whitespace."""
    return not value or not str(value).strip()


def _coerce_scalar_to_list(value: Any) -> Any:
    """Wrap a lone str/dict into a one-element list; pass lists/None through."""
    if value is None or isinstance(value, list):
        return value
    if isinstance(value, str | dict):
        return [value]
    return value


def _normalize_structured(scope: str, structured: dict[str, Any]) -> dict[str, Any]:
    """Return a tolerant copy of ``structured`` for decision/reflect scopes.

    Stop rejecting thin notes. List-typed fields tolerate a lone
    scalar (wrapped into a one-element list), and missing/blank narrative
    fields are defaulted to a visible placeholder so the entry still records
    instead of returning `incomplete_input` (which trips the circuit breaker).
    Other scopes are returned unchanged.
    """
    normalized = dict(structured)
    for field in _LIST_FIELDS:
        if field in normalized:
            normalized[field] = _coerce_scalar_to_list(normalized[field])
    for field in _SCOPE_NARRATIVE_FIELDS.get(scope, ()):
        if _scalar_field_missing(normalized.get(field)):
            normalized[field] = _NARRATIVE_PLACEHOLDER
    return normalized


def _ownership_violation(task_id: UUID) -> Envelope:
    """Standard envelope for Gate Set D ownership violations.

    Pre-gateway, agents could not even see tasks they didn't own; the
    gateway exposes task_id parameters so the explicit gate is required.
    """
    return Envelope.not_authorized(
        message=(f"you are not the assignee of {task_id}; cannot post content to it"),
        remediate=(
            "only the task's assignee may attach content (commit/note/"
            "dm/evidence) to it. Use a different task_id or omit task_id "
            "for off-task messages (dm only)."
        ),
        context_briefing={},
    )


def _not_active_claimant(task_id: UUID) -> Envelope:
    """Envelope for a caller who holds no active claim on ``task_id``.

    The caller may still be the historical ``assigned_to`` (e.g. its claim
    was reaped for going silent, or the task was handed to another agent),
    but ``active_claimant_id`` no longer points at it. Writing would race the
    real claimant, so the write is refused.
    """
    return Envelope.not_authorized(
        message=(
            f"you do not hold the active claim on {task_id}; "
            "another agent owns it now or your claim was released"
        ),
        remediate=(
            "call i_am_idle() and give_me_work() to pick up fresh work; "
            "if you believe this is your task, re-claim it before writing"
        ),
        context_briefing={},
    )


@dataclass(frozen=True)
class ContentActionsDeps:
    """Service deps for ContentActions; bundled to keep init signature flat."""

    task: Any
    git: Any
    a2a: Any
    journal: Any
    workspace: Any
    notifications: Any
    # Wave 1 added inbox-read verbs (notify_list/get/ack) that live on
    # `NotificationDeliveryService`, not `NotificationService`. Keeping
    # them separate so the sender vs receiver concerns stay split.
    notification_delivery: Any = None
    # evidence() returns journal_highlights for QA/reviewer
    # context. Matches the choreographer's EvidenceRepo wiring so both
    # paths surface the same shape.
    evidence_repo: Any = None
    # request_sandbox's ensure_sandbox call. Mirrors ChoreographerDeps.orchestrator:
    # optional so tests that don't exercise sandbox provisioning need not plumb
    # it in; None degrades to a retryable "orchestrator unavailable" envelope.
    orchestrator: Any = None


@dataclass(frozen=True)
class _RenderSource:
    """A request_render source: a directory containing ``motion/``, plus its
    provenance. ``cleanup`` (set only for a QA branch-export scratch dir) is
    always called by request_render's ``finally``, dev or QA alike."""

    root: Path
    head_sha: str | None
    dirty: bool
    kind: str  # "workspace" (dev's own tree) | "branch" (QA's read-only export)
    cleanup: Callable[[], None] | None = None


_VALID_NOTIFY_PRIORITIES: frozenset[str] = frozenset(p.value for p in _comms.Priority)
# The Board roles that may author a pitch (a product proposal for CEO approval).
_PITCH_ROLES: frozenset[str] = frozenset({"product_owner", "head_marketing"})

# Roadmap cycles are PO-authored in v1 — HoM stays a reviewer via the normal
# board gate when an approved item later ships as real work (see the roadmap
# spec's non-goals).
_ROADMAP_ROLES: frozenset[str] = frozenset({"product_owner"})

# Pest Control (Board Program) bug hunts are Product-Owner-only, mirroring
# _ROADMAP_ROLES.
_PEST_ROLES: frozenset[str] = frozenset({"product_owner"})

# Spackle (Board Program) gap-fill audits are Product-Owner-only, mirroring
# _PEST_ROLES.
_GAP_FILL_ROLES: frozenset[str] = frozenset({"product_owner"})

# Mirror (Board Program) messaging-fix audits are Head-of-Marketing-only —
# the mirror image of _GAP_FILL_ROLES.
_MESSAGING_FIXES_ROLES: frozenset[str] = frozenset({"head_marketing"})

# Feature spotlights are HoM-authored — the Product Owner stays out of this
# cycle (mirrors _ROADMAP_ROLES's PO-only symmetry, reversed).
_FEATURE_SPOTLIGHT_ROLES: frozenset[str] = frozenset({"head_marketing"})

# Periscope market briefs are HoM-authored, mirroring _FEATURE_SPOTLIGHT_ROLES.
_PERISCOPE_ROLES: frozenset[str] = frozenset({"head_marketing"})

# Megaphone editorial posts are HoM-authored, mirroring _PERISCOPE_ROLES.
_MEGAPHONE_ROLES: frozenset[str] = frozenset({"head_marketing"})
_EDITORIAL_ANGLES: frozenset[str] = frozenset(
    {"dev_log", "behind_scenes", "changelog_highlight", "other"}
)
_EDITORIAL_RATIONALE_MAX_CHARS = 300

# Barfly (Board Program) conversation replies are HoM-authored, mirroring
# _PERISCOPE_ROLES.
_BARFLY_ROLES: frozenset[str] = frozenset({"head_marketing"})
_BARFLY_RATIONALE_MAX_CHARS = 300

# Market-brief free-text caps (spec §4 / Task 2). ``source_url`` is validated
# separately (a URL, not soup-checked prose) — see _reject_market_brief_url.
_MARKET_BRIEF_HEADLINE_MAX_CHARS = 200
_MARKET_BRIEF_FINDING_CLAIM_MAX_CHARS = 500
_MARKET_BRIEF_FINDING_SOURCE_URL_MAX_CHARS = 300
_MARKET_BRIEF_FINDING_RELEVANCE_MAX_CHARS = 300
_MARKET_BRIEF_POSITIONING_NOTE_MAX_CHARS = 500
_MARKET_BRIEF_LIST_MAX_ITEMS = 5  # threats / opportunities cap
_MARKET_BRIEF_LIST_ITEM_MAX_CHARS = 300

# Coroner postmortems are Auditor-authored — the one program the Auditor
# originates content for (spec §4), distinct from its curation-only
# approve/reject_playbook verbs.
_CORONER_ROLES: frozenset[str] = frozenset({"auditor"})
_CORONER_PROCESS_CHANGE_KINDS: frozenset[str] = frozenset(
    {"playbook", "prompt_fix", "conventions_rule", "other"}
)
_CORONER_INCIDENT_SUMMARY_MAX_CHARS = 500
_CORONER_ROOT_CAUSE_MAX_CHARS = 800
_CORONER_PROCESS_CHANGE_DESC_MAX_CHARS = 800

# Sentinel (Board Program) quality reports are Auditor-authored — a bounded
# expansion mirroring _PEST_ROLES/_PERISCOPE_ROLES.
_SENTINEL_ROLES: frozenset[str] = frozenset({"auditor"})

# Quality-report free-text caps (spec §4).
_QUALITY_REPORT_HEADLINE_MAX_CHARS = 200
_QUALITY_REPORT_ITEM_OBSERVATION_MAX_CHARS = 500
_QUALITY_REPORT_ITEM_EVIDENCE_MAX_CHARS = 500
_QUALITY_REPORT_ITEM_SUGGESTED_ACTION_MAX_CHARS = 300
_QUALITY_REPORT_OVERALL_ASSESSMENT_MAX_CHARS = 800
_QUALITY_REPORT_AREAS: frozenset[str] = frozenset(
    {"waivers", "findings", "conventions", "budget", "docs", "other"}
)

# Librarian (Board Program) playbook drafts are Auditor-authored, mirroring
# _SENTINEL_ROLES. Each draft is created directly via PlaybookService (the
# Coroner _draft_coroner_playbook precedent) — the Auditor does NOT also gain
# draft_playbook (see _DRAFT_PLAYBOOK_ROLES above / role_config.py).
_LIBRARIAN_ROLES: frozenset[str] = frozenset({"auditor"})
_PLAYBOOK_DRAFT_TITLE_MAX_CHARS = 200  # matches PlaybookCreate.title's own cap
_PLAYBOOK_DRAFT_BODY_MAX_CHARS = 4000
_PLAYBOOK_DRAFT_PATTERN_EVIDENCE_MAX_CHARS = 500

# Text fields on a roadmap item draft, with their anti-soup minimum length.
_ROADMAP_ITEM_TEXT_FIELDS: tuple[tuple[str, int], ...] = (
    ("title", 5),
    ("description", 15),
    ("project_slug", 2),
    ("team", 2),
    ("rationale", 8),
)

# Text fields on a pest-hunt item draft. ``evidence`` is the load-bearing one
# (spec §4: "a bug hunt without evidence is noise") — required, substantive,
# and capped so a runaway dump can't blow out the marker payload.
_PEST_HUNT_ITEM_TEXT_FIELDS: tuple[tuple[str, int], ...] = (
    ("title", 5),
    ("description", 15),
    ("project_slug", 2),
    ("team", 2),
    ("evidence", 20),
)
_PEST_HUNT_EVIDENCE_MAX_CHARS = 2000

# Text fields on a gap-fill item draft. ``evidence`` is the load-bearing one
# (spec §4: evidence must name BOTH sides of the gap — e.g. the route that
# exists and the panel surface that doesn't) — required, substantive, and
# capped so a runaway dump can't blow out the marker payload. Mirrors
# _PEST_HUNT_ITEM_TEXT_FIELDS.
_GAP_FILL_ITEM_TEXT_FIELDS: tuple[tuple[str, int], ...] = (
    ("title", 5),
    ("description", 15),
    ("project_slug", 2),
    ("team", 2),
    ("evidence", 20),
)
_GAP_FILL_EVIDENCE_MAX_CHARS = 2000

# Scales (Board Program) rebalance plans are Product-Owner-only, mirroring
# _PEST_ROLES. Unlike a roadmap/pest-control item, a rebalance item never
# drafts a NEW task — it references a LIVE one (``task_ref``) that approval
# mutates (reprioritize) or cancels, so there is no team/project-slug/
# acceptance-criteria shape to validate here, just the action + rationale.
_SCALES_ROLES: frozenset[str] = frozenset({"product_owner"})
_SCALES_ACTIONS: frozenset[str] = frozenset({"reprioritize", "cancel"})
_SCALES_VALID_PRIORITIES: frozenset[int] = frozenset({0, 1, 2, 3})
_SCALES_RATIONALE_MAX_CHARS = 500

# Text fields on a messaging-fix item draft. ``evidence`` is the load-bearing
# one (spec §4: must name the drifted claim AND the reality it contradicts)
# — required, substantive, and capped so a runaway dump can't blow out the
# marker payload. Mirrors _GAP_FILL_ITEM_TEXT_FIELDS.
_MESSAGING_FIX_ITEM_TEXT_FIELDS: tuple[tuple[str, int], ...] = (
    ("title", 5),
    ("description", 15),
    ("project_slug", 2),
    ("team", 2),
    ("evidence", 20),
)
_MESSAGING_FIX_EVIDENCE_MAX_CHARS = 2000

# War Room (Board Program) campaigns are HoM-authored, mirroring
# _FEATURE_SPOTLIGHT_ROLES/_PERISCOPE_ROLES.
_WAR_ROOM_ROLES: frozenset[str] = frozenset({"head_marketing"})
_CAMPAIGN_STAGE_LABELS: frozenset[str] = frozenset(
    {"teaser", "launch", "follow_up", "spotlight", "other"}
)
_CAMPAIGN_NAME_MAX_CHARS = 100
_CAMPAIGN_MIN_POSTS = 2
_CAMPAIGN_MAX_POSTS = 6

# Dogfood (Board Program) friction-fix audits are Product-Owner-only,
# mirroring _GAP_FILL_ROLES.
_DOGFOOD_ROLES: frozenset[str] = frozenset({"product_owner"})

# Text fields on a friction-fix item draft. ``evidence`` is the load-bearing
# one (spec §4: the actual walked path — clicks/pages — plus what broke or
# felt wrong, prose, no screenshots) — required, substantive, and capped so
# a runaway dump can't blow out the marker payload. Mirrors
# _GAP_FILL_ITEM_TEXT_FIELDS.
_FRICTION_FIXES_ITEM_TEXT_FIELDS: tuple[tuple[str, int], ...] = (
    ("title", 5),
    ("description", 15),
    ("project_slug", 2),
    ("team", 2),
    ("evidence", 20),
)
_FRICTION_FIXES_EVIDENCE_MAX_CHARS = 2000

# nothing_to_propose is registry-driven, not role-frozenset-gated like every
# verb above — it resolves the caller's NAMED task, derives the program from
# that task's own source, then requires the caller's role to equal THAT
# program's declared explorer role
# (roboco.foundation.policy.board_programs.PROGRAMS), so a program registered
# later needs no edit here.
_NOTHING_TO_PROPOSE_REASON_MIN_CHARS = 15
_NOTHING_TO_PROPOSE_REASON_MAX_CHARS = 800

# Playbook curation RBAC: delivery roles DRAFT; only the Auditor CURATES.
# The Auditor is deliberately NOT in this set — "auditor curates but does not
# draft" is an enforced invariant (test_playbook_verbs.py). A Coroner
# postmortem's playbook-kind process change is drafted through a DIFFERENT
# path — directly via PlaybookService inside propose_postmortem, never this
# do-verb (see _draft_coroner_playbook below) — so the invariant holds even
# though the Auditor now originates playbook drafts by another route; that
# draft rides the SAME pending-playbook curation queue every delivery-role
# draft does, curated by the Auditor same as any other, never self-approved
# inline.
_DRAFT_PLAYBOOK_ROLES: frozenset[str] = frozenset(
    {"developer", "qa", "documenter", "cell_pm", "main_pm"}
)
_CURATE_PLAYBOOK_ROLES: frozenset[str] = frozenset({"auditor"})

# Vault-curation: only the Auditor writes a root task-tree's narrative
# (mirrors the playbook-curation bounded-expansion pattern above).
_CURATE_VAULT_ROLES: frozenset[str] = frozenset({"auditor"})

# propose_video's target-platform set + TikTok caption limit (the X caption
# reuses MAX_TWEET_CHARS). No role frozenset here, unlike the sets above:
# propose_video is gated on the caller's TEAM at runtime (_caller_team), not
# role — Role.DEVELOPER doesn't distinguish a ux-dev from a be-dev/fe-dev.
# Kept in lockstep with video-renderer/server.js COMPOSITION_ID_RE.
_COMPOSITION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$")
_VIDEO_PLATFORMS: frozenset[str] = frozenset({"x", "tiktok"})
_MAX_TIKTOK_CAPTION_CHARS = 2200


def _coerce_pitch_cells(target_cells: list[str]) -> list[Any]:
    """Validate target-cell slugs into Team values; raise ValueError on a bad one."""
    from roboco.foundation.identity import CELL_TEAMS, Team

    cells: list[Any] = []
    for c in target_cells:
        try:
            team = Team(c)
        except ValueError as exc:
            raise ValueError(f"unknown cell {c!r}") from exc
        if team not in CELL_TEAMS:
            raise ValueError(f"{c!r} is not a cell team")
        cells.append(team)
    return cells


def _normalize_roadmap_item(idx: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce a validated raw item dict into the stored marker shape.

    ``id`` is server-assigned (index-based, stable within the cycle) — the PO
    never sets it, so there's no collision/typo surface for the CEO's
    per-item approve/reject to key on.
    """
    priority = raw.get("priority")
    try:
        priority = int(priority) if priority is not None else 2
    except (TypeError, ValueError):
        priority = 2
    return {
        "id": f"item-{idx}",
        "title": str(raw["title"]).strip(),
        "description": str(raw["description"]).strip(),
        "acceptance_criteria": [str(c).strip() for c in raw["acceptance_criteria"]],
        "project_slug": str(raw["project_slug"]).strip(),
        "team": str(raw["team"]).strip(),
        "priority": priority,
        "rationale": str(raw["rationale"]).strip(),
        "status": "proposed",
        "reject_reason": None,
        "materialized_task_id": None,
    }


def _normalize_pest_hunt_item(idx: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce a validated raw pest-hunt item dict into the stored marker
    shape. Mirrors ``_normalize_roadmap_item`` — ``id`` is server-assigned."""
    priority = raw.get("priority")
    try:
        priority = int(priority) if priority is not None else 2
    except (TypeError, ValueError):
        priority = 2
    return {
        "id": f"item-{idx}",
        "title": str(raw["title"]).strip(),
        "description": str(raw["description"]).strip(),
        "acceptance_criteria": [str(c).strip() for c in raw["acceptance_criteria"]],
        "project_slug": str(raw["project_slug"]).strip(),
        "team": str(raw["team"]).strip(),
        "priority": priority,
        "evidence": str(raw["evidence"]).strip(),
        "status": "proposed",
        "reject_reason": None,
        "materialized_task_id": None,
    }


def _normalize_gap_fill_item(idx: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce a validated raw gap-fill item dict into the stored marker
    shape. Mirrors ``_normalize_pest_hunt_item`` — ``id`` is server-assigned."""
    priority = raw.get("priority")
    try:
        priority = int(priority) if priority is not None else 2
    except (TypeError, ValueError):
        priority = 2
    return {
        "id": f"item-{idx}",
        "title": str(raw["title"]).strip(),
        "description": str(raw["description"]).strip(),
        "acceptance_criteria": [str(c).strip() for c in raw["acceptance_criteria"]],
        "project_slug": str(raw["project_slug"]).strip(),
        "team": str(raw["team"]).strip(),
        "priority": priority,
        "evidence": str(raw["evidence"]).strip(),
        "status": "proposed",
        "reject_reason": None,
        "materialized_task_id": None,
    }


def _normalize_scales_item(
    idx: int, raw: dict[str, Any], target: Any
) -> dict[str, Any]:
    """Coerce a validated raw rebalance item dict + its resolved target task
    into the stored marker shape. Mirrors ``_normalize_pest_hunt_item`` —
    ``id`` is server-assigned. Unlike a pest-hunt item there is no draft to
    normalize: ``target`` (resolved by ``TaskService.resolve_scales_task_ref``
    before this is called) supplies the id/title actually acted on."""
    action = str(raw["action"]).strip()
    return {
        "id": f"item-{idx}",
        "task_ref": str(raw["task_ref"]).strip(),
        "target_task_id": str(target.id),
        "target_task_title": target.title,
        "action": action,
        "new_priority": raw.get("new_priority") if action == "reprioritize" else None,
        "rationale": str(raw["rationale"]).strip(),
        "status": "proposed",
        "reject_reason": None,
        "executed_detail": None,
    }


def _normalize_messaging_fix_item(idx: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce a validated raw messaging-fix item dict into the stored marker
    shape. Mirrors ``_normalize_gap_fill_item`` — ``id`` is server-assigned."""
    priority = raw.get("priority")
    try:
        priority = int(priority) if priority is not None else 2
    except (TypeError, ValueError):
        priority = 2
    return {
        "id": f"item-{idx}",
        "title": str(raw["title"]).strip(),
        "description": str(raw["description"]).strip(),
        "acceptance_criteria": [str(c).strip() for c in raw["acceptance_criteria"]],
        "project_slug": str(raw["project_slug"]).strip(),
        "team": str(raw["team"]).strip(),
        "priority": priority,
        "evidence": str(raw["evidence"]).strip(),
        "status": "proposed",
        "reject_reason": None,
        "materialized_task_id": None,
    }


def _normalize_friction_fix_item(idx: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce a validated raw friction-fix item dict into the stored marker
    shape. Mirrors ``_normalize_messaging_fix_item`` — ``id`` is server-
    assigned."""
    priority = raw.get("priority")
    try:
        priority = int(priority) if priority is not None else 2
    except (TypeError, ValueError):
        priority = 2
    return {
        "id": f"item-{idx}",
        "title": str(raw["title"]).strip(),
        "description": str(raw["description"]).strip(),
        "acceptance_criteria": [str(c).strip() for c in raw["acceptance_criteria"]],
        "project_slug": str(raw["project_slug"]).strip(),
        "team": str(raw["team"]).strip(),
        "priority": priority,
        "evidence": str(raw["evidence"]).strip(),
        "status": "proposed",
        "reject_reason": None,
        "materialized_task_id": None,
    }


def _normalize_market_brief_finding(idx: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce a validated raw market-brief finding into the stored marker
    shape. Mirrors ``_normalize_pest_hunt_item`` — ``id`` is server-assigned.

    ``status``/``reject_reason``/``materialized_task_id`` mirror the roadmap/
    pest-hunt item shape even though the exploration task itself completes
    at propose time — the finding still carries its OWN per-item CEO
    decision (``PeriscopeService.approve_finding``/``reject_finding``),
    orthogonal to the task's own terminal status.
    """
    return {
        "id": f"finding-{idx}",
        "claim": str(raw["claim"]).strip(),
        "source_url": str(raw["source_url"]).strip(),
        "relevance": str(raw["relevance"]).strip(),
        "status": "proposed",
        "reject_reason": None,
        "materialized_task_id": None,
    }


def _normalize_quality_report_item(idx: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce a validated raw quality-report item into the stored marker
    shape. Mirrors ``_normalize_market_brief_finding`` — ``id`` is
    server-assigned, and the same per-item ``status`` triple applies (see
    ``SentinelService.approve_item``/``reject_item``)."""
    return {
        "id": f"item-{idx}",
        "area": str(raw["area"]).strip(),
        "observation": str(raw["observation"]).strip(),
        "evidence": str(raw["evidence"]).strip(),
        "suggested_action": str(raw["suggested_action"]).strip(),
        "status": "proposed",
        "reject_reason": None,
        "materialized_task_id": None,
    }


def _render_market_brief_for_screening(
    headline: str,
    findings: list[dict[str, Any]],
    threats: list[str],
    opportunities: list[str],
    positioning_note: str,
) -> str:
    """One line per content piece — every line is independently checked by
    ``screen_external_text``, so a single injected line among otherwise-clean
    web-derived content is flagged without dropping the rest of the brief."""
    lines = [f"Headline: {headline}"]
    for f in findings:
        lines.append(f"Finding: {f['claim']} (source: {f['source_url']})")
        lines.append(f"Relevance: {f['relevance']}")
    lines.extend(f"Threat: {t}" for t in threats)
    lines.extend(f"Opportunity: {o}" for o in opportunities)
    if positioning_note:
        lines.append(f"Positioning: {positioning_note}")
    return "\n".join(lines)


class ContentActions:
    def __init__(self, deps: ContentActionsDeps) -> None:
        self._deps = deps

    @property
    def task(self) -> Any:
        return self._deps.task

    @property
    def git(self) -> Any:
        return self._deps.git

    @property
    def a2a(self) -> Any:
        return self._deps.a2a

    @property
    def journal(self) -> Any:
        return self._deps.journal

    @property
    def workspace(self) -> Any:
        return self._deps.workspace

    @property
    def notifications(self) -> Any:
        return self._deps.notifications

    @property
    def evidence_repo(self) -> Any:
        return self._deps.evidence_repo

    @property
    def orchestrator(self) -> Any:
        return self._deps.orchestrator

    async def _touch_heartbeat(self, task_id: UUID | None) -> None:
        """Best-effort heartbeat refresh on a content-write success path.

        Mirrors the choreographer's rejection-path heartbeat: an agent that
        is actively committing / posting progress is alive, so refresh
        ``last_heartbeat_at`` here too — otherwise the reaper sees the claim
        as stale between verb successes. Wrapped in ``suppress`` so a
        heartbeat write failure can never alter the response the agent gets.
        """
        if task_id is None:
            return
        with contextlib.suppress(Exception):
            await self.task.heartbeat(task_id)

    async def _active_claim_violation(
        self, agent_id: UUID, task: Any
    ) -> Envelope | None:
        """Refuse a content write when the caller is not the active claimant.

        ``assigned_to`` alone is insufficient: a reaped or handed-off agent
        keeps ``assigned_to`` until reassignment, but ``active_claimant_id``
        is cleared the moment its claim is released. Only the holder of the
        active claim may write. A board co-reviewer on a coordination task is
        exempt (it shares the task with the other board member by design).
        """
        claimant = getattr(task, "active_claimant_id", None)
        if claimant == agent_id:
            return None
        if await self._board_may_co_review(agent_id, task):
            return None
        return _not_active_claimant(task.id)

    @staticmethod
    def _reject_soup(value: str, *, field: str, min_chars: int = 3) -> Envelope | None:
        """Universal anti-soup guard for agent free-text.

        Returns a remediation Envelope (never a raw 422 — a 422 at the route
        trips the do-server circuit breaker) when ``value`` is empty, too short,
        or a placeholder/filler token, so soup lands NOWHERE. ``None`` = clean.
        """
        try:
            reject_trivial(value, field=field, min_chars=min_chars)
        except ValueError as exc:
            return Envelope.invalid_state(
                message=str(exc),
                remediate=(
                    f"write a substantive {field} (>={min_chars} chars, no filler "
                    "like 'asdf'/'wip'/'tbd'/'...'); state what actually happened."
                ),
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_structured_soup(
        cls, scope: str, structured: dict[str, Any] | None
    ) -> Envelope | None:
        """Soup-guard the scope's narrative sub-fields when the agent fills them.

        Only *provided, non-empty* fields are checked — an omitted narrative
        field keeps its tolerant ``(not provided)`` placeholder default (so a
        thin note is never hard-rejected, preserving the do-server breaker
        contract), but ``rationale='asdf'`` is soup and lands nowhere.
        """
        for field in _SCOPE_NARRATIVE_FIELDS.get(scope, ()):
            value = (structured or {}).get(field)
            if not (value and str(value).strip()):
                continue
            if rej := cls._reject_soup(str(value), field=field, min_chars=4):
                return rej
        return None

    @classmethod
    def _pr_update_input_check(
        cls, title: str | None, body: str | None, reviewers: list[str] | None
    ) -> Envelope | None:
        """At-least-one-field + anti-soup gate for ``pr_update`` inputs.

        Folds the no-op guard and the title/body soup guard into one call so
        the verb body keeps its return count under the complexity bound.
        """
        if title is None and body is None and reviewers is None:
            return Envelope.invalid_state(
                message="no fields to update",
                remediate=(
                    "provide at least one of title, body, or reviewers; "
                    "passing all None has no effect"
                ),
                context_briefing={},
            )
        for _pf, _pv, _min in (("title", title, 8), ("body", body, 15)):
            if _pv is not None and (
                rej := cls._reject_soup(_pv, field=_pf, min_chars=_min)
            ):
                return rej
        return None

    async def commit(
        self,
        *,
        agent_id: UUID,
        message: str,
        files: list[str] | None = None,
    ) -> Envelope:
        """Make a git commit on the agent's active task branch.

        Auto-prefixes [task-id], validates message via commit_validator,
        records progress entry from the commit message.
        """
        agent = await self.task.agent_for(agent_id)
        caller_role = str(agent.role) if agent is not None else ""
        if caller_role not in _COMMIT_ALLOWED_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role '{caller_role}' may not commit code; only"
                    " developers and documenters write commits"
                ),
                remediate=(
                    "PMs delegate code work via delegate(); board members"
                    " do not write code. If you intended to record an"
                    " observation, use note() instead."
                ),
                context_briefing={},
            )
        subject = _strip_task_prefix(_strip_ai_attribution(message)).strip()
        result = validate_commit_message(
            subject,
            min_chars=settings.commit_subject_min_chars,
            banned_words=settings.commit_banned_words,
        )
        if not result.ok:
            return Envelope.invalid_state(
                message=result.reason or "commit message invalid",
                remediate=result.remediate or "",
                context_briefing={},
            )
        t = await self.task.get_active_task_for_agent(agent_id)
        if t is None:
            return Envelope.invalid_state(
                message="no active task; cannot commit",
                remediate="call give_me_work() first",
                context_briefing={},
            )
        if reject := await self._active_claim_violation(agent_id, t):
            return reject
        canonical_prefix = f"[{str(t.id)[:8]}]"
        final_message = f"{canonical_prefix} {subject}"
        try:
            commit_result = await self.git.commit(
                branch_name=t.branch_name,
                message=final_message,
                task_id=t.id,
                files=files,
            )
        except GitError as exc:
            return self._commit_git_error_envelope(exc, files=files)
        sha = commit_result.get("sha", "")
        await self.task.add_progress(
            t.id, agent_id, f"committed {sha[:8]}: {final_message}"
        )
        await self._touch_heartbeat(t.id)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(t.id),
            next="continue committing, or open_pr when ready",
            context_briefing={},
        )

    @staticmethod
    def _commit_git_error_envelope(
        exc: GitError, *, files: list[str] | None
    ) -> Envelope:
        """Map a failed `git commit` onto an actionable invalid_state envelope.

        A "no changes added to commit" / "nothing to commit" failure means
        the passed `files` matched no modified paths; anything else is a
        generic git failure the agent should inspect and retry.
        """
        text = str(exc)
        if files and (
            "no changes added to commit" in text or "nothing to commit" in text
        ):
            remediate = (
                f"the files list {files!r} matched no modified paths; omit "
                "files to stage all changes, or pass the exact modified paths"
            )
        else:
            remediate = "inspect the git error above and retry"
        return Envelope.invalid_state(
            message=text,
            remediate=remediate,
            context_briefing={},
        )

    # Board roles co-review board/coordination tasks: a
    # board/coordination task is dispatched to BOTH the Product Owner and the
    # Head of Marketing, but it carries a single ``assigned_to``. The
    # non-assignee reviewer must still be able to record its review note on
    # that task, so a board role posting content to a board/coordination task
    # is exempt from the strict single-assignee ownership gate.
    _BOARD_ROLES: ClassVar[frozenset[str]] = frozenset(
        {"product_owner", "head_marketing"}
    )

    @staticmethod
    def _is_coordination_task(task: Any) -> bool:
        """True for a board/fan-out task: carries a product but no repo of its own.

        Mirrors the orchestrator's ``_is_coordination_task``. Such a task does
        no git work of its own (no ``project_id``) and is the shared subject of
        the two-reviewer board review, so both board members may attach content.
        """
        return getattr(task, "project_id", None) is None and bool(
            getattr(task, "product_id", None)
        )

    async def _board_may_co_review(self, agent_id: UUID, task: Any) -> bool:
        """True iff a board role is posting to a board/coordination task.

        Lets the non-assignee board reviewer record its review on a task held
        by the other board member, without widening ownership for any other
        role or any project-backed task.
        """
        if not self._is_coordination_task(task):
            return False
        agent = await self.task.agent_for(agent_id)
        role = str(agent.role) if agent is not None else ""
        return role in self._BOARD_ROLES

    async def _verify_explicit_task_ownership(
        self, agent_id: UUID, task_id: UUID
    ) -> Envelope | None:
        """Gate Set D: refuse content posts on tasks the caller does not own.

        Only call this for *explicit* task_id (caller passed it themselves).
        Auto-fill from get_journal_context_task_for_agent is implicitly
        self-owned and does not need a re-check.

        Allows ``assigned_to=None`` (post-handoff transient state) so QA /
        documenter can still inspect tasks between reassignments. A board role
        co-reviewing a board/coordination task is also allowed even when the
        task is assigned to the other board member.
        """
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        if t.assigned_to is not None and t.assigned_to != agent_id:
            if await self._board_may_co_review(agent_id, t):
                return None
            return _ownership_violation(task_id)
        # assigned_to is stale across a reap/handoff (persists until
        # reassignment; active_claimant_id is cleared on release). Require
        # the active claim so a reaped agent can't keep posting.
        if t.assigned_to == agent_id:
            return await self._active_claim_violation(agent_id, t)
        return None

    async def note(
        self,
        *,
        agent_id: UUID,
        text: str,
        scope: str = "note",
        task_id: UUID | None = None,
        structured: dict[str, Any] | None = None,
        section: dict[str, Any] | None = None,
        done: str = "",
        next: str = "",
        where_to_look: list[str] | None = None,
    ) -> Envelope:
        """Write a journal entry, or (scope='handoff') the role's note section.

        scope ∈ note|decision|reflect|learning|struggle write the JOURNAL;
        scope='handoff' writes the agent's dedicated SECTION (dev_notes /
        quick_context / auditor_notes …) from ``section`` (or a summary from
        ``text`` when ``section`` is omitted).

        ``structured`` carries scope-specific fields:

        - decision: context, options[], chosen, rationale, consequences
        - reflect: what_done, what_learned, what_struggled, next_steps

        For ``scope='handoff'`` the resumption section (PM / coordinator
        roles) can be authored two ways: the nested ``section`` dict, OR the
        top-level ``done`` / ``next`` / ``where_to_look`` string fields. The
        top-level path is the LLM-facing contract — ``section: dict[str, Any]``
        renders a tool schema with no visible sub-fields, so a weak model
        (minimax-m3) emits ``section={}`` and the resumption gate rejects
        ``done — Field required``; the top-level typed strings show up in the
        tool schema as discrete fields the same model fills fine (proven on
        the decision scope). Top-level fields fill any keys the explicit
        ``section`` omits without overwriting supplied ones.

        The note is always recorded. List-typed fields tolerate a
        lone scalar (wrapped into a one-element list) and missing decision/
        reflect narrative fields default to a visible placeholder, so a
        well-intentioned note is never hard-rejected (which previously tripped
        the do-server circuit breaker on repeated ``incomplete_input``).

        Non-None fields are formatted into the entry content as markdown
        sections so the panel's Decisions / Reflections views render
        them as named blocks instead of a one-line phrase. The ``title``
        is taken from ``structured["title"]`` when present, otherwise
        from the first line of ``text``.
        """
        if scope == "handoff":
            # Section write (dev_notes / quick_context / auditor_notes / …), not
            # a journal entry. Content quality is enforced by the content model
            # (apply_structured_note), so skip the journal-text soup check.
            return await self._record_section_handoff(
                agent_id=agent_id,
                text=text,
                task_id=task_id,
                structured=_merge_resumption_fields(
                    section, done=done, next=next, where_to_look=where_to_look
                ),
            )
        return await self._write_journal_note(
            agent_id=agent_id,
            text=text,
            scope=scope,
            task_id=task_id,
            structured=structured,
        )

    async def _write_journal_note(
        self,
        *,
        agent_id: UUID,
        text: str,
        scope: str,
        task_id: UUID | None,
        structured: dict[str, Any] | None,
    ) -> Envelope:
        """Validate + persist a journal entry for the non-handoff scopes
        (note|decision|reflect|learning|struggle). Extracted from ``note`` so
        both stay under the cyclomatic-complexity bound.
        """
        if rej := self._reject_soup(text, field="note", min_chars=8):
            return rej
        if scope not in _VALID_NOTE_SCOPES:
            return Envelope.invalid_state(
                message=f"invalid scope {scope!r}",
                remediate=f"scope must be one of: {sorted(_VALID_NOTE_SCOPES)}",
                context_briefing={},
            )
        if rej := self._reject_structured_soup(scope, structured):
            return rej
        if task_id is not None:
            if reject := await self._verify_explicit_task_ownership(agent_id, task_id):
                return reject
        else:
            t = await self.task.get_journal_context_task_for_agent(agent_id)
            if t is not None:
                task_id = t.id
        # Tolerate thin notes instead of rejecting them. List-typed
        # fields accept a lone scalar; missing decision/reflect narrative fields
        # are defaulted to a visible placeholder. The note is always recorded so
        # the audit trail survives and a well-intentioned note never trips the
        # do-server circuit breaker on repeated `incomplete_input`.
        s = _normalize_structured(scope, structured or {})
        title = (s.get("title") or text.split("\n", 1)[0])[:200]
        content = _render_journal_content(scope, text, s)
        await self.journal.write_entry(
            agent_id=agent_id,
            task_id=task_id,
            scope=scope,
            title=title,
            content=content,
        )
        await self._touch_heartbeat(task_id)
        return Envelope.ok(
            status="noted",
            task_id=str(task_id) if task_id else None,
            next="continue",
            context_briefing={},
        )

    async def _caller_role(self, agent_id: UUID) -> str:
        agent = await self.task.agent_for(agent_id)
        return str(agent.role) if agent is not None else ""

    async def _caller_team(self, agent_id: UUID) -> Team | None:
        """The caller's Team (or None), via the same agent_for lookup
        _caller_role uses. Team-scoped verbs (propose_video) need this
        instead of role: Role.DEVELOPER alone can't tell a ux-dev from a
        be-dev/fe-dev — the team is the only signal that distinguishes them.
        """
        from roboco.foundation.identity import Team

        agent = await self.task.agent_for(agent_id)
        if agent is None or not agent.team:
            return None
        try:
            return Team(agent.team)
        except ValueError:
            return None

    async def _trace_board_proposal(
        self, *, agent_id: UUID, verb: str, payload: dict[str, Any]
    ) -> None:
        """Durably trace a board-program ``propose_*`` payload BEFORE any
        validation runs.

        The 2026-07-25 incident: a rejected/mis-persisted proposal came back
        as a 200 carrying an error envelope, with the raw payload living
        nowhere but that one HTTP response — unrecoverable once the caller
        moved on. Uses ``AuditService``'s own independent session/commit
        (never ``self.task.session``), so the trace survives even when this
        verb's own validation rejects the call outright and no downstream
        write ever happens. Best-effort — ``AuditService.log_event`` never
        raises, so a trace failure can never block the proposal itself.
        """
        from roboco.services.audit import get_audit_service

        await get_audit_service().log_event(
            event_type="board_program.proposal_trace",
            agent_id=agent_id,
            details={"verb": verb, "payload": payload},
            severity="info",
        )

    async def draft_playbook(
        self,
        *,
        agent_id: UUID,
        title: str,
        problem: str,
        procedure: str,
        tags: list[str] | None = None,
        source_task_id: UUID | None = None,
    ) -> Envelope:
        """Draft a curated playbook (delivery roles); the Auditor approves it."""
        role = await self._caller_role(agent_id)
        if role not in _DRAFT_PLAYBOOK_ROLES:
            return Envelope.not_authorized(
                message=f"role {role!r} may not draft a playbook",
                remediate="Only delivery roles draft playbooks; the Auditor curates.",
                context_briefing={},
            )
        from roboco.models.playbook import PlaybookCreate
        from roboco.services.base import ConflictError
        from roboco.services.playbook import get_playbook_service

        try:
            playbook = await get_playbook_service(self.task.session).draft(
                PlaybookCreate(
                    title=title,
                    problem=problem,
                    procedure=procedure,
                    tags=tags or [],
                    source_task_id=source_task_id,
                ),
                created_by=agent_id,
            )
        except ConflictError as exc:
            return Envelope.invalid_state(
                message=str(exc),
                remediate="Use a more distinct title (the slug must be unique).",
                context_briefing={},
            )
        return Envelope.ok(
            status="playbook_drafted",
            task_id=None,
            next="continue",
            context_briefing={
                "playbook_id": str(playbook.id),
                "playbook_status": "draft",
            },
        )

    async def approve_playbook(self, *, agent_id: UUID, playbook_id: UUID) -> Envelope:
        """Auditor approves a draft playbook (-> approved + indexed)."""
        return await self._curate_playbook(
            agent_id=agent_id, playbook_id=playbook_id, action="approve"
        )

    async def reject_playbook(
        self, *, agent_id: UUID, playbook_id: UUID, reason: str
    ) -> Envelope:
        """Auditor rejects a playbook (-> archived, with a reason)."""
        return await self._curate_playbook(
            agent_id=agent_id,
            playbook_id=playbook_id,
            action="reject",
            reason=reason,
        )

    async def archive_playbook(self, *, agent_id: UUID, playbook_id: UUID) -> Envelope:
        """Auditor archives an approved playbook (-> archived, retired)."""
        return await self._curate_playbook(
            agent_id=agent_id,
            playbook_id=playbook_id,
            action="archive",
        )

    async def _curate_playbook(
        self,
        *,
        agent_id: UUID,
        playbook_id: UUID,
        action: str,
        reason: str = "",
    ) -> Envelope:
        """Shared Auditor-only curation path for approve / reject / archive."""
        role = await self._caller_role(agent_id)
        if role not in _CURATE_PLAYBOOK_ROLES:
            return Envelope.not_authorized(
                message=f"role {role!r} may not curate playbooks",
                remediate="Only the Auditor approves/rejects/archives playbooks.",
                context_briefing={},
            )
        from roboco.services.base import ConflictError, NotFoundError
        from roboco.services.playbook import get_playbook_service

        svc = get_playbook_service(self.task.session)
        try:
            if action == "approve":
                playbook = await svc.approve(playbook_id, approver_id=agent_id)
                status = "playbook_approved"
            elif action == "archive":
                playbook = await svc.archive(playbook_id, approver_id=agent_id)
                status = "playbook_archived"
            else:
                playbook = await svc.reject(
                    playbook_id, approver_id=agent_id, reason=reason or action
                )
                status = "playbook_archived"
        except NotFoundError:
            return Envelope.not_found(message=f"playbook {playbook_id} not found")
        except ConflictError as exc:
            # A status-precondition violation (approve/reject on a non-draft,
            # archive on a non-approved) is a clean invalid_state, not a 500 —
            # the agent gets a remediate hint to re-fetch the playbook's
            # current status before re-trying.
            return Envelope.invalid_state(
                message=str(exc),
                remediate=(
                    "Only a draft can be approved/rejected; only an approved "
                    "playbook can be archived. Re-list drafts/approved to see "
                    "the playbook's current status before re-trying."
                ),
                context_briefing={"playbook_id": str(playbook_id)},
            )
        # Commit the status change BEFORE touching the RAG index: the index write
        # runs through its own auto-committing connection, so indexing before the
        # status commit would durably land (or drop) a playbook in the corpus even
        # if this transaction rolled back — a divergence agents surface in
        # briefings. ``get_db`` commits the session again after the route returns
        # (a no-op on the now-clean transaction); this explicit commit is what
        # gates the index. A poisoned session (a prior mid-verb failure rolled it
        # back -> PendingRollbackError) must NOT 500 the curation verb NOR fall
        # through to index an uncommitted playbook: surface a clean invalid_state
        # and skip the index. See #55.
        from sqlalchemy.exc import PendingRollbackError

        try:
            await self.task.session.commit()
        except PendingRollbackError:
            logger.warning(
                "playbook curate: gating commit on a rolled-back session",
                action=action,
                playbook_id=str(playbook_id),
            )
            return Envelope.invalid_state(
                message=(
                    "the DB session was rolled back by a prior failure; the "
                    "playbook status change was not committed"
                ),
                remediate=(
                    "re-fetch the playbook's current status and re-try the "
                    "curation verb"
                ),
                context_briefing={"playbook_id": str(playbook_id)},
            )
        if action == "approve":
            await svc.index_approved(playbook)
        else:
            await svc.unindex_playbook(playbook)
        return Envelope.ok(
            status=status,
            task_id=None,
            next="continue",
            context_briefing={
                "playbook_id": str(playbook.id),
                "playbook_status": str(playbook.status),
            },
        )

    async def curate_vault(
        self, *, agent_id: UUID, task_id: UUID, narrative: str
    ) -> Envelope:
        """Auditor-only: write a root task-tree's vault narrative section.

        Fully re-materializes the task's note (parent/subtasks/dependencies
        resolved fresh) with ``narrative`` filling the ``## Narrative``
        section a deterministic write otherwise leaves as a placeholder.
        """
        role = await self._caller_role(agent_id)
        if role not in _CURATE_VAULT_ROLES:
            return Envelope.not_authorized(
                message=f"role {role!r} may not curate the vault",
                remediate="Only the Auditor writes vault narratives.",
                context_briefing={},
            )
        if not settings.obsidian_vault_enabled:
            return Envelope.invalid_state(
                message="the Obsidian vault is disabled",
                remediate="ROBOCO_OBSIDIAN_VAULT_ENABLED is off — nothing to curate.",
                context_briefing={},
            )
        task = await self.task.get(task_id)
        if task is None:
            return Envelope.not_found(message=f"task {task_id} not found")

        from roboco.services.project import get_project_service
        from roboco.services.vault_assembly import assemble_task_note_data
        from roboco.services.vault_writer import get_vault_writer

        try:
            data = await assemble_task_note_data(
                self.task,
                get_project_service(self.task.session),
                task,
                narrative=narrative,
            )
            get_vault_writer().write_task(data)
        except Exception as exc:
            logger.warning(
                "vault curation write failed", task_id=str(task_id), error=str(exc)
            )
            return Envelope.invalid_state(
                message=f"vault write failed: {exc}",
                remediate="retry curate_vault; check ROBOCO_VAULT_PATH is writable",
                context_briefing={},
            )
        return Envelope.ok(
            status="vault_curated",
            task_id=str(task_id),
            next="continue",
            context_briefing={},
        )

    async def _record_section_handoff(
        self,
        *,
        agent_id: UUID,
        text: str,
        task_id: UUID | None,
        structured: dict[str, Any] | None,
    ) -> Envelope:
        """Write the agent's dedicated note SECTION — the structured-content
        counterpart to a journal note. ``note()`` only ever wrote the journal;
        this is how a developer / PM / auditor authors dev_notes / quick_context
        / auditor_notes (etc.).

        Routes by role to the right content type, persists through the
        ``apply_structured_note`` chokepoint, and also drops a journal trail
        entry so the write shows in the activity log (and the auditor's
        session has a signal). Validation failures return a remediation
        Envelope, never a raw 422.
        """
        agent = await self.task.agent_for(agent_id)
        role = str(agent.role) if agent is not None else ""
        content_type = content_type_for_role(role)
        if content_type is None:
            return Envelope.invalid_state(
                message=f"role {role!r} has no dedicated note section",
                remediate=(
                    "only developer / qa / documenter / pr_reviewer / auditor / "
                    "cell_pm / main_pm author a section — use scope='note' (or "
                    "decision/reflect/learning/struggle) for a journal entry"
                ),
                context_briefing={},
            )
        if task_id is not None:
            if reject := await self._verify_explicit_task_ownership(agent_id, task_id):
                return reject
        else:
            t = await self.task.get_journal_context_task_for_agent(agent_id)
            if t is None:
                return Envelope.invalid_state(
                    message="no task to attach the section note to",
                    remediate="pass task_id='<the task whose section you write>'",
                    context_briefing={},
                )
            task_id = t.id
        payload: dict[str, Any] = dict(structured) if structured else {"summary": text}
        try:
            await self.task.record_section_note(task_id, content_type, payload)
        except ContentValidationError as exc:
            return Envelope.invalid_state(
                message=f"section note rejected: {exc.field} — {exc.reason}",
                remediate=(
                    f"provide the section's fields via structured=... for content "
                    f"type {content_type!r} (resumption needs done+next; auditor "
                    "needs summary+severity; others need a substantive summary), "
                    "then retry"
                ),
                context_briefing={},
            )
        await self.journal.write_entry(
            agent_id=agent_id,
            task_id=task_id,
            scope="note",
            title=text.split("\n", 1)[0][:200] if text else f"{content_type} note",
            content=text or "(structured section note)",
        )
        await self._touch_heartbeat(task_id)
        return Envelope.ok(
            status="noted",
            task_id=str(task_id),
            next="continue",
            context_briefing={},
        )

    async def pitch(
        self,
        *,
        agent_id: UUID,
        title: str,
        slug: str,
        problem: str,
        proposed_solution: str,
        target_cells: list[str],
    ) -> Envelope:
        """Board (PO / Head of Marketing) proposes a product for the CEO to approve.

        A pitch is content, not a lifecycle transition: it records the Board's
        proposal. On CEO approval the system provisions a repo per target cell,
        registers the projects, and seeds the first Main-PM task.
        """
        for _pf, _pv, _min in (
            ("title", title, 5),
            ("slug", slug, 2),
            ("problem", problem, 15),
            ("proposed_solution", proposed_solution, 15),
        ):
            if rej := self._reject_soup(_pv, field=_pf, min_chars=_min):
                return rej
        from pydantic import ValidationError as PydanticValidationError

        from roboco.models.pitch import PitchCreate
        from roboco.services.base import ConflictError, ValidationError
        from roboco.services.pitch import get_pitch_service

        agent = await self.task.agent_for(agent_id)
        caller_role = str(agent.role) if agent is not None else ""
        if caller_role not in _PITCH_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {caller_role!r} cannot pitch; only the Board "
                    "(product_owner / head_marketing) may propose products"
                ),
                remediate="this verb is Board-only",
                context_briefing={},
            )
        try:
            create = PitchCreate(
                title=title,
                slug=slug,
                problem=problem,
                proposed_solution=proposed_solution,
                target_cells=_coerce_pitch_cells(target_cells),
            )
            pitch = await get_pitch_service(self.task.session).create(
                create, created_by=agent_id
            )
        except (
            ConflictError,
            ValidationError,
            PydanticValidationError,
            ValueError,
        ) as exc:
            detail = getattr(exc, "message", None) or str(exc)
            return Envelope.invalid_state(
                message=detail,
                remediate="fix the pitch fields and retry",
                context_briefing={},
            )
        await self._notify_pitch(pitch)
        return Envelope.ok(
            status="proposed",
            task_id=str(pitch.id),
            next="await the CEO's approval in the Pitches queue",
            context_briefing={},
        )

    async def _notify_pitch(self, pitch: Any) -> None:
        """Best-effort CEO nudge the moment a pitch is proposed — without it
        a pitch rots silently until the CEO happens to open the Pitches
        queue. A send failure never fails ``pitch()`` itself."""
        if self._deps.notification_delivery is None:
            return
        try:
            # Savepoint: this persists a notification row, so a mid-flush DB
            # failure swallowed here would otherwise poison the session and
            # blow up the commit-at-send with PendingRollbackError.
            async with self.task.session.begin_nested():
                await self._deps.notification_delivery.notify_ceo_of_pitch(pitch=pitch)
        except Exception as exc:
            logger.warning("pitch telegram notify failed (best-effort)", error=str(exc))

    @classmethod
    def _reject_roadmap_item_fields(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the text + acceptance-criteria fields of one item dict."""
        for field, min_chars in _ROADMAP_ITEM_TEXT_FIELDS:
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                return Envelope.invalid_state(
                    message=f"item {idx} is missing '{field}'",
                    remediate=f"provide a substantive '{field}' for item {idx}",
                    context_briefing={},
                )
            if rej := cls._reject_soup(
                value, field=f"item {idx} {field}", min_chars=min_chars
            ):
                return rej
        ac = raw.get("acceptance_criteria")
        if (
            not isinstance(ac, list)
            or not ac
            or not all(isinstance(c, str) and c.strip() for c in ac)
        ):
            return Envelope.invalid_state(
                message=f"item {idx} is missing acceptance_criteria",
                remediate=(
                    f"provide a non-empty list of acceptance criteria for item {idx}"
                ),
                context_briefing={},
            )
        return None

    @staticmethod
    def _reject_roadmap_item_team(raw: dict[str, Any], idx: int) -> Envelope | None:
        """Validate the item's ``team`` is a known cell (backend/frontend/ux_ui)."""
        from roboco.foundation.identity import CELL_TEAMS, Team

        try:
            team = Team(str(raw.get("team")))
        except ValueError:
            team = None
        if team is None or team not in CELL_TEAMS:
            return Envelope.invalid_state(
                message=f"item {idx} has an unknown team {raw.get('team')!r}",
                remediate="team must be one of: backend, frontend, ux_ui",
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_roadmap_item_shape(cls, raw: Any, idx: int) -> Envelope | None:
        """Validate one raw roadmap item dict's shape/fields; None when clean.

        Synchronous — no DB access. Split from ``_reject_roadmap_item`` (which
        adds the Task-6b project-exclusion check) so the shape checks stay
        classmethod-testable without a session.
        """
        if not isinstance(raw, dict):
            return Envelope.invalid_state(
                message=f"item {idx} is not an object",
                remediate=(
                    "each item must be an object with title/description/"
                    "acceptance_criteria/project_slug/team/priority/rationale"
                ),
                context_briefing={},
            )
        if rej := cls._reject_roadmap_item_fields(raw, idx):
            return rej
        return cls._reject_roadmap_item_team(raw, idx)

    async def _reject_roadmap_item(
        self, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate one raw roadmap item dict; None when clean.

        Folds the shape/fields/team checks with the Task-6b project-exclusion
        check into one call so the ``propose_roadmap`` loop keeps a single
        return point per item (xenon/PLR0911 budget).
        """
        if rej := self._reject_roadmap_item_shape(raw, idx):
            return rej
        return await self._reject_excluded_roadmap_project(raw, idx)

    async def _reject_excluded_roadmap_project(
        self, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Reject an item targeting a project that excluded itself from the
        roadmap program (``!roadmap`` in its ``board_programs``) — the PO
        learns this at propose time instead of a silent materialize-time skip.

        An unresolvable ``project_slug`` is NOT rejected here; that surfaces
        downstream at approve/materialize time as it already did before Task
        6b (this check only ever narrows an otherwise-valid slug).
        """
        from roboco.foundation.policy.board_programs import (
            PROGRAMS,
            project_participates,
        )
        from roboco.services.project import get_project_service

        slug = str(raw.get("project_slug", "")).strip()
        project = await get_project_service(self.task.session).get_by_slug(slug)
        if project is None:
            return None
        if not project_participates(PROGRAMS["roadmap"], project.board_programs):
            return Envelope.invalid_state(
                message=(
                    f"item {idx} targets project {slug!r}, which excluded "
                    "itself from the roadmap program"
                ),
                remediate=(
                    f"drop item {idx} or retarget it to a project not "
                    "excluded via '!roadmap'"
                ),
                context_briefing={},
            )
        return None

    async def propose_roadmap(
        self,
        *,
        agent_id: UUID,
        cycle_goal: str,
        items: list[dict[str, Any]],
    ) -> Envelope:
        """Product Owner authors a themed roadmap cycle (goal + item drafts).

        Persists the cycle onto the caller's open exploration task (markers)
        — each item starts 'proposed', awaiting the CEO's per-item approve/
        reject in the roadmap queue. One call per cycle: the exploration task
        stays open (and this verb keeps refusing) until every item is
        terminal.
        """
        await self._trace_board_proposal(
            agent_id=agent_id,
            verb="propose_roadmap",
            payload={"cycle_goal": cycle_goal, "items": items},
        )
        role = await self._caller_role(agent_id)
        if role not in _ROADMAP_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose a roadmap cycle; only the "
                    "Product Owner authors one"
                ),
                remediate="this verb is Product-Owner-only",
                context_briefing={},
            )
        if rej := self._reject_soup(cycle_goal, field="cycle_goal", min_chars=8):
            return rej
        min_items = settings.roadmap_min_items_per_cycle
        max_items = settings.roadmap_max_items_per_cycle
        if not (min_items <= len(items) <= max_items):
            return Envelope.invalid_state(
                message=(
                    f"a cycle needs {min_items}-{max_items} item drafts, "
                    f"got {len(items)}"
                ),
                remediate=f"propose between {min_items} and {max_items} roadmap items",
                context_briefing={},
            )
        normalized: list[dict[str, Any]] = []
        for idx, raw in enumerate(items):
            if rej := await self._reject_roadmap_item(raw, idx):
                return rej
            normalized.append(_normalize_roadmap_item(idx, raw))

        from roboco.services.task import get_task_service

        task_svc = get_task_service(self.task.session)
        cycles = await task_svc.list_open_roadmap_cycles()
        task = next(
            (
                t
                for t in cycles
                if t.assigned_to == agent_id and markers.get_roadmap_cycle(t) is None
            ),
            None,
        )
        if task is None:
            return Envelope.invalid_state(
                message="no open roadmap exploration task assigned to you",
                remediate=(
                    "propose_roadmap only runs against an active exploration "
                    "cycle spawned by the roadmap engine; wait for the next cycle"
                ),
                context_briefing={},
            )
        markers.set_roadmap_cycle(
            task, {"goal": cycle_goal.strip(), "items": normalized}
        )
        await self.task.session.flush()
        await self._notify_roadmap_items(task, normalized)
        return Envelope.ok(
            status="roadmap_proposed",
            task_id=str(task.id),
            next="i_am_idle() — the CEO reviews each item in the roadmap queue",
            context_briefing={
                "cycle_goal": cycle_goal.strip(),
                "item_count": len(normalized),
            },
        )

    async def _notify_roadmap_items(
        self, task: Any, items: list[dict[str, Any]]
    ) -> None:
        """Best-effort push DM per proposed item — this is the moment a
        roadmap item first becomes CEO-actionable (the engine's own
        exploration-task origination has nothing to review yet), so the DM
        fires here rather than from ``RoadmapEngine``. A send failure never
        blocks ``propose_roadmap`` itself."""
        if self._deps.notification_delivery is None:
            return
        id8 = str(task.id)[:8]
        for item in items:
            try:
                await self._deps.notification_delivery.notify_ceo_of_queue_item(
                    kind="roadmap",
                    id8=id8,
                    extra=str(item.get("id") or ""),
                    title=item.get("title") or "untitled",
                    related_task_id=task.id,
                )
            except Exception as exc:
                logger.warning(
                    "roadmap telegram notify failed (best-effort)", error=str(exc)
                )

    @classmethod
    def _reject_pest_hunt_item_text_fields(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the plain text fields (title/description/project_slug/
        team/evidence) of one pest-hunt item dict."""
        for field, min_chars in _PEST_HUNT_ITEM_TEXT_FIELDS:
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                return Envelope.invalid_state(
                    message=f"item {idx} is missing '{field}'",
                    remediate=f"provide a substantive '{field}' for item {idx}",
                    context_briefing={},
                )
            if rej := cls._reject_soup(
                value, field=f"item {idx} {field}", min_chars=min_chars
            ):
                return rej
        return None

    @staticmethod
    def _reject_pest_hunt_item_evidence_and_ac(
        raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the evidence char-cap and acceptance_criteria list of one
        pest-hunt item dict — split from the text-fields loop above to keep
        ``_reject_pest_hunt_item_fields`` under the xenon complexity budget."""
        evidence = str(raw.get("evidence", ""))
        if len(evidence) > _PEST_HUNT_EVIDENCE_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"item {idx} evidence is {len(evidence)} chars, over the "
                    f"{_PEST_HUNT_EVIDENCE_MAX_CHARS}-char cap"
                ),
                remediate=(
                    f"shorten item {idx}'s evidence to "
                    f"{_PEST_HUNT_EVIDENCE_MAX_CHARS} characters or fewer"
                ),
                context_briefing={},
            )
        ac = raw.get("acceptance_criteria")
        if (
            not isinstance(ac, list)
            or not ac
            or not all(isinstance(c, str) and c.strip() for c in ac)
        ):
            return Envelope.invalid_state(
                message=f"item {idx} is missing acceptance_criteria",
                remediate=(
                    f"provide a non-empty list of acceptance criteria for item {idx}"
                ),
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_pest_hunt_item_fields(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the text + evidence-cap + acceptance-criteria fields of
        one pest-hunt item dict. Mirrors ``_reject_roadmap_item_fields``."""
        if rej := cls._reject_pest_hunt_item_text_fields(raw, idx):
            return rej
        return cls._reject_pest_hunt_item_evidence_and_ac(raw, idx)

    @classmethod
    def _reject_pest_hunt_item_shape(cls, raw: Any, idx: int) -> Envelope | None:
        """Validate one raw pest-hunt item dict's shape/fields; None when
        clean. Reuses ``_reject_roadmap_item_team`` — the cell-team check is
        identical for both item kinds."""
        if not isinstance(raw, dict):
            return Envelope.invalid_state(
                message=f"item {idx} is not an object",
                remediate=(
                    "each item must be an object with title/description/"
                    "acceptance_criteria/project_slug/team/priority/evidence"
                ),
                context_briefing={},
            )
        if rej := cls._reject_pest_hunt_item_fields(raw, idx):
            return rej
        return cls._reject_roadmap_item_team(raw, idx)

    async def _reject_pest_hunt_item(
        self, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate one raw pest-hunt item dict; None when clean. Mirrors
        ``_reject_roadmap_item``."""
        if rej := self._reject_pest_hunt_item_shape(raw, idx):
            return rej
        return await self._reject_unparticipating_pest_control_project(raw, idx)

    async def _reject_unparticipating_pest_control_project(
        self, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Reject an item targeting a project that has NOT opted into the
        pest_control program (``"pest_control"`` absent from its
        ``board_programs``) — the positive-gate mirror of
        ``_reject_excluded_roadmap_project``'s ``!roadmap`` exclusion check:
        project-scoped programs are opt-in, so the polarity flips.

        An unresolvable ``project_slug`` is NOT rejected here; that surfaces
        downstream at approve/materialize time, same posture as roadmap's
        check.
        """
        from roboco.foundation.policy.board_programs import (
            PROGRAMS,
            project_participates,
        )
        from roboco.services.project import get_project_service

        slug = str(raw.get("project_slug", "")).strip()
        project = await get_project_service(self.task.session).get_by_slug(slug)
        if project is None:
            return None
        if not project_participates(PROGRAMS["pest_control"], project.board_programs):
            return Envelope.invalid_state(
                message=(
                    f"item {idx} targets project {slug!r}, which has not "
                    "opted into the pest_control program"
                ),
                remediate=(
                    f"drop item {idx} or ask the CEO to opt {slug!r} into "
                    "pest_control on its project settings page"
                ),
                context_briefing={},
            )
        return None

    async def propose_bug_hunt(
        self,
        *,
        agent_id: UUID,
        items: list[dict[str, Any]],
    ) -> Envelope:
        """Product Owner authors a Pest Control bug hunt (1-N evidence-backed
        item drafts, N = the registry's ``max_items_per_cycle``).

        Persists the hunt onto the caller's open exploration task (markers)
        — each item starts 'proposed', awaiting the CEO's per-item approve/
        reject in the pest-control queue. One call per cycle: the exploration
        task stays open (and this verb keeps refusing) until every item is
        terminal. Mirrors ``propose_roadmap`` — no top-level theme goal here,
        just the items.
        """
        await self._trace_board_proposal(
            agent_id=agent_id, verb="propose_bug_hunt", payload={"items": items}
        )
        from roboco.foundation.policy.board_programs import PROGRAMS

        role = await self._caller_role(agent_id)
        if role not in _PEST_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose a bug hunt; only the "
                    "Product Owner authors one"
                ),
                remediate="this verb is Product-Owner-only",
                context_briefing={},
            )
        max_items = PROGRAMS["pest_control"].max_items_per_cycle
        if not (1 <= len(items) <= max_items):
            return Envelope.invalid_state(
                message=(
                    f"a bug hunt needs 1-{max_items} item drafts, got {len(items)}"
                ),
                remediate=f"propose between 1 and {max_items} evidence-backed items",
                context_briefing={},
            )
        normalized: list[dict[str, Any]] = []
        for idx, raw in enumerate(items):
            if rej := await self._reject_pest_hunt_item(raw, idx):
                return rej
            normalized.append(_normalize_pest_hunt_item(idx, raw))

        from roboco.services.task import get_task_service

        task_svc = get_task_service(self.task.session)
        cycles = await task_svc.list_open_pest_control_cycles()
        task = next(
            (
                t
                for t in cycles
                if t.assigned_to == agent_id and markers.get_pest_hunt(t) is None
            ),
            None,
        )
        if task is None:
            return Envelope.invalid_state(
                message="no open pest-control exploration task assigned to you",
                remediate=(
                    "propose_bug_hunt only runs against an active exploration "
                    "cycle spawned by the pest-control engine; wait for the "
                    "next cycle"
                ),
                context_briefing={},
            )
        markers.set_pest_hunt(task, {"items": normalized})
        await self.task.session.flush()
        await self._notify_pest_hunt_items(task, normalized)
        return Envelope.ok(
            status="pest_hunt_proposed",
            task_id=str(task.id),
            next="i_am_idle() — the CEO reviews each item in the pest-control queue",
            context_briefing={"item_count": len(normalized)},
        )

    async def _notify_pest_hunt_items(
        self, task: Any, items: list[dict[str, Any]]
    ) -> None:
        """Best-effort push DM per proposed item — mirrors
        ``_notify_roadmap_items``."""
        if self._deps.notification_delivery is None:
            return
        id8 = str(task.id)[:8]
        for item in items:
            try:
                await self._deps.notification_delivery.notify_ceo_of_queue_item(
                    kind="pest_control",
                    id8=id8,
                    extra=str(item.get("id") or ""),
                    title=item.get("title") or "untitled",
                    related_task_id=task.id,
                )
            except Exception as exc:
                logger.warning(
                    "pest-control telegram notify failed (best-effort)",
                    error=str(exc),
                )

    @staticmethod
    def _reject_scales_item_action_and_priority(
        raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate ``action`` + the conditional ``new_priority`` requirement
        of one raw rebalance item dict — split out to keep
        ``_reject_scales_item_shape`` under the xenon/PLR0911 budget."""
        action = raw.get("action")
        if action not in _SCALES_ACTIONS:
            return Envelope.invalid_state(
                message=f"item {idx} has an invalid action {action!r}",
                remediate="action must be 'reprioritize' or 'cancel'",
                context_briefing={},
            )
        if action != "reprioritize":
            return None
        new_priority = raw.get("new_priority")
        if (
            not isinstance(new_priority, int)
            or isinstance(new_priority, bool)
            or new_priority not in _SCALES_VALID_PRIORITIES
        ):
            return Envelope.invalid_state(
                message=(
                    f"item {idx} is 'reprioritize' but new_priority is {new_priority!r}"
                ),
                remediate=(
                    "new_priority is required for a reprioritize item and must "
                    "be one of 0 (P0/highest) .. 3 (P3/lowest)"
                ),
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_scales_item_rationale(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the required ``rationale`` field — split out of
        ``_reject_scales_item_shape`` to keep its own return-statement count
        under the xenon/PLR0911 budget."""
        rationale = raw.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            return Envelope.invalid_state(
                message=f"item {idx} is missing 'rationale'",
                remediate=f"provide a substantive rationale for item {idx}",
                context_briefing={},
            )
        if rej := cls._reject_soup(
            rationale, field=f"item {idx} rationale", min_chars=8
        ):
            return rej
        if len(rationale) > _SCALES_RATIONALE_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"item {idx} rationale is {len(rationale)} chars, over the "
                    f"{_SCALES_RATIONALE_MAX_CHARS}-char cap"
                ),
                remediate=(
                    f"shorten item {idx}'s rationale to "
                    f"{_SCALES_RATIONALE_MAX_CHARS} characters or fewer"
                ),
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_scales_item_shape(cls, raw: Any, idx: int) -> Envelope | None:
        """Validate one raw rebalance item dict's shape/fields; None when
        clean (before ``task_ref`` resolution, which needs the DB)."""
        if not isinstance(raw, dict):
            return Envelope.invalid_state(
                message=f"item {idx} is not an object",
                remediate=(
                    "each item must be an object with task_ref/action/"
                    "new_priority/rationale"
                ),
                context_briefing={},
            )
        task_ref = raw.get("task_ref")
        if not isinstance(task_ref, str) or not task_ref.strip():
            return Envelope.invalid_state(
                message=f"item {idx} is missing 'task_ref'",
                remediate=(
                    f"provide the id8 or exact title of the live task item "
                    f"{idx} targets"
                ),
                context_briefing={},
            )
        if rej := cls._reject_scales_item_action_and_priority(raw, idx):
            return rej
        return cls._reject_scales_item_rationale(raw, idx)

    async def _reject_scales_item(
        self, raw: dict[str, Any], idx: int
    ) -> tuple[Envelope | None, Any]:
        """Validate one raw rebalance item, then resolve its ``task_ref``.

        Returns ``(None, target_task)`` when clean, ``(rejection, None)``
        otherwise. Resolution happens here (not a separate pass) since a
        ``task_ref`` only makes sense checked against a real live task.
        """
        if rej := self._reject_scales_item_shape(raw, idx):
            return rej, None
        from roboco.services.task import get_task_service

        target = await get_task_service(self.task.session).resolve_scales_task_ref(
            str(raw["task_ref"]).strip()
        )
        if target is None:
            return (
                Envelope.invalid_state(
                    message=(
                        f"item {idx} task_ref {raw['task_ref']!r} does not "
                        "resolve to a live BACKLOG/PENDING task"
                    ),
                    remediate=(
                        f"item {idx}'s task_ref must be the id8 or exact title "
                        "of a live BACKLOG/PENDING task"
                    ),
                    context_briefing={},
                ),
                None,
            )
        return None, target

    async def propose_rebalance(
        self,
        *,
        agent_id: UUID,
        items: list[dict[str, Any]],
    ) -> Envelope:
        """Product Owner authors a Scales portfolio-rebalance plan (1-N
        re-priority/cancellation items against the LIVE backlog, N = the
        registry's ``max_items_per_cycle``).

        Persists the plan onto the caller's open exploration task (markers)
        — each item starts 'proposed', awaiting the CEO's per-item approve/
        reject in the Scales queue. One call per cycle: the exploration task
        stays open (and this verb keeps refusing) until every item is
        terminal. Unlike ``propose_roadmap``/``propose_bug_hunt`` an item
        never drafts a NEW task — it references a LIVE one (``task_ref``,
        resolved to a real BACKLOG/PENDING task here) that approval MUTATES
        (reprioritize) or cancels, never creates.
        """
        await self._trace_board_proposal(
            agent_id=agent_id, verb="propose_rebalance", payload={"items": items}
        )
        from roboco.foundation.policy.board_programs import PROGRAMS

        role = await self._caller_role(agent_id)
        if role not in _SCALES_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose a rebalance plan; only the "
                    "Product Owner authors one"
                ),
                remediate="this verb is Product-Owner-only",
                context_briefing={},
            )
        max_items = PROGRAMS["scales"].max_items_per_cycle
        if not (1 <= len(items) <= max_items):
            return Envelope.invalid_state(
                message=(
                    f"a rebalance plan needs 1-{max_items} item drafts, got "
                    f"{len(items)}"
                ),
                remediate=f"propose between 1 and {max_items} items",
                context_briefing={},
            )
        normalized: list[dict[str, Any]] = []
        for idx, raw in enumerate(items):
            rejection, target = await self._reject_scales_item(raw, idx)
            if rejection is not None:
                return rejection
            normalized.append(_normalize_scales_item(idx, raw, target))

        from roboco.services.task import get_task_service

        task_svc = get_task_service(self.task.session)
        cycles = await task_svc.list_open_scales_cycles()
        task = next(
            (
                t
                for t in cycles
                if t.assigned_to == agent_id and markers.get_rebalance_plan(t) is None
            ),
            None,
        )
        if task is None:
            return Envelope.invalid_state(
                message="no open scales exploration task assigned to you",
                remediate=(
                    "propose_rebalance only runs against an active exploration "
                    "cycle spawned by the scales engine; wait for the next cycle"
                ),
                context_briefing={},
            )
        markers.set_rebalance_plan(task, {"items": normalized})
        await self.task.session.flush()
        await self._notify_rebalance_items(task, normalized)
        return Envelope.ok(
            status="rebalance_proposed",
            task_id=str(task.id),
            next="i_am_idle() — the CEO reviews each item in the Scales queue",
            context_briefing={"item_count": len(normalized)},
        )

    async def _notify_rebalance_items(
        self, task: Any, items: list[dict[str, Any]]
    ) -> None:
        """Best-effort push DM per proposed item — mirrors
        ``_notify_pest_hunt_items``."""
        if self._deps.notification_delivery is None:
            return
        id8 = str(task.id)[:8]
        for item in items:
            try:
                await self._deps.notification_delivery.notify_ceo_of_queue_item(
                    kind="scales",
                    id8=id8,
                    extra=str(item.get("id") or ""),
                    title=item.get("target_task_title") or "untitled",
                    related_task_id=task.id,
                )
            except Exception as exc:
                logger.warning(
                    "scales telegram notify failed (best-effort)", error=str(exc)
                )

    @classmethod
    def _reject_gap_fill_item_text_fields(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the plain text fields (title/description/project_slug/
        team/evidence) of one gap-fill item dict. Mirrors
        ``_reject_pest_hunt_item_text_fields``."""
        for field, min_chars in _GAP_FILL_ITEM_TEXT_FIELDS:
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                return Envelope.invalid_state(
                    message=f"item {idx} is missing '{field}'",
                    remediate=f"provide a substantive '{field}' for item {idx}",
                    context_briefing={},
                )
            if rej := cls._reject_soup(
                value, field=f"item {idx} {field}", min_chars=min_chars
            ):
                return rej
        return None

    @staticmethod
    def _reject_gap_fill_item_evidence_and_ac(
        raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the evidence char-cap and acceptance_criteria list of one
        gap-fill item dict — split from the text-fields loop above to keep
        ``_reject_gap_fill_item_fields`` under the xenon complexity budget."""
        evidence = str(raw.get("evidence", ""))
        if len(evidence) > _GAP_FILL_EVIDENCE_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"item {idx} evidence is {len(evidence)} chars, over the "
                    f"{_GAP_FILL_EVIDENCE_MAX_CHARS}-char cap"
                ),
                remediate=(
                    f"shorten item {idx}'s evidence to "
                    f"{_GAP_FILL_EVIDENCE_MAX_CHARS} characters or fewer"
                ),
                context_briefing={},
            )
        ac = raw.get("acceptance_criteria")
        if (
            not isinstance(ac, list)
            or not ac
            or not all(isinstance(c, str) and c.strip() for c in ac)
        ):
            return Envelope.invalid_state(
                message=f"item {idx} is missing acceptance_criteria",
                remediate=(
                    f"provide a non-empty list of acceptance criteria for item {idx}"
                ),
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_gap_fill_item_fields(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the text + evidence-cap + acceptance-criteria fields of
        one gap-fill item dict. Mirrors ``_reject_pest_hunt_item_fields``."""
        if rej := cls._reject_gap_fill_item_text_fields(raw, idx):
            return rej
        return cls._reject_gap_fill_item_evidence_and_ac(raw, idx)

    @classmethod
    def _reject_gap_fill_item_shape(cls, raw: Any, idx: int) -> Envelope | None:
        """Validate one raw gap-fill item dict's shape/fields; None when
        clean. Reuses ``_reject_roadmap_item_team`` — the cell-team check is
        identical for every item kind."""
        if not isinstance(raw, dict):
            return Envelope.invalid_state(
                message=f"item {idx} is not an object",
                remediate=(
                    "each item must be an object with title/description/"
                    "acceptance_criteria/project_slug/team/priority/evidence"
                ),
                context_briefing={},
            )
        if rej := cls._reject_gap_fill_item_fields(raw, idx):
            return rej
        return cls._reject_roadmap_item_team(raw, idx)

    async def _reject_gap_fill_item(
        self, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate one raw gap-fill item dict; None when clean. Mirrors
        ``_reject_pest_hunt_item``."""
        if rej := self._reject_gap_fill_item_shape(raw, idx):
            return rej
        return await self._reject_unparticipating_spackle_project(raw, idx)

    async def _reject_unparticipating_spackle_project(
        self, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Reject an item targeting a project that has NOT opted into the
        spackle program (``"spackle"`` absent from its ``board_programs``) —
        mirrors ``_reject_unparticipating_pest_control_project``.

        An unresolvable ``project_slug`` is NOT rejected here; that surfaces
        downstream at approve/materialize time, same posture as pest_control's
        check.
        """
        from roboco.foundation.policy.board_programs import (
            PROGRAMS,
            project_participates,
        )
        from roboco.services.project import get_project_service

        slug = str(raw.get("project_slug", "")).strip()
        project = await get_project_service(self.task.session).get_by_slug(slug)
        if project is None:
            return None
        if not project_participates(PROGRAMS["spackle"], project.board_programs):
            return Envelope.invalid_state(
                message=(
                    f"item {idx} targets project {slug!r}, which has not "
                    "opted into the spackle program"
                ),
                remediate=(
                    f"drop item {idx} or ask the CEO to opt {slug!r} into "
                    "spackle on its project settings page"
                ),
                context_briefing={},
            )
        return None

    async def propose_gap_fill(
        self,
        *,
        agent_id: UUID,
        items: list[dict[str, Any]],
    ) -> Envelope:
        """Product Owner authors a Spackle gap-fill audit (1-N evidence-backed
        item drafts, N = the registry's ``max_items_per_cycle``).

        Persists the audit onto the caller's open exploration task (markers)
        — each item starts 'proposed', awaiting the CEO's per-item approve/
        reject in the spackle queue. One call per cycle: the exploration
        task stays open (and this verb keeps refusing) until every item is
        terminal. Mirrors ``propose_bug_hunt`` — no top-level theme goal
        here, just the items.
        """
        await self._trace_board_proposal(
            agent_id=agent_id, verb="propose_gap_fill", payload={"items": items}
        )
        from roboco.foundation.policy.board_programs import PROGRAMS

        role = await self._caller_role(agent_id)
        if role not in _GAP_FILL_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose a gap-fill audit; only "
                    "the Product Owner authors one"
                ),
                remediate="this verb is Product-Owner-only",
                context_briefing={},
            )
        max_items = PROGRAMS["spackle"].max_items_per_cycle
        if not (1 <= len(items) <= max_items):
            return Envelope.invalid_state(
                message=(
                    f"a gap-fill audit needs 1-{max_items} item drafts, "
                    f"got {len(items)}"
                ),
                remediate=f"propose between 1 and {max_items} evidence-backed items",
                context_briefing={},
            )
        normalized: list[dict[str, Any]] = []
        for idx, raw in enumerate(items):
            if rej := await self._reject_gap_fill_item(raw, idx):
                return rej
            normalized.append(_normalize_gap_fill_item(idx, raw))

        from roboco.services.task import get_task_service

        task_svc = get_task_service(self.task.session)
        cycles = await task_svc.list_open_spackle_cycles()
        task = next(
            (
                t
                for t in cycles
                if t.assigned_to == agent_id and markers.get_gap_fill(t) is None
            ),
            None,
        )
        if task is None:
            return Envelope.invalid_state(
                message="no open spackle exploration task assigned to you",
                remediate=(
                    "propose_gap_fill only runs against an active exploration "
                    "cycle spawned by the spackle engine; wait for the next "
                    "cycle"
                ),
                context_briefing={},
            )
        markers.set_gap_fill(task, {"items": normalized})
        await self.task.session.flush()
        await self._notify_gap_fill_items(task, normalized)
        return Envelope.ok(
            status="gap_fill_proposed",
            task_id=str(task.id),
            next="i_am_idle() — the CEO reviews each item in the spackle queue",
            context_briefing={"item_count": len(normalized)},
        )

    async def _notify_gap_fill_items(
        self, task: Any, items: list[dict[str, Any]]
    ) -> None:
        """Best-effort push DM per proposed item — mirrors
        ``_notify_pest_hunt_items``."""
        if self._deps.notification_delivery is None:
            return
        id8 = str(task.id)[:8]
        for item in items:
            try:
                await self._deps.notification_delivery.notify_ceo_of_queue_item(
                    kind="spackle",
                    id8=id8,
                    extra=str(item.get("id") or ""),
                    title=item.get("title") or "untitled",
                    related_task_id=task.id,
                )
            except Exception as exc:
                logger.warning(
                    "spackle telegram notify failed (best-effort)",
                    error=str(exc),
                )

    @classmethod
    def _reject_messaging_fix_item_text_fields(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the plain text fields (title/description/project_slug/
        team/evidence) of one messaging-fix item dict. Mirrors
        ``_reject_gap_fill_item_text_fields``."""
        for field, min_chars in _MESSAGING_FIX_ITEM_TEXT_FIELDS:
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                return Envelope.invalid_state(
                    message=f"item {idx} is missing '{field}'",
                    remediate=f"provide a substantive '{field}' for item {idx}",
                    context_briefing={},
                )
            if rej := cls._reject_soup(
                value, field=f"item {idx} {field}", min_chars=min_chars
            ):
                return rej
        return None

    @staticmethod
    def _reject_messaging_fix_item_evidence_and_ac(
        raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the evidence char-cap and acceptance_criteria list of one
        messaging-fix item dict — split from the text-fields loop above to
        keep ``_reject_messaging_fix_item_fields`` under the xenon complexity
        budget."""
        evidence = str(raw.get("evidence", ""))
        if len(evidence) > _MESSAGING_FIX_EVIDENCE_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"item {idx} evidence is {len(evidence)} chars, over the "
                    f"{_MESSAGING_FIX_EVIDENCE_MAX_CHARS}-char cap"
                ),
                remediate=(
                    f"shorten item {idx}'s evidence to "
                    f"{_MESSAGING_FIX_EVIDENCE_MAX_CHARS} characters or fewer"
                ),
                context_briefing={},
            )
        ac = raw.get("acceptance_criteria")
        if (
            not isinstance(ac, list)
            or not ac
            or not all(isinstance(c, str) and c.strip() for c in ac)
        ):
            return Envelope.invalid_state(
                message=f"item {idx} is missing acceptance_criteria",
                remediate=(
                    f"provide a non-empty list of acceptance criteria for item {idx}"
                ),
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_messaging_fix_item_fields(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the text + evidence-cap + acceptance-criteria fields of
        one messaging-fix item dict. Mirrors ``_reject_gap_fill_item_fields``."""
        if rej := cls._reject_messaging_fix_item_text_fields(raw, idx):
            return rej
        return cls._reject_messaging_fix_item_evidence_and_ac(raw, idx)

    @classmethod
    def _reject_messaging_fix_item_shape(cls, raw: Any, idx: int) -> Envelope | None:
        """Validate one raw messaging-fix item dict's shape/fields; None when
        clean. Reuses ``_reject_roadmap_item_team`` — the cell-team check is
        identical for every item kind."""
        if not isinstance(raw, dict):
            return Envelope.invalid_state(
                message=f"item {idx} is not an object",
                remediate=(
                    "each item must be an object with title/description/"
                    "acceptance_criteria/project_slug/team/priority/evidence"
                ),
                context_briefing={},
            )
        if rej := cls._reject_messaging_fix_item_fields(raw, idx):
            return rej
        return cls._reject_roadmap_item_team(raw, idx)

    async def _reject_messaging_fix_item(
        self, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate one raw messaging-fix item dict; None when clean. Mirrors
        ``_reject_gap_fill_item``."""
        if rej := self._reject_messaging_fix_item_shape(raw, idx):
            return rej
        return await self._reject_unparticipating_mirror_project(raw, idx)

    async def _reject_unparticipating_mirror_project(
        self, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Reject an item targeting a project that has NOT opted into the
        mirror program (``"mirror"`` absent from its ``board_programs``) —
        mirrors ``_reject_unparticipating_spackle_project``.

        An unresolvable ``project_slug`` is NOT rejected here; that surfaces
        downstream at approve/materialize time, same posture as spackle's
        check.
        """
        from roboco.foundation.policy.board_programs import (
            PROGRAMS,
            project_participates,
        )
        from roboco.services.project import get_project_service

        slug = str(raw.get("project_slug", "")).strip()
        project = await get_project_service(self.task.session).get_by_slug(slug)
        if project is None:
            return None
        if not project_participates(PROGRAMS["mirror"], project.board_programs):
            return Envelope.invalid_state(
                message=(
                    f"item {idx} targets project {slug!r}, which has not "
                    "opted into the mirror program"
                ),
                remediate=(
                    f"drop item {idx} or ask the CEO to opt {slug!r} into "
                    "mirror on its project settings page"
                ),
                context_briefing={},
            )
        return None

    async def propose_messaging_fixes(
        self,
        *,
        agent_id: UUID,
        items: list[dict[str, Any]],
    ) -> Envelope:
        """Head of Marketing authors a Mirror positioning audit (1-N
        evidence-backed item drafts, N = the registry's
        ``max_items_per_cycle``).

        Persists the audit onto the caller's open exploration task (markers)
        — each item starts 'proposed', awaiting the CEO's per-item approve/
        reject in the mirror queue. One call per cycle: the exploration
        task stays open (and this verb keeps refusing) until every item is
        terminal. Mirrors ``propose_gap_fill`` — no top-level theme goal
        here, just the items.
        """
        await self._trace_board_proposal(
            agent_id=agent_id,
            verb="propose_messaging_fixes",
            payload={"items": items},
        )
        from roboco.foundation.policy.board_programs import PROGRAMS

        role = await self._caller_role(agent_id)
        if role not in _MESSAGING_FIXES_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose messaging fixes; only "
                    "the Head of Marketing authors this audit"
                ),
                remediate="this verb is Head-of-Marketing-only",
                context_briefing={},
            )
        max_items = PROGRAMS["mirror"].max_items_per_cycle
        if not (1 <= len(items) <= max_items):
            return Envelope.invalid_state(
                message=(
                    f"a messaging-fixes audit needs 1-{max_items} item drafts, "
                    f"got {len(items)}"
                ),
                remediate=f"propose between 1 and {max_items} evidence-backed items",
                context_briefing={},
            )
        normalized: list[dict[str, Any]] = []
        for idx, raw in enumerate(items):
            if rej := await self._reject_messaging_fix_item(raw, idx):
                return rej
            normalized.append(_normalize_messaging_fix_item(idx, raw))

        from roboco.services.task import get_task_service

        task_svc = get_task_service(self.task.session)
        cycles = await task_svc.list_open_mirror_cycles()
        task = next(
            (
                t
                for t in cycles
                if t.assigned_to == agent_id and markers.get_messaging_fixes(t) is None
            ),
            None,
        )
        if task is None:
            return Envelope.invalid_state(
                message="no open mirror exploration task assigned to you",
                remediate=(
                    "propose_messaging_fixes only runs against an active "
                    "exploration cycle spawned by the mirror engine; wait "
                    "for the next cycle"
                ),
                context_briefing={},
            )
        markers.set_messaging_fixes(task, {"items": normalized})
        await self.task.session.flush()
        await self._notify_messaging_fix_items(task, normalized)
        return Envelope.ok(
            status="messaging_fixes_proposed",
            task_id=str(task.id),
            next="i_am_idle() — the CEO reviews each item in the mirror queue",
            context_briefing={"item_count": len(normalized)},
        )

    async def _notify_messaging_fix_items(
        self, task: Any, items: list[dict[str, Any]]
    ) -> None:
        """Best-effort push DM per proposed item — mirrors
        ``_notify_gap_fill_items``."""
        if self._deps.notification_delivery is None:
            return
        id8 = str(task.id)[:8]
        for item in items:
            try:
                await self._deps.notification_delivery.notify_ceo_of_queue_item(
                    kind="mirror",
                    id8=id8,
                    extra=str(item.get("id") or ""),
                    title=item.get("title") or "untitled",
                    related_task_id=task.id,
                )
            except Exception as exc:
                logger.warning(
                    "mirror telegram notify failed (best-effort)",
                    error=str(exc),
                )

    @classmethod
    def _reject_friction_fix_item_text_fields(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the plain text fields (title/description/project_slug/
        team/evidence) of one friction-fix item dict. Mirrors
        ``_reject_messaging_fix_item_text_fields``."""
        for field, min_chars in _FRICTION_FIXES_ITEM_TEXT_FIELDS:
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                return Envelope.invalid_state(
                    message=f"item {idx} is missing '{field}'",
                    remediate=f"provide a substantive '{field}' for item {idx}",
                    context_briefing={},
                )
            if rej := cls._reject_soup(
                value, field=f"item {idx} {field}", min_chars=min_chars
            ):
                return rej
        return None

    @staticmethod
    def _reject_friction_fix_item_evidence_and_ac(
        raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the evidence char-cap and acceptance_criteria list of one
        friction-fix item dict — split from the text-fields loop above to
        keep ``_reject_friction_fix_item_fields`` under the xenon complexity
        budget."""
        evidence = str(raw.get("evidence", ""))
        if len(evidence) > _FRICTION_FIXES_EVIDENCE_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"item {idx} evidence is {len(evidence)} chars, over the "
                    f"{_FRICTION_FIXES_EVIDENCE_MAX_CHARS}-char cap"
                ),
                remediate=(
                    f"shorten item {idx}'s evidence to "
                    f"{_FRICTION_FIXES_EVIDENCE_MAX_CHARS} characters or fewer"
                ),
                context_briefing={},
            )
        ac = raw.get("acceptance_criteria")
        if (
            not isinstance(ac, list)
            or not ac
            or not all(isinstance(c, str) and c.strip() for c in ac)
        ):
            return Envelope.invalid_state(
                message=f"item {idx} is missing acceptance_criteria",
                remediate=(
                    f"provide a non-empty list of acceptance criteria for item {idx}"
                ),
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_friction_fix_item_fields(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the text + evidence-cap + acceptance-criteria fields of
        one friction-fix item dict. Mirrors ``_reject_messaging_fix_item_fields``."""
        if rej := cls._reject_friction_fix_item_text_fields(raw, idx):
            return rej
        return cls._reject_friction_fix_item_evidence_and_ac(raw, idx)

    @classmethod
    def _reject_friction_fix_item_shape(cls, raw: Any, idx: int) -> Envelope | None:
        """Validate one raw friction-fix item dict's shape/fields; None when
        clean. Reuses ``_reject_roadmap_item_team`` — the cell-team check is
        identical for every item kind."""
        if not isinstance(raw, dict):
            return Envelope.invalid_state(
                message=f"item {idx} is not an object",
                remediate=(
                    "each item must be an object with title/description/"
                    "acceptance_criteria/project_slug/team/priority/evidence"
                ),
                context_briefing={},
            )
        if rej := cls._reject_friction_fix_item_fields(raw, idx):
            return rej
        return cls._reject_roadmap_item_team(raw, idx)

    async def _reject_friction_fix_item(
        self, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate one raw friction-fix item dict; None when clean. Mirrors
        ``_reject_messaging_fix_item``."""
        if rej := self._reject_friction_fix_item_shape(raw, idx):
            return rej
        return await self._reject_unparticipating_dogfood_project(raw, idx)

    async def _reject_unparticipating_dogfood_project(
        self, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Reject an item targeting a project that has NOT opted into the
        dogfood program (``"dogfood"`` absent from its ``board_programs``) —
        mirrors ``_reject_unparticipating_mirror_project``.

        An unresolvable ``project_slug`` is NOT rejected here; that surfaces
        downstream at approve/materialize time, same posture as mirror's
        check.
        """
        from roboco.foundation.policy.board_programs import (
            PROGRAMS,
            project_participates,
        )
        from roboco.services.project import get_project_service

        slug = str(raw.get("project_slug", "")).strip()
        project = await get_project_service(self.task.session).get_by_slug(slug)
        if project is None:
            return None
        if not project_participates(PROGRAMS["dogfood"], project.board_programs):
            return Envelope.invalid_state(
                message=(
                    f"item {idx} targets project {slug!r}, which has not "
                    "opted into the dogfood program"
                ),
                remediate=(
                    f"drop item {idx} or ask the CEO to opt {slug!r} into "
                    "dogfood on its project settings page"
                ),
                context_briefing={},
            )
        return None

    async def propose_friction_fixes(
        self,
        *,
        agent_id: UUID,
        items: list[dict[str, Any]],
    ) -> Envelope:
        """Product Owner authors a Dogfood friction audit (1-N evidence-backed
        item drafts, N = the registry's ``max_items_per_cycle``).

        Persists the audit onto the caller's open exploration task (markers)
        — each item starts 'proposed', awaiting the CEO's per-item approve/
        reject in the dogfood queue. One call per cycle: the exploration
        task stays open (and this verb keeps refusing) until every item is
        terminal. Mirrors ``propose_messaging_fixes`` — no top-level theme
        goal here, just the items.
        """
        await self._trace_board_proposal(
            agent_id=agent_id,
            verb="propose_friction_fixes",
            payload={"items": items},
        )
        from roboco.foundation.policy.board_programs import PROGRAMS

        role = await self._caller_role(agent_id)
        if role not in _DOGFOOD_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose friction fixes; only "
                    "the Product Owner authors this audit"
                ),
                remediate="this verb is Product-Owner-only",
                context_briefing={},
            )
        max_items = PROGRAMS["dogfood"].max_items_per_cycle
        if not (1 <= len(items) <= max_items):
            return Envelope.invalid_state(
                message=(
                    f"a friction audit needs 1-{max_items} item drafts, "
                    f"got {len(items)}"
                ),
                remediate=f"propose between 1 and {max_items} evidence-backed items",
                context_briefing={},
            )
        normalized: list[dict[str, Any]] = []
        for idx, raw in enumerate(items):
            if rej := await self._reject_friction_fix_item(raw, idx):
                return rej
            normalized.append(_normalize_friction_fix_item(idx, raw))

        from roboco.services.task import get_task_service

        task_svc = get_task_service(self.task.session)
        cycles = await task_svc.list_open_dogfood_cycles()
        task = next(
            (
                t
                for t in cycles
                if t.assigned_to == agent_id and markers.get_friction_fixes(t) is None
            ),
            None,
        )
        if task is None:
            return Envelope.invalid_state(
                message="no open dogfood exploration task assigned to you",
                remediate=(
                    "propose_friction_fixes only runs against an active "
                    "exploration cycle spawned by the dogfood engine; wait "
                    "for the next cycle"
                ),
                context_briefing={},
            )
        markers.set_friction_fixes(task, {"items": normalized})
        await self.task.session.flush()
        await self._notify_friction_fix_items(task, normalized)
        return Envelope.ok(
            status="friction_fixes_proposed",
            task_id=str(task.id),
            next="i_am_idle() — the CEO reviews each item in the dogfood queue",
            context_briefing={"item_count": len(normalized)},
        )

    async def _notify_friction_fix_items(
        self, task: Any, items: list[dict[str, Any]]
    ) -> None:
        """Best-effort push DM per proposed item — mirrors
        ``_notify_messaging_fix_items``."""
        if self._deps.notification_delivery is None:
            return
        id8 = str(task.id)[:8]
        for item in items:
            try:
                await self._deps.notification_delivery.notify_ceo_of_queue_item(
                    kind="dogfood",
                    id8=id8,
                    extra=str(item.get("id") or ""),
                    title=item.get("title") or "untitled",
                    related_task_id=task.id,
                )
            except Exception as exc:
                logger.warning(
                    "dogfood telegram notify failed (best-effort)",
                    error=str(exc),
                )

    @classmethod
    def _reject_feature_spotlight_fields(
        cls, feature_slug: str, feature_title: str, body: str
    ) -> Envelope | None:
        """Soup + 280-char validation for a spotlight draft's free-text fields,
        collapsed into one caller-side check (keeps propose_feature_spotlight's
        return-statement count under the xenon/PLR0911 budget)."""
        if rej := cls._reject_soup(feature_slug, field="feature_slug", min_chars=2):
            return rej
        if rej := cls._reject_soup(feature_title, field="feature_title", min_chars=4):
            return rej
        if rej := cls._reject_soup(body, field="body", min_chars=8):
            return rej
        if len(body) > MAX_TWEET_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"body is {len(body)} chars, over the {MAX_TWEET_CHARS}-char "
                    "tweet limit"
                ),
                remediate="shorten the post to 280 characters or fewer",
                context_briefing={},
            )
        return None

    async def _propose_feature_spotlight_skip(
        self, agent_id: UUID, skip_reason: str
    ) -> Envelope:
        """``skip=True`` branch of ``propose_feature_spotlight``: validate the
        reason, find the caller's open exploration, and record the skip.
        Split out to keep the caller's return-statement count under the
        xenon/PLR0911 budget."""
        if rej := self._reject_soup(skip_reason, field="skip_reason", min_chars=8):
            return rej

        from roboco.services.task import get_task_service
        from roboco.services.x_engine import get_x_engine

        task_svc = get_task_service(self.task.session)
        explorations = await task_svc.list_open_feature_explorations()
        task = next((t for t in explorations if t.assigned_to == agent_id), None)
        if task is None:
            return Envelope.invalid_state(
                message="no open feature-spotlight exploration task assigned to you",
                remediate=(
                    "propose_feature_spotlight only runs against an active "
                    "exploration spawned by the X engine; wait for the next cycle"
                ),
                context_briefing={},
            )
        engine = get_x_engine(self.task.session)
        await engine.skip_feature_spotlight(exploration_task=task, reason=skip_reason)
        return Envelope.ok(
            status="feature_spotlight_skipped",
            task_id=str(task.id),
            next="i_am_idle() — no draft was materialized this cycle",
            context_briefing={"skip_reason": skip_reason},
        )

    async def propose_feature_spotlight(
        self,
        *,
        agent_id: UUID,
        feature_slug: str = "",
        feature_title: str = "",
        body: str = "",
        wants_video: bool = False,
        video_script: str = "",
        skip: bool = False,
        skip_reason: str = "",
    ) -> Envelope:
        """Head of Marketing authors ONE feature-spotlight draft, or skips.

        Validates role, field lengths, the 280-char tweet limit, and that the
        feature hasn't already been covered, then materializes the held X-queue
        draft and completes the caller's exploration task. One call per cycle.

        ``skip=True`` is the "nothing worth spotlighting this cycle" exit — a
        forced, weak spotlight is worse than skipping one. It requires a
        substantive ``skip_reason`` and ignores ``feature_slug``/
        ``feature_title``/``body``/``wants_video``/``video_script`` entirely:
        no draft is materialized, no feature is marked seen, but the
        exploration task still completes (``XEngine.skip_feature_spotlight``)
        so the skip counts as this cycle's activity for the engine's
        smart-cadence guard.

        ``wants_video`` optionally requests a companion video. The video
        authoring task no longer opens here — it opens later, at CEO-approve
        time (``XPostService._open_spotlight_video``, gated on
        ``video_engine_enabled AND video_on_spotlight``), so a ux-dev never
        burns a cycle on a spotlight the CEO then rejects. This verb only
        records the request (+ optional script) on the draft's
        ``x_feature_ref`` marker for approve time to read. Defaults leave the
        flow byte-for-byte unchanged.
        """
        await self._trace_board_proposal(
            agent_id=agent_id,
            verb="propose_feature_spotlight",
            payload={
                "feature_slug": feature_slug,
                "feature_title": feature_title,
                "body": body,
                "wants_video": wants_video,
                "video_script": video_script,
                "skip": skip,
                "skip_reason": skip_reason,
            },
        )
        role = await self._caller_role(agent_id)
        if role not in _FEATURE_SPOTLIGHT_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose a feature spotlight; only the "
                    "Head of Marketing does"
                ),
                remediate="this verb is Head-of-Marketing-only",
                context_briefing={},
            )
        if skip:
            return await self._propose_feature_spotlight_skip(agent_id, skip_reason)
        if rej := self._reject_feature_spotlight_fields(
            feature_slug, feature_title, body
        ):
            return rej

        from roboco.services.task import get_task_service
        from roboco.services.x_engine import get_x_engine

        task_svc = get_task_service(self.task.session)
        explorations = await task_svc.list_open_feature_explorations()
        task = next((t for t in explorations if t.assigned_to == agent_id), None)
        if task is None:
            return Envelope.invalid_state(
                message="no open feature-spotlight exploration task assigned to you",
                remediate=(
                    "propose_feature_spotlight only runs against an active "
                    "exploration spawned by the X engine; wait for the next cycle"
                ),
                context_briefing={},
            )
        engine = get_x_engine(self.task.session)
        if await engine.is_feature_seen(feature_slug):
            return Envelope.invalid_state(
                message=f"feature {feature_slug!r} was already covered",
                remediate=(
                    "pick a different, not-yet-covered feature — see the "
                    "seen-features list in your briefing"
                ),
                context_briefing={},
            )
        new_task = await engine.materialize_feature_spotlight(
            exploration_task=task,
            feature_slug=feature_slug,
            feature_title=feature_title,
            body=body,
        )
        if wants_video:
            # Gating (video_engine_enabled AND video_on_spotlight) and the
            # actual open_video_task call happen later, at CEO-approve time —
            # see XPostService._open_spotlight_video. Stash the request here
            # so approve time has it; slug/title are already on this ref from
            # materialize_feature_spotlight above, so re-set alongside them.
            markers.set_x_feature_ref(
                new_task,
                {
                    "slug": feature_slug,
                    "title": feature_title,
                    "wants_video": True,
                    "video_script": video_script.strip(),
                },
            )
            await self.task.session.flush()
        return Envelope.ok(
            status="feature_spotlight_proposed",
            task_id=str(new_task.id),
            next="i_am_idle() — the CEO reviews the draft in the X post queue",
            context_briefing={
                "feature_slug": feature_slug,
                "feature_title": feature_title,
            },
        )

    @classmethod
    def _reject_editorial_post_fields(
        cls, angle: str, body: str, rationale: str
    ) -> Envelope | None:
        """Angle vocabulary + soup + 280-char validation for a Megaphone
        draft's fields, collapsed into one caller-side check (keeps
        propose_editorial_post's return-statement count under the
        xenon/PLR0911 budget) — mirrors
        ``_reject_feature_spotlight_fields``."""
        if angle not in _EDITORIAL_ANGLES:
            return Envelope.invalid_state(
                message=f"angle {angle!r} is not a recognized editorial angle",
                remediate=(
                    "angle must be one of: " + ", ".join(sorted(_EDITORIAL_ANGLES))
                ),
                context_briefing={},
            )
        if rej := cls._reject_soup(body, field="body", min_chars=8):
            return rej
        if len(body) > MAX_TWEET_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"body is {len(body)} chars, over the {MAX_TWEET_CHARS}-char "
                    "tweet limit"
                ),
                remediate="shorten the post to 280 characters or fewer",
                context_briefing={},
            )
        if rej := cls._reject_soup(rationale, field="rationale", min_chars=8):
            return rej
        if len(rationale) > _EDITORIAL_RATIONALE_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"rationale is {len(rationale)} chars, over the "
                    f"{_EDITORIAL_RATIONALE_MAX_CHARS}-char cap"
                ),
                remediate="shorten the rationale",
                context_briefing={},
            )
        return None

    async def propose_editorial_post(
        self,
        *,
        agent_id: UUID,
        angle: str = "",
        body: str = "",
        rationale: str = "",
    ) -> Envelope:
        """Head of Marketing authors ONE Megaphone editorial-calendar post.

        Validates role, the angle vocabulary, the 280-char tweet limit, and
        the rationale, then materializes the SAME held X-queue draft
        ``propose_feature_spotlight`` uses (via ``XEngine.
        materialize_editorial_post``, source=x_editorial) and completes the
        caller's exploration task in the same call — a Megaphone post has no
        per-item CEO decision to leave the exploration open for, mirroring
        the x_feature complete-at-propose asymmetry. One call per cycle.
        """
        await self._trace_board_proposal(
            agent_id=agent_id,
            verb="propose_editorial_post",
            payload={"angle": angle, "body": body, "rationale": rationale},
        )
        role = await self._caller_role(agent_id)
        if role not in _MEGAPHONE_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose an editorial post; only "
                    "the Head of Marketing does"
                ),
                remediate="this verb is Head-of-Marketing-only",
                context_briefing={},
            )
        if rej := self._reject_editorial_post_fields(angle, body, rationale):
            return rej

        from roboco.services.task import get_task_service
        from roboco.services.x_engine import get_x_engine

        task_svc = get_task_service(self.task.session)
        cycles = await task_svc.list_open_megaphone_cycles()
        task = next((t for t in cycles if t.assigned_to == agent_id), None)
        if task is None:
            return Envelope.invalid_state(
                message="no open megaphone exploration task assigned to you",
                remediate=(
                    "propose_editorial_post only runs against an active "
                    "exploration spawned by the megaphone engine; wait for "
                    "the next cycle"
                ),
                context_briefing={},
            )
        engine = get_x_engine(self.task.session)
        new_task = await engine.materialize_editorial_post(
            exploration_task=task, angle=angle, body=body, rationale=rationale
        )
        return Envelope.ok(
            status="editorial_post_proposed",
            task_id=str(new_task.id),
            next="i_am_idle() — the CEO reviews the draft in the X post queue",
            context_briefing={"angle": angle, "rationale": rationale},
        )

    @classmethod
    def _reject_barfly_item_shape(cls, raw: Any, idx: int) -> Envelope | None:
        """Validate one raw conversation-reply item dict's shape; None when
        clean (before ``tweet_id`` resolution against the task's real
        candidates, which needs the task in hand)."""
        if not isinstance(raw, dict):
            return Envelope.invalid_state(
                message=f"item {idx} is not an object",
                remediate=(
                    "each item must be an object with tweet_id/reply_body/rationale"
                ),
                context_briefing={},
            )
        tweet_id = raw.get("tweet_id")
        if not isinstance(tweet_id, str) or not tweet_id.strip():
            return Envelope.invalid_state(
                message=f"item {idx} is missing 'tweet_id'",
                remediate=(
                    f"item {idx}'s tweet_id must name one of the candidate "
                    "conversations already on this task"
                ),
                context_briefing={},
            )
        reply_body = raw.get("reply_body")
        if not isinstance(reply_body, str) or not reply_body.strip():
            return Envelope.invalid_state(
                message=f"item {idx} is missing 'reply_body'",
                remediate=f"provide a substantive reply_body for item {idx}",
                context_briefing={},
            )
        if rej := cls._reject_soup(
            reply_body, field=f"item {idx} reply_body", min_chars=8
        ):
            return rej
        if len(reply_body) > MAX_TWEET_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"item {idx} reply_body is {len(reply_body)} chars, over "
                    f"the {MAX_TWEET_CHARS}-char tweet limit"
                ),
                remediate=f"shorten item {idx}'s reply_body to {MAX_TWEET_CHARS} chars",
                context_briefing={},
            )
        return cls._reject_barfly_item_rationale(raw, idx)

    @classmethod
    def _reject_barfly_item_rationale(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate the required ``rationale`` field — split out of
        ``_reject_barfly_item_shape`` to keep its own return-statement count
        under the xenon/PLR0911 budget."""
        rationale = raw.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            return Envelope.invalid_state(
                message=f"item {idx} is missing 'rationale'",
                remediate=f"provide a substantive rationale for item {idx}",
                context_briefing={},
            )
        if rej := cls._reject_soup(
            rationale, field=f"item {idx} rationale", min_chars=8
        ):
            return rej
        if len(rationale) > _BARFLY_RATIONALE_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"item {idx} rationale is {len(rationale)} chars, over "
                    f"the {_BARFLY_RATIONALE_MAX_CHARS}-char cap"
                ),
                remediate=f"shorten item {idx}'s rationale",
                context_briefing={},
            )
        return None

    @staticmethod
    def _reject_barfly_item_candidate(
        raw: dict[str, Any], idx: int, candidates_by_id: dict[str, dict[str, Any]]
    ) -> Envelope | None:
        """Reject a ``tweet_id`` that doesn't name one of THIS cycle's real
        screened candidates — the agent must reply to what was actually
        found, never invent a tweet. Split out so
        ``_reject_barfly_item``'s xenon budget stays flat."""
        tweet_id = str(raw["tweet_id"]).strip()
        if tweet_id in candidates_by_id:
            return None
        valid = ", ".join(sorted(candidates_by_id)) or "(none)"
        return Envelope.invalid_state(
            message=(
                f"item {idx} tweet_id {tweet_id!r} does not match any "
                "candidate conversation on this task"
            ),
            remediate=f"item {idx}'s tweet_id must be one of: {valid}",
            context_briefing={},
        )

    def _reject_barfly_caller_and_bounds(
        self, role: str, items: list[dict[str, Any]], max_items: int
    ) -> Envelope | None:
        """Role gate + item-count bounds — split out so the main verb's own
        branch count stays flat (xenon budget)."""
        if role not in _BARFLY_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose conversation replies; only "
                    "the Head of Marketing authors them"
                ),
                remediate="this verb is Head-of-Marketing-only",
                context_briefing={},
            )
        if not (1 <= len(items) <= max_items):
            return Envelope.invalid_state(
                message=(
                    f"conversation replies need 1-{max_items} item drafts, "
                    f"got {len(items)}"
                ),
                remediate=f"propose between 1 and {max_items} drafted replies",
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_barfly_item_shapes(cls, items: list[dict[str, Any]]) -> Envelope | None:
        """Pure, DB-free pass over every item's shape — split out so the
        main verb's own branch count stays flat (xenon budget)."""
        for idx, raw in enumerate(items):
            if rej := cls._reject_barfly_item_shape(raw, idx):
                return rej
        return None

    @staticmethod
    def _reject_barfly_item_candidates(
        items: list[dict[str, Any]], candidates_by_id: dict[str, dict[str, Any]]
    ) -> Envelope | None:
        """Second pass, once the exploration task (and so its real
        candidates) is in hand — split out so the main verb's own branch
        count stays flat (xenon budget)."""
        for idx, raw in enumerate(items):
            if rej := ContentActions._reject_barfly_item_candidate(
                raw, idx, candidates_by_id
            ):
                return rej
        return None

    @staticmethod
    async def _materialize_barfly_replies(
        engine: Any,
        task: Any,
        items: list[dict[str, Any]],
        candidates_by_id: dict[str, dict[str, Any]],
    ) -> list[str]:
        """One held draft per approved-shape item, through the shared
        ``_originate_post`` chokepoint — split out so the main verb's own
        branch count stays flat (xenon budget)."""
        materialized_ids: list[str] = []
        for raw in items:
            candidate = candidates_by_id[str(raw["tweet_id"]).strip()]
            new_task = await engine.materialize_barfly_reply(
                exploration_task=task,
                candidate=candidate,
                reply_body=str(raw["reply_body"]).strip(),
                rationale=str(raw["rationale"]).strip(),
            )
            materialized_ids.append(str(new_task.id))
        return materialized_ids

    async def propose_conversation_replies(
        self,
        *,
        agent_id: UUID,
        items: list[dict[str, Any]],
    ) -> Envelope:
        """Head of Marketing drafts 1-N replies (N = the registry's
        ``max_items_per_cycle``) to screened X conversations Barfly's search
        cycle already gathered onto the exploration task.

        Validation runs in two passes, mirroring every other item-verb's
        pure-then-DB split: first EVERY item's shape (dict/tweet_id/
        reply_body/rationale — no DB touched), then the exploration task is
        resolved, then EVERY item's ``tweet_id`` is checked against that
        task's own screened candidates — an invented tweet is rejected
        naming the valid ids. Unlike ``propose_gap_fill``/``propose_bug_
        hunt`` (a per-item CEO queue that keeps the exploration task open)
        this mirrors ``propose_feature_spotlight``'s complete-at-propose
        asymmetry MULTIPLIED across every item: each approved-shape reply
        materializes its own held draft (source=x_barfly) through
        ``XEngine.materialize_barfly_reply`` in this same call, then the
        exploration task itself completes — the CEO decides each
        materialized draft individually in the existing X post queue, not on
        this task.
        """
        await self._trace_board_proposal(
            agent_id=agent_id,
            verb="propose_conversation_replies",
            payload={"items": items},
        )
        from roboco.foundation.policy.board_programs import PROGRAMS

        role = await self._caller_role(agent_id)
        max_items = PROGRAMS["barfly"].max_items_per_cycle
        if rej := self._reject_barfly_caller_and_bounds(role, items, max_items):
            return rej
        if rej := self._reject_barfly_item_shapes(items):
            return rej

        from roboco.services.task import get_task_service

        task_svc = get_task_service(self.task.session)
        cycles = await task_svc.list_open_barfly_cycles()
        task = next((t for t in cycles if t.assigned_to == agent_id), None)
        if task is None:
            return Envelope.invalid_state(
                message="no open barfly exploration task assigned to you",
                remediate=(
                    "propose_conversation_replies only runs against an active "
                    "exploration cycle spawned by the barfly engine; wait for "
                    "the next cycle"
                ),
                context_briefing={},
            )
        candidates_by_id = {
            str(c.get("id")): c
            for c in markers.get_barfly_candidates(task)
            if isinstance(c, dict) and c.get("id")
        }
        if rej := self._reject_barfly_item_candidates(items, candidates_by_id):
            return rej

        from roboco.services.x_engine import get_x_engine

        engine = get_x_engine(self.task.session)
        materialized_ids = await self._materialize_barfly_replies(
            engine, task, items, candidates_by_id
        )
        task.status = TaskStatus.COMPLETED
        await self.task.session.flush()
        return Envelope.ok(
            status="conversation_replies_proposed",
            task_id=str(task.id),
            next="i_am_idle() — the CEO reviews each reply in the X post queue",
            context_briefing={
                "item_count": len(items),
                "materialized_task_ids": materialized_ids,
            },
        )

    @staticmethod
    def _reject_market_brief_url(url: str, idx: int) -> Envelope | None:
        """An uncited market claim is noise (spec Task 2) — validate
        ``source_url`` parses as a real http(s) URL rather than soup-checking
        it as prose."""
        if len(url) > _MARKET_BRIEF_FINDING_SOURCE_URL_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"finding {idx} source_url is {len(url)} chars, over the "
                    f"{_MARKET_BRIEF_FINDING_SOURCE_URL_MAX_CHARS}-char cap"
                ),
                remediate=f"shorten finding {idx}'s source_url",
                context_briefing={},
            )
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return Envelope.invalid_state(
                message=f"finding {idx} source_url {url!r} is not a valid http(s) URL",
                remediate=(
                    f"provide a real http(s) URL finding {idx}'s claim came from"
                ),
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_market_brief_finding(cls, raw: Any, idx: int) -> Envelope | None:
        """Validate one raw market-brief finding dict; None when clean."""
        if not isinstance(raw, dict):
            return Envelope.invalid_state(
                message=f"finding {idx} is not an object",
                remediate="each finding needs claim/source_url/relevance",
                context_briefing={},
            )
        if rej := cls._reject_market_brief_finding_claim(raw, idx):
            return rej
        if rej := cls._reject_market_brief_finding_source(raw, idx):
            return rej
        return cls._reject_market_brief_finding_relevance(raw, idx)

    @classmethod
    def _reject_market_brief_finding_claim(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Split out of ``_reject_market_brief_finding`` to keep its
        return-statement count under the xenon/PLR0911 budget."""
        claim = raw.get("claim")
        if not isinstance(claim, str) or not claim.strip():
            return Envelope.invalid_state(
                message=f"finding {idx} is missing 'claim'",
                remediate=f"provide a substantive claim for finding {idx}",
                context_briefing={},
            )
        if rej := cls._reject_soup(claim, field=f"finding {idx} claim", min_chars=8):
            return rej
        if len(claim) > _MARKET_BRIEF_FINDING_CLAIM_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"finding {idx} claim is {len(claim)} chars, over the "
                    f"{_MARKET_BRIEF_FINDING_CLAIM_MAX_CHARS}-char cap"
                ),
                remediate=f"shorten finding {idx}'s claim",
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_market_brief_finding_source(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Split out of ``_reject_market_brief_finding`` to keep its
        return-statement count under the xenon/PLR0911 budget."""
        source_url = raw.get("source_url")
        if not isinstance(source_url, str) or not source_url.strip():
            return Envelope.invalid_state(
                message=(
                    f"finding {idx} is missing 'source_url' — an uncited "
                    "market claim is noise"
                ),
                remediate=(
                    f"provide the http(s) source URL finding {idx}'s claim came from"
                ),
                context_briefing={},
            )
        return cls._reject_market_brief_url(source_url, idx)

    @classmethod
    def _reject_market_brief_finding_relevance(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Split out of ``_reject_market_brief_finding`` to keep its
        return-statement count under the xenon/PLR0911 budget."""
        relevance = raw.get("relevance")
        if not isinstance(relevance, str) or not relevance.strip():
            return Envelope.invalid_state(
                message=f"finding {idx} is missing 'relevance'",
                remediate=f"provide a substantive relevance for finding {idx}",
                context_briefing={},
            )
        if rej := cls._reject_soup(
            relevance, field=f"finding {idx} relevance", min_chars=8
        ):
            return rej
        if len(relevance) > _MARKET_BRIEF_FINDING_RELEVANCE_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"finding {idx} relevance is {len(relevance)} chars, over "
                    f"the {_MARKET_BRIEF_FINDING_RELEVANCE_MAX_CHARS}-char cap"
                ),
                remediate=f"shorten finding {idx}'s relevance",
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_market_brief_text_list(
        cls, values: Any, *, field: str
    ) -> Envelope | None:
        """Validate an optional ``threats``/``opportunities`` list: at most
        ``_MARKET_BRIEF_LIST_MAX_ITEMS`` substantive strings, each capped at
        ``_MARKET_BRIEF_LIST_ITEM_MAX_CHARS``. ``None`` (omitted) is clean."""
        if values is None:
            return None
        if not isinstance(values, list) or len(values) > _MARKET_BRIEF_LIST_MAX_ITEMS:
            return Envelope.invalid_state(
                message=(
                    f"{field} must be a list of at most "
                    f"{_MARKET_BRIEF_LIST_MAX_ITEMS} strings"
                ),
                remediate=f"provide at most {_MARKET_BRIEF_LIST_MAX_ITEMS} {field}",
                context_briefing={},
            )
        for i, v in enumerate(values):
            if rej := cls._reject_market_brief_list_item(v, field=field, idx=i):
                return rej
        return None

    @classmethod
    def _reject_market_brief_list_item(
        cls, value: Any, *, field: str, idx: int
    ) -> Envelope | None:
        """One ``threats``/``opportunities`` entry — split out of
        ``_reject_market_brief_text_list`` to keep its own return-statement
        count under the xenon/PLR0911 budget."""
        if not isinstance(value, str) or not value.strip():
            return Envelope.invalid_state(
                message=f"{field}[{idx}] is empty",
                remediate=f"provide substantive text for {field}[{idx}] or drop it",
                context_briefing={},
            )
        if rej := cls._reject_soup(value, field=f"{field}[{idx}]", min_chars=4):
            return rej
        if len(value) > _MARKET_BRIEF_LIST_ITEM_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"{field}[{idx}] is {len(value)} chars, over the "
                    f"{_MARKET_BRIEF_LIST_ITEM_MAX_CHARS}-char cap"
                ),
                remediate=f"shorten {field}[{idx}]",
                context_briefing={},
            )
        return None

    async def propose_market_brief(
        self,
        *,
        agent_id: UUID,
        headline: str,
        findings: list[dict[str, Any]],
        threats: list[str] | None = None,
        opportunities: list[str] | None = None,
        positioning_note: str = "",
    ) -> Envelope:
        """Head of Marketing files ONE Periscope weekly market-research brief
        — competitors, adjacent-tool releases, positioning shifts — delivered
        as a held REPORT to the CEO.

        Unlike ``propose_roadmap``/``propose_bug_hunt`` (a per-item CEO queue
        that keeps the exploration task open until every item is decided),
        this mirrors ``propose_feature_spotlight``'s complete-at-propose
        asymmetry: a report has no per-item decision, so the exploration task
        completes in this same call. The brief is screened through
        ``injection_guard.screen_external_text`` before persisting — it is
        web-derived content that later reaches the roadmap exploration
        prompt, same untrusted-text posture as X mentions / vault notes
        (screen-and-flag, never drop).
        """
        await self._trace_board_proposal(
            agent_id=agent_id,
            verb="propose_market_brief",
            payload={
                "headline": headline,
                "findings": findings,
                "threats": threats,
                "opportunities": opportunities,
                "positioning_note": positioning_note,
            },
        )
        role = await self._caller_role(agent_id)
        if role not in _PERISCOPE_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose a market brief; only the "
                    "Head of Marketing authors one"
                ),
                remediate="this verb is Head-of-Marketing-only",
                context_briefing={},
            )
        if rej := self._reject_market_brief_fields(
            headline, findings, threats, opportunities, positioning_note
        ):
            return rej

        from roboco.services.task import get_task_service

        task_svc = get_task_service(self.task.session)
        cycles = await task_svc.list_open_periscope_cycles()
        task = next(
            (
                t
                for t in cycles
                if t.assigned_to == agent_id and markers.get_market_brief(t) is None
            ),
            None,
        )
        if task is None:
            return Envelope.invalid_state(
                message="no open periscope exploration task assigned to you",
                remediate=(
                    "propose_market_brief only runs against an active "
                    "exploration cycle spawned by the periscope engine; wait "
                    "for the next cycle"
                ),
                context_briefing={},
            )

        await self._persist_market_brief(
            task,
            headline=headline,
            findings=findings,
            threats=threats,
            opportunities=opportunities,
            positioning_note=positioning_note,
        )
        await self._notify_periscope_brief(task, headline.strip())
        return Envelope.ok(
            status="market_brief_proposed",
            task_id=str(task.id),
            next="i_am_idle() — the CEO reads the brief as a report in the panel",
            context_briefing={
                "headline": headline.strip(),
                "finding_count": len(findings),
            },
        )

    async def _persist_market_brief(
        self,
        task: Any,
        *,
        headline: str,
        findings: list[dict[str, Any]],
        threats: list[str] | None,
        opportunities: list[str] | None,
        positioning_note: str,
    ) -> None:
        """Normalize, screen, persist, and complete — split out of
        ``propose_market_brief`` to keep its own cyclomatic complexity under
        the xenon budget. Complete-at-propose: a report has no per-item CEO
        decision to wait on (the x_feature asymmetry, not the roadmap/
        pest-control per-item flow) — BoardProgramEngine's dedup ledger
        auto-closes the cycle row the moment it next checks this now-terminal
        exploration task.
        """
        normalized_findings = [
            _normalize_market_brief_finding(idx, raw)
            for idx, raw in enumerate(findings)
        ]
        normalized_threats = [str(t).strip() for t in (threats or [])]
        normalized_opportunities = [str(o).strip() for o in (opportunities or [])]
        normalized_note = positioning_note.strip()
        screened = screen_external_text(
            _render_market_brief_for_screening(
                headline,
                normalized_findings,
                normalized_threats,
                normalized_opportunities,
                normalized_note,
            ),
            source=f"periscope_brief:{task.id}",
        )
        if screened.flagged:
            logger.warning(
                "periscope: injection pattern detected in market brief",
                task_id=str(task.id),
                hits=screened.hits,
            )
        markers.set_market_brief(
            task,
            {
                "headline": headline.strip(),
                "findings": normalized_findings,
                "threats": normalized_threats,
                "opportunities": normalized_opportunities,
                "positioning_note": normalized_note,
                "injection_hits": screened.hits,
            },
        )
        task.status = TaskStatus.COMPLETED
        await self.task.session.flush()

    @classmethod
    def _reject_market_brief_findings_list(
        cls, findings: list[dict[str, Any]]
    ) -> Envelope | None:
        """The count cap + per-finding validation loop, split out of
        ``_reject_market_brief_fields`` to keep its own return-statement
        count under the xenon/PLR0911 budget."""
        from roboco.foundation.policy.board_programs import PROGRAMS

        max_findings = PROGRAMS["periscope"].max_items_per_cycle
        if not (1 <= len(findings) <= max_findings):
            return Envelope.invalid_state(
                message=(
                    f"a brief needs 1-{max_findings} cited findings, got "
                    f"{len(findings)}"
                ),
                remediate=f"propose between 1 and {max_findings} cited findings",
                context_briefing={},
            )
        for idx, raw in enumerate(findings):
            if rej := cls._reject_market_brief_finding(raw, idx):
                return rej
        return None

    @classmethod
    def _reject_market_brief_fields(
        cls,
        headline: str,
        findings: list[dict[str, Any]],
        threats: list[str] | None,
        opportunities: list[str] | None,
        positioning_note: str,
    ) -> Envelope | None:
        """Full field validation for ``propose_market_brief``, split out to
        keep the verb's own return-statement count under the xenon/PLR0911
        budget."""
        if rej := cls._reject_soup(headline, field="headline", min_chars=8):
            return rej
        if len(headline) > _MARKET_BRIEF_HEADLINE_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"headline is {len(headline)} chars, over the "
                    f"{_MARKET_BRIEF_HEADLINE_MAX_CHARS}-char cap"
                ),
                remediate="shorten the headline",
                context_briefing={},
            )
        if rej := cls._reject_market_brief_findings_list(findings):
            return rej
        if rej := cls._reject_market_brief_text_list(threats, field="threats"):
            return rej
        if rej := cls._reject_market_brief_text_list(
            opportunities, field="opportunities"
        ):
            return rej
        return cls._reject_market_brief_positioning_note(positioning_note)

    @classmethod
    def _reject_market_brief_positioning_note(cls, value: str) -> Envelope | None:
        """Split out of ``_reject_market_brief_fields`` to keep its own
        return-statement count under the xenon/PLR0911 budget. Optional —
        empty is clean; only a non-empty value is soup/length-checked."""
        if not value or not value.strip():
            return None
        if rej := cls._reject_soup(value, field="positioning_note", min_chars=8):
            return rej
        if len(value) > _MARKET_BRIEF_POSITIONING_NOTE_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"positioning_note is {len(value)} chars, over the "
                    f"{_MARKET_BRIEF_POSITIONING_NOTE_MAX_CHARS}-char cap"
                ),
                remediate="shorten positioning_note",
                context_briefing={},
            )
        return None

    async def _notify_periscope_brief(self, task: Any, headline: str) -> None:
        """Best-effort CEO nudge the moment a market brief lands — ONE call
        per cycle (a report, not N queue items), so unlike
        ``_notify_pest_hunt_items``/``_notify_roadmap_items`` this fires once,
        not per-finding."""
        if self._deps.notification_delivery is None:
            return
        try:
            # Savepoint: persists a notification row — see _notify_pitch.
            async with self.task.session.begin_nested():
                await self._deps.notification_delivery.notify_ceo_of_periscope_brief(
                    task=task, task_id=task.id, headline=headline
                )
        except Exception as exc:
            logger.warning(
                "periscope telegram notify failed (best-effort)", error=str(exc)
            )

    @staticmethod
    def _reject_quality_report_item_area(
        raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        area = raw.get("area")
        if not isinstance(area, str) or area.strip() not in _QUALITY_REPORT_AREAS:
            return Envelope.invalid_state(
                message=(
                    f"item {idx} area {area!r} must be one of "
                    f"{sorted(_QUALITY_REPORT_AREAS)}"
                ),
                remediate=(
                    f"set item {idx}'s area to one of {sorted(_QUALITY_REPORT_AREAS)}"
                ),
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_quality_report_item_text_fields(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Validate observation/evidence/suggested_action of one quality-
        report item dict — split from ``_reject_quality_report_item`` to
        keep its own xenon complexity budget."""
        for field, max_chars in (
            ("observation", _QUALITY_REPORT_ITEM_OBSERVATION_MAX_CHARS),
            ("evidence", _QUALITY_REPORT_ITEM_EVIDENCE_MAX_CHARS),
            ("suggested_action", _QUALITY_REPORT_ITEM_SUGGESTED_ACTION_MAX_CHARS),
        ):
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                return Envelope.invalid_state(
                    message=f"item {idx} is missing '{field}'",
                    remediate=f"provide a substantive '{field}' for item {idx}",
                    context_briefing={},
                )
            if rej := cls._reject_soup(value, field=f"item {idx} {field}", min_chars=8):
                return rej
            if len(value) > max_chars:
                return Envelope.invalid_state(
                    message=(
                        f"item {idx} {field} is {len(value)} chars, over the "
                        f"{max_chars}-char cap"
                    ),
                    remediate=f"shorten item {idx}'s {field}",
                    context_briefing={},
                )
        return None

    @classmethod
    def _reject_quality_report_item(cls, raw: Any, idx: int) -> Envelope | None:
        """Validate one raw quality-report item dict; None when clean.
        Mirrors ``_reject_market_brief_finding``."""
        if not isinstance(raw, dict):
            return Envelope.invalid_state(
                message=f"item {idx} is not an object",
                remediate=(
                    "each item needs area/observation/evidence/suggested_action"
                ),
                context_briefing={},
            )
        if rej := cls._reject_quality_report_item_area(raw, idx):
            return rej
        return cls._reject_quality_report_item_text_fields(raw, idx)

    @classmethod
    def _reject_quality_report_items(
        cls, items: list[dict[str, Any]]
    ) -> Envelope | None:
        """The count cap + per-item validation loop, split out of
        ``_reject_quality_report_fields`` to keep its own return-statement
        count under the xenon/PLR0911 budget."""
        from roboco.foundation.policy.board_programs import PROGRAMS

        max_items = PROGRAMS["sentinel"].max_items_per_cycle
        if not (1 <= len(items) <= max_items):
            return Envelope.invalid_state(
                message=(
                    f"a quality report needs 1-{max_items} items, got {len(items)}"
                ),
                remediate=f"propose between 1 and {max_items} evidence-backed items",
                context_briefing={},
            )
        for idx, raw in enumerate(items):
            if rej := cls._reject_quality_report_item(raw, idx):
                return rej
        return None

    @classmethod
    def _reject_quality_report_fields(
        cls, headline: str, items: list[dict[str, Any]], overall_assessment: str
    ) -> Envelope | None:
        """Full field validation for ``propose_quality_report``, split out to
        keep the verb's own return-statement count under the xenon/PLR0911
        budget."""
        if rej := cls._reject_soup(headline, field="headline", min_chars=8):
            return rej
        if len(headline) > _QUALITY_REPORT_HEADLINE_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"headline is {len(headline)} chars, over the "
                    f"{_QUALITY_REPORT_HEADLINE_MAX_CHARS}-char cap"
                ),
                remediate="shorten the headline",
                context_briefing={},
            )
        if rej := cls._reject_quality_report_items(items):
            return rej
        if rej := cls._reject_soup(
            overall_assessment, field="overall_assessment", min_chars=8
        ):
            return rej
        if len(overall_assessment) > _QUALITY_REPORT_OVERALL_ASSESSMENT_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"overall_assessment is {len(overall_assessment)} chars, "
                    "over the "
                    f"{_QUALITY_REPORT_OVERALL_ASSESSMENT_MAX_CHARS}-char cap"
                ),
                remediate="shorten overall_assessment",
                context_briefing={},
            )
        return None

    async def propose_quality_report(
        self,
        *,
        agent_id: UUID,
        headline: str,
        items: list[dict[str, Any]],
        overall_assessment: str,
    ) -> Envelope:
        """Auditor files ONE Sentinel weekly "state of quality" report —
        waiver-accumulation trends, conventions-violation hotspots, budget
        anomalies — delivered as a held REPORT to the CEO.

        Mirrors ``propose_market_brief``'s complete-at-propose asymmetry: a
        report has no per-item CEO decision, so the exploration task
        completes in this same call. Unlike ``propose_market_brief`` this is
        deliberately NOT screened through
        ``injection_guard.screen_external_text`` — every input here is
        internal org data (the findings ledger, the conventions table, the
        spend tables, the Auditor's own read of the codebase), never
        untrusted web/external text, so there is nothing to screen.
        """
        await self._trace_board_proposal(
            agent_id=agent_id,
            verb="propose_quality_report",
            payload={
                "headline": headline,
                "items": items,
                "overall_assessment": overall_assessment,
            },
        )
        role = await self._caller_role(agent_id)
        if role not in _SENTINEL_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose a quality report; only "
                    "the Auditor authors one"
                ),
                remediate="this verb is Auditor-only",
                context_briefing={},
            )
        if rej := self._reject_quality_report_fields(
            headline, items, overall_assessment
        ):
            return rej

        from roboco.services.task import get_task_service

        task_svc = get_task_service(self.task.session)
        cycles = await task_svc.list_open_sentinel_cycles()
        task = next(
            (
                t
                for t in cycles
                if t.assigned_to == agent_id and markers.get_quality_report(t) is None
            ),
            None,
        )
        if task is None:
            return Envelope.invalid_state(
                message="no open sentinel exploration task assigned to you",
                remediate=(
                    "propose_quality_report only runs against an active "
                    "exploration cycle spawned by the sentinel engine; wait "
                    "for the next cycle"
                ),
                context_briefing={},
            )

        await self._persist_quality_report(
            task,
            headline=headline,
            items=items,
            overall_assessment=overall_assessment,
        )
        await self._notify_quality_report(task, headline.strip())
        return Envelope.ok(
            status="quality_report_proposed",
            task_id=str(task.id),
            next="i_am_idle() — the CEO reads the report in the panel",
            context_briefing={
                "headline": headline.strip(),
                "item_count": len(items),
            },
        )

    async def _persist_quality_report(
        self,
        task: Any,
        *,
        headline: str,
        items: list[dict[str, Any]],
        overall_assessment: str,
    ) -> None:
        """Normalize, persist, and complete — split out of
        ``propose_quality_report`` to keep its own cyclomatic complexity
        under the xenon budget. Complete-at-propose: mirrors
        ``_persist_market_brief``."""
        normalized_items = [
            _normalize_quality_report_item(idx, raw) for idx, raw in enumerate(items)
        ]
        markers.set_quality_report(
            task,
            {
                "headline": headline.strip(),
                "items": normalized_items,
                "overall_assessment": overall_assessment.strip(),
            },
        )
        task.status = TaskStatus.COMPLETED
        await self.task.session.flush()

    async def _notify_quality_report(self, task: Any, headline: str) -> None:
        """Best-effort CEO nudge the moment a quality report lands — ONE call
        per cycle (a report, not N queue items), mirrors
        ``_notify_periscope_brief``."""
        if self._deps.notification_delivery is None:
            return
        try:
            # Savepoint: persists a notification row — see _notify_pitch.
            async with self.task.session.begin_nested():
                await self._deps.notification_delivery.notify_ceo_of_sentinel_report(
                    task=task, task_id=task.id, headline=headline
                )
        except Exception as exc:
            logger.warning(
                "sentinel telegram notify failed (best-effort)", error=str(exc)
            )

    @classmethod
    def _reject_campaign_name(cls, campaign_name: str) -> Envelope | None:
        if rej := cls._reject_soup(campaign_name, field="campaign_name", min_chars=3):
            return rej
        if len(campaign_name) > _CAMPAIGN_NAME_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"campaign_name is {len(campaign_name)} chars, over the "
                    f"{_CAMPAIGN_NAME_MAX_CHARS}-char cap"
                ),
                remediate="shorten campaign_name",
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_campaign_post_body(
        cls, raw: dict[str, Any], idx: int
    ) -> Envelope | None:
        """Split out of ``_reject_campaign_post`` to keep its own
        return-statement count under the xenon/PLR0911 budget."""
        body = raw.get("body")
        if not isinstance(body, str) or not body.strip():
            return Envelope.invalid_state(
                message=f"post {idx} is missing 'body'",
                remediate=f"provide the tweet text for post {idx}",
                context_briefing={},
            )
        if rej := cls._reject_soup(body, field=f"post {idx} body", min_chars=8):
            return rej
        if len(body) > MAX_TWEET_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"post {idx} body is {len(body)} chars, over the "
                    f"{MAX_TWEET_CHARS}-char tweet limit"
                ),
                remediate=(
                    f"shorten post {idx} to {MAX_TWEET_CHARS} characters or fewer"
                ),
                context_briefing={},
            )
        return None

    @staticmethod
    def _reject_campaign_post_stage(raw: dict[str, Any], idx: int) -> Envelope | None:
        stage = raw.get("stage_label")
        if not isinstance(stage, str) or stage.strip() not in _CAMPAIGN_STAGE_LABELS:
            return Envelope.invalid_state(
                message=(
                    f"post {idx} stage_label must be one of "
                    f"{sorted(_CAMPAIGN_STAGE_LABELS)}"
                ),
                remediate=(
                    f"set post {idx}'s stage_label to one of "
                    f"{sorted(_CAMPAIGN_STAGE_LABELS)}"
                ),
                context_briefing={},
            )
        return None

    @staticmethod
    def _reject_campaign_post_timing(
        raw: dict[str, Any], idx: int, previous: datetime | None
    ) -> tuple[Envelope | None, Any]:
        """Parse + validate one post's ``publish_after``: a real ISO 8601
        datetime, strictly in the future at propose time, and strictly after
        the previous item's (ascending order across the campaign — spec §4's
        teaser -> launch -> follow-up -> spotlight arc). Returns
        ``(rejection, parsed)`` — a non-None rejection means ``parsed`` is
        unusable; the caller threads the clean ``parsed`` value into the NEXT
        item's ascending-order check."""
        value = raw.get("publish_after")
        if not isinstance(value, str) or not value.strip():
            return (
                Envelope.invalid_state(
                    message=f"post {idx} is missing 'publish_after'",
                    remediate=(
                        f"provide post {idx}'s recommended publish time as an "
                        "ISO 8601 datetime"
                    ),
                    context_briefing={},
                ),
                None,
            )
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            return (
                Envelope.invalid_state(
                    message=(
                        f"post {idx} publish_after {value!r} is not a valid "
                        "ISO 8601 datetime"
                    ),
                    remediate=(
                        f"provide post {idx}'s publish_after as an ISO 8601 "
                        "datetime, e.g. '2026-08-01T09:00:00+00:00'"
                    ),
                    context_briefing={},
                ),
                None,
            )
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        if parsed <= datetime.now(UTC):
            return (
                Envelope.invalid_state(
                    message=f"post {idx} publish_after {value!r} is not in the future",
                    remediate=f"post {idx}'s publish_after must be a future timestamp",
                    context_briefing={},
                ),
                None,
            )
        if previous is not None and parsed <= previous:
            return (
                Envelope.invalid_state(
                    message=(
                        f"post {idx} publish_after must be strictly after post "
                        f"{idx - 1}'s — campaign posts run in ascending order"
                    ),
                    remediate=(
                        f"push post {idx}'s publish_after later than post {idx - 1}'s"
                    ),
                    context_briefing={},
                ),
                None,
            )
        return None, parsed

    @classmethod
    def _reject_campaign_post(
        cls, raw: Any, idx: int, previous: datetime | None
    ) -> tuple[Envelope | None, Any]:
        """Validate one raw campaign-post dict; ``(None, parsed_publish_after)``
        when clean."""
        if not isinstance(raw, dict):
            return (
                Envelope.invalid_state(
                    message=f"post {idx} is not an object",
                    remediate=(
                        "each post must be an object with body/publish_after/"
                        "stage_label"
                    ),
                    context_briefing={},
                ),
                None,
            )
        if rej := cls._reject_campaign_post_body(raw, idx):
            return rej, None
        if rej := cls._reject_campaign_post_stage(raw, idx):
            return rej, None
        return cls._reject_campaign_post_timing(raw, idx, previous)

    async def propose_campaign(
        self,
        *,
        agent_id: UUID,
        campaign_name: str,
        posts: list[dict[str, Any]],
    ) -> Envelope:
        """Head of Marketing authors ONE War Room campaign — an ordered set
        of 2-6 held X drafts (teaser -> launch -> follow-up -> spotlight),
        each carrying a recommended ``publish_after`` timestamp.

        V1 is manual-cadence (spec, 2026-07-24, pinned by the orchestrating
        session): ``publish_after`` is GUIDANCE rendered in the panel queue,
        never a schedule anything acts on — the CEO approves each draft at
        its own moment, exactly like every other X-queue draft ("nothing
        auto-posts" stays absolute; see ``WarRoomEngine``'s module docstring
        for the documented auto-schedule ceiling, not built here). Call this
        exactly once per cycle: it materializes every post (via
        ``XEngine.materialize_campaign_post``) and completes the exploration
        task in the same call — mirrors ``propose_market_brief``'s
        complete-at-propose shape, batched over N posts.
        """
        await self._trace_board_proposal(
            agent_id=agent_id,
            verb="propose_campaign",
            payload={"campaign_name": campaign_name, "posts": posts},
        )
        role = await self._caller_role(agent_id)
        if role not in _WAR_ROOM_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose a campaign; only the "
                    "Head of Marketing authors one"
                ),
                remediate="this verb is Head-of-Marketing-only",
                context_briefing={},
            )
        if rej := self._reject_campaign_name(campaign_name):
            return rej
        if not (_CAMPAIGN_MIN_POSTS <= len(posts) <= _CAMPAIGN_MAX_POSTS):
            return Envelope.invalid_state(
                message=(
                    f"a campaign needs {_CAMPAIGN_MIN_POSTS}-"
                    f"{_CAMPAIGN_MAX_POSTS} ordered posts, got {len(posts)}"
                ),
                remediate=(
                    f"propose between {_CAMPAIGN_MIN_POSTS} and "
                    f"{_CAMPAIGN_MAX_POSTS} posts"
                ),
                context_briefing={},
            )
        name = campaign_name.strip()
        normalized: list[dict[str, Any]] = []
        previous: datetime | None = None
        for idx, raw in enumerate(posts):
            rejection, parsed = self._reject_campaign_post(raw, idx, previous)
            if rejection is not None:
                return rejection
            previous = parsed
            normalized.append(
                {
                    "body": str(raw["body"]).strip(),
                    "campaign_name": name,
                    "stage_label": str(raw["stage_label"]).strip(),
                    "publish_after": parsed.isoformat(),
                    "sequence": idx + 1,
                }
            )

        from roboco.services.task import get_task_service
        from roboco.services.x_engine import get_x_engine

        task_svc = get_task_service(self.task.session)
        cycles = await task_svc.list_open_war_room_cycles()
        task = next((t for t in cycles if t.assigned_to == agent_id), None)
        if task is None:
            return Envelope.invalid_state(
                message="no open war-room exploration task assigned to you",
                remediate=(
                    "propose_campaign only runs against an active exploration "
                    "cycle spawned by the War Room engine; wait for the next "
                    "cycle"
                ),
                context_briefing={},
            )
        engine = get_x_engine(self.task.session)
        for item in normalized:
            await engine.materialize_campaign_post(
                exploration_task=task,
                campaign_ref={
                    "campaign_name": item["campaign_name"],
                    "stage_label": item["stage_label"],
                    "publish_after": item["publish_after"],
                    "sequence": item["sequence"],
                },
                body=item["body"],
            )
        task.status = TaskStatus.COMPLETED
        await self.task.session.flush()
        return Envelope.ok(
            status="campaign_proposed",
            task_id=str(task.id),
            next="i_am_idle() — the CEO reviews each post in the X post queue",
            context_briefing={"campaign_name": name, "post_count": len(normalized)},
        )

    @classmethod
    def _reject_caption(
        cls, value: str, *, field: str, max_chars: int
    ) -> Envelope | None:
        """Soup + max-length check for one caption field, folded into a single
        return point so ``_reject_video_fields`` (which calls this twice) stays
        under the xenon/PLR0911 return-count budget."""
        if rej := cls._reject_soup(value, field=field, min_chars=8):
            return rej
        if len(value) > max_chars:
            return Envelope.invalid_state(
                message=(
                    f"{field} is {len(value)} chars, over the {max_chars}-char limit"
                ),
                remediate=f"shorten {field} to {max_chars} characters or fewer",
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_video_fields(
        cls,
        composition_id: str,
        x_caption: str,
        tiktok_caption: str,
        platforms: list[str],
    ) -> Envelope | None:
        """Soup + limit + platform-set validation for a video draft's fields,
        collapsed into one caller-side check (keeps propose_video's return-
        statement count under the xenon/PLR0911 budget)."""
        if rej := cls._reject_soup(composition_id, field="composition_id", min_chars=2):
            return rej
        # Mirror the video-renderer sidecar's charset rule so an unrenderable
        # id is refused at authoring time, not at render time days later.
        if not _COMPOSITION_ID_RE.fullmatch(composition_id.strip()):
            return Envelope.invalid_state(
                message=(
                    f"composition_id {composition_id!r} is not renderable — "
                    "letters, digits, '_' or '-' with optional interior dots"
                ),
                remediate=(
                    "rename the composition dir to match (e.g. "
                    "'release-0-25-0' or 'release-0.25.0') and call "
                    "propose_video again with that id"
                ),
            )
        if rej := cls._reject_caption(
            x_caption, field="x_caption", max_chars=MAX_TWEET_CHARS
        ):
            return rej
        if rej := cls._reject_caption(
            tiktok_caption, field="tiktok_caption", max_chars=_MAX_TIKTOK_CAPTION_CHARS
        ):
            return rej
        if not platforms or not set(platforms) <= _VIDEO_PLATFORMS:
            return Envelope.invalid_state(
                message=(
                    f"platforms {platforms!r} must be a non-empty subset of "
                    f"{sorted(_VIDEO_PLATFORMS)}"
                ),
                remediate="pass platforms as a non-empty list from {'x','tiktok'}",
                context_briefing={},
            )
        return None

    async def propose_video(
        self,
        *,
        agent_id: UUID,
        composition_id: str,
        x_caption: str,
        tiktok_caption: str,
        platforms: list[str],
        input_props: dict[str, Any] | None = None,
    ) -> Envelope:
        """UX/UI dev proposes a video's composition ref + captions.

        Metadata-only — NO render, no sidecar/HTTP call: rendering happens
        later in an orchestrator-async loop, off this path (the do-tool
        transport has a fixed 30s timeout a real render would blow through).

        Gated on the caller's TEAM, not role (v1: UX/UI only) — every dev's
        manifest carries this tool, so the runtime check here is the real
        gate. Validates the caption limits + platform set, then MERGES the
        fields onto the caller's open authoring task's ``video_draft``
        marker, preserving the occasion/script/brief the video engine seeded
        it with. commit + open_pr afterward sends the composition through
        the normal PR-review gate.
        """
        await self._trace_board_proposal(
            agent_id=agent_id,
            verb="propose_video",
            payload={
                "composition_id": composition_id,
                "x_caption": x_caption,
                "tiktok_caption": tiktok_caption,
                "platforms": platforms,
                "input_props": input_props,
            },
        )
        from roboco.foundation.identity import Team

        team = await self._caller_team(agent_id)
        if team is not Team.UX_UI:
            team_label = team.value if team is not None else "none"
            return Envelope.not_authorized(
                message=(
                    f"team {team_label!r} cannot propose video metadata; only "
                    "UX/UI authors video compositions"
                ),
                remediate="this verb is UX/UI-only",
                context_briefing={},
            )
        if rej := self._reject_video_fields(
            composition_id, x_caption, tiktok_caption, platforms
        ):
            return rej

        from roboco.services.task import VIDEO_SOURCE, get_task_service

        task_svc = get_task_service(self.task.session)
        task = await task_svc.get_active_task_for_agent(agent_id)
        if task is None or task.source != VIDEO_SOURCE:
            return Envelope.invalid_state(
                message="no active video-authoring task assigned to you",
                remediate=(
                    "propose_video runs against the video task you're actively "
                    "working on; claim your assigned authoring task first"
                ),
                context_briefing={},
            )
        existing = markers.get_video_draft(task) or {}
        markers.set_video_draft(
            task,
            {
                **existing,
                "composition_id": composition_id,
                "input_props": input_props or {},
                "x_caption": x_caption,
                "tiktok_caption": tiktok_caption,
                "platforms": platforms,
            },
        )
        await self.task.session.flush()
        return Envelope.ok(
            status="video_proposed",
            task_id=str(task.id),
            next="commit your composition, then open_pr to send it through the PR gate",
            context_briefing={
                "composition_id": composition_id,
                "platforms": platforms,
            },
        )

    @classmethod
    def _reject_postmortem_text_fields(
        cls, incident_summary: str, root_cause: str
    ) -> Envelope | None:
        """Soup + length caps on the postmortem's two free-text narrative
        fields, folded into one caller-side check (xenon/PLR0911 budget —
        mirrors ``_reject_feature_spotlight_fields``)."""
        if rej := cls._reject_soup(
            incident_summary, field="incident_summary", min_chars=20
        ):
            return rej
        if len(incident_summary) > _CORONER_INCIDENT_SUMMARY_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"incident_summary is {len(incident_summary)} chars, over "
                    f"the {_CORONER_INCIDENT_SUMMARY_MAX_CHARS}-char limit"
                ),
                remediate="shorten incident_summary",
                context_briefing={},
            )
        if rej := cls._reject_soup(root_cause, field="root_cause", min_chars=20):
            return rej
        if len(root_cause) > _CORONER_ROOT_CAUSE_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"root_cause is {len(root_cause)} chars, over the "
                    f"{_CORONER_ROOT_CAUSE_MAX_CHARS}-char limit"
                ),
                remediate="shorten root_cause",
                context_briefing={},
            )
        return None

    @staticmethod
    def _reject_postmortem_failed_stage(failed_stage: str) -> Envelope | None:
        """``failed_stage`` must be a real lifecycle status — never a made-up
        label — so it stays comparable across postmortems."""
        from roboco.models.base import TaskStatus

        valid = {s.value for s in TaskStatus}
        if failed_stage not in valid:
            return Envelope.invalid_state(
                message=f"failed_stage {failed_stage!r} is not a real task status",
                remediate=f"pass one of: {sorted(valid)}",
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_postmortem_process_change(cls, process_change: Any) -> Envelope | None:
        if not isinstance(process_change, dict):
            return Envelope.invalid_state(
                message="process_change must be an object",
                remediate="pass process_change={'kind': ..., 'description': ...}",
                context_briefing={},
            )
        kind = process_change.get("kind")
        if kind not in _CORONER_PROCESS_CHANGE_KINDS:
            return Envelope.invalid_state(
                message=f"process_change.kind {kind!r} is invalid",
                remediate=(
                    f"kind must be one of {sorted(_CORONER_PROCESS_CHANGE_KINDS)}"
                ),
                context_briefing={},
            )
        description = str(process_change.get("description", ""))
        if rej := cls._reject_soup(
            description, field="process_change.description", min_chars=15
        ):
            return rej
        if len(description) > _CORONER_PROCESS_CHANGE_DESC_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"process_change.description is {len(description)} chars, "
                    f"over the {_CORONER_PROCESS_CHANGE_DESC_MAX_CHARS}-char limit"
                ),
                remediate="shorten process_change.description",
                context_briefing={},
            )
        return None

    @classmethod
    def _reject_postmortem_playbook(
        cls, process_change: dict[str, Any], playbook: dict[str, Any] | None
    ) -> Envelope | None:
        """``playbook`` is required iff ``process_change.kind == 'playbook'``
        (spec §4) — never optional-but-ignored on that kind, never demanded
        on any other."""
        if process_change.get("kind") != "playbook":
            return None
        if not isinstance(playbook, dict):
            return Envelope.invalid_state(
                message="process_change.kind='playbook' requires a playbook",
                remediate="pass playbook={'title': ..., 'body': ...}",
                context_briefing={},
            )
        if rej := cls._reject_soup(
            str(playbook.get("title", "")), field="playbook.title", min_chars=5
        ):
            return rej
        return cls._reject_soup(
            str(playbook.get("body", "")), field="playbook.body", min_chars=20
        )

    @classmethod
    def _reject_postmortem(
        cls,
        incident_summary: str,
        root_cause: str,
        failed_stage: str,
        process_change: Any,
        playbook: dict[str, Any] | None,
    ) -> Envelope | None:
        if rej := cls._reject_postmortem_text_fields(incident_summary, root_cause):
            return rej
        if rej := cls._reject_postmortem_failed_stage(failed_stage):
            return rej
        if rej := cls._reject_postmortem_process_change(process_change):
            return rej
        return cls._reject_postmortem_playbook(process_change, playbook)

    async def _draft_coroner_playbook(
        self, agent_id: UUID, incident_summary: str, playbook: dict[str, Any]
    ) -> tuple[str | None, Envelope | None]:
        """Create the process-change playbook DRAFT directly via
        ``PlaybookService`` — not the ``draft_playbook`` do-verb, since this
        call already runs inside the Auditor-only ``propose_postmortem`` gate.
        The Auditor also carries ``draft_playbook`` on its manifest
        (role_config.py) so a coroner-authored draft is indistinguishable
        from any other in the pending-playbook curation queue — reviewed and
        approved/rejected there same as any delivery-role draft, never
        self-approved in this same call. Returns (playbook_id, None) on
        success or (None, rejection_envelope) on a title conflict — checked
        BEFORE any postmortem-task mutation so a conflict is a clean,
        retryable rejection, not a half-completed autopsy."""
        from roboco.models.playbook import PlaybookCreate
        from roboco.services.base import ConflictError
        from roboco.services.playbook import get_playbook_service

        try:
            drafted = await get_playbook_service(self.task.session).draft(
                PlaybookCreate(
                    title=str(playbook["title"]).strip(),
                    problem=incident_summary.strip(),
                    procedure=str(playbook["body"]).strip(),
                    tags=["coroner", "postmortem"],
                ),
                created_by=agent_id,
                source_program="coroner",
            )
        except ConflictError as exc:
            return None, Envelope.invalid_state(
                message=str(exc),
                remediate="use a more distinct playbook title (slug must be unique)",
                context_briefing={},
            )
        return str(drafted.id), None

    @classmethod
    def _reject_playbook_draft_item(cls, raw: Any, idx: int) -> Envelope | None:
        """Validate one raw playbook-draft dict; None when clean. Mirrors
        ``_reject_quality_report_item_text_fields``'s loop-over-fields shape."""
        if not isinstance(raw, dict):
            return Envelope.invalid_state(
                message=f"draft {idx} is not an object",
                remediate="each draft needs title/body/pattern_evidence",
                context_briefing={},
            )
        for field, min_chars, max_chars in (
            ("title", 5, _PLAYBOOK_DRAFT_TITLE_MAX_CHARS),
            ("body", 20, _PLAYBOOK_DRAFT_BODY_MAX_CHARS),
            ("pattern_evidence", 15, _PLAYBOOK_DRAFT_PATTERN_EVIDENCE_MAX_CHARS),
        ):
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                return Envelope.invalid_state(
                    message=f"draft {idx} is missing '{field}'",
                    remediate=f"provide a substantive '{field}' for draft {idx}",
                    context_briefing={},
                )
            if rej := cls._reject_soup(
                value, field=f"draft {idx} {field}", min_chars=min_chars
            ):
                return rej
            if len(value) > max_chars:
                return Envelope.invalid_state(
                    message=(
                        f"draft {idx} {field} is {len(value)} chars, over the "
                        f"{max_chars}-char cap"
                    ),
                    remediate=f"shorten draft {idx}'s {field}",
                    context_briefing={},
                )
        return None

    @staticmethod
    def _reject_playbook_draft_duplicate_titles(
        drafts: list[dict[str, Any]],
    ) -> Envelope | None:
        """Case-insensitive dedup WITHIN this batch — split out so the
        title-conflict message is distinct from the per-item field check."""
        seen: set[str] = set()
        for idx, raw in enumerate(drafts):
            title = str(raw.get("title", "")).strip().lower()
            if title in seen:
                return Envelope.invalid_state(
                    message=f"draft {idx} title duplicates another draft in this batch",
                    remediate="give each draft a distinct title",
                    context_briefing={},
                )
            seen.add(title)
        return None

    @classmethod
    def _reject_playbook_drafts_batch(
        cls, drafts: list[dict[str, Any]]
    ) -> Envelope | None:
        """The count cap + per-draft validation + in-batch dedup, split out
        of ``propose_playbook_drafts`` to keep its own return-statement
        count under the xenon/PLR0911 budget."""
        from roboco.foundation.policy.board_programs import PROGRAMS

        max_drafts = PROGRAMS["librarian"].max_items_per_cycle
        if not (1 <= len(drafts) <= max_drafts):
            return Envelope.invalid_state(
                message=f"propose 1-{max_drafts} playbook drafts, got {len(drafts)}",
                remediate=f"propose between 1 and {max_drafts} drafts",
                context_briefing={},
            )
        for idx, raw in enumerate(drafts):
            if rej := cls._reject_playbook_draft_item(raw, idx):
                return rej
        return cls._reject_playbook_draft_duplicate_titles(drafts)

    async def _reject_playbook_drafts_existing_titles(
        self, drafts: list[dict[str, Any]]
    ) -> Envelope | None:
        """Live, unbounded case-insensitive dedup against every non-archived
        playbook already in the store — the mining prompt only shows the
        most-recent 20 as a hint, so this re-checks fresh at propose time
        rather than trusting what the Auditor read minutes ago."""
        from roboco.services.librarian_engine import get_librarian_engine

        existing = await get_librarian_engine(
            self.task.session
        ).existing_playbook_titles_lower()
        for idx, raw in enumerate(drafts):
            title = str(raw["title"]).strip()
            if title.lower() in existing:
                return Envelope.invalid_state(
                    message=(
                        f"draft {idx} title {title!r} duplicates an existing playbook"
                    ),
                    remediate=(
                        "give this draft a distinct title, or drop it — a playbook "
                        "for this pattern may already exist"
                    ),
                    context_briefing={},
                )
        return None

    async def _draft_librarian_playbooks(
        self, agent_id: UUID, drafts: list[dict[str, Any]]
    ) -> tuple[list[dict[str, str]], Envelope | None]:
        """Create each validated draft as a real DRAFT playbook via
        ``PlaybookService.draft()`` directly — the Coroner precedent
        (``_draft_coroner_playbook`` above), never the ``draft_playbook``
        do-verb, so "auditor curates but does not draft" stays true at the
        do-verb surface even though the Auditor originates these drafts.

        A per-item ``ConflictError`` (a genuine same-tick race — the live
        pre-check in ``_reject_playbook_drafts_existing_titles`` already
        closed the realistic window) aborts the rest of the batch with a
        clean rejection.
        ponytail: no rollback of earlier successes in this loop — any draft
        already created before the conflict stays a real, independently
        valid playbook riding the normal curation queue (just not
        cross-referenced on this particular report); this is the same
        residual race Coroner already accepts, and Librarian's own
        single-open-cycle dedup makes a same-cycle collision vanishingly
        rare in practice.
        """
        from roboco.models.playbook import PlaybookCreate
        from roboco.services.base import ConflictError
        from roboco.services.playbook import get_playbook_service

        svc = get_playbook_service(self.task.session)
        created: list[dict[str, str]] = []
        for idx, raw in enumerate(drafts):
            try:
                drafted = await svc.draft(
                    PlaybookCreate(
                        title=str(raw["title"]).strip(),
                        problem=str(raw["pattern_evidence"]).strip(),
                        procedure=str(raw["body"]).strip(),
                        tags=["librarian", "auto-authored"],
                    ),
                    created_by=agent_id,
                    source_program="librarian",
                )
            except ConflictError as exc:
                return created, Envelope.invalid_state(
                    message=f"draft {idx}: {exc}",
                    remediate="use a more distinct title (slug must be unique)",
                    context_briefing={"drafted_before_conflict": len(created)},
                )
            created.append({"id": str(drafted.id), "title": drafted.title})
        return created, None

    async def propose_playbook_drafts(
        self, *, agent_id: UUID, drafts: list[dict[str, Any]]
    ) -> Envelope:
        """Auditor mines journals/learnings for repeated patterns and drafts
        1-3 playbooks on its open Librarian cycle task, completing it in the
        same call (mirrors ``propose_market_brief``/``propose_quality_report``'s
        complete-at-propose asymmetry — a mining cycle has no per-item CEO
        decision to wait on).

        Each draft is created via ``PlaybookService.draft()`` DIRECTLY — the
        same Coroner precedent ``_draft_coroner_playbook`` established: the
        Auditor does NOT also carry ``draft_playbook`` on its manifest
        (``role_config.py``, ``test_playbook_verbs.py``'s "auditor curates
        but does not draft" invariant), so a Librarian-authored draft reaches
        the pending-playbook curation queue through this direct service
        call, never the do-verb every delivery role uses. A LATER Auditor
        spawn curates them — a deliberate, documented self-curation
        asymmetry (see ``agents/prompts/identities/auditor.md``).
        """
        await self._trace_board_proposal(
            agent_id=agent_id,
            verb="propose_playbook_drafts",
            payload={"drafts": drafts},
        )
        role = await self._caller_role(agent_id)
        if role not in _LIBRARIAN_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose playbook drafts; only the "
                    "Auditor mines for patterns"
                ),
                remediate="this verb is Auditor-only",
                context_briefing={},
            )
        if rej := self._reject_playbook_drafts_batch(drafts):
            return rej

        from roboco.services.task import get_task_service

        task_svc = get_task_service(self.task.session)
        cycles = await task_svc.list_open_librarian_cycles()
        task = next(
            (
                t
                for t in cycles
                if t.assigned_to == agent_id and markers.get_playbook_drafts(t) is None
            ),
            None,
        )
        if task is None:
            return Envelope.invalid_state(
                message="no open Librarian mining task assigned to you",
                remediate=(
                    "propose_playbook_drafts only runs against an active mining "
                    "cycle spawned by the librarian engine; wait for the next cycle"
                ),
                context_briefing={},
            )

        if rej := await self._reject_playbook_drafts_existing_titles(drafts):
            return rej

        created, rej = await self._draft_librarian_playbooks(agent_id, drafts)
        if rej is not None:
            return rej

        markers.set_playbook_drafts(task, {"drafts": created})
        task.status = TaskStatus.COMPLETED
        await self.task.session.flush()
        await self._notify_librarian_drafts(task, created)
        return Envelope.ok(
            status="playbook_drafts_proposed",
            task_id=str(task.id),
            next=(
                "i_am_idle() — the drafts ride the normal pending-playbook "
                "curation queue"
            ),
            context_briefing={"draft_count": len(created)},
        )

    async def _notify_librarian_drafts(
        self, task: Any, created: list[dict[str, str]]
    ) -> None:
        """Best-effort CEO nudge the moment Librarian mines its drafts —
        mirrors ``_notify_postmortem``."""
        if self._deps.notification_delivery is None:
            return
        try:
            # Savepoint: persists a notification row — see _notify_pitch.
            async with self.task.session.begin_nested():
                await self._deps.notification_delivery.notify_ceo_of_librarian_drafts(
                    task=task,
                    task_id=task.id,
                    titles=[d["title"] for d in created],
                )
        except Exception as exc:
            logger.warning(
                "librarian telegram notify failed (best-effort)", error=str(exc)
            )

    async def propose_postmortem(
        self,
        *,
        agent_id: UUID,
        incident_summary: str,
        root_cause: str,
        failed_stage: str,
        process_change: dict[str, Any],
        playbook: dict[str, Any] | None = None,
    ) -> Envelope:
        """Auditor authors ONE Coroner postmortem on its open autopsy task,
        completing it in the same call — no per-item CEO queue (spec §4:
        a report, not a list of items the CEO decides one by one). Call
        exactly once per autopsy cycle.
        """
        await self._trace_board_proposal(
            agent_id=agent_id,
            verb="propose_postmortem",
            payload={
                "incident_summary": incident_summary,
                "root_cause": root_cause,
                "failed_stage": failed_stage,
                "process_change": process_change,
                "playbook": playbook,
            },
        )
        role = await self._caller_role(agent_id)
        if role not in _CORONER_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot propose a postmortem; only the "
                    "Auditor authors one"
                ),
                remediate="this verb is Auditor-only",
                context_briefing={},
            )
        if rej := self._reject_postmortem(
            incident_summary, root_cause, failed_stage, process_change, playbook
        ):
            return rej

        from roboco.services.coroner_engine import get_coroner_engine
        from roboco.services.task import get_task_service

        task_svc = get_task_service(self.task.session)
        cycles = await task_svc.list_open_coroner_cycles()
        task = next((t for t in cycles if t.assigned_to == agent_id), None)
        if task is None:
            return Envelope.invalid_state(
                message="no open Coroner autopsy task assigned to you",
                remediate=(
                    "propose_postmortem only runs against an active autopsy "
                    "spawned by the coroner engine; wait for the next incident"
                ),
                context_briefing={},
            )

        playbook_id: str | None = None
        if process_change["kind"] == "playbook":
            playbook_id, rej = await self._draft_coroner_playbook(
                agent_id, incident_summary, playbook or {}
            )
            if rej is not None:
                return rej

        engine = get_coroner_engine(self.task.session)
        await engine.complete_with_postmortem(
            task,
            {
                "incident_summary": incident_summary.strip(),
                "root_cause": root_cause.strip(),
                "failed_stage": failed_stage,
                "process_change": {
                    "kind": process_change["kind"],
                    "description": str(process_change["description"]).strip(),
                    # A "playbook" kind already routed straight into the
                    # playbook curation queue above — nothing left for the
                    # CEO to decide on THIS process change, so it never
                    # enters the proposed/approved/rejected per-item flow
                    # (CoronerService.approve_process_change/
                    # reject_process_change refuse it outright).
                    "status": (
                        "not_applicable"
                        if process_change["kind"] == "playbook"
                        else "proposed"
                    ),
                    "reject_reason": None,
                    "materialized_task_id": None,
                },
                "playbook_id": playbook_id,
            },
        )
        await self._notify_postmortem(task, incident_summary, process_change["kind"])
        return Envelope.ok(
            status="postmortem_proposed",
            task_id=str(task.id),
            next="i_am_idle() — the CEO is notified; no per-item decision needed",
            context_briefing={
                "failed_stage": failed_stage,
                "process_change_kind": process_change["kind"],
                "playbook_id": playbook_id,
            },
        )

    async def _notify_postmortem(
        self, task: Any, incident_summary: str, process_change_kind: str
    ) -> None:
        """Best-effort push notification the moment a postmortem lands —
        mirrors ``_notify_pest_hunt_items``."""
        if self._deps.notification_delivery is None:
            return
        try:
            # Savepoint: persists a notification row — see _notify_pitch.
            async with self.task.session.begin_nested():
                await self._deps.notification_delivery.notify_ceo_of_postmortem(
                    task=task,
                    task_id=task.id,
                    incident_summary=incident_summary,
                    process_change_kind=process_change_kind,
                )
        except Exception as exc:
            logger.warning(
                "coroner postmortem telegram notify failed (best-effort)",
                error=str(exc),
            )

    async def _resolve_nothing_to_propose_task(
        self, agent_id: UUID, task_id: UUID
    ) -> Envelope | tuple[Any, BoardProgram]:
        """Resolve + validate ``task_id`` for ``nothing_to_propose``: exists,
        its ``source`` is a registered Board Program, it is assigned to the
        caller, it is non-terminal, and the caller's role matches that
        program's declared explorer role. Returns the first failing check's
        envelope, else ``(task, program)`` — split out of the verb itself
        purely to keep its own return count under the lint ceiling.
        """
        from roboco.foundation.policy.board_programs import PROGRAMS

        task = await self.task.get(task_id)
        if task is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        program = next((p for p in PROGRAMS.values() if p.source == task.source), None)
        if program is None:
            return Envelope.invalid_state(
                message=(
                    f"task {task_id} source {task.source!r} is not a registered "
                    "Board Program"
                ),
                remediate="this task is not a Board Program exploration cycle",
                context_briefing={},
            )
        if task.assigned_to != agent_id:
            return Envelope.not_authorized(
                message=f"task {task_id} is not assigned to you",
                remediate=(
                    "nothing_to_propose only completes the caller's own "
                    "exploration task — pass the task_id printed as "
                    "'TASK: <id>' at the top of your prompt"
                ),
                context_briefing={},
            )
        if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
            return Envelope.invalid_state(
                message=f"task {task_id} is already {task.status.value}",
                remediate="this exploration cycle is already closed; nothing to do",
                context_briefing={},
            )
        role = await self._caller_role(agent_id)
        if role != program.role:
            return Envelope.not_authorized(
                message=(
                    f"role {role!r} cannot resolve {program.key!r}'s exploration "
                    f"task; only {program.role!r} does"
                ),
                remediate=(
                    f"nothing_to_propose only completes a {program.role} "
                    "explorer's own cycle"
                ),
                context_briefing={},
            )
        return task, program

    async def nothing_to_propose(
        self,
        *,
        agent_id: UUID,
        task_id: UUID,
        reason: str,
    ) -> Envelope:
        """The explicit "this cycle found nothing worth proposing" exit for
        ANY Board Program exploration task, named explicitly by ``task_id``.

        Every ``propose_*`` verb requires at least one item (or a single
        substantive report), so an explorer that legitimately has nothing —
        Barfly found no worthwhile X conversations, Coroner has no autopsy
        subject worth a process change — had no way to complete its task; it
        correctly declined and called ``i_am_idle()``, leaving the task
        PENDING forever: a permanent, expensive respawn loop, and (worse)
        ``BoardProgramEngine``'s one-open-cycle dedup wedges that whole
        program shut, since the ledger row never closes.

        ``task_id`` is REQUIRED, not inferred: one explorer role owns several
        independently-cadenced programs at once (e.g. head_marketing owns
        x_feature/periscope/mirror/megaphone/war_room/barfly), each assigning
        its own exploration task to the same agent — so several of one
        agent's exploration tasks can be open simultaneously by design, and
        guessing (e.g. "the oldest one") completes the WRONG cycle and
        stamps its LEARN reason onto the wrong ledger row. The exploration
        prompt always prints "TASK: <id>", so the caller has it in hand
        (mirrors ``curate_vault``'s explicit ``task_id`` convention). See
        ``_resolve_nothing_to_propose_task`` for the resolution + validation
        (exists, registered program, assigned to caller, non-terminal, role
        matches) — registry-driven, not a hardcoded role set, so a program
        registered later needs no edit here.

        Completes the task (mirrors ``propose_conversation_replies``'/
        ``propose_postmortem``'s complete-at-propose pattern) and records the
        reason onto the LEARN ledger row so the next cycle's exploration
        prompt sees WHY, not just a bare "proposed 0, approved 0".
        """
        if rej := self._reject_soup(
            reason,
            field="reason",
            min_chars=_NOTHING_TO_PROPOSE_REASON_MIN_CHARS,
        ):
            return rej
        if len(reason) > _NOTHING_TO_PROPOSE_REASON_MAX_CHARS:
            return Envelope.invalid_state(
                message=(
                    f"reason is {len(reason)} chars, over the "
                    f"{_NOTHING_TO_PROPOSE_REASON_MAX_CHARS}-char cap"
                ),
                remediate="shorten the reason",
                context_briefing={},
            )

        resolved = await self._resolve_nothing_to_propose_task(agent_id, task_id)
        if isinstance(resolved, Envelope):
            return resolved
        task, program = resolved

        reason = reason.strip()
        task.status = TaskStatus.COMPLETED
        await self.task.session.flush()

        from roboco.services.board_programs import get_board_program_engine

        try:
            # Isolated in its own savepoint: record_nothing_to_propose does
            # its own flush() on this SAME session, and a bare try/except
            # around a same-session flush is not enough — a genuine DB
            # failure there leaves the session pending-rollback, so the
            # completion flushed just above would be silently discarded at
            # the outer commit despite this except swallowing the error.
            async with self.task.session.begin_nested():
                await get_board_program_engine(
                    self.task.session
                ).record_nothing_to_propose(program.key, cast("UUID", task.id), reason)
        except Exception:
            logger.warning(
                "nothing_to_propose: LEARN record failed (best-effort)",
                program=program.key,
                task_id=str(task.id),
            )

        return Envelope.ok(
            status="nothing_to_propose",
            task_id=str(task.id),
            next="i_am_idle()",
            context_briefing={"program": program.key, "reason": reason},
        )

    async def dm(
        self,
        *,
        agent_id: UUID,
        recipient: str,
        text: str,
        task_id: UUID | None = None,
        skill: str | None = None,
    ) -> Envelope:
        """A2A direct message. Requires task_id (active or explicit)."""
        if rej := self._reject_soup(text, field="message", min_chars=2):
            return rej
        # Spec §5.5: no-comms roles — defense-in-depth runtime guard. dm() is
        # the channel through which a no-comms role could "speak"; covers the
        # human-only prompter/secretary (own dedicated chat pages, no agent
        # A2A surface at all).
        agent = await self.task.agent_for(agent_id)
        caller_role = str(agent.role) if agent is not None else ""
        if caller_role in _NO_COMMS_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role '{caller_role}' is a silent / no-comms role;"
                    " dm is not permitted"
                ),
                remediate=(
                    "use note() to record; this human-only role has no"
                    " agent-comms surface"
                ),
                context_briefing={},
            )

        if task_id is not None:
            if reject := await self._verify_explicit_task_ownership(agent_id, task_id):
                return reject
        else:
            t = await self.task.get_journal_context_task_for_agent(agent_id)
            if t is not None:
                task_id = t.id
        if task_id is None:
            return Envelope.invalid_state(
                message="dm requires a task_id (no active task and none provided)",
                remediate="provide task_id explicitly or claim a task first",
                context_briefing={},
            )
        # Catch A2A access denials and return an Envelope. If the
        # error escapes here it's caught by FastAPI's middleware and
        # rendered as RobocoError.to_dict() — a dict-shaped 'error'
        # field that breaks do_server's circuit-breaker frozenset
        # check (a TypeError: unhashable type: 'dict').
        from roboco.enforcement.a2a_access import A2AAccessDeniedError

        try:
            await self.a2a.send(
                from_agent=agent_id,
                to_agent=recipient,
                task_id=task_id,
                body=text,
                skill=skill,
            )
        except A2AAccessDeniedError as e:
            remediate = e.route_hint or e.reason
            return Envelope.not_authorized(
                message=e.message,
                remediate=remediate,
                context_briefing={},
            )
        return Envelope.ok(
            status="sent",
            task_id=str(task_id),
            next="continue",
            context_briefing={},
        )

    async def notify(
        self,
        *,
        agent_id: UUID,
        target: str,
        text: str,
        priority: str = "normal",
        task_id: UUID | None = None,
    ) -> Envelope:
        """Send a formal ack-required notification (PMs and Board only).

        Distinct from `dm` (informal A2A, no ack):
        a notification is a formal signal that the recipient must
        acknowledge. Pre-gateway, NotificationService restricted senders
        to PMs/Board; the gateway re-asserts that gate here because the
        do.py router is shared by all roles (no router-level dep).

        ``target`` is an agent slug ("be-dev-1", "main-pm", "ceo");
        NotificationService resolves it to a UUID at insert time.
        ``priority`` is one of normal|high|urgent. ``task_id`` is
        auto-filled from the caller's active task when omitted, but
        omission is permitted for off-task notifications (e.g., Board
        broadcasts).
        """
        from roboco.models import NotificationPriority

        if rej := self._reject_soup(text, field="notification", min_chars=5):
            return rej
        if priority not in _VALID_NOTIFY_PRIORITIES:
            return Envelope.invalid_state(
                message=f"invalid priority {priority!r}",
                remediate=(
                    f"priority must be one of: {sorted(_VALID_NOTIFY_PRIORITIES)}"
                ),
                context_briefing={},
            )
        agent = await self.task.agent_for(agent_id)
        caller_role = str(agent.role) if agent is not None else ""
        if caller_role not in _NOTIFY_ALLOWED_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role {caller_role!r} cannot send formal notifications; "
                    "only PMs and Board may issue ack-required signals"
                ),
                remediate=(
                    "use dm() for informal A2A. "
                    "notify() is reserved for cell_pm, main_pm, "
                    "product_owner, and head_marketing."
                ),
                context_briefing={},
            )
        if task_id is not None:
            if reject := await self._verify_explicit_task_ownership(agent_id, task_id):
                return reject
        else:
            t = await self.task.get_journal_context_task_for_agent(agent_id)
            if t is not None:
                task_id = t.id
        # A dependency block is a "wait silently" situation — never a CEO signal.
        # An agent must not page the CEO to relax or escalate a task that is
        # simply waiting on an unfinished upstream; that wait clears on its own.
        # Also reject human-only recipients (prompter/secretary) — they have no
        # agent ack path, so an ack-required signal would sit permanently unacked
        # and suppress later same-purpose notifications via the dedup query.
        if reject := await self._reject_disallowed_recipient(target, task_id):
            return reject
        await self.notifications.send_ack_notification(
            from_agent=agent_id,
            to_agent=target,
            body=text,
            priority=NotificationPriority(priority),
            task_id=task_id,
        )
        return Envelope.ok(
            status="sent",
            task_id=str(task_id) if task_id else None,
            next="continue",
            context_briefing={},
        )

    async def _reject_disallowed_recipient(
        self, target: str, task_id: UUID | None
    ) -> Envelope | None:
        """Rejection envelope for a notify() recipient the design disallows.

        Two cases, checked in order:
        1. F048 — a human-only recipient (prompter/secretary) with no agent ack
           path. The knowledge-share path already excludes all three human-only
           roles (learning.py); the general notify path did not, so an
           ack-required ALERT could reach a human-driven role and sit permanently
           unacked (polluting the panel's pending-ack view and, via the dedup
           query's ``~acked_by.contains``, permanently suppressing any later
           same-purpose notification from the same sender to that human role).
           The CEO is human too but acks via the panel, so it is NOT rejected
           here (its only disallowed case — a dependency-block page — is case 2).
        2. A CEO notification about an open dependency block — pure noise; the
           wait clears when the upstream completes.
        """
        from roboco.agents_config import get_agent_role

        recipient_role = get_agent_role(target)
        if recipient_role in ("prompter", "secretary"):
            return Envelope.not_authorized(
                message=(
                    f"cannot notify {target!r} — the {recipient_role} is a"
                    " human-only role with no agent ack path; an ack-required"
                    " signal would sit permanently unacked and suppress later"
                    " same-purpose notifications via the dedup query"
                ),
                remediate=(
                    "escalate via the"
                    " CEO route. ack-required notify() targets must be agents"
                    " (or the CEO, who acks via the panel)"
                ),
                context_briefing={},
            )
        return await self._reject_ceo_dependency_notify(target, task_id)

    async def _reject_ceo_dependency_notify(
        self, target: str, task_id: UUID | None
    ) -> Envelope | None:
        """Rejection envelope if this is a CEO notification about a dep block.

        A dependency block clears when the upstream completes — paging the CEO
        about it is pure noise and burn. Returns None when the notification is
        allowed (non-CEO target, no task, or no open dependency).
        """
        from roboco.agents_config import is_ceo

        if task_id is None or not is_ceo(target):
            return None
        dep_block = await self._dependency_block_reason(task_id)
        if not dep_block:
            return None
        return Envelope.invalid_state(
            message=f"cannot notify the CEO about a dependency block — {dep_block}",
            remediate=(
                "a dependency block clears automatically when the upstream task "
                "completes — do not notify or escalate. Call i_am_idle() and "
                "wait; the task resumes on its own."
            ),
            context_briefing={},
        )

    async def _dependency_block_reason(self, task_id: UUID) -> str | None:
        """Reason string if ``task_id`` is waiting on an unfinished dependency.

        Used to refuse CEO notifications about a dependency block: such a block
        is resolved by the upstream completing, not by a human, so paging the
        CEO is pure noise and burn.
        """
        task = await self.task.get(task_id)
        if task is None:
            return None
        dep_ids = list(task.dependency_ids or [])
        if not dep_ids:
            return None
        unmet = await self.task.unmet_dependency_ids(dep_ids)
        if unmet:
            noun = "dependency" if len(unmet) == 1 else "dependencies"
            return f"{len(unmet)} {noun} not yet completed"
        return None

    async def _is_caller_dependency(self, agent_id: UUID, task: Any) -> bool:
        """True when ``task`` is a dependency of a task the caller is assigned to.

        A dependent agent (e.g. a frontend cell waiting on a UX design task)
        must be able to inspect what it is blocked on; read-only evidence is the
        right tool, and the strict cross-agent ownership gate would otherwise
        reject it.
        """
        assigned = await self.task.list_assigned_for_agent(agent_id)
        return any(task.id in (a.dependency_ids or []) for a in assigned)

    async def evidence(
        self,
        *,
        agent_id: UUID,
        task_id: UUID,
    ) -> Envelope:
        """Inspect a task's PR diff, commits, files.

        Fetches the dev branch into the agent's workspace before diffing.
        Allows inspection when the caller is the assignee, the task is
        unassigned, the caller co-reviews a shared board task, or the task is a
        dependency the caller is waiting on — strict ownership only blocks
        snooping an unrelated, actively-owned task.

        ``files_changed`` and ``pr_diff_summary`` are pulled from git (against
        the branch's parent — the authoritative source) rather than the latest
        commit's delta, so reviewers see the full multi-commit change set.

        The workspace-branch-fetch leg and the combined git diff leg
        (``diff_and_files`` — one workspace/token/head/base resolution, the
        full diff and the ``--name-only`` diff run concurrently as
        subprocesses) each run bounded via ``run_bounded_leg`` against ONE
        shared ``LegBudget`` for this call — a timeout skips that piece and
        records a note (naming both the diff and files_changed losses — a
        combined-leg timeout kills both together) in ``evidence_gaps``
        instead of hanging this advisory (read-only, non-gating) verb for
        the whole ``flow_verb_timeout_seconds`` budget.

        The three independent DB-only reads (journal highlights, ancestor
        context, open findings) run BEFORE the pool-release commit below —
        not alongside the git legs — so a connection doesn't have to sit
        checked out for the (potentially minutes-long) git work. They are
        awaited SEQUENTIALLY, not gathered: ``evidence_repo`` and
        ``self.task`` share the same request-scoped ``AsyncSession`` (see
        ``deps.py``), and SQLAlchemy's ``AsyncSession`` does not support
        concurrent queries — this matches the pre-dedup behavior (these
        reads were sequential before the pool-release commit was added),
        so latency is unchanged in practice.
        """
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        # Reads are allowed for the assignee, an unassigned task, a board
        # co-reviewer of a shared coordination task, OR a caller whose own work
        # depends on this task. Strict ownership only blocks snooping an
        # unrelated, actively-owned task.
        if (
            t.assigned_to is not None
            and t.assigned_to != agent_id
            and not await self._board_may_co_review(agent_id, t)
            and not await self._is_caller_dependency(agent_id, t)
        ):
            return _ownership_violation(task_id)
        journal_highlights = await self.evidence_repo.journal_highlights_for_task(
            task_id, include_ancestors=True
        )
        parent_context = await self.evidence_repo.ancestor_context_for_task(task_id)
        open_findings = await findings_lib.open_findings_for_task(
            self.task.session, task_id
        )
        # Release the request's transaction before the git work below: fetch +
        # diff can run for minutes (cold workspace, serialized behind the
        # per-workspace ensure lock), and an open transaction pins one of the
        # pool's connections for that whole time — enough concurrent evidence
        # calls exhaust the pool (2026-07-29 incident). Reads after this
        # reopen a fresh transaction on demand; expire_on_commit=False keeps
        # ``t`` usable. A poisoned session (PendingRollbackError) rolls back
        # instead — the point is ending the transaction, either way works.
        from sqlalchemy.exc import PendingRollbackError

        try:
            await self.task.session.commit()
        except PendingRollbackError:
            await self.task.session.rollback()
        evidence_gaps: list[str] = []
        budget = LegBudget(settings.evidence_assembly_timeout_seconds)
        if t.branch_name and t.work_session_id:
            # subprocess_timeout self-bounds the underlying git-fetch
            # subprocess (on the shared DEFAULT asyncio executor, not
            # git.py's dedicated pool) to roughly this leg's own share of
            # the budget, so an abandoned wait_for doesn't leave the
            # subprocess occupying a thread for up to workspace_clone_timeout
            # (300s) after we've already given up on it.
            await run_bounded_leg(
                self.workspace.fetch_branch_for_inspection(
                    agent_id=agent_id,
                    branch_name=t.branch_name,
                    subprocess_timeout=budget.remaining(),
                ),
                default=None,
                budget=budget,
                leg="branch fetch",
                hint=(
                    "the diff below may reflect a stale workspace; review "
                    "the PR diff on GitHub directly"
                ),
                task_id=task_id,
                gaps=evidence_gaps,
            )
        diff = ""
        files_changed: list[str] = []
        if t.branch_name:
            diff, files_changed = await run_bounded_leg(
                self.git.diff_and_files(
                    branch_name=t.branch_name, actor_agent_id=agent_id
                ),
                default=("", []),
                budget=budget,
                leg="pr diff + files_changed",
                hint="review the PR diff on GitHub directly",
                task_id=task_id,
                gaps=evidence_gaps,
            )
        ev = build_evidence_for_task(
            t,
            journal_highlights=journal_highlights,
            files_changed=files_changed,
            pr_diff_summary=diff,
            revision_findings=open_findings,
            parent_context=parent_context,
            evidence_gaps=evidence_gaps,
        )
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next="continue",
            evidence=ev.as_dict(),
            context_briefing={},
        )

    async def _sandbox_active_task(self, agent_id: UUID) -> tuple[Any, Envelope | None]:
        """request_sandbox's task guard: an active, project-bound task, or a
        clean invalid_state rejection."""
        t = await self.task.get_active_task_for_agent(agent_id)
        if t is None or t.project_id is None:
            return None, Envelope.invalid_state(
                message="no claimed task with a project — cannot scope a sandbox",
                remediate=(
                    "call give_me_work() first; request_sandbox needs an "
                    "active, project-bound task"
                ),
                context_briefing={},
            )
        return t, None

    @staticmethod
    def _sandbox_scope(
        project: Any, services: list[str] | None
    ) -> tuple[frozenset[str], Envelope | None]:
        """request_sandbox's opt-in guard: the resolved service set, or a
        clean invalid_state rejection naming the project's allowed set."""
        opted = frozenset(project.sandbox_services or []) if project else frozenset()
        if not opted:
            return frozenset(), Envelope.invalid_state(
                message="project has not opted into any sandbox service",
                remediate=(
                    "ask a PM/CEO to set sandbox_services in project "
                    "settings before requesting a sandbox"
                ),
                context_briefing={},
            )
        requested = opted if services is None else frozenset(services)
        unknown = requested - opted
        if unknown:
            return frozenset(), Envelope.invalid_state(
                message=f"requested service(s) {sorted(unknown)} not opted into",
                remediate=(
                    f"this project's opted-in set is {sorted(opted)} — "
                    "request a subset of that"
                ),
                context_briefing={},
            )
        return requested, None

    @staticmethod
    def _validate_per_call_extensions(
        extensions: dict[str, list[str]] | None,
        opted: frozenset[str],
        merged: dict[str, set[str]],
    ) -> Envelope | None:
        """Allowlist-validate the per-call extension map and union it into
        ``merged``; return a clean invalid_state rejection or None.

        Extracted from ``_sandbox_features_scope`` so its loop's two rejection
        branches don't push the caller over the complexity bound. A feature
        for a non-opted service or outside the service's allowlist is rejected
        naming the allowed set — the containment that keeps a ``plpython3u``
        from reaching the provisioner.
        """
        from roboco.models.sandbox import SANDBOX_ENGINE_FEATURES

        for svc, feats in (extensions or {}).items():
            if svc not in opted:
                return Envelope.invalid_state(
                    message=(
                        f"extensions given for {svc!r}, which this project has "
                        f"not opted into"
                    ),
                    remediate=(
                        f"this project's opted-in set is {sorted(opted)} — "
                        "request extensions only for those services"
                    ),
                    context_briefing={},
                )
            allowed = SANDBOX_ENGINE_FEATURES.get(svc, frozenset())
            bad = sorted(set(feats or []) - allowed)
            if bad:
                return Envelope.invalid_state(
                    message=f"unallowed {svc} extension(s) {bad}",
                    remediate=(
                        f"the allowlist for {svc} is {sorted(allowed)} — "
                        "request a subset; plpython3u and other superuser-"
                        "language extensions are excluded by construction"
                    ),
                    context_briefing={},
                )
            merged.setdefault(svc, set()).update(feats or [])
        return None

    @staticmethod
    def _sandbox_features_scope(
        project: Any,
        extensions: dict[str, list[str]] | None,
        opted: frozenset[str],
    ) -> tuple[dict[str, list[str]], Envelope | None]:
        """request_sandbox's extension guard: the per-service feature map to
        activate (project standing union per-call, bounded by the opted set +
        the allowlist), or a clean invalid_state rejection.

        Per-call ``extensions`` is allowlist-validated HERE (not only at the
        provisioner) so a ``plpython3u`` gets a remediate naming the allowed
        set, mirroring the unknown-service remediate. The project's standing
        ``sandbox_extensions`` was allowlist-validated at write time, so it is
        trusted and unioned in; entries for a service no longer opted into are
        dropped (a venture may deactivate a service without clearing its
        standing extensions). Returns only services with a non-empty feature
        list — a service with no features is bare (the provisioner's default).
        """
        standing = (project.sandbox_extensions if project else None) or {}
        # Union per service: standing (trusted) + per-call (validated below).
        merged: dict[str, set[str]] = {}
        for svc, feats in standing.items():
            if svc in opted:
                merged.setdefault(svc, set()).update(feats or [])
        rejection = ContentActions._validate_per_call_extensions(
            extensions, opted, merged
        )
        if rejection is not None:
            return {}, rejection
        return {svc: sorted(f) for svc, f in merged.items() if f}, None

    async def _sandbox_provision_or_reject(
        self,
        agent_slug: str,
        requested: frozenset[str],
        opted: frozenset[str],
        features: dict[str, list[str]] | None,
        task_id: UUID,
    ) -> tuple[Any, Envelope | None]:
        """Run ``ensure_sandbox`` for request_sandbox; return (info, None) on
        success or (None, rejection) when the orchestrator handle is missing
        or provisioning raises. Extracted so ``request_sandbox``'s
        orchestrator-None guard + try/except don't push it over the
        complexity bound. Heartbeats the caller's task on success.
        """
        if self.orchestrator is None:
            return None, Envelope.invalid_state(
                message="orchestrator handle unavailable — cannot provision a sandbox",
                remediate=(
                    "retry request_sandbox shortly; the orchestrator may be restarting"
                ),
                context_briefing={},
            )
        from roboco.runtime.sandbox import SandboxProvisionError

        try:
            info = await self.orchestrator.ensure_sandbox(
                agent_slug, sorted(requested), sorted(opted), features=features or None
            )
        except SandboxProvisionError as e:
            return None, Envelope.invalid_state(
                message=f"sandbox provisioning failed: {e}",
                remediate="retry shortly; escalate to your PM if it keeps failing",
                context_briefing={},
            )
        await self._touch_heartbeat(task_id)
        return info, None

    async def request_sandbox(
        self,
        *,
        agent_id: UUID,
        services: list[str] | None = None,
        extensions: dict[str, list[str]] | None = None,
    ) -> Envelope:
        """On-demand sandbox DB/Redis/Mongo (dev + QA only, see role_config).

        Replaces eager per-spawn provisioning: a sandbox is created only when
        an agent actually asks for one, keyed off the CALLER's authenticated
        slug (never another agent's). ``services`` omitted means the
        project's whole opted-in set. ``extensions`` (per-service
        extensions/modules, e.g. ``{"postgres": ["vector"]}``) is an additive
        per-call override unioned with the project's standing
        ``sandbox_extensions`` and bounded by the opted set + the allowlist —
        a ``plpython3u`` is rejected here with the allowed set named.

        Guards, in order: flag off; caller has no claimed/active,
        project-bound task (`_sandbox_active_task`); project not opted into
        any sandbox service, or a requested service outside its opted set
        (`_sandbox_scope`, names the allowed set); per-call extensions for a
        non-opted service or outside the allowlist (`_sandbox_features_scope`,
        names the allowed set); orchestrator handle unavailable (retryable).
        `ensure_sandbox` always provisions the project's whole opted-in set
        regardless of ``services`` (so a later call can never trigger a
        mid-session teardown of a live container); the evidence payload here
        is filtered back down to what THIS call asked for. Creds come back in
        the evidence payload, never as injected env — see
        ``docs/internal/specs/2026-07-08-sandbox-on-demand.md`` §4.
        """
        if not settings.sandbox_db_enabled:
            return Envelope.invalid_state(
                message="sandbox provisioning is disabled",
                remediate=(
                    "ROBOCO_SANDBOX_DB_ENABLED is off — ask the CEO to arm "
                    "it, or rely on the legacy gate env if this project "
                    "isn't sandboxed"
                ),
                context_briefing={},
            )
        t, rejection = await self._sandbox_active_task(agent_id)
        if rejection is not None:
            return rejection
        from roboco.services.project import get_project_service

        project = await get_project_service(self.task.session).get(t.project_id)
        requested, rej_scope = self._sandbox_scope(project, services)
        opted = frozenset(project.sandbox_services or []) if project else frozenset()
        features, rej_features = self._sandbox_features_scope(
            project, extensions, opted
        )
        # Scope before features: an unknown-service rejection wins over a
        # per-call extension rejection for the same call.
        rejection = rej_scope or rej_features
        if rejection is not None:
            return rejection
        from roboco.agents_config import _resolve_to_slug

        agent_slug = _resolve_to_slug(str(agent_id))
        info, rej = await self._sandbox_provision_or_reject(
            agent_slug, requested, opted, features, t.id
        )
        if rej is not None:
            return rej
        # ensure_sandbox provisions the project's whole opted-in set (see its
        # docstring); the evidence payload stays scoped to what THIS call
        # asked for.
        payload = info.as_payload()
        filtered = {
            name: payload[name] for name in sorted(requested) if name in payload
        }
        return Envelope.ok(
            status=str(t.status),
            task_id=str(t.id),
            next="use the returned creds for this session; call again anytime",
            evidence=filtered,
            context_briefing={},
        )

    async def _render_active_video_task(
        self, agent_id: UUID
    ) -> tuple[Any, Envelope | None]:
        """request_render's task guard: an active, project-bound video-
        authoring task, or a clean invalid_state rejection."""
        from roboco.services.task import VIDEO_SOURCE

        t = await self.task.get_active_task_for_agent(agent_id)
        if t is None or t.project_id is None or t.source != VIDEO_SOURCE:
            return None, Envelope.invalid_state(
                message="no active video-authoring task assigned to you",
                remediate=(
                    "request_render is only available on a video-authoring "
                    "task — claim your assigned authoring task first"
                ),
                context_briefing={},
            )
        return t, None

    @staticmethod
    def _render_resolve_composition_id(
        task: Any, composition_id: str | None
    ) -> tuple[str, Envelope | None]:
        """Explicit ``composition_id``, else the task's ``video_draft``
        marker's, validated with the SAME charset regex ``propose_video``
        enforces so an unrenderable id is refused here, not deep inside the
        sidecar call."""
        resolved = composition_id or (markers.get_video_draft(task) or {}).get(
            "composition_id"
        )
        if not resolved or not str(resolved).strip():
            return "", Envelope.incomplete_input(
                missing=["composition_id"],
                field_hints={"composition_id": "the HyperFrames composition id"},
                remediate=(
                    "pass composition_id explicitly, or call propose_video "
                    "first so it's on the task's video_draft marker"
                ),
                context_briefing={},
            )
        resolved = str(resolved).strip()
        if not _COMPOSITION_ID_RE.fullmatch(resolved):
            return "", Envelope.invalid_state(
                message=f"composition_id {resolved!r} is not renderable",
                remediate=(
                    "letters, digits, '_' or '-' with optional interior dots — "
                    "match the directory name under motion/compositions/"
                ),
                context_briefing={},
            )
        return resolved, None

    _RENDER_ORIENTATIONS: ClassVar[frozenset[str]] = frozenset({"vertical", "square"})
    _RENDER_MAX_FRAMES: ClassVar[int] = 32

    @classmethod
    def _render_validate_params(
        cls, orientation: str, frame_count: int
    ) -> Envelope | None:
        """``orientation``/``frame_count`` bounds guard, folded into one
        rejection point so ``request_render`` keeps one return per guard."""
        if orientation not in cls._RENDER_ORIENTATIONS:
            return Envelope.invalid_state(
                message=f"orientation {orientation!r} must be 'vertical' or 'square'",
                remediate="pass orientation='vertical' or orientation='square'",
                context_briefing={},
            )
        if not isinstance(frame_count, int) or not (
            1 <= frame_count <= cls._RENDER_MAX_FRAMES
        ):
            bound = cls._RENDER_MAX_FRAMES
            return Envelope.invalid_state(
                message=f"frame_count {frame_count!r} must be an integer 1-{bound}",
                remediate=f"pass frame_count between 1 and {bound}",
                context_briefing={},
            )
        return None

    @staticmethod
    def _render_resolve_input_props(
        task: Any, input_props: dict[str, Any] | None
    ) -> dict[str, Any]:
        if input_props is not None:
            return input_props
        draft = markers.get_video_draft(task) or {}
        return draft.get("input_props") or draft.get("suggested_input_props") or {}

    @staticmethod
    async def _render_git_rev(workspace: Path, ref: str) -> str | None:
        """Best-effort ``git rev-parse <ref>`` in ``workspace``; ``None`` on
        any failure (missing ref, missing dir, no git binary). The render
        preview's provenance stamp is best-effort — a git hiccup must never
        block the render response itself."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(workspace),
                "rev-parse",
                ref,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await proc.communicate()
        except OSError:
            return None
        if proc.returncode != 0:
            return None
        sha = out.decode().strip()
        return sha or None

    @classmethod
    async def _render_git_head_and_dirty(
        cls, workspace: Path
    ) -> tuple[str | None, bool]:
        """Best-effort ``(HEAD sha, has-uncommitted-changes)`` for a dev's own
        working tree. ``(None, False)`` on any failure."""
        head = await cls._render_git_rev(workspace, "HEAD")
        if head is None:
            return None, False
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(workspace),
                "status",
                "--porcelain",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await proc.communicate()
        except OSError:
            return head, False
        dirty = bool(out.decode().strip()) if proc.returncode == 0 else False
        return head, dirty

    async def _render_dev_source(
        self, agent_id: UUID, agent_slug: str, task: Any, project: Any
    ) -> tuple[Any, Envelope | None]:
        """A developer's own working tree — the per-task worktree when one
        exists on disk, else the clone root (F123)."""
        from roboco.services.workspace import WorkspaceError

        agent = await self.task.agent_for(agent_id)
        if agent is None or not agent.team:
            return None, Envelope.invalid_state(
                message="your team could not be resolved",
                remediate="ensure your agent record has a team, then retry",
                context_briefing={},
            )
        try:
            clone_root = self.workspace.get_clone_root_path(
                project.slug, agent.team, agent_slug
            )
            worktree = self.workspace.get_worktree_path(
                project.slug, agent.team, agent_slug, task.id.hex[:8]
            )
        except WorkspaceError as exc:
            return None, Envelope.invalid_state(
                message=f"could not resolve your workspace path: {exc}",
                remediate="retry request_render; escalate to your PM if it persists",
                context_briefing={},
            )
        root = worktree if worktree.exists() else clone_root
        head_sha, dirty = await self._render_git_head_and_dirty(root)
        return (
            _RenderSource(root=root, head_sha=head_sha, dirty=dirty, kind="workspace"),
            None,
        )

    async def _render_qa_source(
        self, task: Any, project: Any
    ) -> tuple[Any, Envelope | None]:
        """QA never renders from a working tree: a read-only export of the
        assembled branch's ``motion/`` subtree via
        ``WorkspaceService.export_branch_motion``."""
        from roboco.services.workspace import WorkspaceError

        branch = getattr(task, "branch_name", None)
        if not branch:
            return None, Envelope.invalid_state(
                message="task has no recorded branch to export for a QA render",
                remediate=(
                    "the assembled PR's branch must exist before requesting a render"
                ),
                context_briefing={},
            )
        try:
            scratch = await self.workspace.export_branch_motion(project, branch)
        except WorkspaceError as exc:
            return None, Envelope.invalid_state(
                message=f"could not export branch {branch!r} for render: {exc}",
                remediate=(
                    "ensure the branch is pushed to origin, then retry request_render"
                ),
                context_briefing={},
            )
        read_clone = await self.workspace.ensure_read_clone(project.slug)
        head_sha = await self._render_git_rev(
            read_clone, f"refs/remotes/origin/{branch}"
        )

        def _cleanup() -> None:
            shutil.rmtree(scratch, ignore_errors=True)

        return (
            _RenderSource(
                root=scratch,
                head_sha=head_sha,
                dirty=False,
                kind="branch",
                cleanup=_cleanup,
            ),
            None,
        )

    async def _render_resolve_source(
        self, agent_id: UUID, agent_slug: str, task: Any, project: Any
    ) -> tuple[Any, Envelope | None]:
        """Dispatch the render SOURCE by the caller's role: developer → their
        own tree; QA → a read-only branch export; anyone else → refused."""
        role = await self._caller_role(agent_id)
        if role == "developer":
            return await self._render_dev_source(agent_id, agent_slug, task, project)
        if role == "qa":
            return await self._render_qa_source(task, project)
        return None, Envelope.not_authorized(
            message=f"role {role!r} may not request a render",
            remediate="request_render is developer/QA only",
            context_briefing={},
        )

    async def _render_resolve_project_and_source(
        self, agent_id: UUID, agent_slug: str, task: Any
    ) -> tuple[Any, Envelope | None]:
        """Resolve the task's project, then the caller's render source —
        bundled into one combined-rejection helper so ``request_render``'s
        own return count stays under the xenon/PLR0911 budget. Success value
        is a ``(project, source)`` pair."""
        from roboco.services.project import get_project_service

        project = await get_project_service(self.task.session).get(task.project_id)
        if project is None:
            return None, Envelope.invalid_state(
                message="task's project could not be resolved",
                remediate="retry shortly; the project record may be mid-update",
                context_briefing={},
            )
        source, rejection = await self._render_resolve_source(
            agent_id, agent_slug, task, project
        )
        if rejection is not None:
            return None, rejection
        return (project, source), None

    def _render_extract_frames(
        self, project_slug: str, task_id: Any, orientation: str, frames_tar_gz: bytes
    ) -> list[str]:
        """Extract the sidecar's frames tar.gz to the container-shared
        preview dir, wiping any stale render first; returns sorted absolute
        frame paths. Every agent container mounts the same /data/workspaces
        volume, so this path is identical inside every container regardless
        of who rendered — that's what lets a dev render and a QA (or PM)
        read the same frames from their own container."""
        out_dir = (
            Path(settings.workspaces_root)
            / project_slug
            / ".previews"
            / task_id.hex[:8]
            / orientation
        )
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(frames_tar_gz)) as tar:
            tar.extractall(out_dir, filter="data")
        return sorted(str(p) for p in out_dir.rglob("*") if p.is_file())

    async def _render_execute(
        self,
        *,
        task: Any,
        project: Any,
        agent_slug: str,
        resolved_id: str,
        resolved_props: dict[str, Any],
        orientation: str,
        frame_count: int,
        source: Any,
    ) -> Envelope:
        """The render call + frame extraction + marker stamp, once every
        guard in ``request_render`` has passed. Always cleans up a QA
        scratch-dir source (dev sources have no cleanup callback)."""
        try:
            comp_dir = source.root / "motion" / "compositions" / resolved_id
            if not comp_dir.is_dir():
                expected = f"motion/compositions/{resolved_id}/"
                return Envelope.invalid_state(
                    message=f"no composition found at {expected}",
                    remediate=(
                        f"build the composition under {expected} first "
                        "(propose_video / commit it), then retry request_render"
                    ),
                    context_briefing={},
                )
            from roboco.services.video_renderer_client import (
                VideoRendererError,
                get_video_renderer,
            )

            try:
                frames_tar_gz, duration = await get_video_renderer().render_frames(
                    str(source.root / "motion"),
                    composition_id=resolved_id,
                    input_props=resolved_props,
                    orientation=orientation,
                    frame_count=frame_count,
                )
            except VideoRendererError as exc:
                return Envelope.invalid_state(
                    message=f"render failed: {exc}",
                    remediate=(
                        "retry request_render shortly; escalate to your PM if "
                        "it keeps failing"
                    ),
                    context_briefing={},
                )
            frames = self._render_extract_frames(
                project.slug, task.id, orientation, frames_tar_gz
            )
            payload = {
                "at": datetime.now(UTC).isoformat(),
                "composition_id": resolved_id,
                "orientation": orientation,
                "frame_count": frame_count,
                "duration_seconds": duration,
                "frames": frames,
                "head_sha": source.head_sha,
                "dirty": source.dirty,
                "rendered_by": agent_slug,
                "source": source.kind,
            }
            markers.set_render_preview(task, payload)
            # The post-completion render loop keys on video_draft.composition_id
            # — a dev who only ever passed composition_id explicitly (never
            # propose_video) must still leave it stamped, or the loop skips the
            # completed task silently (proven live on task 1dae04a7).
            draft = markers.get_video_draft(task) or {}
            if not draft.get("composition_id"):
                markers.set_video_draft(task, {**draft, "composition_id": resolved_id})
            await self.task.session.flush()
            await self._touch_heartbeat(task.id)
            return Envelope.ok(
                status=str(task.status),
                task_id=str(task.id),
                next=(
                    "Read every frames[] path with your file tools; if any "
                    "scene is missing or clipped, fix the composition and "
                    "call request_render again."
                ),
                evidence={
                    **payload,
                    "note": (
                        "Read each frame image and verify every scene/feature "
                        "from the brief appears fully and legibly before "
                        "i_am_done."
                    ),
                },
                context_briefing={},
            )
        finally:
            if source.cleanup is not None:
                source.cleanup()

    async def request_render(
        self,
        *,
        agent_id: UUID,
        composition_id: str | None = None,
        orientation: str = "vertical",
        frame_count: int = 8,
        input_props: dict[str, Any] | None = None,
    ) -> Envelope:
        """Render a video composition to a strip of preview frames the
        caller reads with file tools — verifying the RENDERED artifact, not
        just the HyperFrames source, which can look plausible and still
        render wrong (missing scene, clipped layout, wrong text).

        Guards, in order: video engine flag off; caller has no active,
        project-bound video-authoring task (`_render_active_video_task`);
        the renderer sidecar unconfigured (`video_renderer_base_url`);
        `composition_id` unresolvable or failing `propose_video`'s own
        charset regex, or `orientation`/`frame_count` out of range; the
        caller's role-based SOURCE (`_render_resolve_source` — a developer's
        own tree, or QA's read-only branch export, never a QA working tree);
        the resolved source missing `motion/compositions/<id>/`; the sidecar
        call itself (`VideoRendererError` -> a retryable rejection). On
        success, extracts the returned frames to a container-shared
        `.previews/<task>/<orientation>/` path and stamps `render_preview` —
        the marker `i_am_done`'s RENDER_VERIFIED gate checks.
        """
        if not settings.video_engine_enabled:
            return Envelope.invalid_state(
                message="the video engine is disabled",
                remediate="ROBOCO_VIDEO_ENGINE_ENABLED is off — nothing to render",
                context_briefing={},
            )
        t, rejection = await self._render_active_video_task(agent_id)
        if rejection is not None:
            return rejection
        renderer_rej = (
            None
            if settings.video_renderer_base_url.strip()
            else Envelope.invalid_state(
                message="the video-renderer sidecar is not configured",
                remediate="ask the CEO to set ROBOCO_VIDEO_RENDERER_BASE_URL",
                context_briefing={},
            )
        )
        resolved_id, rej_id = self._render_resolve_composition_id(t, composition_id)
        rej_params = self._render_validate_params(orientation, frame_count)
        rejection = renderer_rej or rej_id or rej_params
        if rejection is not None:
            return rejection
        resolved_props = self._render_resolve_input_props(t, input_props)

        from roboco.agents_config import _resolve_to_slug

        agent_slug = _resolve_to_slug(str(agent_id))
        resolved, rejection = await self._render_resolve_project_and_source(
            agent_id, agent_slug, t
        )
        if rejection is not None:
            return rejection
        project, source = resolved
        return await self._render_execute(
            task=t,
            project=project,
            agent_slug=agent_slug,
            resolved_id=resolved_id,
            resolved_props=resolved_props,
            orientation=orientation,
            frame_count=frame_count,
            source=source,
        )

    # =========================================================================
    # Wave 1 — pre-gateway parity restoration
    # =========================================================================

    _PROGRESS_ACTIVE_STATUSES: ClassVar[frozenset[str]] = frozenset(
        {"in_progress", "verifying", "awaiting_qa", "awaiting_documentation"}
    )

    async def _progress_precondition_reject(
        self, agent_id: UUID, task: Any
    ) -> Envelope | None:
        """Ownership + active-claim + active-status gate for progress().

        Returns the rejection envelope, or None when all preconditions hold.
        Extracted so ``progress`` stays under the return-count bound.
        """
        if task.assigned_to != agent_id:
            return _ownership_violation(task.id)
        if reject := await self._active_claim_violation(agent_id, task):
            return reject
        if str(task.status) not in self._PROGRESS_ACTIVE_STATUSES:
            return Envelope.invalid_state(
                message=(
                    f"task is in {task.status!r}; progress updates only valid "
                    f"in active statuses ({sorted(self._PROGRESS_ACTIVE_STATUSES)})"
                ),
                remediate=(
                    "use evidence(task_id) to re-read state; if you're past "
                    "i_am_done, the run has moved on — call i_am_idle()"
                ),
                context_briefing={},
            )
        return None

    async def progress(
        self,
        *,
        agent_id: UUID,
        task_id: UUID,
        message: str,
        plan_step: str | None = None,
        percentage: int | None = None,
    ) -> Envelope:
        """Append a progress update; % is derived from the plan checklist.

        Pass ``plan_step`` (a sub_task id or 1-based order) as you
        finish each plan step — it is marked complete and the % is
        computed from completed/total (the agent cannot set it). A
        narrative entry without ``plan_step`` is allowed for important
        mid-step documentation and carries the current derived %.
        ``percentage`` is only a fallback for tasks with no checklist.

        Omitting ``plan_step`` on a task that *has* steps is accepted (a
        product decision — narrative mid-step updates are valid) but logs a
        soft warning so the gap is visible. It is never rejected.

        Caller must be the active claimant and the task must be in an
        active status — same constraints as the pre-gateway handler, plus
        the single-claimant guard so a reaped/handed-off assignee cannot
        keep writing.
        """
        if rej := self._reject_soup(message, field="progress update", min_chars=5):
            return rej
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        if reject := await self._progress_precondition_reject(agent_id, t):
            return reject
        result = await self.task.record_plan_progress(
            task_id=task_id,
            agent_id=agent_id,
            message=message,
            plan_step=plan_step,
            fallback_percentage=percentage,
        )
        if result is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        if result["step_resolved"] is False:
            valid = result["valid_steps"]
            return Envelope.invalid_state(
                message=f"plan_step {plan_step!r} does not match any plan step",
                remediate=(
                    "pass a sub_task id or its 1-based order. Valid steps: "
                    f"{valid}. Re-read them with evidence(task_id)."
                ),
                context_briefing={},
            )
        if plan_step is None and result["valid_steps"]:
            logger.warning(
                "progress() called without plan_step on a stepped task",
                task_id=str(task_id),
                agent_id=str(agent_id),
                valid_steps=result["valid_steps"],
            )
        await self._touch_heartbeat(task_id)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=f"progress {result['percentage']}% — continue",
            context_briefing={},
        )

    async def notify_list(
        self,
        *,
        agent_id: UUID,
        unread_only: bool = True,
        pending_ack_only: bool = False,
        limit: int = 20,
    ) -> Envelope:
        """Read this agent's notification inbox.

        Closes the pre-gateway parity gap that left `i_am_idle()` deadlocked:
        the verb is documented to soft-block on unread notifications, but
        previously there was no way for the agent to read or acknowledge them.
        """
        items = await self._deps.notification_delivery.list_for_agent(
            agent_id=agent_id,
            unread_only=unread_only,
            pending_ack_only=pending_ack_only,
            type_filter=None,
            limit=limit,
        )
        notifications = [
            {
                "id": str(n.id),
                "type": str(n.type),
                "priority": str(n.priority),
                "subject": n.subject,
                "body": n.body,
                "requires_ack": n.requires_ack,
                "timestamp": n.timestamp.isoformat() if n.timestamp else None,
                "from_agent": str(n.from_agent) if n.from_agent else None,
            }
            for n in items
        ]
        return Envelope.ok(
            status="ok",
            task_id=None,
            next="continue",
            evidence={"notifications": notifications, "count": len(notifications)},
            context_briefing={},
        )

    async def notify_get(
        self,
        *,
        agent_id: UUID,
        notification_id: UUID,
    ) -> Envelope:
        """Read one notification (also marks it read)."""
        from roboco.services.base import NotFoundError

        # Only the two domain outcomes map to not_found; a DB error (e.g. the
        # mark-read UPDATE hitting lock_timeout) must propagate so the session
        # is rolled back — swallowing it here poisoned the session and blew up
        # the commit-at-send with PendingRollbackError, while lying to the
        # agent that an existing notification didn't exist.
        try:
            n = await self._deps.notification_delivery.get_for_recipient_and_mark_read(
                notification_id=notification_id,
                agent_id=agent_id,
            )
        except (NotFoundError, PermissionError):
            return Envelope.not_found(
                message=f"notification {notification_id} not found"
            )
        return Envelope.ok(
            status="ok",
            task_id=None,
            next="continue",
            evidence={
                "id": str(n.id),
                "type": str(n.type),
                "priority": str(n.priority),
                "subject": n.subject,
                "body": n.body,
                "requires_ack": n.requires_ack,
                "from_agent": str(n.from_agent) if n.from_agent else None,
            },
            context_briefing={},
        )

    _PM_ROLES_FOR_PR_UPDATE: ClassVar[frozenset[str]] = frozenset(
        {"cell_pm", "main_pm"}
    )

    @staticmethod
    def _pr_update_is_authorized(agent_id: UUID, task: Any, agent: Any) -> bool:
        """True iff caller is the task's assignee, main_pm, or cell_pm on team.

        Extracted so pr_update stays under xenon's cyclomatic-complexity
        bound — the three branches plus the team-string compare push the
        verb itself over the line when inlined.
        """
        if task.assigned_to == agent_id:
            return True
        role_str = str(agent.role) if agent is not None else ""
        if role_str == "main_pm":
            return True
        if role_str != "cell_pm" or agent is None:
            return False
        if agent.team is None or task.team is None:
            return False
        return str(agent.team) == str(task.team)

    async def pr_update(
        self,
        *,
        agent_id: UUID,
        task_id: UUID,
        title: str | None = None,
        body: str | None = None,
        reviewers: list[str] | None = None,
    ) -> Envelope:
        """Update an existing PR's title, body, and/or requested reviewers.

        Dogfooding surfaced this gap: agents who needed to edit a PR's
        title/body or assign a reviewer after ``open_pr`` had no verb
        for it and got bash-shimmed by the ``gh pr edit`` guard. This
        verb is the gateway-native replacement.

        Authorization: caller must be the task's ``assigned_to`` OR a
        PM on the task's team (cell_pm.team == task.team, or main_pm
        which is cross-team).

        Preconditions:
          - task must exist (else not_found)
          - task.pr_number must be set (else invalid_state, remediate
            'call open_pr')
          - at least one of title/body/reviewers must be non-None (else
            invalid_state — schema-level check is the first line of
            defense; this guard catches direct gateway calls)
        """
        if rej := self._pr_update_input_check(title, body, reviewers):
            return rej
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        if t.pr_number is None:
            return Envelope.invalid_state(
                message=f"task {task_id} has no PR open",
                remediate=(
                    "call open_pr(task_id) first; pr_update only edits an "
                    "already-open PR"
                ),
                context_briefing={},
            )
        agent = await self.task.agent_for(agent_id)
        if not self._pr_update_is_authorized(agent_id, t, agent):
            role_str = str(agent.role) if agent is not None else ""
            return Envelope.not_authorized(
                message=(
                    f"role {role_str!r} is neither the assignee nor a PM on "
                    f"this task's team; cannot update PR"
                ),
                remediate=(
                    "only the task's assignee or a PM on the task's team "
                    "may edit the PR; ask the assignee or your PM to call "
                    "pr_update instead"
                ),
                context_briefing={},
            )
        try:
            result = await self.git.update_pr_for_task(
                task_id,
                title=title,
                body=body,
                reviewers=reviewers,
                actor_agent_id=agent_id,
            )
        except GitError as exc:
            return Envelope.invalid_state(
                message=str(exc),
                remediate=(
                    "check the PR number on the task and retry; if the PR "
                    "was closed externally, the task should be reset"
                ),
                context_briefing={},
            )
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next="continue working, or i_am_done when ready",
            evidence=result,
            context_briefing={},
        )

    async def notify_ack(
        self,
        *,
        agent_id: UUID,
        notification_id: UUID,
    ) -> Envelope:
        """Acknowledge a notification.

        Returns ``not_authorized`` if the caller isn't a recipient.
        """
        try:
            n = await self._deps.notification_delivery.acknowledge(
                notification_id=notification_id,
                agent_id=agent_id,
                ack_type="received",
            )
        except ValueError as exc:
            return Envelope.not_authorized(
                message=str(exc),
                remediate="only recipients of a notification can ack it",
                context_briefing={},
            )
        if n is None:
            return Envelope.not_found(
                message=f"notification {notification_id} not found"
            )
        return Envelope.ok(
            status="acked",
            task_id=None,
            next="continue",
            evidence={"id": str(notification_id), "acked": True},
            context_briefing={},
        )

    async def read_messages(self, *, agent_id: UUID) -> Envelope:
        """Mark all of the caller's unread A2A direct messages as read.

        Clears the A2A side of ``i_am_idle``'s unread soft-block: zeroes the
        per-conversation unread counter and stamps ``read_at`` on the inbound
        messages. Notifications are separate (notify_list / notify_get /
        notify_ack).
        """
        cleared = await self.a2a.mark_all_read(agent_id)
        return Envelope.ok(
            status="read",
            task_id=None,
            next="retry i_am_idle() — your A2A inbox is clear",
            evidence={"conversations_cleared": cleared},
            context_briefing={},
        )

    async def read_a2a(self, *, agent_id: UUID) -> Envelope:
        """Return the caller's unread INCOMING A2A message bodies, then clear them.

        The content-bearing read: ``read_messages`` only zeroes the unread
        counter, so an agent could see "3 unread from be-qa" without ever
        reading what was said. This returns the actual text of the inbound
        messages (never the caller's own sends) so it can act on them.
        """
        messages = await self.a2a.get_unread_messages(agent_id)
        return Envelope.ok(
            status="read",
            task_id=None,
            next="act on the messages, then retry i_am_idle()",
            evidence={"messages": messages},
            context_briefing={},
        )


def _strip_task_prefix(msg: str) -> str:
    """Strip any [task-id] prefix the agent supplied; gateway re-adds canonical."""
    return _TASK_ID_PREFIX_RE.sub("", msg)


_AI_ATTRIBUTION_RE = re.compile(
    r"co-authored-by:.*(?:anthropic\.com|claude|grok|x\.?ai)"
    r"|generated with.*(?:claude|grok)",
    re.IGNORECASE,
)


def _strip_ai_attribution(msg: str) -> str:
    """Drop model self-attribution lines from a commit message.

    Company policy: agent commits carry the agent's own identity, never the
    model vendor's. The settings-level ``includeCoAuthoredBy: false`` removes
    the harness nudge, but the model can still hand-write the trailer — this
    chokepoint covers every provider deterministically.
    """
    kept = [ln for ln in msg.splitlines() if not _AI_ATTRIBUTION_RE.search(ln)]
    return "\n".join(kept)
