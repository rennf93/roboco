"""Documenter verbs (audit P2-2 second per-role split).

Mixin for ``claim_doc_task`` and ``i_documented``. Relies on the base
class for: ``self.task``, ``self.git``, ``self.work_session``,
``self.a2a``, ``self.evidence_repo``, ``self._briefing_for``,
``self._emit_rejection``, ``self._run_claim_guards``,
``self._with_briefing``. ``settings`` and ``build_evidence_for_task``
are module-level imports here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from roboco.config import settings
from roboco.services.gateway.envelope import Envelope
from roboco.services.gateway.evidence_builder import build_evidence_for_task

if TYPE_CHECKING:
    from uuid import UUID


class DocMixin:
    """Documenter-role verbs."""

    async def claim_doc_task(self, doc_agent_id: UUID, task_id: UUID) -> Envelope:
        """Documenter claims task in awaiting_documentation; returns evidence inline."""
        t = await self.task.get(task_id)  # type: ignore[attr-defined]
        if t is None:
            return await self._emit_rejection(  # type: ignore[attr-defined]
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="claim_doc_task",
            )
        if str(t.status) != "awaiting_documentation":
            return await self._emit_rejection(  # type: ignore[attr-defined]
                Envelope.invalid_state(
                    message=(
                        f"task {task_id} is in {t.status}, "
                        "expected awaiting_documentation"
                    ),
                    remediate="call give_me_work() to find an actionable doc task",
                    context_briefing=await self._briefing_for(doc_agent_id, task_id),  # type: ignore[attr-defined]
                ),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="claim_doc_task",
            )

        guard = await self._run_claim_guards(  # type: ignore[attr-defined]
            agent_id=doc_agent_id,
            task=t,
            skip_role_typed=True,
            skip_pm_code=True,
            skip_sequence=True,
        )
        if guard:
            return await self._emit_rejection(  # type: ignore[attr-defined]
                self._with_briefing(  # type: ignore[attr-defined]
                    guard,
                    await self._briefing_for(doc_agent_id, task_id),  # type: ignore[attr-defined]
                ),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="claim_doc_task",
            )

        t = await self.task.doc_claim(doc_agent_id, task_id)  # type: ignore[attr-defined]
        files_changed: list[str] = []
        if t.work_session_id:
            files_changed = await self.work_session.files_changed(t.work_session_id)  # type: ignore[attr-defined]
        diff = ""
        if t.branch_name:
            diff = await self.git.diff(branch_name=t.branch_name)  # type: ignore[attr-defined]
        journal_highlights = (
            await self.evidence_repo.journal_highlights_for_task(task_id)  # type: ignore[attr-defined]
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
            context_briefing=await self._briefing_for(doc_agent_id, task_id),  # type: ignore[attr-defined]
        )

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
        t = await self.task.get(task_id)  # type: ignore[attr-defined]
        if t is None:
            return await self._emit_rejection(  # type: ignore[attr-defined]
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="i_documented",
            )
        if t.assigned_to != doc_agent_id:
            return await self._emit_rejection(  # type: ignore[attr-defined]
                Envelope.not_authorized(
                    message="not assigned to you",
                    remediate="claim it via claim_doc_task(task_id) first",
                    context_briefing=await self._briefing_for(doc_agent_id, task_id),  # type: ignore[attr-defined]
                ),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="i_documented",
            )
        if not notes or len(notes) < settings.docs_notes_min_chars:
            return await self._emit_rejection(  # type: ignore[attr-defined]
                Envelope.tracing_gap(
                    missing=["docs_notes>=20"],
                    remediate=(
                        "i_documented requires notes>=20 chars summarizing what you "
                        "documented and where (file paths)."
                        " Include each file in `files=...`."
                    ),
                    context_briefing=await self._briefing_for(doc_agent_id, task_id),  # type: ignore[attr-defined]
                ),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="i_documented",
            )
        if not files:
            return await self._emit_rejection(  # type: ignore[attr-defined]
                Envelope.tracing_gap(
                    missing=["files"],
                    remediate=(
                        "i_documented requires files=['<path>', ...]"
                        " listing the doc files written."
                    ),
                    context_briefing=await self._briefing_for(doc_agent_id, task_id),  # type: ignore[attr-defined]
                ),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="i_documented",
            )
        t = await self.task.docs_complete(  # type: ignore[attr-defined]
            doc_agent_id, task_id, notes=notes, files=files
        )
        pm_agent = await self.task.cell_pm_for_team(t.team)  # type: ignore[attr-defined]
        if pm_agent is not None:
            await self.task.reassign(task_id, pm_agent.id)  # type: ignore[attr-defined]
            await self.a2a.send(  # type: ignore[attr-defined]
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
            context_briefing=await self._briefing_for(doc_agent_id, task_id),  # type: ignore[attr-defined]
        )
