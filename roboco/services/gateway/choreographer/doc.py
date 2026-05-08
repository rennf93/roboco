"""Documenter verbs (audit P2-2 second per-role split).

Mixin for ``claim_doc_task`` and ``i_documented``. Inherits typed
helpers via ``ChoreographerHelpers`` under ``TYPE_CHECKING``; runtime
class is the composed ``Choreographer``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from roboco.config import settings
from roboco.services.gateway.envelope import Envelope
from roboco.services.gateway.evidence_builder import build_evidence_for_task

if TYPE_CHECKING:
    from uuid import UUID

    from roboco.services.gateway.choreographer._protocol import ChoreographerHelpers

    _Base = ChoreographerHelpers
else:
    _Base = object


class DocMixin(_Base):
    """Documenter-role verbs."""

    async def claim_doc_task(self, doc_agent_id: UUID, task_id: UUID) -> Envelope:
        """Documenter claims task in awaiting_documentation; returns evidence inline."""
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="claim_doc_task",
            )
        if str(t.status) != "awaiting_documentation":
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=(
                        f"task {task_id} is in {t.status}, "
                        "expected awaiting_documentation"
                    ),
                    remediate="call give_me_work() to find an actionable doc task",
                    context_briefing=await self._briefing_for(doc_agent_id, task_id),
                ).with_introspection(task=t, role="documenter"),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="claim_doc_task",
            )

        guard = await self._run_claim_guards(
            agent_id=doc_agent_id,
            task=t,
            skip_role_typed=True,
            skip_pm_code=True,
            skip_sequence=True,
        )
        if guard:
            guard.with_introspection(task=t, role="documenter")
            return await self._emit_rejection(
                self._with_briefing(
                    guard,
                    await self._briefing_for(doc_agent_id, task_id),
                ),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="claim_doc_task",
            )

        t = await self.task.doc_claim(doc_agent_id, task_id)
        files_changed: list[str] = []
        if t.work_session_id:
            files_changed = await self.work_session.files_changed(t.work_session_id)
        diff = ""
        if t.branch_name:
            diff = await self.git.diff(branch_name=t.branch_name)
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
            next=(
                "write docs in your workspace, commit them, then call "
                "i_documented(task_id, notes, files)"
            ),
            evidence=ev.as_dict(),
            context_briefing=await self._briefing_for(doc_agent_id, task_id),
        ).with_introspection(task=t, role="documenter")

    async def _verify_doc_owner(
        self, doc_agent_id: UUID, task_id: UUID
    ) -> tuple[Envelope | None, Any]:
        """Lookup task + verify doc agent is assignee. Returns (rejection, task)."""
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="i_documented",
            ), None
        if t.assigned_to != doc_agent_id:
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message="not assigned to you",
                    remediate="claim it via claim_doc_task(task_id) first",
                    context_briefing=await self._briefing_for(doc_agent_id, task_id),
                ).with_introspection(task=t, role="documenter"),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="i_documented",
            ), None
        return None, t

    async def _check_i_documented_inputs(
        self,
        doc_agent_id: UUID,
        task_id: UUID,
        notes: str,
        files: list[str],
        task: Any,
    ) -> Envelope | None:
        """Validate notes length + files non-empty. Returns rejection or None."""
        if not notes or len(notes) < settings.docs_notes_min_chars:
            return await self._emit_rejection(
                Envelope.tracing_gap(
                    missing=["docs_notes>=20"],
                    remediate=(
                        "i_documented requires notes>=20 chars summarizing what you "
                        "documented and where (file paths)."
                        " Include each file in `files=...`."
                    ),
                    context_briefing=await self._briefing_for(doc_agent_id, task_id),
                ).with_introspection(task=task, role="documenter"),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="i_documented",
            )
        if not files:
            return await self._emit_rejection(
                Envelope.tracing_gap(
                    missing=["files"],
                    remediate=(
                        "i_documented requires files=['<path>', ...]"
                        " listing the doc files written."
                    ),
                    context_briefing=await self._briefing_for(doc_agent_id, task_id),
                ).with_introspection(task=task, role="documenter"),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="i_documented",
            )
        return None

    async def i_documented(
        self,
        doc_agent_id: UUID,
        task_id: UUID,
        notes: str,
        files: list[str],
    ) -> Envelope:
        """Documenter signals docs complete.

        Transitions awaiting_documentation → awaiting_pm_review.
        """
        rejection, owned_task = await self._verify_doc_owner(doc_agent_id, task_id)
        if rejection is not None:
            return rejection
        input_rejection = await self._check_i_documented_inputs(
            doc_agent_id, task_id, notes, files, owned_task
        )
        if input_rejection is not None:
            return input_rejection

        # TaskService.docs_complete signature is (task_id, doc_notes); it
        # reads task.documents for indexing. Stamp the file list onto the
        # task before the transition so the indexer sees it.
        existing = await self.task.get(task_id)
        if existing is not None:
            existing.documents = files
            await self.task.session.flush()
        t = await self.task.docs_complete(task_id, doc_notes=notes)
        pm_agent = await self.task.cell_pm_for_team(t.team)
        if pm_agent is not None:
            await self.task.reassign(task_id, pm_agent.id)
            await self.a2a.send(
                from_agent=doc_agent_id,
                to_agent=pm_agent.id,
                skill="task_management",
                task_id=task_id,
                body=f"Docs complete for {t.id}. Ready for PM review + merge.",
            )
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next="idle until PM completes",
            context_briefing=await self._briefing_for(doc_agent_id, task_id),
        ).with_introspection(task=t, role="documenter")
