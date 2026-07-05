"""Smart-wrapped content tools — commit, note, dm, read_a2a, evidence.

Each method:
1. Validates input (e.g., commit_validator for commit messages).
2. Auto-injects task_id when the agent has an active claim and the param is missing.
3. Calls the underlying service.
4. Returns a standardized Envelope.

Pure orchestration; no DB writes outside what the underlying services do.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

import structlog

from roboco.config import settings
from roboco.exceptions import GitError
from roboco.foundation.policy import communications as _comms
from roboco.foundation.policy.content import ContentValidationError, markers
from roboco.foundation.policy.content.validators import reject_trivial
from roboco.foundation.policy.journaling import Scope as _Scope
from roboco.services.content_notes import content_type_for_role
from roboco.services.gateway.commit_validator import validate_commit_message
from roboco.services.gateway.envelope import Envelope
from roboco.services.gateway.evidence_builder import build_evidence_for_task
from roboco.services.x_client import MAX_TWEET_CHARS

if TYPE_CHECKING:
    from uuid import UUID

    from roboco.foundation.identity import Team


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

# Roles with NO agent-comms surface (CLAUDE.md): auditor (silent observer),
# pr_reviewer (posts review findings on the PR itself — no dm), prompter
# and secretary (human-only, restricted to note + evidence — no dm/notify).
# The spawn manifest already omits dm from these roles' tool surfaces, but
# that is convention-only — this frozenset is the handler-level defence-in-depth
# that refuses any call that bypassed the manifest (direct verb dispatch, test
# harness, future routing change), so the no-comms invariant holds regardless of
# how the call arrived. Matches the explicit role-frozenset gates on commit /
# notify / pitch / playbook.
_NO_COMMS_ROLES: frozenset[str] = frozenset(
    {"auditor", "pr_reviewer", "prompter", "secretary"}
)


def _no_comms_remediate(role: str) -> str:
    """Role-appropriate remediation for a no-comms role blocked at dm."""
    if role == "auditor":
        return "record observations via note(scope='reflect') instead"
    if role == "pr_reviewer":
        return "post review findings on the PR itself via pr_pass/pr_fail instead"
    # prompter / secretary are human-only (note + evidence).
    return "use note() to record; this human-only role has no agent-comms surface"


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


_VALID_NOTIFY_PRIORITIES: frozenset[str] = frozenset(p.value for p in _comms.Priority)
# The Board roles that may author a pitch (a product proposal for CEO approval).
_PITCH_ROLES: frozenset[str] = frozenset({"product_owner", "head_marketing"})

# Roadmap cycles are PO-authored in v1 — HoM stays a reviewer via the normal
# board gate when an approved item later ships as real work (see the roadmap
# spec's non-goals).
_ROADMAP_ROLES: frozenset[str] = frozenset({"product_owner"})

# Feature spotlights are HoM-authored — the Product Owner stays out of this
# cycle (mirrors _ROADMAP_ROLES's PO-only symmetry, reversed).
_FEATURE_SPOTLIGHT_ROLES: frozenset[str] = frozenset({"head_marketing"})

# Text fields on a roadmap item draft, with their anti-soup minimum length.
_ROADMAP_ITEM_TEXT_FIELDS: tuple[tuple[str, int], ...] = (
    ("title", 5),
    ("description", 15),
    ("project_slug", 2),
    ("team", 2),
    ("rationale", 8),
)

# Playbook curation RBAC: delivery roles DRAFT; only the Auditor CURATES.
_DRAFT_PLAYBOOK_ROLES: frozenset[str] = frozenset(
    {"developer", "qa", "documenter", "cell_pm", "main_pm"}
)
_CURATE_PLAYBOOK_ROLES: frozenset[str] = frozenset({"auditor"})

# propose_video's target-platform set + TikTok caption limit (the X caption
# reuses MAX_TWEET_CHARS). No role frozenset here, unlike the sets above:
# propose_video is gated on the caller's TEAM at runtime (_caller_team), not
# role — Role.DEVELOPER doesn't distinguish a ux-dev from a be-dev/fe-dev.
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
        subject = _strip_task_prefix(message).strip()
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
        commit_result = await self.git.commit(
            branch_name=t.branch_name,
            message=final_message,
            task_id=t.id,
            files=files,
        )
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
        return Envelope.ok(
            status="proposed",
            task_id=str(pitch.id),
            next="await the CEO's approval in the Pitches queue",
            context_briefing={},
        )

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
    def _reject_roadmap_item(cls, raw: Any, idx: int) -> Envelope | None:
        """Validate one raw roadmap item dict; None when clean."""
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
            if rej := self._reject_roadmap_item(raw, idx):
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
        return Envelope.ok(
            status="roadmap_proposed",
            task_id=str(task.id),
            next="i_am_idle() — the CEO reviews each item in the roadmap queue",
            context_briefing={
                "cycle_goal": cycle_goal.strip(),
                "item_count": len(normalized),
            },
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

    async def propose_feature_spotlight(
        self,
        *,
        agent_id: UUID,
        feature_slug: str,
        feature_title: str,
        body: str,
        wants_video: bool = False,
        video_script: str = "",
    ) -> Envelope:
        """Head of Marketing authors ONE feature-spotlight draft.

        Validates role, field lengths, the 280-char tweet limit, and that the
        feature hasn't already been covered, then materializes the held X-queue
        draft and completes the caller's exploration task. One call per cycle.

        ``wants_video`` optionally requests a companion video (gated on
        ``video_engine_enabled AND video_on_spotlight``, on top of this
        default-False param) — a best-effort side effect that never disturbs
        the spotlight draft above. Defaults leave the flow byte-for-byte
        unchanged.
        """
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
        video_armed = settings.video_engine_enabled and settings.video_on_spotlight
        if wants_video and video_armed:
            await self._open_spotlight_video(
                feature_slug, feature_title, body, video_script
            )
        return Envelope.ok(
            status="feature_spotlight_proposed",
            task_id=str(new_task.id),
            next="i_am_idle() — the CEO reviews the draft in the X post queue",
            context_briefing={
                "feature_slug": feature_slug,
                "feature_title": feature_title,
            },
        )

    async def _open_spotlight_video(
        self, feature_slug: str, feature_title: str, body: str, video_script: str
    ) -> None:
        """Best-effort: a spotlight video failure must never break the
        spotlight draft that already materialized above. HoM decides *what*
        (this brief); UX/UI later builds *how* (the composition)."""
        try:
            from roboco.services.video_engine import get_video_engine

            feature_brief = f"{feature_title}: {body}"
            await get_video_engine(self.task.session).open_video_task(
                occasion=f"spotlight {feature_slug}",
                script=video_script.strip() or feature_brief,
                platforms=["x", "tiktok"],
                brief=feature_brief,
            )
        except Exception as exc:
            logger.warning("spotlight video draft failed (best-effort)", error=str(exc))

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
        # Spec §5.5: silent / no-comms roles — defense-in-depth runtime guard.
        # Defense-in-depth: dm() is the channel through which a no-comms role
        # could "speak". Covers auditor,
        # pr_reviewer, and the human-only prompter / secretary.
        agent = await self.task.agent_for(agent_id)
        caller_role = str(agent.role) if agent is not None else ""
        if caller_role in _NO_COMMS_ROLES:
            return Envelope.not_authorized(
                message=(
                    f"role '{caller_role}' is a silent / no-comms role;"
                    " dm is not permitted"
                ),
                remediate=_no_comms_remediate(caller_role),
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
        if t.branch_name and t.work_session_id:
            await self.workspace.fetch_branch_for_inspection(
                agent_id=agent_id, branch_name=t.branch_name
            )
        diff = ""
        files_changed: list[str] = []
        if t.branch_name:
            diff = await self.git.diff(
                branch_name=t.branch_name, actor_agent_id=agent_id
            )
            files_changed = await self.git.list_changed_files(
                branch_name=t.branch_name, actor_agent_id=agent_id
            )
        journal_highlights = await self.evidence_repo.journal_highlights_for_task(
            task_id
        )
        ev = build_evidence_for_task(
            t,
            journal_highlights=journal_highlights,
            files_changed=files_changed,
            pr_diff_summary=diff,
        )
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next="continue",
            evidence=ev.as_dict(),
            context_briefing={},
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
        try:
            n = await self._deps.notification_delivery.get_for_recipient_and_mark_read(
                notification_id=notification_id,
                agent_id=agent_id,
            )
        except Exception:
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
