"""Typed accessors for ``Task.orchestration_markers``.

The machine markers that used to be string-packed into the human
``quick_context`` blob (``original_developer:<uuid>``, ``documenter:<uuid>``,
``required_cells:``, ``external_pr_head=``, ``self_heal_fp=``, ``dismissed=1``,
``external_pr_supersede ...``) live in the ``orchestration_markers`` JSON column
after migration 041. These accessors are the single read/write surface for them.

Writes REASSIGN the dict (``task.orchestration_markers = {...}``) rather than
mutate in place, so SQLAlchemy's change tracking flags the column dirty (a plain
JSON column does not detect in-place mutation).
"""

from __future__ import annotations

from typing import Any, Protocol


class HasMarkers(Protocol):
    """Anything carrying the markers column (the ORM task row or domain model)."""

    orchestration_markers: dict[str, Any] | None


# Marker keys — the single source of the vocabulary.
ORIGINAL_DEVELOPER = "original_developer"
DOCUMENTER = "documenter"
REQUIRED_CELLS = "required_cells"
EXTERNAL_PR_HEAD = "external_pr_head"
EXTERNAL_PR_AUTHOR = "external_pr_author"
EXTERNAL_PR_SUPERSEDE = "external_pr_supersede"
SELF_HEAL_FP = "self_heal_fp"
DISMISSED = "dismissed"
ESCALATION = "escalation"
APPROVE_AND_START_NOTES = "approve_and_start_notes"
RELEASE_REPORT = "release_report"
RELEASE_REQUIRED_CHANGES = "release_required_changes"
RELEASE_EXECUTE_STATUS = "release_execute_status"
RELEASE_EXECUTE_DETAIL = "release_execute_detail"
X_DRAFT_BODY = "x_draft_body"
X_RELEASE_VERSION = "x_release_version"
X_MENTION_REF = "x_mention_ref"
X_REJECT_REASON = "x_reject_reason"
X_POSTED_TWEET_ID = "x_posted_tweet_id"
X_FEATURE_REF = "x_feature_ref"
X_EDITORIAL_REF = "x_editorial_ref"
X_SEEN_FEATURES = "x_seen_features"
X_SPOTLIGHT_BRIEF = "x_spotlight_brief"
X_SPOTLIGHT_SKIP_REASON = "x_spotlight_skip_reason"
X_CAMPAIGN_REF = "x_campaign_ref"
ROADMAP_CYCLE = "roadmap_cycle"
PEST_HUNT = "pest_hunt"
MARKET_BRIEF = "market_brief"
CORONER_INCIDENT = "coroner_incident"
CORONER_POSTMORTEM = "coroner_postmortem"
QUALITY_REPORT = "quality_report"
GAP_FILL = "gap_fill"
MESSAGING_FIXES = "messaging_fixes"
FRICTION_FIXES = "friction_fixes"
VIDEO_DRAFT = "video_draft"
VIDEO_REJECT_REASON = "video_reject_reason"
RENDER_PREVIEW = "render_preview"
VAULT_CURATION_DISPATCHED = "vault_curation_dispatched"
VAULT_NOTE_REF = "vault_note_ref"
DOCS_SYNC_RELEASE_VERSION = "docs_sync_release_version"
REBALANCE_PLAN = "rebalance_plan"
PLAYBOOK_DRAFTS = "playbook_drafts"
WAR_ROOM_BRIEF = "war_room_brief"
BARFLY_CANDIDATES = "barfly_candidates"
BARFLY_REPLY_REF = "barfly_reply_ref"
PR_WAIVED = "pr_waived"
BRANCH_PENDING = "branch_pending"
SUPERSEDE_COMMENT_POSTED = "supersede_comment_posted"
BRANCH_CUT_FAILED = "branch_cut_failed"
BRANCH_CUT_NEXT_RETRY_AT = "branch_cut_next_retry_at"


def get_marker(task: HasMarkers, key: str, default: Any = None) -> Any:
    om = getattr(task, "orchestration_markers", None)
    if not isinstance(om, dict):
        return default
    return om.get(key, default)


def set_marker(task: HasMarkers, key: str, value: Any) -> None:
    om = getattr(task, "orchestration_markers", None)
    markers = dict(om) if isinstance(om, dict) else {}
    markers[key] = value
    task.orchestration_markers = markers


def clear_marker(task: HasMarkers, key: str) -> None:
    om = getattr(task, "orchestration_markers", None)
    if not isinstance(om, dict) or key not in om:
        return
    markers = dict(om)
    del markers[key]
    task.orchestration_markers = markers or None


# --- original developer ---------------------------------------------------- #


def get_original_developer(task: HasMarkers) -> str | None:
    val = get_marker(task, ORIGINAL_DEVELOPER)
    return str(val) if val else None


def set_original_developer(task: HasMarkers, agent_id: Any) -> None:
    set_marker(task, ORIGINAL_DEVELOPER, str(agent_id))


# --- documenter ------------------------------------------------------------ #


def get_documenter(task: HasMarkers) -> str | None:
    val = get_marker(task, DOCUMENTER)
    return str(val) if val else None


def set_documenter(task: HasMarkers, agent_id: Any) -> None:
    set_marker(task, DOCUMENTER, str(agent_id))


# --- required cells -------------------------------------------------------- #


def get_required_cells(task: HasMarkers) -> list[str]:
    val = get_marker(task, REQUIRED_CELLS, [])
    return [str(c) for c in val] if isinstance(val, list) else []


def set_required_cells(task: HasMarkers, cells: list[str]) -> None:
    set_marker(task, REQUIRED_CELLS, [str(c) for c in cells])


# --- self-heal fingerprint ------------------------------------------------- #


def get_self_heal_fingerprint(task: HasMarkers) -> str | None:
    val = get_marker(task, SELF_HEAL_FP)
    return str(val) if val else None


def set_self_heal_fingerprint(task: HasMarkers, fingerprint: str) -> None:
    set_marker(task, SELF_HEAL_FP, fingerprint)


# --- release proposal ------------------------------------------------------ #


def get_release_report(task: HasMarkers) -> dict[str, Any] | None:
    val = get_marker(task, RELEASE_REPORT)
    return val if isinstance(val, dict) else None


def set_release_report(task: HasMarkers, report: dict[str, Any]) -> None:
    set_marker(task, RELEASE_REPORT, report)


def get_release_required_changes(task: HasMarkers) -> str | None:
    val = get_marker(task, RELEASE_REQUIRED_CHANGES)
    return str(val) if val else None


def set_release_required_changes(task: HasMarkers, text: str) -> None:
    set_marker(task, RELEASE_REQUIRED_CHANGES, text)


def get_release_execute_outcome(task: HasMarkers) -> tuple[str, str] | None:
    """The last execute outcome ``(status, detail)`` — e.g. ``("gate_failed",
    "...")`` — or None when the proposal has never been approved. Surfaced to
    the CEO via ``GET /proposal`` so a failed ~40min execute isn't a silent
    PENDING."""
    status = get_marker(task, RELEASE_EXECUTE_STATUS)
    if not status:
        return None
    detail = get_marker(task, RELEASE_EXECUTE_DETAIL)
    return str(status), str(detail) if detail else ""


def set_release_execute_outcome(task: HasMarkers, status: str, detail: str) -> None:
    """Record the outcome of the latest approve execute on the proposal."""
    set_marker(task, RELEASE_EXECUTE_STATUS, status)
    set_marker(task, RELEASE_EXECUTE_DETAIL, detail)


# --- X (Twitter) held post/reply -------------------------------------------
# A held x_post / x_reply proposal (never dispatched — CEO approve/reject
# only) carries its draft body, and a reply additionally carries the mention
# it answers. Set once at origination; approve may overwrite the draft body
# with the CEO's edited text before posting.


def get_x_draft_body(task: HasMarkers) -> str | None:
    val = get_marker(task, X_DRAFT_BODY)
    return str(val) if val else None


def set_x_draft_body(task: HasMarkers, body: str) -> None:
    set_marker(task, X_DRAFT_BODY, body)


def get_x_release_version(task: HasMarkers) -> str | None:
    val = get_marker(task, X_RELEASE_VERSION)
    return str(val) if val else None


def set_x_release_version(task: HasMarkers, version: str) -> None:
    set_marker(task, X_RELEASE_VERSION, version)


def get_x_mention_ref(task: HasMarkers) -> dict[str, Any] | None:
    val = get_marker(task, X_MENTION_REF)
    return val if isinstance(val, dict) else None


def set_x_mention_ref(task: HasMarkers, ref: dict[str, Any]) -> None:
    set_marker(task, X_MENTION_REF, ref)


def get_x_reject_reason(task: HasMarkers) -> str | None:
    val = get_marker(task, X_REJECT_REASON)
    return str(val) if val else None


def set_x_reject_reason(task: HasMarkers, reason: str) -> None:
    set_marker(task, X_REJECT_REASON, reason)


def get_x_posted_tweet_id(task: HasMarkers) -> str | None:
    val = get_marker(task, X_POSTED_TWEET_ID)
    return str(val) if val else None


def set_x_posted_tweet_id(task: HasMarkers, tweet_id: str) -> None:
    set_marker(task, X_POSTED_TWEET_ID, tweet_id)


def get_x_feature_ref(task: HasMarkers) -> dict[str, Any] | None:
    val = get_marker(task, X_FEATURE_REF)
    return val if isinstance(val, dict) else None


def set_x_feature_ref(task: HasMarkers, ref: dict[str, Any]) -> None:
    set_marker(task, X_FEATURE_REF, ref)


def get_x_editorial_ref(task: HasMarkers) -> dict[str, Any] | None:
    val = get_marker(task, X_EDITORIAL_REF)
    return val if isinstance(val, dict) else None


def set_x_editorial_ref(task: HasMarkers, ref: dict[str, Any]) -> None:
    set_marker(task, X_EDITORIAL_REF, ref)


def get_x_seen_features(task: HasMarkers) -> list[str]:
    val = get_marker(task, X_SEEN_FEATURES, [])
    return [str(s) for s in val] if isinstance(val, list) else []


def set_x_seen_features(task: HasMarkers, slugs: list[str]) -> None:
    set_marker(task, X_SEEN_FEATURES, [str(s) for s in slugs])


def get_x_spotlight_brief(task: HasMarkers) -> dict[str, Any] | None:
    val = get_marker(task, X_SPOTLIGHT_BRIEF)
    return val if isinstance(val, dict) else None


def set_x_spotlight_brief(task: HasMarkers, brief: dict[str, Any]) -> None:
    set_marker(task, X_SPOTLIGHT_BRIEF, brief)


def get_x_spotlight_skip_reason(task: HasMarkers) -> str | None:
    val = get_marker(task, X_SPOTLIGHT_SKIP_REASON)
    return str(val) if val else None


def set_x_spotlight_skip_reason(task: HasMarkers, reason: str) -> None:
    set_marker(task, X_SPOTLIGHT_SKIP_REASON, reason)


def get_x_campaign_ref(task: HasMarkers) -> dict[str, Any] | None:
    val = get_marker(task, X_CAMPAIGN_REF)
    return val if isinstance(val, dict) else None


def set_x_campaign_ref(task: HasMarkers, ref: dict[str, Any]) -> None:
    set_marker(task, X_CAMPAIGN_REF, ref)


# --- board roadmap cycle ---------------------------------------------------
# The themed cycle (goal + item drafts) the Product Owner authors via
# ``propose_roadmap`` onto the exploration task the roadmap engine opened.
# Each item carries its own status (proposed/approved/rejected) so the CEO's
# per-item approve/reject lives entirely in this one payload — no extra table.


def get_roadmap_cycle(task: HasMarkers) -> dict[str, Any] | None:
    val = get_marker(task, ROADMAP_CYCLE)
    return val if isinstance(val, dict) else None


def set_roadmap_cycle(task: HasMarkers, payload: dict[str, Any]) -> None:
    set_marker(task, ROADMAP_CYCLE, payload)


# --- board pest control hunt -------------------------------------------------
# The evidence-backed bug-item drafts the Product Owner authors via
# ``propose_bug_hunt`` onto the exploration task the pest-control engine
# opened. Mirrors ROADMAP_CYCLE exactly — no top-level theme goal, just items,
# each carrying its own status (proposed/approved/rejected) for the CEO's
# per-item approve/reject.


def get_pest_hunt(task: HasMarkers) -> dict[str, Any] | None:
    val = get_marker(task, PEST_HUNT)
    return val if isinstance(val, dict) else None


def set_pest_hunt(task: HasMarkers, payload: dict[str, Any]) -> None:
    set_marker(task, PEST_HUNT, payload)


# --- board spackle gap-fill audit -------------------------------------------
# The evidence-backed gap-fill item drafts the Product Owner authors via
# ``propose_gap_fill`` onto the exploration task the spackle engine opened.
# Mirrors PEST_HUNT exactly — no top-level theme goal, just items, each
# carrying its own status (proposed/approved/rejected) for the CEO's per-item
# approve/reject.


def get_gap_fill(task: HasMarkers) -> dict[str, Any] | None:
    val = get_marker(task, GAP_FILL)
    return val if isinstance(val, dict) else None


def set_gap_fill(task: HasMarkers, payload: dict[str, Any]) -> None:
    set_marker(task, GAP_FILL, payload)


# --- board mirror messaging-fixes audit -------------------------------------
# The evidence-backed messaging-fix item drafts the Head of Marketing authors
# via ``propose_messaging_fixes`` onto the exploration task the mirror engine
# opened. Mirrors GAP_FILL exactly — no top-level theme goal, just items, each
# carrying its own status (proposed/approved/rejected) for the CEO's per-item
# approve/reject.


def get_messaging_fixes(task: HasMarkers) -> dict[str, Any] | None:
    val = get_marker(task, MESSAGING_FIXES)
    return val if isinstance(val, dict) else None


def set_messaging_fixes(task: HasMarkers, payload: dict[str, Any]) -> None:
    set_marker(task, MESSAGING_FIXES, payload)


# --- Periscope market brief --------------------------------------------------
# The Head of Marketing's weekly market-research report, authored via
# ``propose_market_brief`` onto the exploration task the Periscope engine
# opened: {headline, findings (list of {id, claim, source_url, relevance,
# status, reject_reason, materialized_task_id}), threats, opportunities,
# positioning_note, injection_hits}. Unlike ROADMAP_CYCLE/PEST_HUNT the
# EXPLORATION TASK itself has no per-item decision to wait on — the verb
# completes it in the same call (mirrors X_FEATURE_REF's complete-at-propose
# asymmetry), so this marker is set exactly once. Each FINDING still carries
# its own proposed/approved/rejected status the CEO decides afterward
# (PeriscopeService.approve_finding/reject_finding), independent of the
# task's own terminal status.


def get_market_brief(task: HasMarkers) -> dict[str, Any] | None:
    val = get_marker(task, MARKET_BRIEF)
    return val if isinstance(val, dict) else None


def set_market_brief(task: HasMarkers, payload: dict[str, Any]) -> None:
    set_marker(task, MARKET_BRIEF, payload)


# --- coroner postmortem ------------------------------------------------------
# The incident ref the CoronerEngine stamps on the postmortem-exploration task
# it opens ({incident_task_id, kind, revision_count, title}), and the
# Auditor-authored postmortem ({incident_summary, root_cause, failed_stage,
# process_change: {kind, description, status, reject_reason,
# materialized_task_id}, playbook_id?}) it writes via ``propose_postmortem``.
# A single call completes the EXPLORATION TASK — unlike ROADMAP_CYCLE/
# PEST_HUNT there is no multi-item queue to keep it open for — but the one
# ``process_change`` still carries its own proposed/approved/rejected status
# for the CEO's after-the-fact decision (CoronerService.
# approve_process_change/reject_process_change), except when its kind is
# "playbook" (status "not_applicable" — already routed straight into the
# playbook curation queue, nothing left to decide here).


def get_coroner_incident(task: HasMarkers) -> dict[str, Any] | None:
    val = get_marker(task, CORONER_INCIDENT)
    return val if isinstance(val, dict) else None


def set_coroner_incident(task: HasMarkers, ref: dict[str, Any]) -> None:
    set_marker(task, CORONER_INCIDENT, ref)


def get_coroner_postmortem(task: HasMarkers) -> dict[str, Any] | None:
    val = get_marker(task, CORONER_POSTMORTEM)
    return val if isinstance(val, dict) else None


def set_coroner_postmortem(task: HasMarkers, payload: dict[str, Any]) -> None:
    set_marker(task, CORONER_POSTMORTEM, payload)


# --- Sentinel quality report --------------------------------------------------
# The Auditor's weekly org-wide drift report, authored via
# ``propose_quality_report`` onto the exploration task the sentinel engine
# opened: {headline, items (list of {id, area, observation, evidence,
# suggested_action, status, reject_reason, materialized_task_id}),
# overall_assessment}. Mirrors MARKET_BRIEF exactly — the exploration task
# is set exactly once (complete-at-propose), but each ITEM still carries its
# own proposed/approved/rejected status the CEO decides afterward
# (SentinelService.approve_item/reject_item).


def get_quality_report(task: HasMarkers) -> dict[str, Any] | None:
    val = get_marker(task, QUALITY_REPORT)
    return val if isinstance(val, dict) else None


def set_quality_report(task: HasMarkers, payload: dict[str, Any]) -> None:
    set_marker(task, QUALITY_REPORT, payload)


# --- Scales rebalance plan ---------------------------------------------------
# The re-priority / cancellation items the Product Owner authors via
# ``propose_rebalance`` onto the exploration task the Scales engine opened.
# Mirrors PEST_HUNT exactly — no top-level theme goal, just items, each
# carrying its own status (proposed/approved/rejected) for the CEO's per-item
# approve/reject. Unlike PEST_HUNT an item never drafts a NEW task: it
# references a LIVE backlog task (``target_task_id``, resolved from the
# agent's ``task_ref`` at propose time) that approval MUTATES in place
# (reprioritize) or cancels — never creates.


def get_rebalance_plan(task: HasMarkers) -> dict[str, Any] | None:
    val = get_marker(task, REBALANCE_PLAN)
    return val if isinstance(val, dict) else None


def set_rebalance_plan(task: HasMarkers, payload: dict[str, Any]) -> None:
    set_marker(task, REBALANCE_PLAN, payload)


# --- Librarian playbook drafts ------------------------------------------------
# The playbooks the Auditor mined and drafted via ``propose_playbook_drafts``
# onto the exploration task the Librarian engine opened: {drafts: [{id, title},
# ...]}. Mirrors QUALITY_REPORT/MARKET_BRIEF — no per-item CEO decision (each
# draft is already a real PlaybookTable row riding the normal pending-playbook
# curation queue), so this marker is set exactly once (complete-at-propose).


def get_playbook_drafts(task: HasMarkers) -> dict[str, Any] | None:
    val = get_marker(task, PLAYBOOK_DRAFTS)
    return val if isinstance(val, dict) else None


def set_playbook_drafts(task: HasMarkers, payload: dict[str, Any]) -> None:
    set_marker(task, PLAYBOOK_DRAFTS, payload)


# --- War Room campaign brief -------------------------------------------------
# The exploration task's starting context, stamped once at origination by
# ``WarRoomEngine``: {version, highlights} when opened by the release-publish
# hook, or {} when opened on-demand (a CEO "run now" call with no release to
# anchor to — the Head of Marketing designs a brief-less campaign). Read-only
# after that; ``propose_campaign`` never mutates it.


def get_war_room_brief(task: HasMarkers) -> dict[str, Any] | None:
    val = get_marker(task, WAR_ROOM_BRIEF)
    return val if isinstance(val, dict) else None


def set_war_room_brief(task: HasMarkers, payload: dict[str, Any]) -> None:
    set_marker(task, WAR_ROOM_BRIEF, payload)


# --- Barfly conversation candidates + reply ref -----------------------------
# The screened candidate X conversations (search results — RoboCo is relevant
# but unmentioned) the barfly engine gathers onto the exploration task it
# opens: a list of {id, author_handle, text, engagement_note}. The Head of
# Marketing picks from THIS list only via ``propose_conversation_replies``
# (never inventing a tweet) — no per-item status here, unlike PEST_HUNT/
# GAP_FILL, since each approved-shape reply materializes its OWN held draft
# immediately (mirrors X_FEATURE_REF's complete-at-propose asymmetry). The
# materialized draft (source=x_barfly) then carries BARFLY_REPLY_REF: the
# candidate it answers plus the HoM's rationale — mirrors X_MENTION_REF.


def get_barfly_candidates(task: HasMarkers) -> list[dict[str, Any]]:
    val = get_marker(task, BARFLY_CANDIDATES, [])
    return val if isinstance(val, list) else []


def set_barfly_candidates(task: HasMarkers, candidates: list[dict[str, Any]]) -> None:
    set_marker(task, BARFLY_CANDIDATES, candidates)


def get_barfly_reply_ref(task: HasMarkers) -> dict[str, Any] | None:
    val = get_marker(task, BARFLY_REPLY_REF)
    return val if isinstance(val, dict) else None


def set_barfly_reply_ref(task: HasMarkers, ref: dict[str, Any]) -> None:
    set_marker(task, BARFLY_REPLY_REF, ref)


# --- board dogfood friction fixes --------------------------------------------
# The evidence-backed UX-friction item drafts the Product Owner authors via
# ``propose_friction_fixes`` onto the exploration task the dogfood engine
# opened. Mirrors GAP_FILL exactly — no top-level theme goal, just items, each
# carrying its own status (proposed/approved/rejected) for the CEO's per-item
# approve/reject. ``evidence`` on each item is the walked path (the actual
# clicks/pages) plus what broke or felt wrong — not a screenshot.


def get_friction_fixes(task: HasMarkers) -> dict[str, Any] | None:
    val = get_marker(task, FRICTION_FIXES)
    return val if isinstance(val, dict) else None


def set_friction_fixes(task: HasMarkers, payload: dict[str, Any]) -> None:
    set_marker(task, FRICTION_FIXES, payload)


# --- video draft ------------------------------------------------------------
# The video engine's working payload, carried on both the UX/UI authoring task
# (source=video) and the held post draft it later produces (source=video_post):
# {occasion, script, composition_id, input_props, mp4_paths, x_caption,
# tiktok_caption, platforms, render_status, render_attempts, render_error,
# source_task_id}. Set once at authoring-open time and extended (not replaced)
# once rendering fills in the mp4/caption fields.

# Bounded retry for the video render loop: a failed render (read-clone not yet
# synced to the just-merged composition, or a transient sidecar blip) retries
# on a later cycle; only after this many attempts is a task marked terminally
# failed, so a genuinely broken composition can't re-render forever. Single
# source of truth for the orchestrator's render loop AND the pipeline-strip
# API — importable from both without the API importing the orchestrator.
MAX_VIDEO_RENDER_ATTEMPTS = 5


def get_video_draft(task: HasMarkers) -> dict[str, Any] | None:
    val = get_marker(task, VIDEO_DRAFT)
    return val if isinstance(val, dict) else None


def set_video_draft(task: HasMarkers, payload: dict[str, Any]) -> None:
    set_marker(task, VIDEO_DRAFT, payload)


def get_video_reject_reason(task: HasMarkers) -> str | None:
    val = get_marker(task, VIDEO_REJECT_REASON)
    return str(val) if val else None


def set_video_reject_reason(task: HasMarkers, reason: str) -> None:
    set_marker(task, VIDEO_REJECT_REASON, reason)


# Task-source tag for a video-authoring task. Canonical here so foundation-
# layer gates can key on it without importing the services layer;
# services/task.py's VIDEO_SOURCE aliases this.
VIDEO_TASK_SOURCE = "video"


def get_render_preview(task: HasMarkers) -> dict[str, Any] | None:
    """The last ``request_render`` preview stamped on a video-authoring task.

    Payload: {at, composition_id, orientation, frame_count, duration_seconds,
    frames (absolute container paths, readable from every agent container),
    head_sha, dirty, rendered_by, source ("workspace"|"branch")}. Its
    presence is what the i_am_done RENDER_VERIFIED gate checks — proof the
    author looked at the actual rendered artifact, not just the source.
    """
    val = get_marker(task, RENDER_PREVIEW)
    return val if isinstance(val, dict) else None


def set_render_preview(task: HasMarkers, payload: dict[str, Any]) -> None:
    set_marker(task, RENDER_PREVIEW, payload)


# --- vault curation ---------------------------------------------------------
# One-shot guard for the root-completion Auditor spawn (orchestrator): set the
# moment the spawn fires so a restart can't re-spawn a root another process
# instance already dispatched (in-memory `_board_dispatched` covers the
# same-process race; this covers cross-restart).


def is_vault_curation_dispatched(task: HasMarkers) -> bool:
    return bool(get_marker(task, VAULT_CURATION_DISPATCHED, False))


def mark_vault_curation_dispatched(task: HasMarkers) -> None:
    set_marker(task, VAULT_CURATION_DISPATCHED, True)


# --- vault note ref ----------------------------------------------------------
# The vault-intake watcher's origin ref on a held ``vault_note`` draft:
# {path, content_hash, action_items}. Set once at origination; read back by
# nothing else yet (phase 2 could use it to re-locate the source note).


def get_vault_note_ref(task: HasMarkers) -> dict[str, Any] | None:
    val = get_marker(task, VAULT_NOTE_REF)
    return val if isinstance(val, dict) else None


def set_vault_note_ref(task: HasMarkers, ref: dict[str, Any]) -> None:
    set_marker(task, VAULT_NOTE_REF, ref)


# --- external PR head ------------------------------------------------------ #


def get_external_pr_head(task: HasMarkers) -> str | None:
    val = get_marker(task, EXTERNAL_PR_HEAD)
    return str(val) if val else None


def set_external_pr_head(task: HasMarkers, head_sha: str) -> None:
    set_marker(task, EXTERNAL_PR_HEAD, head_sha)


def get_external_pr_author(task: HasMarkers) -> str | None:
    val = get_marker(task, EXTERNAL_PR_AUTHOR)
    return str(val) if val else None


def set_external_pr_author(task: HasMarkers, login: str) -> None:
    set_marker(task, EXTERNAL_PR_AUTHOR, login)


# --- external PR supersede ------------------------------------------------- #


def get_external_pr_supersede(task: HasMarkers) -> str | None:
    val = get_marker(task, EXTERNAL_PR_SUPERSEDE)
    return str(val) if val else None


def set_external_pr_supersede(task: HasMarkers, marker: str) -> None:
    set_marker(task, EXTERNAL_PR_SUPERSEDE, marker)


# --- dismissed ------------------------------------------------------------- #


def is_dismissed(task: HasMarkers) -> bool:
    return bool(get_marker(task, DISMISSED, False))


def mark_dismissed(task: HasMarkers) -> None:
    set_marker(task, DISMISSED, True)


# --- escalation ------------------------------------------------------------ #
# A coordination event, NOT a developer note. It used to be appended to
# ``dev_notes`` (polluting the developer's space and growing unboundedly on a
# re-escalation loop); it lives here as the latest structured record instead.
# Delivery of the reason to the target is handled by the escalate notification.


def get_escalation(task: HasMarkers) -> dict[str, str] | None:
    val = get_marker(task, ESCALATION)
    return val if isinstance(val, dict) else None


def set_escalation(
    task: HasMarkers, *, from_slug: str, to_slug: str, reason: str
) -> None:
    set_marker(task, ESCALATION, {"from": from_slug, "to": to_slug, "reason": reason})


# --- approve-and-start notes ----------------------------------------------- #
# The CEO's note when approving a board-reviewed coordination root. Used to be
# string-packed into ``quick_context`` as ``approve_and_start_notes:<text>``;
# kept here so ``quick_context`` carries only the human ResumptionNote.


def get_approve_and_start_notes(task: HasMarkers) -> str | None:
    val = get_marker(task, APPROVE_AND_START_NOTES)
    return str(val) if val else None


def set_approve_and_start_notes(task: HasMarkers, notes: str) -> None:
    set_marker(task, APPROVE_AND_START_NOTES, notes)


# --- lifecycle transition notes -------------------------------------------- #
# A PM/CEO note attached to a lifecycle transition (completion, escalate_to_ceo,
# ceo_approval, ceo_rejection). These used to be string-packed into
# ``quick_context`` as ``<event>:<text>`` soup; they live here keyed by event so
# ``quick_context`` carries only the human ResumptionNote.

TRANSITION_NOTES = "transition_notes"


def get_transition_note(task: HasMarkers, event: str) -> str | None:
    notes = get_marker(task, TRANSITION_NOTES)
    if not isinstance(notes, dict):
        return None
    val = notes.get(event)
    return str(val) if val else None


def set_transition_note(task: HasMarkers, event: str, note: str) -> None:
    existing = get_marker(task, TRANSITION_NOTES)
    notes = dict(existing) if isinstance(existing, dict) else {}
    notes[event] = note
    set_marker(task, TRANSITION_NOTES, notes)


# --- docs-sync release version --------------------------------------------- #
# The docs-sync engine stamps the release version (e.g. "0.23.0") on each
# docs_update task it originates so it can dedupe per release.


def get_docs_sync_release_version(task: HasMarkers) -> str | None:
    val = get_marker(task, DOCS_SYNC_RELEASE_VERSION)
    return str(val) if val else None


def set_docs_sync_release_version(task: HasMarkers, version: str) -> None:
    set_marker(task, DOCS_SYNC_RELEASE_VERSION, version)


# --- resubmit-unchanged-head exemption -------------------------------------
# submit_root/submit_up hard-refuse a re-submit whose assembled PR head is
# unchanged since the last pr_fail (the 2026-06-27 loop-stopper). That's a
# structural deadlock when the rejection round needed no code change (e.g. a
# transient CI-lookup error) — every ledger finding gets addressed/waived but
# the head sha never moves. This records the head sha that was granted ONE
# resubmit exemption, so a second attempt at the SAME head still refuses (see
# choreographer._unchanged_pr_guard).

RESUBMIT_UNCHANGED_HEAD = "resubmit_unchanged_head"


def get_resubmit_unchanged_head(task: HasMarkers) -> str | None:
    val = get_marker(task, RESUBMIT_UNCHANGED_HEAD)
    return str(val) if val else None


def set_resubmit_unchanged_head(task: HasMarkers, head_sha: str) -> None:
    set_marker(task, RESUBMIT_UNCHANGED_HEAD, head_sha)


# --- block/unblock flip breaker --------------------------------------------
# Counts successful `unblock` calls on a task so a repeating block/unblock
# cycle (e.g. escalate_up auto-blocks, a PM unblocks, repeat — live incident:
# 10 flips, 43 spawns with no forward progress) can alert the CEO instead of
# burning spawns forever. Payload: {"count": int, "notified": bool} — one key
# so the counter and the notified-once flag stay atomic with each other.

BLOCK_FLIP_COUNT = "block_flip_count"


def get_block_flip_count(task: HasMarkers) -> int:
    val = get_marker(task, BLOCK_FLIP_COUNT)
    count = val.get("count") if isinstance(val, dict) else None
    return int(count) if isinstance(count, int) else 0


def is_block_flip_notified(task: HasMarkers) -> bool:
    val = get_marker(task, BLOCK_FLIP_COUNT)
    return bool(val.get("notified")) if isinstance(val, dict) else False


def bump_block_flip_count(task: HasMarkers) -> int:
    """Increment the flip counter; returns the new count."""
    count = get_block_flip_count(task) + 1
    set_marker(
        task,
        BLOCK_FLIP_COUNT,
        {"count": count, "notified": is_block_flip_notified(task)},
    )
    return count


def mark_block_flip_notified(task: HasMarkers) -> None:
    set_marker(
        task, BLOCK_FLIP_COUNT, {"count": get_block_flip_count(task), "notified": True}
    )


# --- budget-breach block ----------------------------------------------------
# Stamped by the orchestrator's task-budget sweep the moment it BLOCKs a task
# for exceeding its $ budget (ROBOCO_TASK_BUDGETS_ENABLED). `unblock` consults
# it to re-check spend-vs-cap: still over refuses (naming the budget
# remediation) so a PM can't silently re-breach the same cap the next tick;
# under (the CEO raised it) clears the marker and lets the unblock through.
# No historical cap/spend stored here — the re-check always reads live values.

BUDGET_BLOCKED = "budget_blocked"


def mark_budget_blocked(task: HasMarkers) -> None:
    set_marker(task, BUDGET_BLOCKED, True)


def is_budget_blocked(task: HasMarkers) -> bool:
    return bool(get_marker(task, BUDGET_BLOCKED, False))


def clear_budget_blocked(task: HasMarkers) -> None:
    clear_marker(task, BUDGET_BLOCKED)


# --- PR waiver (zero-diff report-only work) ---------------------------------
# submit_up / submit_root skip create_pr / create_root_pr when the task's
# branch carries zero commits relative to its resolved parent branch (a
# report-only audit/findings subtree with no code diff — the Board Program
# catalog generates this by design). Stamped by the verb runner the moment it
# waives PR creation so every downstream PR-required gate (the in-path
# review gate, PM completion, the CEO-escalation pr_number check) recognizes
# a legitimately PR-less task instead of wedging on a missing PR. The
# human-readable reason rides the existing TRANSITION_NOTES marker under the
# "pr_waived" event, alongside a progress entry for reviewer/PM visibility.

PR_WAIVED_TRANSITION_EVENT = "pr_waived"


def is_pr_waived(task: HasMarkers) -> bool:
    return bool(get_marker(task, PR_WAIVED, False))


def mark_pr_waived(task: HasMarkers) -> None:
    set_marker(task, PR_WAIVED, True)


def clear_pr_waived(task: HasMarkers) -> None:
    """Un-latch a stale waiver once a REAL PR is about to be created.

    ``PR_WAIVED`` is otherwise a one-way latch: nothing clears it once set,
    so a waived task that later gets real commits (round trip: waived ->
    ``request_changes`` -> NEEDS_REVISION -> re-submit with real work) keeps
    disabling the PR-merged backstops (``TaskService.complete``'s
    work-session-merged check, the CEO-escalation ``pr_number`` check) even
    though a real PR now exists. Called from
    ``VerbRunner._run_pre_side_effects`` the moment ``create_pr`` /
    ``create_root_pr`` actually run (``ahead > 0``) — i.e. whenever this
    round is NOT itself waiving PR creation.
    """
    clear_marker(task, PR_WAIVED)


# --- supersede branch-pending (async branch cut) --------------------------- #
# The CEO's supersede-external-PR action commits the umbrella first, stamps
# branch_pending, and returns within the 60s client window. The branch cut
# (workspace resolve + fetch refs/pull/{n}/head + push) runs in a background
# task; the dispatcher skips a branch_pending umbrella so Main PM is not
# routed until the branch is ready. SUPERSEDE_COMMENT_POSTED tracks whether
# the contributor-facing PR comment was delivered (fast-path or background).
# BRANCH_CUT_FAILED stores the failure attempt count (int) so the sweep can
# apply a backoff; after MAX_BRANCH_CUT_ATTEMPTS it escalates to BLOCKED.
# BRANCH_CUT_NEXT_RETRY_AT is the epoch timestamp the sweep waits for before
# retrying a failed cut.


def is_branch_pending(task: HasMarkers) -> bool:
    return bool(get_marker(task, BRANCH_PENDING, False))


def mark_branch_pending(task: HasMarkers) -> None:
    set_marker(task, BRANCH_PENDING, True)


def clear_branch_pending(task: HasMarkers) -> None:
    clear_marker(task, BRANCH_PENDING)


def is_supersede_comment_posted(task: HasMarkers) -> bool:
    return bool(get_marker(task, SUPERSEDE_COMMENT_POSTED, False))


def mark_supersede_comment_posted(task: HasMarkers) -> None:
    set_marker(task, SUPERSEDE_COMMENT_POSTED, True)


def is_branch_cut_failed(task: HasMarkers) -> bool:
    return bool(get_marker(task, BRANCH_CUT_FAILED, False))


def get_branch_cut_attempts(task: HasMarkers) -> int:
    val = get_marker(task, BRANCH_CUT_FAILED, 0)
    return int(val) if isinstance(val, int | float) else 1


def mark_branch_cut_failed(task: HasMarkers, attempts: int = 1) -> None:
    set_marker(task, BRANCH_CUT_FAILED, attempts)


def clear_branch_cut_failed(task: HasMarkers) -> None:
    clear_marker(task, BRANCH_CUT_FAILED)


def get_branch_cut_next_retry_at(task: HasMarkers) -> float | None:
    val = get_marker(task, BRANCH_CUT_NEXT_RETRY_AT, None)
    return float(val) if isinstance(val, int | float) else None


def set_branch_cut_next_retry_at(task: HasMarkers, ts: float) -> None:
    set_marker(task, BRANCH_CUT_NEXT_RETRY_AT, ts)


def clear_branch_cut_next_retry_at(task: HasMarkers) -> None:
    clear_marker(task, BRANCH_CUT_NEXT_RETRY_AT)


# --- escalate_up/unblock oscillation breaker -------------------------------
# A round trip (escalate_up blocks the task, unblock restores it) that keeps
# repeating with nothing landing in between is a deadlock, not rework — and
# the orchestrator's per-(agent, task) respawn breaker misses it structurally:
# the escalator and the resolver each own only half the cycle's spawns, so
# neither agent's own counter accrues at the cycle's real rate. `unblock`
# stamps this marker on every restore with a cheap progress fingerprint
# (commit count + revision_count, both already loaded on the task row, plus
# one COUNT query for terminal children — a PM coordination root never
# commits itself, so child completions are its only progress signal); an
# unchanged fingerprint accrues a strike, any change (real forward motion
# between escalations) resets to 1. Past the trip threshold the task is
# force-BLOCKED for a human instead of restored, and `unblock` refuses every
# further call on it until a human clears it — either an admin status
# override out of BLOCKED (`TaskService._admin_out_of_blocked`, snapshot or
# not) or the legacy human/panel unblock route (`TaskService.unblock`).
# Payload: {"strikes": int, "progress_fp": [int, int, int], "tripped": bool}.

OSCILLATION_STRIKES = "oscillation_strikes"


def get_oscillation_strikes(task: HasMarkers) -> int:
    val = get_marker(task, OSCILLATION_STRIKES)
    strikes = val.get("strikes") if isinstance(val, dict) else None
    return int(strikes) if isinstance(strikes, int) else 0


def _get_oscillation_progress_fp(task: HasMarkers) -> list[int] | None:
    val = get_marker(task, OSCILLATION_STRIKES)
    fp = val.get("progress_fp") if isinstance(val, dict) else None
    return list(fp) if isinstance(fp, list) else None


def is_oscillation_tripped(task: HasMarkers) -> bool:
    val = get_marker(task, OSCILLATION_STRIKES)
    return bool(val.get("tripped")) if isinstance(val, dict) else False


def bump_oscillation_strikes(task: HasMarkers, progress_fp: list[int]) -> int:
    """Record one restore cycle against ``progress_fp``; returns the new
    strike count. A fingerprint that differs from the last recorded one
    resets to 1 (real progress happened between escalations); the first-ever
    call (no prior fingerprint) and a repeated, unchanged fingerprint both
    accrue from wherever the counter already was."""
    prior_fp = _get_oscillation_progress_fp(task)
    strikes = (
        get_oscillation_strikes(task) + 1
        if prior_fp is None or prior_fp == progress_fp
        else 1
    )
    set_marker(
        task,
        OSCILLATION_STRIKES,
        {
            "strikes": strikes,
            "progress_fp": progress_fp,
            "tripped": is_oscillation_tripped(task),
        },
    )
    return strikes


def mark_oscillation_tripped(task: HasMarkers) -> None:
    set_marker(
        task,
        OSCILLATION_STRIKES,
        {
            "strikes": get_oscillation_strikes(task),
            "progress_fp": _get_oscillation_progress_fp(task) or [],
            "tripped": True,
        },
    )
