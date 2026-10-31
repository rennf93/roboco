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
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast
from uuid import UUID

import structlog

from roboco.config import settings
from roboco.exceptions import GitError
from roboco.foundation.policy import communications as _comms
from roboco.foundation.policy.content import ContentValidationError, markers
from roboco.foundation.policy.content.validators import reject_trivial
from roboco.foundation.policy.journaling import Scope as _Scope
from roboco.llm.providers.nebius_sandboxes import (
    run_sandbox_command,
    tail_for_evidence,
)
from roboco.models.base import TaskStatus
from roboco.services.content_notes import content_type_for_role
from roboco.services.gateway.choreographer import findings as findings_lib
from roboco.services.gateway.choreographer.evidence_legs import (
    LegBudget,
    run_bounded_leg,
)
from roboco.services.gateway.commit_validator import validate_commit_message
from roboco.services.gateway.content_actions_proposals import (
    BoardProposalActions,
    DogfoodActions,
    MirrorActions,
    PestControlActions,
    PitchActions,
    RoadmapActions,
    ScalesActions,
    SpackleActions,
)
from roboco.services.gateway.content_actions_reports import (
    CoronerActions,
    LibrarianActions,
    PeriscopeActions,
    PlaybookActions,
    SentinelActions,
)
from roboco.services.gateway.envelope import Envelope
from roboco.services.gateway.evidence_builder import build_evidence_for_task
from roboco.services.uptime import UptimeLedger
from roboco.services.workspace_git import (
    UnsafeWorkspaceGitConfigError,
    archive_workspace_ref,
    run_workspace_git,
)
from roboco.services.x_client import MAX_TWEET_CHARS

if TYPE_CHECKING:
    from collections.abc import Callable

    from roboco.db.tables import TaskTable
    from roboco.foundation.identity import Team


logger = structlog.get_logger()

# Shortest task reference the DO tools resolve (task cards render 8-char
# prefixes); anything shorter is not identifiable.
_MIN_TASK_REF_HEX_CHARS = 8


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


def _task_time_seconds(
    timestamps: dict[str, datetime | None],
    *,
    now: datetime,
    ledger: UptimeLedger,
) -> tuple[dict[str, float | None], dict[str, float | None]]:
    """Wall-clock vs uptime-adjusted elapsed seconds for named timestamps.

    Pure given an already-loaded ``ledger`` (no DB access), so it is
    unit-testable without a session. A ``None`` timestamp maps to ``None``
    in both returned dicts.
    """
    wall: dict[str, float | None] = {}
    active: dict[str, float | None] = {}
    for key, ts in timestamps.items():
        if ts is None:
            wall[key] = None
            active[key] = None
            continue
        ts_utc = ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)
        wall[key] = (now - ts_utc).total_seconds()
        active[key] = ledger.active_seconds(ts_utc, now)
    return wall, active


def _iso(ts: datetime | None) -> str | None:
    """ISO-8601 string, or ``None`` for an unset timestamp."""
    return ts.isoformat() if ts is not None else None


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
_COMMIT_ALLOWED_ROLES: frozenset[str] = frozenset({"developer", "documenter", "devops"})
# Roles whose verb surface carries no claim verb: they are assigned tasks
# but can never satisfy an active-claim gate. Journal notes from these
# roles on a task with NO active claimant are allowed (see
# ContentActions._active_claim_violation). Extend only for roles with that
# same documented shape.
_CLAIMLESS_NOTE_ROLES: frozenset[str] = frozenset({"auditor"})
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


def _preflight_ownership_violation(
    t: Any, agent_id: UUID, task_id: UUID
) -> Envelope | None:
    """The ownership-violation envelope for a preflight caller who is not
    the task's assignee, or None when the caller may preflight the task."""
    if t.assigned_to is not None and t.assigned_to != agent_id:
        return _ownership_violation(task_id)
    return None


def _preflight_criteria(t: Any) -> list[str]:
    """The task's non-empty acceptance criteria as strings."""
    return [str(c) for c in (t.acceptance_criteria or []) if str(c).strip()]


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
    # "workspace" (dev's own tree) | "branch" (QA's read-only export)
    kind: str
    cleanup: Callable[[], None] | None = None


_VALID_NOTIFY_PRIORITIES: frozenset[str] = frozenset(p.value for p in _comms.Priority)


# Feature spotlights are HoM-authored — the Product Owner stays out of this
# cycle (mirrors _ROADMAP_ROLES's PO-only symmetry, reversed).
_FEATURE_SPOTLIGHT_ROLES: frozenset[str] = frozenset({"head_marketing"})


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


# War Room (Board Program) campaigns are HoM-authored, mirroring
# _FEATURE_SPOTLIGHT_ROLES/_PERISCOPE_ROLES.
_WAR_ROOM_ROLES: frozenset[str] = frozenset({"head_marketing"})
_CAMPAIGN_STAGE_LABELS: frozenset[str] = frozenset(
    {"teaser", "launch", "follow_up", "spotlight", "other"}
)
_CAMPAIGN_NAME_MAX_CHARS = 100
_CAMPAIGN_MIN_POSTS = 2
_CAMPAIGN_MAX_POSTS = 6


# propose_video's target-platform set + TikTok caption limit (the X caption
# reuses MAX_TWEET_CHARS). No role frozenset here, unlike the sets above:
# propose_video is gated on the caller's TEAM at runtime (_caller_team), not
# role — Role.DEVELOPER doesn't distinguish a ux-dev from a be-dev/fe-dev.
# Kept in lockstep with video-renderer/server.js COMPOSITION_ID_RE.
_COMPOSITION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$")
_VIDEO_PLATFORMS: frozenset[str] = frozenset({"x", "tiktok"})
_MAX_TIKTOK_CAPTION_CHARS = 2200


class ContentActions(
    BoardProposalActions,
    CoronerActions,
    DogfoodActions,
    LibrarianActions,
    MirrorActions,
    PeriscopeActions,
    PestControlActions,
    PitchActions,
    PlaybookActions,
    RoadmapActions,
    ScalesActions,
    SentinelActions,
    SpackleActions,
):
    """The content verbs agents reach through ``roboco-do``: each validates its
    input, injects the caller's active task where it is missing, calls the
    service behind it, and returns an Envelope.

    The Board Program proposals that become tasks are mixins in
    ``content_actions_proposals.py``; the filed reports and playbook
    curation are mixins in ``content_actions_reports.py``."""

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
        self,
        agent_id: UUID,
        task: Any,
        *,
        allow_unclaimed: bool = False,
    ) -> Envelope | None:
        """Refuse a content write when the caller is not the active claimant.

        ``assigned_to`` alone is insufficient: a reaped or handed-off agent
        keeps ``assigned_to`` until reassignment, but ``active_claimant_id``
        is cleared the moment its claim is released. Only the holder of the
        active claim may write. A board co-reviewer on a coordination task is
        exempt (it shares the task with the other board member by design).

        ``allow_unclaimed`` narrows the strict rule for journal-style notes
        to CLAIM-LESS ROLES (the auditor): dispatch assigns them tasks, but
        they have no claim verb, so the claim gate is unsatisfiable by
        construction and every note bounced forever (a9739b1c, 2026-09-29).
        When no claimant exists there is no one to race. A REAPED assignee
        is NOT covered — it had a claim and lost it, and the reap doctrine
        says it must not keep posting; the caller's role decides.
        """
        claimant = getattr(task, "active_claimant_id", None)
        if claimant == agent_id:
            return None
        if (
            allow_unclaimed
            and claimant is None
            and task.assigned_to == agent_id
            and await self._caller_is_claimless_role(agent_id)
        ):
            return None
        if await self._board_may_co_review(agent_id, task):
            return None
        return _not_active_claimant(task.id)

    async def _caller_is_claimless_role(self, agent_id: UUID) -> bool:
        """True for roles that can never hold a claim (read-only observers).

        The auditor observes and journals but its verb surface carries no
        claim verb, so demanding an active claim from it is demanding the
        impossible. Extend ``_CLAIMLESS_NOTE_ROLES`` only for roles with
        that same documented shape.
        """
        agent = await self.task.agent_for(agent_id)
        role = str(getattr(agent, "role", "")) if agent is not None else ""
        return role in _CLAIMLESS_NOTE_ROLES

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
        nothing_staged = (
            "no changes added to commit" in text or "nothing to commit" in text
        )
        if files and nothing_staged:
            remediate = (
                f"the files list {files!r} matched no modified paths; omit "
                "files to stage all changes, or pass the exact modified paths"
            )
        elif nothing_staged:
            # Live 2026-10-28: 8 of 11 failed commits were this, each answered
            # with "inspect and retry", and agents retried the same empty
            # commit.
            remediate = (
                "your task workspace has no changes to commit: either the work "
                "is already committed (check the branch log, then continue with "
                "open_pr / i_am_done), or your edits were made outside this "
                "task's workspace and must be re-applied there"
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
        self,
        agent_id: UUID,
        task_id: UUID,
        *,
        allow_unclaimed: bool = False,
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
            # DevOps gate co-claimant: devops-1 co-claims the assembled-PR
            # gate task via the devops_gate_claimant marker (never ownership)
            # and must still journal its review — the verdict verbs demand a
            # learning entry. Found by the e2e devops gate arc.
            from roboco.foundation.policy.content import markers

            if markers.get_devops_gate_claimant(t) == str(agent_id):
                return None
            return _ownership_violation(task_id)
        # assigned_to is stale across a reap/handoff (persists until
        # reassignment; active_claimant_id is cleared on release). Require
        # the active claim so a reaped agent can't keep posting — except on
        # the allow_unclaimed paths (journal notes from a claim-less
        # assignee; see _active_claim_violation).
        if t.assigned_to == agent_id:
            return await self._active_claim_violation(
                agent_id, t, allow_unclaimed=allow_unclaimed
            )
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
            envelope = await self._record_section_handoff(
                agent_id=agent_id,
                text=text,
                task_id=task_id,
                structured=_merge_resumption_fields(
                    section, done=done, next=next, where_to_look=where_to_look
                ),
            )
        else:
            envelope = await self._write_journal_note(
                agent_id=agent_id,
                text=text,
                scope=scope,
                task_id=task_id,
                structured=structured,
            )
        if envelope.error is None:
            await self._complete_note_completing_exploration(agent_id, task_id)
        return envelope

    async def _complete_note_completing_exploration(
        self, agent_id: UUID, task_id: UUID | None
    ) -> None:
        """Complete the caller's own exploration task under the
        complete-at-note contract (``BoardProgram.completes_on_note``).

        Best-effort and narrow: the task must exist, be assigned to the
        caller, be non-terminal, and its ``source`` must belong to a program
        that declares the contract (decisions_audit — one review pass IS the
        cycle). Proposal programs (roadmap/scales/…) stay open awaiting
        per-item CEO review and are never touched here. A failed note never
        completes anything (the caller gates on the envelope)."""
        if task_id is None:
            return
        from roboco.foundation.policy.board_programs import PROGRAMS

        task = await self.task.get(task_id)
        if task is None or task.assigned_to != agent_id:
            return
        program = next((p for p in PROGRAMS.values() if p.source == task.source), None)
        if program is None or not program.completes_on_note:
            return
        if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
            return
        task.status = TaskStatus.COMPLETED
        await self.task.session.flush()

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
            # allow_unclaimed: journal notes are append-only observability;
            # a claim-less assignee (read-only roles like the auditor) must
            # still be able to record them. See _active_claim_violation.
            if reject := await self._verify_explicit_task_ownership(
                agent_id, task_id, allow_unclaimed=True
            ):
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
            # allow_unclaimed: same rationale as the journal-note path —
            # section writes are append-only, and a claim-less assignee
            # (auditor) must still record its section.
            if reject := await self._verify_explicit_task_ownership(
                agent_id, task_id, allow_unclaimed=True
            ):
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

    async def _resolve_task_ref(self, task_ref: str) -> UUID | Envelope:
        """Resolve a task reference to a full UUID, or return an error Envelope.

        Agents are shown short ids everywhere (task cards, prompts, subjects
        render ``#372eac39`` style) and pass them back to the DO tools. A
        strict ``task_id: UUID`` schema 422s those before any handler runs -
        the failed-evidence loop that wedged be-qa's review on 2026-09-28
        (795 evidence errors, verdict never fired) was exactly this. Full
        UUIDs pass through; a hex prefix of at least 8 chars resolves by
        lookup; ambiguity and misses return the error Envelope the caller
        must propagate.
        """
        ref = str(task_ref).strip()
        try:
            return UUID(ref)
        except ValueError:
            pass
        hex_prefix = ref.removeprefix("#").strip()
        if len(hex_prefix) < _MIN_TASK_REF_HEX_CHARS or any(
            c not in "0123456789abcdefABCDEF" for c in hex_prefix
        ):
            return Envelope.incomplete_input(
                missing=["task_id"],
                field_hints={
                    "task_id": (
                        "pass the full UUID or the 8+ char hex prefix shown "
                        "on the task card"
                    )
                },
                remediate="retry with the full task UUID from the envelope",
            )
        from sqlalchemy import String, select

        from roboco.db.tables import TaskTable

        result = await self.task.session.execute(
            select(TaskTable.id).where(
                TaskTable.id.cast(String).ilike(f"{hex_prefix}%")
            )
        )
        matches = [row[0] for row in result.all()]
        if not matches:
            return Envelope.not_found(message=f"no task matches {task_ref!r}")
        if len(matches) > 1:
            return Envelope.incomplete_input(
                missing=["task_id"],
                field_hints={
                    "task_id": (
                        f"{task_ref!r} is ambiguous: {len(matches)} tasks "
                        "share this prefix - pass more characters or the "
                        "full UUID"
                    )
                },
                remediate="retry with the full task UUID from the envelope",
            )
        return cast("UUID", matches[0])

    async def _evidence_task_or_error(
        self, task_id: UUID | str, agent_id: UUID
    ) -> TaskTable | Envelope:
        """Resolve the evidence target and run its read-authorization checks.

        Short-id resolution (``_resolve_task_ref``), existence, and the
        ownership rule: reads are allowed for the assignee, an unassigned
        task, or a caller ``_caller_may_read_evidence`` admits - strict
        ownership only blocks snooping an unrelated, actively-owned task.
        Returns the TaskTable or the error Envelope to propagate.
        """
        resolved = await self._resolve_task_ref(str(task_id))
        if isinstance(resolved, Envelope):
            return resolved
        task_id = resolved
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        if (
            t.assigned_to is not None
            and t.assigned_to != agent_id
            and not await self._caller_may_read_evidence(agent_id, t)
        ):
            return _ownership_violation(task_id)
        return cast("TaskTable", t)

    async def _caller_may_read_evidence(self, agent_id: UUID, t: Any) -> bool:
        """Who besides the assignee may read ``t``'s evidence.

        A board co-reviewer of a shared coordination task, a caller whose own
        work depends on it, the read-only observer (the auditor's brief is to
        "read everything"), and the owner of any ancestor: the PM coordinating
        the subtree, or the reviewer holding the assembled parent PR. The PM
        and auditor prompts direct exactly these reads, yet only the first two
        were admitted (live 2026-10-28: 41 evidence calls refused in under two
        hours, the auditor's and the PMs' own subtree among them).
        """
        return (
            await self._board_may_co_review(agent_id, t)
            or await self._is_caller_dependency(agent_id, t)
            or await self._caller_is_claimless_role(agent_id)
            or await self._caller_owns_an_ancestor(agent_id, t)
        )

    _ANCESTOR_WALK_LIMIT = 8

    async def _caller_owns_an_ancestor(self, agent_id: UUID, t: Any) -> bool:
        """True when ``agent_id`` is assigned a task above ``t`` in its tree."""
        parent_id = getattr(t, "parent_task_id", None)
        for _ in range(self._ANCESTOR_WALK_LIMIT):
            if parent_id is None:
                return False
            parent = await self.task.get(parent_id)
            if parent is None:
                return False
            if parent.assigned_to == agent_id:
                return True
            parent_id = parent.parent_task_id
        return False

    async def evidence(
        self,
        *,
        agent_id: UUID,
        task_id: UUID | str,
    ) -> Envelope:
        """Inspect a task's PR diff, commits, files.

        Fetches the dev branch into the agent's workspace before diffing.
        Allows inspection when the caller is the assignee, the task is
        unassigned, the caller co-reviews a shared board task, the task is a
        dependency the caller is waiting on, the caller is the read-only
        observer (auditor), or the caller owns an ancestor of the task —
        strict ownership only blocks snooping an unrelated, actively-owned
        task.

        ``task_id`` accepts the full UUID or the 8+ char hex prefix the task
        cards display (``#372eac39`` style) - see ``_resolve_task_ref``.

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
        task_or_error = await self._evidence_task_or_error(task_id, agent_id)
        if isinstance(task_or_error, Envelope):
            return task_or_error
        t = task_or_error
        task_id = cast("UUID", t.id)
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

    async def _sandbox_tests_nebius_key(self) -> tuple[str | None, Envelope | None]:
        """run_sandbox_tests' key guard: the stored Nebius API key (Fernet-
        decrypted server-side), or a clean invalid_state rejection. The
        Sandboxes API authenticates with the SAME key as the inference
        provider - no second credential exists."""
        from roboco.models.base import ModelProvider
        from roboco.services.provider import get_provider_service
        from roboco.utils.converters import require_uuid

        provider_svc = get_provider_service(self.task.session)
        providers = await provider_svc.list_providers(include_disabled=True)
        nebius = next((p for p in providers if p.type == ModelProvider.NEBIUS), None)
        if nebius is None or not nebius.auth_token_encrypted:
            return None, Envelope.invalid_state(
                message="no Nebius API key is saved - the Sandboxes API "
                "authenticates with it",
                remediate=(
                    "save the Nebius (Token Factory) API key in the panel's "
                    "Settings -> AI Routing first"
                ),
                context_briefing={},
            )
        api_key = await provider_svc.get_decrypted_token(require_uuid(nebius.id))
        if not api_key:
            return None, Envelope.invalid_state(
                message="the stored Nebius key could not be decrypted",
                remediate="re-save the Nebius API key in the panel",
                context_briefing={},
            )
        return api_key, None

    @staticmethod
    async def _git_capture(root: Path, *args: str) -> tuple[bool, str, str]:
        """One git invocation in workspace ``root`` as the workspace git user;
        (ok, stdout, stderr). A refused clone config reports as a failure."""
        try:
            result = await asyncio.to_thread(
                run_workspace_git,
                root,
                list(args),
                timeout=settings.git_command_timeout_seconds,
                check=False,
            )
        except (UnsafeWorkspaceGitConfigError, subprocess.SubprocessError) as exc:
            return False, "", str(exc)[:300]
        return (
            result.returncode == 0,
            result.stdout.strip(),
            result.stderr.strip()[:300],
        )

    @staticmethod
    async def _git_archive_workspace(
        root: Path, archive: Path, ref: str = "HEAD"
    ) -> tuple[bool, str]:
        """One ``git archive`` attempt of the committed ``ref``; (ok, stderr).

        git runs as the workspace git user writing to the descriptor this
        process opened on ``archive`` (a root-only mkstemp file)."""
        try:
            return await asyncio.to_thread(archive_workspace_ref, root, ref, archive)
        except (
            UnsafeWorkspaceGitConfigError,
            OSError,
            subprocess.SubprocessError,
        ) as exc:
            return False, str(exc)[:300]

    async def _sandbox_tests_ref(
        self, agent_id: UUID, root: Path, branch: str | None
    ) -> tuple[Path, str, Envelope | None]:
        """(archive root, ref). A developer's worktree sits on the task
        branch, so HEAD is right. QA's clone root is never moved onto it
        (claims leave HEAD alone), so when HEAD is on a different branch the
        branch is fetched through the workspace service (project token,
        agent ownership) and FETCH_HEAD of the clone it fetched into is
        archived. No branch: HEAD."""
        if not branch:
            return root, "HEAD", None
        ok, head, _ = await ContentActions._git_capture(
            root, "rev-parse", "--abbrev-ref", "HEAD"
        )
        if ok and head == branch:
            return root, "HEAD", None
        fetched: Path | None = None
        err = ""
        try:
            fetched = await self.workspace.fetch_branch_for_inspection(
                agent_id=agent_id, branch_name=branch
            )
            # The helper only logs a non-zero fetch, so confirm FETCH_HEAD
            # names this branch (a failed fetch leaves a stale one behind).
            ok, fh, err = await ContentActions._git_capture(
                fetched, "rev-parse", "--git-path", "FETCH_HEAD"
            )
            if ok:
                fh_path = Path(fh) if Path(fh).is_absolute() else fetched / fh
                ok = f"branch '{branch}' of" in fh_path.read_text()
                err = err or "fetch did not update FETCH_HEAD"
        except Exception as exc:
            ok, err = False, str(exc)
        if not ok or fetched is None:
            return (
                root,
                "HEAD",
                Envelope.invalid_state(
                    message=f"could not fetch task branch {branch} from origin: {err}",
                    remediate=(
                        "the developer must push the branch (i_am_done pushes it); "
                        "retry run_sandbox_tests once it is on origin"
                    ),
                    context_briefing={},
                ),
            )
        return fetched, "FETCH_HEAD", None

    async def _sandbox_tests_root(
        self, agent_id: UUID, agent_slug: str, task: Any, project: Any
    ) -> tuple[Path | None, Envelope | None]:
        """run_sandbox_tests' workspace resolution: the per-task worktree
        when one exists on disk, else the clone root (mirroring
        _render_dev_source's choice). An unresolved project row or agent
        team rejects here too."""
        from roboco.services.workspace import WorkspaceError

        if project is None:
            return None, Envelope.invalid_state(
                message="the task's project could not be resolved",
                remediate="retry; escalate to your PM if it persists",
                context_briefing={},
            )
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
                remediate=(
                    "retry run_sandbox_tests; escalate to your PM if it persists"
                ),
                context_briefing={},
            )
        root = worktree if worktree.exists() else clone_root
        if not root.exists():
            return None, Envelope.invalid_state(
                message="no workspace found on disk for this task",
                remediate=(
                    "call give_me_work() to provision the clone first, then "
                    "retry run_sandbox_tests"
                ),
                context_briefing={},
            )
        return root, None

    @staticmethod
    async def _sandbox_tests_archive(
        root: Path, ref: str = "HEAD"
    ) -> tuple[Path | None, Envelope | None]:
        """Archive the committed ``ref`` of ``root`` into a random-named temp
        tarball (mkstemp: unpredictable on the shared orchestrator host,
        never cross-task colliding) and enforce the upload ceiling; the
        caller owns unlinking the path."""
        fd, archive_name = tempfile.mkstemp(prefix="roboco-sandbox-", suffix=".tar.gz")
        os.close(fd)
        archive = Path(archive_name)
        ok, stderr = await ContentActions._git_archive_workspace(root, archive, ref)
        if not ok:
            with contextlib.suppress(OSError):
                archive.unlink(missing_ok=True)
            return None, Envelope.invalid_state(
                message=f"git archive of the workspace failed: {stderr}",
                remediate=(
                    "commit your work first (an empty or unborn HEAD cannot "
                    "be archived), then retry run_sandbox_tests"
                ),
                context_briefing={},
            )
        size = archive.stat().st_size
        if size > settings.token_factory_sandboxes_max_archive_bytes:
            with contextlib.suppress(OSError):
                archive.unlink(missing_ok=True)
            return None, Envelope.invalid_state(
                message=(
                    f"workspace archive is {size} bytes, above the "
                    f"{settings.token_factory_sandboxes_max_archive_bytes} "
                    "upload ceiling"
                ),
                remediate=(
                    "prune large artifacts from the repo or raise "
                    "ROBOCO_TOKEN_FACTORY_SANDBOXES_MAX_ARCHIVE_BYTES"
                ),
                context_briefing={},
            )
        return archive, None

    async def _sandbox_tests_guards(
        self, agent_id: UUID
    ) -> tuple[tuple[Any, str] | None, Envelope | None]:
        """run_sandbox_tests' prelude guards, in order: flag off; no
        claimed/active project-bound task; no stored Nebius key. Returns
        ((task, api_key), None) or (None, rejection) - the pairing is total,
        so the caller needs a single None check."""
        if not settings.token_factory_sandboxes_enabled:
            return None, Envelope.invalid_state(
                message="Token Factory Sandboxes are disabled",
                remediate=(
                    "ROBOCO_TOKEN_FACTORY_SANDBOXES_ENABLED is off - ask the "
                    "CEO to arm it (Feature Flags card); until then run "
                    "tests in your own container"
                ),
                context_briefing={},
            )
        t, rejection = await self._sandbox_active_task(agent_id)
        if rejection is not None:
            return None, rejection
        api_key, rejection = await self._sandbox_tests_nebius_key()
        if rejection is not None:
            return None, rejection
        # The key helper's contract: a non-rejection return is a usable str.
        return (t, cast("str", api_key)), None

    async def run_sandbox_tests(
        self,
        *,
        agent_id: UUID,
        command: str,
        image: str | None = None,
        timeout_seconds: int | None = None,
    ) -> Envelope:
        """Run the caller's test command inside a Token Factory Sandbox
        microVM (dev + QA only, see role_config; default-off flag).

        The hackathon-track workload: the "run and test code" leg of the
        agent loop executes in Nebius's ephemeral Sandboxes service instead
        of the agent container's own shell. The committed workspace HEAD is
        git-archived server-side, uploaded to the Sandboxes API, extracted
        inside a disposable microVM prebuilt from the requested image, and
        ``command`` runs at the archive root; the microVM is destroyed after
        the run. Nothing about the LOCAL path changes: the flag off, a
        missing key, or any sandbox failure degrades to an envelope
        rejection and dev/QA keep their container shells.

        Guards, in order (the first three live in ``_sandbox_tests_guards``):
        flag off; no claimed/active project-bound task; no stored Nebius
        key (the Sandboxes API shares the inference key); then workspace/
        project unresolvable, ``git archive`` failing, or the archive over
        the upload ceiling. The
        run's outcome rides in ``evidence`` (exit code, output tails, metered
        cost/duration) and a journal entry records it; the caller's task is
        heartbeated before and after the wait so a long suite never reads as
        a stale claim.
        """
        pair, rejection = await self._sandbox_tests_guards(agent_id)
        if pair is None:
            return rejection or Envelope.invalid_state(
                message="run_sandbox_tests preconditions failed",
                remediate="retry run_sandbox_tests",
                context_briefing={},
            )
        t, api_key = pair
        from roboco.agents_config import _resolve_to_slug
        from roboco.services.project import get_project_service

        agent_slug = _resolve_to_slug(str(agent_id))
        project = await get_project_service(self.task.session).get(t.project_id)
        root, rejection = await self._sandbox_tests_root(
            agent_id, agent_slug, t, project
        )
        if rejection is not None or root is None:
            return rejection or Envelope.invalid_state(
                message="workspace could not be resolved",
                remediate="retry run_sandbox_tests",
                context_briefing={},
            )
        root, ref, rejection = await self._sandbox_tests_ref(
            agent_id, root, getattr(t, "branch_name", None)
        )
        if rejection is not None:
            return rejection
        archive, rejection = await self._sandbox_tests_archive(root, ref)
        if rejection is not None or archive is None:
            return rejection or Envelope.invalid_state(
                message="workspace archive could not be created",
                remediate="retry run_sandbox_tests",
                context_briefing={},
            )
        await self._touch_heartbeat(t.id)
        try:
            result, error = await run_sandbox_command(
                api_key,
                command=command,
                image=image,
                archive_path=archive,
                timeout_seconds=timeout_seconds,
            )
        finally:
            with contextlib.suppress(OSError):
                archive.unlink(missing_ok=True)
        await self._touch_heartbeat(t.id)
        return await self._sandbox_tests_envelope(
            t, agent_id, command, image, result=result, error=error
        )

    async def _sandbox_tests_envelope(
        self,
        t: Any,
        agent_id: UUID,
        command: str,
        image: str | None,
        *,
        result: dict[str, Any] | None,
        error: str | None,
    ) -> Envelope:
        """run_sandbox_tests' outcome half: a run failure is a retryable
        invalid_state; a completed run journals (best-effort - the run
        already happened and must reach the agent even if the write fails,
        hence suppressed, unlike `note`) and returns the evidence payload
        with a next hint that points back into the QA flow."""
        if error is not None or result is None:
            return Envelope.invalid_state(
                message=f"sandbox test run failed: {error}",
                remediate=(
                    "retry run_sandbox_tests; if it keeps failing run the "
                    "suite in your own container and escalate the sandbox "
                    "error to your PM"
                ),
                context_briefing={},
            )
        exit_code = result.get("exit_code")
        outcome = "PASS" if exit_code == 0 else "FAIL"
        stdout_tail = tail_for_evidence(result.get("stdout", ""))
        stderr_tail = tail_for_evidence(result.get("stderr", ""))
        with contextlib.suppress(Exception):
            await self.journal.write_entry(
                agent_id=agent_id,
                task_id=t.id,
                scope="note",
                title=f"Token Factory sandbox test run: {outcome} (exit {exit_code})",
                content=(
                    f"ran in a Token Factory Sandbox microVM "
                    f"(image={image or settings.token_factory_sandboxes_image}, "
                    f"cost={result.get('cost')}, "
                    f"elapsed={result.get('elapsed_time')}s)\n\n"
                    f"command: {command}\n\n"
                    f"stdout (tail):\n{stdout_tail}\n\n"
                    f"stderr (tail):\n{stderr_tail}"
                ),
            )
        return Envelope.ok(
            status=str(t.status),
            task_id=str(t.id),
            next=(
                "tests passed in the Token Factory sandbox - continue the QA "
                "flow (pr_pass)"
                if exit_code == 0
                else "tests failed in the Token Factory sandbox - record "
                "findings via fail_review"
            ),
            evidence={
                "sandbox": {
                    "exit_code": exit_code,
                    "timed_out": result.get("timed_out"),
                    "image": image or settings.token_factory_sandboxes_image,
                    "stdout_tail": stdout_tail,
                    "stderr_tail": stderr_tail,
                    "cost": result.get("cost"),
                    "elapsed_time": result.get("elapsed_time"),
                    "instance_uuid": result.get("instance_uuid"),
                    "operation_uuid": result.get("operation_uuid"),
                }
            },
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
        ok, sha, _err = await ContentActions._git_capture(workspace, "rev-parse", ref)
        return sha if ok and sha else None

    @classmethod
    async def _render_git_head_and_dirty(
        cls, workspace: Path
    ) -> tuple[str | None, bool]:
        """Best-effort ``(HEAD sha, has-uncommitted-changes)`` for a dev's own
        working tree. ``(None, False)`` on any failure."""
        head = await cls._render_git_rev(workspace, "HEAD")
        if head is None:
            return None, False
        ok, out, _err = await cls._git_capture(workspace, "status", "--porcelain")
        return head, bool(out) if ok else False

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

    async def _progress_precheck(
        self, message: str, task_id: UUID | str
    ) -> Envelope | UUID:
        """The two early guards (soup check + short-ref resolution) folded
        into one preflight so the verb body stays under the return bound.
        Returns the rejection Envelope or the resolved task UUID."""
        if rej := self._reject_soup(message, field="progress update", min_chars=5):
            return rej
        return await self._resolve_task_ref(str(task_id))

    async def _pr_update_precheck(
        self,
        title: str | None,
        body: str | None,
        reviewers: list[str] | None,
        task_id: UUID | str,
    ) -> Envelope | UUID:
        """The two early guards (input check + short-ref resolution) folded
        into one preflight so the verb body stays under the return bound.
        Returns the rejection Envelope or the resolved task UUID."""
        if rej := self._pr_update_input_check(title, body, reviewers):
            return rej
        return await self._resolve_task_ref(str(task_id))

    async def progress(
        self,
        *,
        agent_id: UUID,
        task_id: UUID | str,
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
        guard = await self._progress_precheck(message, task_id)
        if isinstance(guard, Envelope):
            return guard
        task_id = guard
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
        task_id: UUID | str,
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
        guard = await self._pr_update_precheck(title, body, reviewers, task_id)
        if isinstance(guard, Envelope):
            return guard
        task_id = guard
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

    async def _task_time_root(self, task: Any, task_id: UUID) -> tuple[UUID, datetime]:
        """Walk ``parent_task_id`` up to the root, bounded so a bad chain
        (cycle, orphaned parent) can't hang the call."""
        root_id, root = task_id, task
        for _ in range(10):
            if root.parent_task_id is None:
                break
            parent = await self.task.get(root.parent_task_id)
            if parent is None:
                break
            root_id, root = root.parent_task_id, parent
        return root_id, root.created_at

    async def task_time(self, *, agent_id: UUID, task_id: UUID | str) -> Envelope:
        """Real, uptime-adjusted elapsed times for a task.

        Read-only telemetry, no ownership check, any allowed role may call
        it for any task. Every "stale / stuck / blocked too long /
        unattended" judgement in the fleet defaults to naive wall-clock
        elapsed time, which counts the hours the CEO had the stack shut
        down or dispatch paused as neglect. This returns both: ``wall_seconds``
        (naive) and ``active_seconds`` (fleet-uptime-adjusted, via
        ``UptimeLedger``), plus the downtime windows that explain the gap.

        ``task_id`` accepts the full UUID or the 8+ char hex prefix the task
        cards display - see ``_resolve_task_ref``.
        """
        # Read-only telemetry: no ownership check.
        del agent_id
        resolved = await self._resolve_task_ref(str(task_id))
        if isinstance(resolved, Envelope):
            return resolved
        task_id = resolved
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")

        root_task_id, root_created_at = await self._task_time_root(t, task_id)
        now = datetime.now(UTC)
        ledger = await UptimeLedger.load(
            self.task.session, since=min(t.created_at, root_created_at)
        )

        timestamps = {
            "age": t.created_at,
            "root_age": root_created_at,
            "in_state": t.updated_at,
            "since_claim": t.claimed_at,
            "since_heartbeat": t.last_heartbeat_at,
            "stalled": t.stalled_since,
        }
        wall_seconds, active_seconds = _task_time_seconds(
            timestamps, now=now, ledger=ledger
        )
        windows = ledger.downtime_windows(t.created_at, now)

        evidence = {
            "task_id": str(task_id),
            "status": str(t.status),
            "root_task_id": str(root_task_id),
            "root_created_at": _iso(root_created_at),
            "created_at": _iso(t.created_at),
            "claimed_at": _iso(t.claimed_at),
            "updated_at": _iso(t.updated_at),
            "last_heartbeat_at": _iso(t.last_heartbeat_at),
            "stalled_since": _iso(t.stalled_since),
            "wall_seconds": wall_seconds,
            "active_seconds": active_seconds,
            "fleet_downtime_seconds": ledger.downtime(
                t.created_at, now
            ).total_seconds(),
            "downtime_windows": [
                {"start": _iso(w.start), "end": _iso(w.end), "kind": w.kind}
                for w in windows
            ],
        }
        return Envelope.ok(
            status="measured",
            task_id=str(task_id),
            next=(
                "cite active_seconds, not wall_seconds, when you call a "
                "task stale or unattended"
            ),
            evidence=evidence,
            context_briefing={},
        )

    async def preflight_diff(self, *, agent_id: UUID, task_id: UUID | str) -> Envelope:
        """Decisions agent lane 6.4 (spec): the pre-submit self-check.

        The Choreographer composes the whole state server-side from the task
        record plus the working diff in the agent's own workspace, so the
        verb cannot be used to smuggle arbitrary text past the budget or
        the audit. Advisory by design: the agent decides; it never blocks
        or waives ``i_am_done`` (self-review exclusion, spec 5).
        """
        resolved = await self._resolve_task_ref(str(task_id))
        if isinstance(resolved, Envelope):
            return resolved
        task_id = resolved
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        violation = _preflight_ownership_violation(t, agent_id, task_id)
        if violation is not None:
            return violation
        criteria = _preflight_criteria(t)
        if not criteria:
            return Envelope.invalid_state(
                message="the task has no acceptance criteria to pre-flight",
                remediate="ask your PM to record acceptance criteria on the task",
            )

        # Release the request transaction before the git work (pool-exhaustion
        # guard, mirrors evidence()).
        await self._release_task_session_before_git()

        diff = await self._preflight_working_diff(t, agent_id, task_id)
        if not diff.strip():
            return Envelope.invalid_state(
                message="no working diff found on the task branch",
                remediate=(
                    "commit something to the task branch first, or use "
                    "evidence to inspect state"
                ),
            )

        return await self._preflight_verdict_envelope(task_id, criteria, diff)

    async def _release_task_session_before_git(self) -> None:
        """Commit (or roll back) the request transaction so the git work
        below runs off the request's DB session (pool-exhaustion guard)."""
        from sqlalchemy.exc import PendingRollbackError

        try:
            await self.task.session.commit()
        except PendingRollbackError:
            await self.task.session.rollback()

    async def _preflight_working_diff(
        self, t: Any, agent_id: UUID, task_id: UUID
    ) -> str:
        """The working diff on the task branch; empty when the branch is
        unset or the diff cannot be read."""
        if not t.branch_name:
            return ""
        diff: str
        try:
            diff, _files = await self.git.diff_and_files(
                branch_name=t.branch_name, actor_agent_id=agent_id
            )
        except Exception as exc:
            logger.warning(
                "preflight_diff could not read the working diff",
                task_id=str(task_id),
                error=str(exc),
            )
            return ""
        return diff

    async def _preflight_verdict_envelope(
        self, task_id: UUID, criteria: list[str], diff: str
    ) -> Envelope:
        """Run the decisions preflight screen and wrap its verdict into the
        advisory envelope (no_verdict pass-through when unavailable)."""
        from roboco.services import decisions

        result = await decisions.preflight_diff(
            self.task.session,
            task_id=str(task_id),
            criteria=criteria,
            diff=diff,
        )
        if result is None:
            return Envelope.ok(
                status="no_verdict",
                task_id=str(task_id),
                next=(
                    "decisions unavailable: proceed exactly as usual "
                    "(run your own check before i_am_done)"
                ),
                evidence={"advisory": True, "criteria": criteria},
                context_briefing={},
            )
        low = [c["criterion"] for c in result["criteria"] if not c.get("addresses")]
        hints: list[str] = []
        if low:
            hints.append(
                "low-confidence criteria: re-check before i_am_done -> "
                + "; ".join(low[:3])
            )
        if (result.get("hygiene") or {}).get("flagged"):
            hints.append(
                "hygiene flagged: sweep the diff for debug leftovers, "
                "conflict markers, hardcoded secrets, or TODO stubs"
            )
        return Envelope.ok(
            status="preflight",
            task_id=str(task_id),
            next=" | ".join(hints) or "all criteria plausible: submit when ready",
            evidence={
                "advisory": True,
                "verdicts": result,
                "tier": "see orchestrator decision log",
            },
            context_briefing={},
        )

    async def triage_failure(
        self,
        *,
        agent_id: UUID,
        task_id: UUID | str,
        test_name: str,
        error_excerpt: str = "",
    ) -> Envelope:
        """Decisions agent lane 6.5 (spec): red-test triage.

        The agent sends only the test name and the error excerpt; the
        orchestrator composes the rest of the state server-side. The lane
        is advisory: a ``flaky`` verdict is a hint the agent may cite in
        its submission evidence, never a waiver; below the confidence floor
        the envelope says ``unknown`` and the agent proceeds exactly as
        today (debug first).
        """
        resolved = await self._resolve_task_ref(str(task_id))
        if isinstance(resolved, Envelope):
            return resolved
        task_id = resolved
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        from roboco.services import decisions

        changed_files: list[str] = []
        if t.branch_name:
            try:
                _diff, changed_files = await self.git.diff_and_files(
                    branch_name=t.branch_name, actor_agent_id=agent_id
                )
            except Exception as exc:
                logger.warning(
                    "triage_failure could not read changed files",
                    task_id=str(task_id),
                    error=str(exc),
                )
                changed_files = []
        is_retry = str(t.status) in ("needs_revision", "verifying")
        lane = await decisions.triage_failure(
            self.task.session,
            task_id=str(task_id),
            test_name=test_name,
            error_excerpt=error_excerpt,
            changed_files_in_diff=changed_files[:50],
            is_retry=is_retry,
            recent_flake_history_for_test=[],
        )
        if lane is decisions.TriageLane.UNKNOWN:
            return Envelope.ok(
                status="unknown",
                task_id=str(task_id),
                next=(
                    "no confident triage: debug the failure first, exactly "
                    "as you would without this verb"
                ),
                evidence={"advisory": True, "test_name": test_name},
                context_briefing={},
            )
        next_by_lane = {
            decisions.TriageLane.MY_REGRESSION: (
                "likely your regression: fix it on this branch before submitting"
            ),
            decisions.TriageLane.FLAKY: (
                "likely a flake: you may cite this verdict in your submission "
                "evidence; it is a hint, not a waiver - QA can still fail the "
                "review on the same test"
            ),
            decisions.TriageLane.ENVIRONMENT: (
                "likely environment: consider a rerun or note the runner "
                "failure in your submission evidence"
            ),
        }
        return Envelope.ok(
            status=lane.value,
            task_id=str(task_id),
            next=next_by_lane[lane],
            evidence={
                "advisory": True,
                "test_name": test_name,
                "changed_files_in_diff": changed_files[:50],
                "is_retry": is_retry,
            },
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
