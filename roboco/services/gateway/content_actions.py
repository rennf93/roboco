"""Smart-wrapped content tools — commit, note, say, dm, evidence.

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
from roboco.foundation.policy.journaling import Scope as _Scope
from roboco.services.gateway.commit_validator import validate_commit_message
from roboco.services.gateway.envelope import Envelope
from roboco.services.gateway.evidence_builder import build_evidence_for_task

if TYPE_CHECKING:
    from uuid import UUID


logger = structlog.get_logger()


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


# Narrative fields that decision/reflect scopes want filled. Issue #15: a
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

    Issue #15: stop rejecting thin notes. List-typed fields tolerate a lone
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
            "only the task's assignee may attach content (commit/note/say/"
            "dm/evidence) to it. Use a different task_id or omit task_id "
            "for off-task channel posts (say/dm only)."
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
    messaging: Any
    a2a: Any
    journal: Any
    workspace: Any
    notifications: Any
    # Wave 1 added inbox-read verbs (notify_list/get/ack) that live on
    # `NotificationDeliveryService`, not `NotificationService`. Keeping
    # them separate so the sender vs receiver concerns stay split.
    notification_delivery: Any = None
    # Task #154: evidence() returns journal_highlights for QA/reviewer
    # context. Matches the choreographer's EvidenceRepo wiring so both
    # paths surface the same shape.
    evidence_repo: Any = None


_VALID_NOTIFY_PRIORITIES: frozenset[str] = frozenset(p.value for p in _comms.Priority)


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
    def messaging(self) -> Any:
        return self._deps.messaging

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

    # Board roles co-review board/coordination tasks (cluster C5): a
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
        task is assigned to the other board member (cluster C5).
        """
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        if t.assigned_to is not None and t.assigned_to != agent_id:
            if await self._board_may_co_review(agent_id, t):
                return None
            return _ownership_violation(task_id)
        return None

    async def note(
        self,
        *,
        agent_id: UUID,
        text: str,
        scope: str = "note",
        task_id: UUID | None = None,
        structured: dict[str, Any] | None = None,
    ) -> Envelope:
        """Write a journal entry. scope ∈ note|decision|reflect|learning|struggle.

        ``structured`` carries scope-specific fields:

        - decision: context, options[], chosen, rationale, consequences
        - reflect: what_done, what_learned, what_struggled, next_steps

        Issue #15: the note is always recorded. List-typed fields tolerate a
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
        if scope not in _VALID_NOTE_SCOPES:
            return Envelope.invalid_state(
                message=f"invalid scope {scope!r}",
                remediate=f"scope must be one of: {sorted(_VALID_NOTE_SCOPES)}",
                context_briefing={},
            )
        if task_id is not None:
            if reject := await self._verify_explicit_task_ownership(agent_id, task_id):
                return reject
        else:
            t = await self.task.get_journal_context_task_for_agent(agent_id)
            if t is not None:
                task_id = t.id
        # Issue #15: tolerate thin notes instead of rejecting them. List-typed
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

    async def say(
        self,
        *,
        agent_id: UUID,
        channel: str,
        text: str,
        task_id: UUID | None = None,
    ) -> Envelope:
        """Post to a channel. task_id auto-injected if you have an active task.

        Channel-write RBAC is enforced inside `messaging.post_to_channel`
        (which forwards the agent's slug to `send_message` so
        `validate_channel_access` runs). A denial bubbles up as
        `ChannelAccessDeniedError`; we convert it into a friendly
        `not_authorized` Envelope listing the agent's writable channels.
        """
        from roboco.enforcement.channel_access import (
            ChannelAccessDeniedError,
            get_agent_channels,
        )

        # Spec §5.5: auditor is silent — defense-in-depth runtime guard.
        # The spawn manifest already omits `say` from the auditor's tool
        # surface, but that is convention-only. This guard refuses any
        # call that bypassed the manifest (direct verb dispatch, test
        # harness, future routing change) so the silent-observer rule
        # holds regardless of how the call arrived.
        agent = await self.task.agent_for(agent_id)
        if agent is not None and str(agent.role) == "auditor":
            return Envelope.not_authorized(
                message="auditor is a silent observer; say is not permitted",
                remediate="record observations via note(scope='reflect') instead",
                context_briefing={},
            )

        if task_id is not None:
            if reject := await self._verify_explicit_task_ownership(agent_id, task_id):
                return reject
        else:
            t = await self.task.get_journal_context_task_for_agent(agent_id)
            if t is not None:
                task_id = t.id
        try:
            await self.messaging.post_to_channel(
                agent_id=agent_id,
                channel_slug=channel,
                content=text,
                task_id=task_id,
            )
        except ChannelAccessDeniedError as e:
            writable = get_agent_channels(e.agent_id, action="write")
            writable_str = ", ".join(writable) if writable else "(none)"
            return Envelope.not_authorized(
                message=(
                    f"agent '{e.agent_id}' may not write to channel '{e.channel_slug}'"
                ),
                remediate=f"channels you may write to: {writable_str}",
                context_briefing={},
            )
        return Envelope.ok(
            status="posted",
            task_id=str(task_id) if task_id else None,
            next="continue",
            context_briefing={},
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
        # Spec §5.5: auditor is silent — defense-in-depth runtime guard.
        # See say() above for rationale. Mirrored here because dm() is
        # the other channel through which the auditor could "speak".
        agent = await self.task.agent_for(agent_id)
        if agent is not None and str(agent.role) == "auditor":
            return Envelope.not_authorized(
                message="auditor is a silent observer; dm is not permitted",
                remediate="record observations via note(scope='reflect') instead",
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
        # check (smoke-7: TypeError: unhashable type: 'dict').
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

        Distinct from `say` (channel post, no ack) and `dm` (informal A2A):
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
                    "use say() for channel posts or dm() for informal A2A. "
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

    async def evidence(
        self,
        *,
        agent_id: UUID,
        task_id: UUID,
    ) -> Envelope:
        """Inspect a task's PR diff, commits, files.

        Fetches dev branch into the agent's workspace before diffing.
        Allows inspection when caller is assignee OR task is unassigned
        (post-handoff transient state) — strict ownership only blocks
        cross-agent inspection of an actively-owned task.

        Task #154: ``files_changed`` and ``pr_diff_summary`` are pulled
        from git (against the branch's parent) — the authoritative source.
        Earlier versions hard-coded ``files_changed=[]`` and used
        ``HEAD~1`` for the diff base, so QA / reviewers saw an empty
        change list and only the latest commit's delta even when the PR
        on GitHub had a multi-commit change set.
        """
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        # A board co-reviewer (HoM inspecting a PO-assigned board task, or vice
        # versa) must be able to read the shared coordination task — the same
        # allowance the content verbs grant via co-review.
        if (
            t.assigned_to is not None
            and t.assigned_to != agent_id
            and not await self._board_may_co_review(agent_id, t)
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

    _SESSION_OPENER_ROLES: ClassVar[frozenset[str]] = frozenset(
        {"cell_pm", "main_pm", "product_owner", "head_marketing", "ceo"}
    )

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

        #173: pass ``plan_step`` (a sub_task id or 1-based order) as you
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

    async def open_session(
        self,
        *,
        agent_id: UUID,
        task_id: UUID,
        channel: str,
        topic: str,
        relationship_type: str = "discussion",
        group_id: UUID | None = None,
    ) -> Envelope:
        """PM-or-up creates a discussion session linked to a task.

        Backs the `open_session` do-verb. The
        underlying service de-duplicates: if an ancestor of this task
        already has a primary session in the same channel, it reuses
        that session instead of opening a new one.
        """
        from roboco.models.session import (
            SessionForTasksCreate,
            SessionTaskRelationshipType,
        )

        agent = await self.task.agent_for(agent_id)
        role_str = str(agent.role) if agent is not None else ""
        if role_str not in self._SESSION_OPENER_ROLES:
            return Envelope.not_authorized(
                message=(f"role {role_str!r} cannot open sessions; PM roles only"),
                remediate=(
                    "ask your PM to open_session for you, or escalate_up if "
                    "no session exists for the work you need to discuss"
                ),
                context_briefing={},
            )
        try:
            rel_type = SessionTaskRelationshipType(relationship_type)
        except ValueError:
            rel_type = SessionTaskRelationshipType.DISCUSSION
        req = SessionForTasksCreate(
            task_ids=[task_id],
            channel_slug=channel,
            relationship_type=rel_type,
            group_id=group_id,
        )
        # ``topic`` isn't part of SessionForTasksCreateRequest yet — pre-gateway
        # the session topic was implicit (group name). For now we attach it to
        # the envelope so the panel + journal can surface it; threading it
        # into the session row is Wave 2 work.
        _ = topic
        session, links = await self.messaging.create_session_for_tasks(
            req=req, pm_agent_id=agent_id
        )
        return Envelope.ok(
            status="session_open",
            task_id=str(task_id),
            next="continue",
            evidence={
                "session_id": str(session.id),
                "channel": channel,
                "topic": topic,
                "link_count": len(links),
            },
            context_briefing={},
        )

    async def link_session(
        self,
        *,
        agent_id: UUID,
        session_id: UUID,
        task_id: UUID,
        is_primary: bool = False,
        relationship_type: str = "discussion",
    ) -> Envelope:
        """Link an existing session to a task (idempotent).

        Caller must own the task — prevents cross-agent session-link spam.
        """
        from roboco.models.session import SessionTaskRelationshipType

        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        if t.assigned_to is not None and t.assigned_to != agent_id:
            return _ownership_violation(task_id)
        try:
            rel = SessionTaskRelationshipType(relationship_type)
        except ValueError:
            return Envelope.invalid_state(
                message=(f"invalid relationship_type {relationship_type!r}"),
                remediate=(
                    "use one of: discussion | planning | review | retrospective"
                ),
                context_briefing={},
            )
        link = await self.messaging.link_session_to_task(
            session_id=session_id,
            task_id=task_id,
            added_by=agent_id,
            is_primary=is_primary,
            relationship_type=rel,
        )
        return Envelope.ok(
            status="session_linked",
            task_id=str(task_id),
            next="continue",
            evidence={
                "session_id": str(session_id),
                "link_id": str(link.id),
                "is_primary": is_primary,
            },
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

    async def channels(self, *, agent_id: UUID) -> Envelope:
        """Return the channels this agent can read / write.

        Pre-gateway parity for ``roboco_channel_list``. Stops the
        invented-channel-slug pattern (e.g. ``backend-dev``, ``backend``)
        observed on smoke runs — the LLM sees the closed set in the
        response and can pattern-match valid slugs from it.
        """
        from roboco.enforcement.channel_access import get_agent_channels

        agent = await self.task.agent_for(agent_id)
        slug = getattr(agent, "slug", "") or ""
        if not slug:
            return Envelope.not_found(
                message=f"agent {agent_id} not in registry",
            )
        readable = sorted(get_agent_channels(slug, action="read"))
        writable = sorted(get_agent_channels(slug, action="write"))
        return Envelope.ok(
            status="ok",
            task_id=None,
            next="continue",
            evidence={
                "writable": writable,
                "readable": readable,
                "note": (
                    "Use the slug verbatim (no leading '#'). Inventing slugs "
                    "returns 'Channel not found'."
                ),
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

        Smoke-5 surfaced this gap: agents who needed to edit a PR's
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
        if title is None and body is None and reviewers is None:
            return Envelope.invalid_state(
                message="no fields to update",
                remediate=(
                    "provide at least one of title, body, or reviewers; "
                    "passing all None has no effect"
                ),
                context_briefing={},
            )
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


def _strip_task_prefix(msg: str) -> str:
    """Strip any [task-id] prefix the agent supplied; gateway re-adds canonical."""
    return _TASK_ID_PREFIX_RE.sub("", msg)
