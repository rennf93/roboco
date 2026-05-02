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
from typing import TYPE_CHECKING, Any

from roboco.services.gateway.commit_validator import validate_commit_message
from roboco.services.gateway.envelope import Envelope
from roboco.services.gateway.evidence_builder import build_evidence_for_task

if TYPE_CHECKING:
    from uuid import UUID


_VALID_NOTE_SCOPES: frozenset[str] = frozenset(
    {"note", "decision", "reflect", "learning", "struggle"}
)
_TASK_ID_PREFIX_RE = re.compile(r"^\s*\[[a-zA-Z0-9_-]+\]\s*")


@dataclass(frozen=True)
class ContentActionsDeps:
    """Service deps for ContentActions; bundled to keep init signature flat."""

    task: Any
    git: Any
    messaging: Any
    a2a: Any
    journal: Any
    workspace: Any


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
        commit_result = await self.git.commit(
            branch_name=t.branch_name,
            message=subject,
            task_id=t.id,
            files=files,
        )
        sha = commit_result.get("sha", "")
        await self.task.add_progress(t.id, agent_id, f"committed {sha[:8]}: {subject}")
        return Envelope.ok(
            status=str(t.status),
            task_id=str(t.id),
            next="continue, then i_have_committed or i_am_done",
            context_briefing={},
        )

    async def note(
        self,
        *,
        agent_id: UUID,
        text: str,
        scope: str = "note",
        task_id: UUID | None = None,
    ) -> Envelope:
        """Write a journal entry. scope ∈ note|decision|reflect|learning|struggle."""
        if scope not in _VALID_NOTE_SCOPES:
            return Envelope.invalid_state(
                message=f"invalid scope {scope!r}",
                remediate=f"scope must be one of: {sorted(_VALID_NOTE_SCOPES)}",
                context_briefing={},
            )
        if task_id is None:
            t = await self.task.get_active_task_for_agent(agent_id)
            if t is not None:
                task_id = t.id
        title = text.split("\n", 1)[0][:200]
        await self.journal.write_entry(
            agent_id=agent_id,
            task_id=task_id,
            scope=scope,
            title=title,
            content=text,
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
        """Post to a channel. task_id auto-injected if you have an active task."""
        if task_id is None:
            t = await self.task.get_active_task_for_agent(agent_id)
            if t is not None:
                task_id = t.id
        await self.messaging.post_to_channel(
            agent_id=agent_id,
            channel_slug=channel,
            content=text,
            task_id=task_id,
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
        if task_id is None:
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

    async def evidence(
        self,
        *,
        agent_id: UUID,
        task_id: UUID,
    ) -> Envelope:
        """Inspect a task's PR diff, commits, files.

        Fetches dev branch into the agent's workspace before diffing.
        """
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
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


def _strip_task_prefix(msg: str) -> str:
    """Strip any [task-id] prefix the agent supplied; gateway re-adds canonical."""
    return _TASK_ID_PREFIX_RE.sub("", msg)
