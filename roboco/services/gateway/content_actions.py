"""Smart-wrapped content tools — commit, note, say, dm, evidence.

Each method:
1. Validates input (e.g., commit_validator for commit messages).
2. Auto-injects task_id when the agent has an active claim and the param is missing.
3. Calls the underlying service.
4. Returns a standardized Envelope.

Pure orchestration; no DB writes outside what the underlying services do.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from roboco.foundation.policy import communications as _comms
from roboco.foundation.policy.journaling import Scope as _Scope
from roboco.services.gateway.commit_validator import validate_commit_message
from roboco.services.gateway.envelope import Envelope
from roboco.services.gateway.evidence_builder import build_evidence_for_task

if TYPE_CHECKING:
    from uuid import UUID


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


# Pre-gateway `DecisionLogInput.options` enforced `min_length=2`. Same here.
_MIN_DECISION_OPTIONS = 2


_DECISION_REQUIRED: tuple[tuple[str, str], ...] = (
    ("context", "What situation led to this decision"),
    ("options", "At least 2 alternatives considered as list[{name,pros,cons}]"),
    ("chosen", "Which option you took"),
    ("rationale", "Why this option (cite trade-offs)"),
)

_REFLECT_REQUIRED: tuple[tuple[str, str], ...] = (
    ("what_done", "Literal output: what shipped, where (file:line / commit)"),
    ("what_learned", "New info you didn't have before"),
    ("what_struggled", "Where you got stuck (even briefly)"),
)

_OPTIONS_HINT = (
    "options must be a list of at least 2 dicts with "
    "shape {name: str, pros: str, cons: str}"
)


def _options_field_missing(value: Any) -> bool:
    """True when decision.options is absent or under the minimum count."""
    return not isinstance(value, list) or len(value) < _MIN_DECISION_OPTIONS


def _scalar_field_missing(value: Any) -> bool:
    """True when a required scalar/text field is empty/whitespace."""
    return not value or not str(value).strip()


def _collect_required(
    required: tuple[tuple[str, str], ...], structured: dict[str, Any]
) -> tuple[list[str], dict[str, str]]:
    """Walk a (field, hint) table and collect missing fields with hints."""
    missing: list[str] = []
    hints: dict[str, str] = {}
    for field, hint in required:
        value = structured.get(field)
        if field == "options":
            if _options_field_missing(value):
                missing.append(field)
                hints[field] = _OPTIONS_HINT
            continue
        if _scalar_field_missing(value):
            missing.append(field)
            hints[field] = hint
    return missing, hints


_SCOPE_REQUIRED: dict[str, tuple[tuple[str, str], ...]] = {
    "decision": _DECISION_REQUIRED,
    "reflect": _REFLECT_REQUIRED,
}


def _check_scope_required_fields(
    scope: str, structured: dict[str, Any]
) -> tuple[list[str], dict[str, str]]:
    """Pre-gateway parity: decision/reflect scopes required structured fields."""
    required = _SCOPE_REQUIRED.get(scope)
    if required is None:
        return [], {}
    return _collect_required(required, structured)


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
        result = validate_commit_message(subject)
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
        return Envelope.ok(
            status=str(t.status),
            task_id=str(t.id),
            next="continue committing, or open_pr when ready",
            context_briefing={},
        )

    async def _verify_explicit_task_ownership(
        self, agent_id: UUID, task_id: UUID
    ) -> Envelope | None:
        """Gate Set D: refuse content posts on tasks the caller does not own.

        Only call this for *explicit* task_id (caller passed it themselves).
        Auto-fill from get_active_task_for_agent is implicitly self-owned
        and does not need a re-check.

        Allows ``assigned_to=None`` (post-handoff transient state) so QA /
        documenter can still inspect tasks between reassignments.
        """
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        if t.assigned_to is not None and t.assigned_to != agent_id:
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

        ``structured`` carries scope-specific fields (pre-gateway parity):

        - decision: context, options[], chosen, rationale, consequences
        - reflect: what_done, what_learned, what_struggled, next_steps

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
            t = await self.task.get_active_task_for_agent(agent_id)
            if t is not None:
                task_id = t.id
        s = structured or {}
        # Pre-gateway parity: decision and reflect scopes had required fields.
        missing, hints = _check_scope_required_fields(scope, s)
        if missing:
            if scope == "decision":
                remediate = (
                    "re-issue note(scope='decision', ...) with these fields filled: "
                    f"{', '.join(missing)}.\n\nExample:\n"
                    "note(\n"
                    "  scope='decision',\n"
                    "  text='<one-line summary of the decision>',\n"
                    "  context='<the situation that led to it>',\n"
                    "  options=[\n"
                    "    {'name': 'optionA', 'pros': '<pros>', 'cons': '<cons>'},\n"
                    "    {'name': 'optionB', 'pros': '<pros>', 'cons': '<cons>'},\n"
                    "  ],\n"
                    "  chosen='<which option>',\n"
                    "  rationale='<why this option — cite trade-offs>',\n"
                    ")\n\n"
                    "Pre-gateway parity — these populate the panel's Decisions view."
                )
            else:
                remediate = (
                    "re-issue note(scope='reflect', ...) with these fields filled: "
                    f"{', '.join(missing)}.\n\nExample:\n"
                    "note(\n"
                    "  scope='reflect',\n"
                    "  text='<one-line summary of the reflection>',\n"
                    "  what_done='<what shipped, where (file:line / commit)>',\n"
                    "  what_learned='<new info you didn't have before>',\n"
                    "  what_struggled='<where you got stuck — even briefly>',\n"
                    "  next_steps=['<follow-up #1>', '<follow-up #2>'],\n"
                    ")\n\n"
                    "Pre-gateway parity — these populate the panel's Reflections view."
                )
            return Envelope.incomplete_input(
                missing=missing,
                field_hints=hints,
                remediate=remediate,
                context_briefing={},
            )
        title = (s.get("title") or text.split("\n", 1)[0])[:200]
        content = _render_journal_content(scope, text, s)
        await self.journal.write_entry(
            agent_id=agent_id,
            task_id=task_id,
            scope=scope,
            title=title,
            content=content,
        )
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
            t = await self.task.get_active_task_for_agent(agent_id)
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
            t = await self.task.get_active_task_for_agent(agent_id)
            if t is not None:
                task_id = t.id
        if task_id is None:
            return Envelope.invalid_state(
                message="dm requires a task_id (no active task and none provided)",
                remediate="provide task_id explicitly or claim a task first",
                context_briefing={},
            )
        await self.a2a.send(
            from_agent=agent_id,
            to_agent=recipient,
            task_id=task_id,
            body=text,
            skill=skill,
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
            t = await self.task.get_active_task_for_agent(agent_id)
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
        """
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        if t.assigned_to is not None and t.assigned_to != agent_id:
            return _ownership_violation(task_id)
        if t.branch_name and t.work_session_id:
            await self.workspace.fetch_branch_for_inspection(
                agent_id=agent_id, branch_name=t.branch_name
            )
        diff = ""
        if t.branch_name:
            base = "HEAD~1" if t.commits else None
            diff = await self.git.diff(branch_name=t.branch_name, base=base)
        ev = build_evidence_for_task(
            t,
            journal_highlights=[],
            files_changed=[],
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

    async def progress(
        self,
        *,
        agent_id: UUID,
        task_id: UUID,
        message: str,
        percentage: int,
    ) -> Envelope:
        """Append a narrative progress update (pre-gateway parity).

        Caller must be the task's assignee and the task must be in an
        active status — these are the same constraints the pre-gateway
        `roboco_task_progress` handler enforced.
        """
        active = {
            "in_progress",
            "verifying",
            "awaiting_qa",
            "awaiting_documentation",
        }
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        if t.assigned_to != agent_id:
            return _ownership_violation(task_id)
        if str(t.status) not in active:
            return Envelope.invalid_state(
                message=(
                    f"task is in {t.status!r}; progress updates only valid "
                    f"in active statuses ({sorted(active)})"
                ),
                remediate=(
                    "use evidence(task_id) to re-read state; if you're past "
                    "i_am_done, the run has moved on — call i_am_idle()"
                ),
                context_briefing={},
            )
        await self.task.add_progress(
            task_id=task_id,
            agent_id=agent_id,
            message=message,
            percentage=percentage,
        )
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next="continue",
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

        Pre-gateway parity for `roboco_session_create_for_tasks`. The
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
