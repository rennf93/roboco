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
X_SEEN_FEATURES = "x_seen_features"
X_SPOTLIGHT_BRIEF = "x_spotlight_brief"
X_SPOTLIGHT_SKIP_REASON = "x_spotlight_skip_reason"
ROADMAP_CYCLE = "roadmap_cycle"
VIDEO_DRAFT = "video_draft"
VIDEO_REJECT_REASON = "video_reject_reason"
RENDER_PREVIEW = "render_preview"
VAULT_CURATION_DISPATCHED = "vault_curation_dispatched"
VAULT_NOTE_REF = "vault_note_ref"
DOCS_SYNC_RELEASE_VERSION = "docs_sync_release_version"


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
