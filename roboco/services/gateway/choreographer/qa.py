"""QA verbs (audit P2-2 third per-role split).

Mixin for ``claim_review``, ``pass_review``, ``fail_review`` and the
two QA-specific helpers ``_check_qa_pass_gates`` / ``_qa_tracing_gap``.
Helpers stay together with the verbs that use them — they're not used
by any other role.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from roboco.config import settings
from roboco.services.gateway.envelope import Envelope
from roboco.services.gateway.evidence_builder import build_evidence_for_task

if TYPE_CHECKING:
    from uuid import UUID


class QAMixin:
    """QA-role verbs."""

    async def claim_review(self, qa_agent_id: UUID, task_id: UUID) -> Envelope:
        """QA agent claims task in awaiting_qa for review.

        The response includes evidence (pr_url, pr_number, commits, files_changed,
        journal_highlights, acceptance_criteria_status) INLINE so the QA agent
        cannot miss the PR data. Marks `qa_evidence_inspected=true` automatically.
        """
        t = await self.task.get(task_id)  # type: ignore[attr-defined]
        if t is None:
            return await self._emit_rejection(  # type: ignore[attr-defined]
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="claim_review",
            )
        if str(t.status) != "awaiting_qa":
            return await self._emit_rejection(  # type: ignore[attr-defined]
                Envelope.invalid_state(
                    message=(
                        f"task {task_id} is in {t.status}, "
                        "expected awaiting_qa for review"
                    ),
                    remediate="call give_me_work() to find an actionable QA task",
                    context_briefing=await self._briefing_for(qa_agent_id, task_id),  # type: ignore[attr-defined]
                ),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="claim_review",
            )

        guard = await self._run_claim_guards(  # type: ignore[attr-defined]
            agent_id=qa_agent_id,
            task=t,
            skip_role_typed=True,
            skip_pm_code=True,
            skip_sequence=True,
        )
        if guard:
            return await self._emit_rejection(  # type: ignore[attr-defined]
                self._with_briefing(  # type: ignore[attr-defined]
                    guard,
                    await self._briefing_for(qa_agent_id, task_id),  # type: ignore[attr-defined]
                ),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="claim_review",
            )

        t = await self.task.qa_claim(qa_agent_id, task_id)  # type: ignore[attr-defined]
        await self.task.mark_evidence_inspected(task_id)  # type: ignore[attr-defined]

        files_changed: list[str] = []
        if t.work_session_id:
            files_changed = await self.work_session.files_changed(t.work_session_id)  # type: ignore[attr-defined]
        diff_summary = ""
        if t.branch_name:
            diff_summary = await self.git.diff(branch_name=t.branch_name)  # type: ignore[attr-defined]
        journal_highlights = (
            await self.evidence_repo.journal_highlights_for_task(task_id)  # type: ignore[attr-defined]
        )
        ev = build_evidence_for_task(
            t,
            journal_highlights=journal_highlights,
            files_changed=files_changed,
            pr_diff_summary=diff_summary,
        )
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=(
                "review the diff. Then call pass(notes) to accept or "
                "fail(issues) to request changes."
            ),
            evidence=ev.as_dict(),
            context_briefing=await self._briefing_for(qa_agent_id, task_id),  # type: ignore[attr-defined]
        )

    async def pass_review(
        self, qa_agent_id: UUID, task_id: UUID, notes: str
    ) -> Envelope:
        """QA passes the task; transitions awaiting_qa → awaiting_documentation."""
        t = await self.task.get(task_id)  # type: ignore[attr-defined]
        if t is None:
            return await self._emit_rejection(  # type: ignore[attr-defined]
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="pass_review",
            )
        if t.assigned_to != qa_agent_id:
            return await self._emit_rejection(  # type: ignore[attr-defined]
                Envelope.not_authorized(
                    message="not assigned to you",
                    remediate="claim it via claim_review(task_id) first",
                    context_briefing=await self._briefing_for(qa_agent_id, task_id),  # type: ignore[attr-defined]
                ),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="pass_review",
            )

        has_learning = await self.journal.has_learning_for_task(  # type: ignore[attr-defined]
            qa_agent_id, task_id
        )
        missing = self._check_qa_pass_gates(
            notes=notes,
            has_learning=has_learning,
            evidence_inspected=t.qa_evidence_inspected,
        )
        if missing:
            return await self._emit_rejection(  # type: ignore[attr-defined]
                self._qa_tracing_gap(
                    missing,
                    task_id,
                    await self._briefing_for(qa_agent_id, task_id),  # type: ignore[attr-defined]
                ),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="pass_review",
            )

        t = await self.task.qa_pass(qa_agent_id, task_id, notes)  # type: ignore[attr-defined]

        doc_agent = await self.task.documenter_for_team(t.team)  # type: ignore[attr-defined]
        if doc_agent is not None:
            await self.task.reassign(task_id, doc_agent.id)  # type: ignore[attr-defined]
            await self.a2a.send(  # type: ignore[attr-defined]
                from_agent=qa_agent_id,
                to_agent=doc_agent.id,
                skill="documentation",
                task_id=task_id,
                body=f"QA passed task {t.id}. PR: {t.pr_url}. Please document.",
            )
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next="idle until next QA work arrives",
            context_briefing=await self._briefing_for(qa_agent_id, task_id),  # type: ignore[attr-defined]
        )

    @staticmethod
    def _check_qa_pass_gates(
        *, notes: str, has_learning: bool, evidence_inspected: bool
    ) -> list[str]:
        """Return list of missing gate keys; empty list if all pass."""
        missing: list[str] = []
        if not notes or len(notes) < settings.qa_notes_min_chars:
            missing.append("qa_notes>=min")
        if not has_learning:
            missing.append("journal:learning")
        if not evidence_inspected:
            missing.append("qa_evidence_inspected")
        return missing

    @staticmethod
    def _qa_tracing_gap(
        missing: list[str], task_id: UUID, briefing: dict[str, Any]
    ) -> Envelope:
        """Build a tracing_gap envelope with role-appropriate hints."""
        from roboco.services.gateway.remediation import (
            hint_for_evidence_not_inspected,
            hint_for_missing_journal_learning,
            hint_for_missing_qa_notes,
        )

        hint_map = {
            "qa_notes>=min": hint_for_missing_qa_notes(),
            "journal:learning": hint_for_missing_journal_learning(),
            "qa_evidence_inspected": hint_for_evidence_not_inspected(
                task_id=str(task_id)
            ),
        }
        hints = [hint_map[m] for m in missing if m in hint_map]
        return Envelope.tracing_gap(
            missing=missing,
            remediate=" ; ".join(hints),
            context_briefing=briefing,
        )

    async def fail_review(
        self, qa_agent_id: UUID, task_id: UUID, issues: list[str]
    ) -> Envelope:
        """QA fails the task with concrete issues; transitions to needs_revision."""
        t = await self.task.get(task_id)  # type: ignore[attr-defined]
        if t is None:
            return await self._emit_rejection(  # type: ignore[attr-defined]
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="fail_review",
            )
        if t.assigned_to != qa_agent_id:
            return await self._emit_rejection(  # type: ignore[attr-defined]
                Envelope.not_authorized(
                    message="not assigned to you",
                    remediate="claim it via claim_review(task_id) first",
                    context_briefing=await self._briefing_for(qa_agent_id, task_id),  # type: ignore[attr-defined]
                ),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="fail_review",
            )
        if not issues:
            return await self._emit_rejection(  # type: ignore[attr-defined]
                Envelope.invalid_state(
                    message="fail_review requires at least one issue",
                    remediate="pass issues=['<concrete actionable issue>', ...]",
                    context_briefing=await self._briefing_for(qa_agent_id, task_id),  # type: ignore[attr-defined]
                ),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="fail_review",
            )

        has_learning = await self.journal.has_learning_for_task(  # type: ignore[attr-defined]
            qa_agent_id, task_id
        )
        notes = "Issues:\n" + "\n".join(f"- {issue}" for issue in issues)
        missing = self._check_qa_pass_gates(
            notes=notes,
            has_learning=has_learning,
            evidence_inspected=t.qa_evidence_inspected,
        )
        if missing:
            return await self._emit_rejection(  # type: ignore[attr-defined]
                self._qa_tracing_gap(
                    missing,
                    task_id,
                    await self._briefing_for(qa_agent_id, task_id),  # type: ignore[attr-defined]
                ),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="fail_review",
            )

        t = await self.task.qa_fail(qa_agent_id, task_id, notes, issues)  # type: ignore[attr-defined]
        if t.assigned_to is not None:
            await self.a2a.send(  # type: ignore[attr-defined]
                from_agent=qa_agent_id,
                to_agent=t.assigned_to,
                skill="code_review",
                task_id=task_id,
                body=f"QA needs changes. Issues:\n{notes}",
            )
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next="idle — dev will revise and re-submit",
            context_briefing=await self._briefing_for(qa_agent_id, task_id),  # type: ignore[attr-defined]
        )
