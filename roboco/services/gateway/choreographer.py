"""Choreographer — composes existing services into intent-verb sequences.

This module has interface signatures only in Phase 0. Each verb's full
implementation lands in its respective phase (Phase 1: dev verbs, Phase 2:
QA verbs, Phase 3: doc + PM verbs, Phase 4: board verbs).

The signatures are stable contracts that the MCP servers and the
/api/v2/flow/* endpoints will call into. Phase 0 wires the dependency
injection so later phases just fill in the bodies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from roboco.config import settings
from roboco.services.gateway.envelope import Envelope
from roboco.services.gateway.evidence_builder import (
    BriefingInputs,
    build_context_briefing,
    build_evidence_for_task,
)
from roboco.services.gateway.merge_chain import parent_branch_for
from roboco.services.gateway.remediation import (
    hint_for_missing_progress,
    hint_for_missing_reflect,
    hint_for_unaddressed_acceptance_criteria,
)
from roboco.services.gateway.tracing_gate import (
    GateContext,
    Requirement,
    check_requirements,
)

if TYPE_CHECKING:
    from uuid import UUID


@dataclass(frozen=True)
class ChoreographerDeps:
    """All service dependencies bundled for Choreographer.

    Frozen dataclass to avoid PLR0913 (too many arguments) and to make
    dependency injection explicit. Each field is typed as Any in Phase 1 —
    per-service Protocol typing lands alongside verb implementations that
    actually exercise the methods.
    """

    task: Any
    work_session: Any
    git: Any
    a2a: Any
    journal: Any
    audit: Any
    evidence_repo: Any


class Choreographer:
    """Composes existing services into intent-verb sequences.

    Constructor takes a ``ChoreographerDeps`` bundle (DI). Verb methods are
    async. Each returns a standardized Envelope. Implementations land
    progressively: see __init__ docstring.

    Service deps are typed as ``Any`` in Phase 1 — per-service Protocol typing
    lands alongside the verb implementations that exercise the methods.
    """

    def __init__(self, deps: ChoreographerDeps) -> None:
        """Initialize Choreographer with bundled service dependencies.

        Args:
            deps: Frozen dataclass holding all 7 service dependencies.
        """
        self._deps = deps

    # --- Convenience properties so call-sites stay readable ---

    @property
    def task(self) -> Any:
        return self._deps.task

    @property
    def work_session(self) -> Any:
        return self._deps.work_session

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
    def audit(self) -> Any:
        return self._deps.audit

    @property
    def evidence_repo(self) -> Any:
        return self._deps.evidence_repo

    # --- Phase 1 (developer) verbs ---

    async def give_me_work(self, agent_id: UUID) -> Envelope:
        """Return the agent's most-actionable task or signal idle."""
        assigned = await self._deps.task.list_assigned_for_agent(agent_id)
        if assigned:
            t = assigned[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=f"call i_will_work_on(task_id='{t.id}', plan='<plan>') to start",
                context_briefing=await self._briefing_for(agent_id, t.id),
            )
        paused = await self._deps.task.list_paused_for_agent(agent_id)
        if paused:
            t = paused[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=f"call i_will_work_on(task_id='{t.id}') to resume",
                context_briefing=await self._briefing_for(agent_id, t.id),
            )
        return Envelope.ok(
            status="idle",
            task_id=None,
            next="call i_am_idle() — no work available",
            context_briefing=await self._briefing_for(agent_id, None),
        )

    async def _briefing_for(
        self, agent_id: UUID, task_id: UUID | None
    ) -> dict[str, Any]:
        """Assemble context_briefing for agent_id, optionally scoped to task_id."""
        repo = self._deps.evidence_repo
        inputs = BriefingInputs(
            unread_a2a=await repo.list_unread_a2a(agent_id),
            unread_mentions=await repo.list_unread_mentions(agent_id),
            pending_notifications=await repo.list_pending_notifications(agent_id),
            task_metadata_gaps=(
                await repo.task_metadata_gaps(task_id) if task_id else []
            ),
            recent_team_activity=await repo.recent_team_activity(agent_id),
            blockers_in_my_lane=await repo.blockers_in_lane(agent_id),
        )
        return build_context_briefing(inputs)

    async def i_will_work_on(
        self, agent_id: UUID, task_id: UUID, plan: str | None = None
    ) -> Envelope:
        """Claim/start/recover any actionable state of agent_id's task_id."""
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        status = str(t.status)
        briefing = await self._briefing_for(agent_id, task_id)

        if status == "needs_revision":
            if t.assigned_to != agent_id:
                t = await self.task.claim(agent_id, task_id)
            t = await self.task.start(agent_id, task_id)
        elif status == "pending":
            if t.assigned_to is None or t.assigned_to != agent_id:
                t = await self.task.claim(agent_id, task_id)
            if not t.plan and not plan:
                remediate = (
                    f"call i_will_work_on(task_id='{task_id}',"
                    f" plan='<one-paragraph plan describing what you will do>')"
                )
                return Envelope.tracing_gap(
                    missing=["plan"],
                    remediate=remediate,
                    context_briefing=briefing,
                )
            if plan:
                t = await self.task.set_plan(task_id, plan)
            t = await self.task.start(agent_id, task_id)
        elif status == "claimed" and t.assigned_to == agent_id:
            t = await self.task.start(agent_id, task_id)
        else:
            return Envelope.invalid_state(
                message=f"task {task_id} is in {status}; cannot start work",
                remediate="call give_me_work() to find an actionable task",
                context_briefing=briefing,
            )

        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=(
                "edit + commit; call i_have_committed when ready,"
                " or i_am_done when finished"
            ),
            context_briefing=briefing,
        )

    async def i_have_committed(self, agent_id: UUID, message: str) -> Envelope:
        """Record that the dev made a commit; auto-creates progress entry."""
        t = await self.task.get_active_task_for_agent(agent_id)
        if t is None:
            return Envelope.invalid_state(
                message="no active task for this agent",
                remediate="call give_me_work() then i_will_work_on(task_id, plan)",
                context_briefing=await self._briefing_for(agent_id, None),
            )
        if not t.plan:
            no_plan_remediate = (
                f"plan must be set first;"
                f" call i_will_work_on(task_id='{t.id}', plan='...')"
            )
            return Envelope.tracing_gap(
                missing=["plan"],
                remediate=no_plan_remediate,
                context_briefing=await self._briefing_for(agent_id, t.id),
            )
        await self.task.add_progress(t.id, agent_id, message)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(t.id),
            next="continue working, or i_am_done when finished",
            context_briefing=await self._briefing_for(agent_id, t.id),
        )

    async def i_am_done(self, agent_id: UUID, task_id: UUID, notes: str) -> Envelope:
        """Submit work for QA. Runs verify/push/PR/submit-qa sequentially as needed.

        Each step gated by tracing/state preconditions; returns precise
        remediation hints when prerequisites are missing.
        """
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        if t.assigned_to != agent_id:
            return Envelope.not_authorized(
                message="not assigned to you",
                remediate="claim it via i_will_work_on(task_id) first",
                context_briefing=await self._briefing_for(agent_id, task_id),
            )

        # 1. Tracing-gate preconditions
        has_reflect = await self.journal.has_reflect_for_task(agent_id, task_id)
        gate_ctx = GateContext(journal_reflect_present=has_reflect)
        gate = check_requirements(
            t,
            [
                Requirement.PROGRESS_AT_LEAST_ONE,
                Requirement.JOURNAL_REFLECT,
                Requirement.ACCEPTANCE_CRITERIA_ADDRESSED,
            ],
            gate_ctx,
        )
        if not gate.passed:
            return await self._build_tracing_gap(agent_id, task_id, gate.missing)

        # 2. Smart catch-up: verification, push, PR, submit_qa
        t = await self._run_catch_up(agent_id, task_id, t, notes)

        # 3. Auto-A2A to QA agent for this team
        await self._notify_qa(agent_id, task_id, t)

        # 4. Build evidence for the response
        journal_highlights = await self.evidence_repo.journal_highlights_for_task(
            task_id
        )
        files_changed: list[str] = []
        if t.work_session_id:
            files_changed = await self.work_session.files_changed(t.work_session_id)
        evidence = build_evidence_for_task(
            t,
            journal_highlights=journal_highlights,
            files_changed=files_changed,
        )
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next="idle until QA responds",
            evidence=evidence.as_dict(),
            context_briefing=await self._briefing_for(agent_id, task_id),
        )

    async def _build_tracing_gap(
        self, agent_id: UUID, task_id: UUID, missing: list[str]
    ) -> Envelope:
        """Translate missing requirement keys into agent-facing hints."""
        hints: list[str] = []
        unaddressed: list[str] = []
        for m in missing:
            if m == "progress>=1":
                hints.append(hint_for_missing_progress())
            elif m == "journal:reflect":
                hints.append(hint_for_missing_reflect(task_id=str(task_id)))
            elif m.startswith("acceptance_criterion:"):
                unaddressed.append(m.split(":", 1)[1])
        if unaddressed:
            hints.append(
                hint_for_unaddressed_acceptance_criteria(
                    criteria=unaddressed,
                    task_id=str(task_id),
                )
            )
        return Envelope.tracing_gap(
            missing=missing,
            remediate=" ; ".join(hints),
            context_briefing=await self._briefing_for(agent_id, task_id),
        )

    async def _run_catch_up(
        self, agent_id: UUID, task_id: UUID, t: Any, notes: str
    ) -> Any:
        """Run verification, push, PR creation, and submit_qa as needed."""
        if not t.self_verified:
            t = await self.task.submit_verification(agent_id, task_id, notes)

        has_unpushed = await self.work_session.has_unpushed_commits(t.work_session_id)
        if has_unpushed:
            await self.git.push(t.branch_name)

        if t.pr_number is None:
            parent = parent_branch_for(t.branch_name)
            await self.git.create_pr(t.branch_name, parent=parent, is_root_pr=False)
            t = await self.task.get(task_id)  # refresh after PR creation

        return await self.task.submit_qa(agent_id, task_id, notes)

    async def _notify_qa(self, agent_id: UUID, task_id: UUID, t: Any) -> None:
        """Send A2A notification to the QA agent for this task's team."""
        qa_agent = await self.task.qa_agent_for_team(t.team)
        if qa_agent is not None:
            skill = self._resolve_skill(qa_agent, ["code_review", "qa_review"])
            await self.a2a.send(
                from_agent=agent_id,
                to_agent=qa_agent.id,
                skill=skill,
                task_id=task_id,
                body=f"Ready for review. PR: {t.pr_url}",
            )

    def _resolve_skill(self, target_agent: Any, preference: list[str]) -> str:
        """Pick first skill in preference list that target_agent has.

        Falls back to the first entry in preference when no match is found.
        """
        have: set[str] = set()
        for s in target_agent.skills or []:
            if isinstance(s, dict):
                sid = s.get("id")
                if sid:
                    have.add(sid)
            elif isinstance(s, str):
                have.add(s)
        for skill in preference:
            if skill in have:
                return skill
        return preference[0]

    async def i_am_blocked(
        self, agent_id: UUID, task_id: UUID, reason: str
    ) -> Envelope:
        """Escalate task_id and write a struggle journal entry; idle the agent."""
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        await self.journal.write_struggle(
            agent_id=agent_id, task_id=task_id, content=reason
        )
        t = await self.task.escalate(agent_id, task_id, reason)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next="idle — PM will resolve and notify",
            context_briefing=await self._briefing_for(agent_id, task_id),
        )

    async def i_am_idle(self, agent_id: UUID) -> Envelope:
        """Report no more work. Soft-block if there are unread A2As or @mentions."""
        briefing = await self._briefing_for(agent_id, None)
        if briefing.get("unread_a2a") or briefing.get("unread_mentions"):
            return Envelope.ok(
                status="idle_with_unread",
                task_id=None,
                next=(
                    "address unread A2A and @mentions in context_briefing"
                    " before going idle"
                ),
                context_briefing=briefing,
            )
        await self.task.mark_agent_idle(agent_id)
        return Envelope.ok(
            status="idle",
            task_id=None,
            next="container will shut down",
            context_briefing=briefing,
        )

    # --- Phase 2 (QA) verbs ---

    async def claim_review(self, qa_agent_id: UUID, task_id: UUID) -> Envelope:
        """QA agent claims task in awaiting_qa for review.

        The response includes evidence (pr_url, pr_number, commits, files_changed,
        journal_highlights, acceptance_criteria_status) INLINE so the QA agent
        cannot miss the PR data. Marks `qa_evidence_inspected=true` automatically.
        """
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        if str(t.status) != "awaiting_qa":
            return Envelope.invalid_state(
                message=(
                    f"task {task_id} is in {t.status}, expected awaiting_qa for review"
                ),
                remediate="call give_me_work() to find an actionable QA task",
                context_briefing=await self._briefing_for(qa_agent_id, task_id),
            )
        t = await self.task.qa_claim(qa_agent_id, task_id)

        # Auto-mark evidence as inspected — we surface it inline in this response
        await self.task.mark_evidence_inspected(task_id)

        files_changed: list[str] = []
        if t.work_session_id:
            files_changed = await self.work_session.files_changed(t.work_session_id)
        diff_summary = ""
        if t.branch_name:
            diff_summary = await self.git.diff(branch_name=t.branch_name)
        journal_highlights = await self.evidence_repo.journal_highlights_for_task(
            task_id
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
            context_briefing=await self._briefing_for(qa_agent_id, task_id),
        )

    async def pass_review(
        self, qa_agent_id: UUID, task_id: UUID, notes: str
    ) -> Envelope:
        """QA passes the task; transitions awaiting_qa → awaiting_documentation.

        Gated on tracing requirements: qa_notes >= settings.qa_notes_min_chars,
        journal:learning entry exists for this task, and qa_evidence_inspected
        is True (auto-set by claim_review).
        """
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        if t.assigned_to != qa_agent_id:
            return Envelope.not_authorized(
                message="not assigned to you",
                remediate="claim it via claim_review(task_id) first",
                context_briefing=await self._briefing_for(qa_agent_id, task_id),
            )

        has_learning = await self.journal.has_learning_for_task(qa_agent_id, task_id)
        missing = self._check_qa_pass_gates(
            notes=notes,
            has_learning=has_learning,
            evidence_inspected=t.qa_evidence_inspected,
        )
        if missing:
            return self._qa_tracing_gap(
                missing, task_id, await self._briefing_for(qa_agent_id, task_id)
            )

        t = await self.task.qa_pass(qa_agent_id, task_id, notes)

        doc_agent = await self.task.documenter_for_team(t.team)
        if doc_agent is not None:
            await self.a2a.send(
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
            context_briefing=await self._briefing_for(qa_agent_id, task_id),
        )

    def _check_qa_pass_gates(
        self, *, notes: str, has_learning: bool, evidence_inspected: bool
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

    def _qa_tracing_gap(
        self, missing: list[str], task_id: UUID, briefing: dict[str, Any]
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
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        if t.assigned_to != qa_agent_id:
            return Envelope.not_authorized(
                message="not assigned to you",
                remediate="claim it via claim_review(task_id) first",
                context_briefing=await self._briefing_for(qa_agent_id, task_id),
            )
        if not issues:
            return Envelope.invalid_state(
                message="fail_review requires at least one issue",
                remediate="pass issues=['<concrete actionable issue>', ...]",
                context_briefing=await self._briefing_for(qa_agent_id, task_id),
            )

        has_learning = await self.journal.has_learning_for_task(qa_agent_id, task_id)
        notes = "Issues:\n" + "\n".join(f"- {issue}" for issue in issues)
        missing = self._check_qa_pass_gates(
            notes=notes,
            has_learning=has_learning,
            evidence_inspected=t.qa_evidence_inspected,
        )
        if missing:
            return self._qa_tracing_gap(
                missing, task_id, await self._briefing_for(qa_agent_id, task_id)
            )

        t = await self.task.qa_fail(qa_agent_id, task_id, notes, issues)
        # A2A back to original developer (now reassigned)
        if t.assigned_to is not None:
            await self.a2a.send(
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
            context_briefing=await self._briefing_for(qa_agent_id, task_id),
        )

    # --- Phase 3 (documenter + PM) verbs ---

    async def claim_doc_task(self, doc_agent_id: UUID, task_id: UUID) -> Envelope:
        """Documenter claims task in awaiting_documentation; returns evidence inline."""
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        if str(t.status) != "awaiting_documentation":
            return Envelope.invalid_state(
                message=(
                    f"task {task_id} is in {t.status}, expected awaiting_documentation"
                ),
                remediate="call give_me_work() to find an actionable doc task",
                context_briefing=await self._briefing_for(doc_agent_id, task_id),
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
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        if t.assigned_to != doc_agent_id:
            return Envelope.not_authorized(
                message="not assigned to you",
                remediate="claim it via claim_doc_task(task_id) first",
                context_briefing=await self._briefing_for(doc_agent_id, task_id),
            )
        if not notes or len(notes) < settings.docs_notes_min_chars:
            return Envelope.tracing_gap(
                missing=["docs_notes>=20"],
                remediate=(
                    "i_documented requires notes>=20 chars summarizing what you "
                    "documented and where (file paths)."
                    " Include each file in `files=...`."
                ),
                context_briefing=await self._briefing_for(doc_agent_id, task_id),
            )
        if not files:
            return Envelope.tracing_gap(
                missing=["files"],
                remediate=(
                    "i_documented requires files=['<path>', ...]"
                    " listing the doc files written."
                ),
                context_briefing=await self._briefing_for(doc_agent_id, task_id),
            )
        t = await self.task.docs_complete(
            doc_agent_id, task_id, notes=notes, files=files
        )
        pm_agent = await self.task.cell_pm_for_team(t.team)
        if pm_agent is not None:
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
        )

    async def triage(self, pm_agent_id: UUID) -> Envelope:
        """Cell PM triage: blocked > awaiting_pm_review > idle."""
        pm = await self.task.agent_for(pm_agent_id)
        blocked = await self.task.list_blocked_for_team(pm.team)
        if blocked:
            t = blocked[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=f"investigate the block, then unblock(task_id='{t.id}')",
                context_briefing=await self._briefing_for(pm_agent_id, t.id),
            )
        awaiting = await self.task.list_awaiting_pm_review_for_team(pm.team)
        if awaiting:
            t = awaiting[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=f"review and complete(task_id='{t.id}')",
                context_briefing=await self._briefing_for(pm_agent_id, t.id),
            )
        return Envelope.ok(
            status="idle",
            task_id=None,
            next="no PM work — call i_am_idle",
            context_briefing=await self._briefing_for(pm_agent_id, None),
        )

    async def triage_all(self, pm_agent_id: UUID) -> Envelope:
        """Main PM triage: across all teams. blocked > awaiting_pm_review > idle."""
        blocked = await self.task.list_blocked_all_teams()
        if blocked:
            t = blocked[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=(
                    f"escalation/cross-cell help required: investigate, then "
                    f"unblock(task_id='{t.id}') or escalate_up()"
                ),
                context_briefing=await self._briefing_for(pm_agent_id, t.id),
            )
        awaiting = await self.task.list_awaiting_main_pm_all()
        if awaiting:
            t = awaiting[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=f"complete(task_id='{t.id}') opens master PR + escalates to CEO",
                context_briefing=await self._briefing_for(pm_agent_id, t.id),
            )
        return Envelope.ok(
            status="idle",
            task_id=None,
            next="no Main PM work",
            context_briefing=await self._briefing_for(pm_agent_id, None),
        )

    async def unblock(
        self, pm_agent_id: UUID, task_id: UUID, *, restore: bool = True
    ) -> Envelope:
        """PM unblocks task; restore=True (default) returns to pre_block_state."""
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        if str(t.status) != "blocked":
            return Envelope.invalid_state(
                message=f"task {task_id} is in {t.status}, expected blocked",
                remediate=(
                    "this task is not blocked; call triage() to find blocked tasks"
                ),
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )

        has_decision = await self.journal.has_decision_for_task(pm_agent_id, task_id)
        if not has_decision:
            from roboco.services.gateway.remediation import (
                hint_for_missing_journal_decision,
            )

            return Envelope.tracing_gap(
                missing=["journal:decision"],
                remediate=hint_for_missing_journal_decision(),
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )

        t = await self.task.unblock_with_restore(pm_agent_id, task_id, restore=restore)
        next_msg = (
            "task restored to its pre-block state — original assignee will resume"
            if restore
            else "task back to in_progress; you'll need to re-engage the workflow"
        )
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=next_msg,
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        )

    async def _cell_pm_complete_guard(
        self, pm_agent_id: UUID, task_id: UUID, t: Any
    ) -> Envelope | None:
        """Return a rejection Envelope if pre-merge guards fail; else None."""
        if t.assigned_to != pm_agent_id:
            return Envelope.not_authorized(
                message="not assigned to you",
                remediate="claim the task or wait for it to be assigned",
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )
        if str(t.status) != "awaiting_pm_review":
            return Envelope.invalid_state(
                message=(
                    f"task {task_id} is in {t.status}, expected awaiting_pm_review"
                ),
                remediate="this task is not ready for completion",
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )
        has_decision = await self.journal.has_decision_for_task(pm_agent_id, task_id)
        if not has_decision:
            from roboco.services.gateway.remediation import (
                hint_for_missing_journal_decision,
            )

            return Envelope.tracing_gap(
                missing=["journal:decision"],
                remediate=hint_for_missing_journal_decision(),
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )
        all_terminal = await self.task.all_subtasks_terminal(task_id)
        if not all_terminal:
            return Envelope.tracing_gap(
                missing=["subtasks not all terminal"],
                remediate=(
                    "all subtasks must be in completed/cancelled before"
                    " completing parent. Call triage() to find pending subtasks."
                ),
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )
        if t.pr_number is None:
            return Envelope.invalid_state(
                message="task has no PR; cannot merge",
                remediate=(
                    "this state should not occur post-Phase-1;"
                    " investigate dev's i_am_done path"
                ),
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )
        return None

    async def cell_pm_complete(
        self, pm_agent_id: UUID, task_id: UUID, notes: str
    ) -> Envelope:
        """Cell PM completes a task — auto-merges leaf PR into parent branch."""
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        guard = await self._cell_pm_complete_guard(pm_agent_id, task_id, t)
        if guard is not None:
            return guard
        target = parent_branch_for(t.branch_name)
        merge_result = await self.git.pr_merge(t.pr_number, target=target)
        t = await self.task.cell_pm_complete(
            pm_agent_id,
            task_id,
            notes,
            merge_commit=merge_result.get("merge_commit_sha"),
        )
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=f"merged into {target}; triage() for next item",
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        )

    async def _main_pm_complete_guard(
        self, main_pm_agent_id: UUID, root_task_id: UUID, t: Any
    ) -> Envelope | None:
        """Return a rejection Envelope if pre-escalation guards fail; else None."""
        if t.assigned_to != main_pm_agent_id:
            return Envelope.not_authorized(
                message="not assigned to you",
                remediate="wait for assignment or claim",
                context_briefing=await self._briefing_for(
                    main_pm_agent_id, root_task_id
                ),
            )
        if str(t.status) != "awaiting_pm_review":
            return Envelope.invalid_state(
                message=(
                    f"task {root_task_id} is in {t.status}, expected awaiting_pm_review"
                ),
                remediate="this task is not ready for main-PM completion",
                context_briefing=await self._briefing_for(
                    main_pm_agent_id, root_task_id
                ),
            )
        if t.parent_task_id is not None:
            return Envelope.invalid_state(
                message=(
                    "main_pm complete only operates on root tasks (no parent_task_id)"
                ),
                remediate=(
                    "cell PM should complete this task;"
                    " main PM only completes root tasks"
                ),
                context_briefing=await self._briefing_for(
                    main_pm_agent_id, root_task_id
                ),
            )
        has_decision = await self.journal.has_decision_for_task(
            main_pm_agent_id, root_task_id
        )
        if not has_decision:
            from roboco.services.gateway.remediation import (
                hint_for_missing_journal_decision,
            )

            return Envelope.tracing_gap(
                missing=["journal:decision"],
                remediate=hint_for_missing_journal_decision(),
                context_briefing=await self._briefing_for(
                    main_pm_agent_id, root_task_id
                ),
            )
        all_terminal = await self.task.all_subtasks_terminal(root_task_id)
        if not all_terminal:
            return Envelope.tracing_gap(
                missing=["subtasks not all terminal"],
                remediate="all subtasks must be in completed/cancelled state",
                context_briefing=await self._briefing_for(
                    main_pm_agent_id, root_task_id
                ),
            )
        return None

    async def main_pm_complete(
        self, main_pm_agent_id: UUID, root_task_id: UUID, notes: str
    ) -> Envelope:
        """Main PM completes a root task; opens master PR + escalates to CEO."""
        t = await self.task.get(root_task_id)
        if t is None:
            return Envelope.not_found(message=f"task {root_task_id} not found")
        guard = await self._main_pm_complete_guard(main_pm_agent_id, root_task_id, t)
        if guard is not None:
            return guard

        needs_pr = t.pr_number is None
        if not needs_pr:
            current_target = await self.git.pr_target(t.pr_number)
            needs_pr = current_target != "master"
        if needs_pr:
            await self.git.create_pr(t.branch_name, parent="master", is_root_pr=True)

        t = await self.task.escalate_to_ceo(main_pm_agent_id, root_task_id, notes)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(root_task_id),
            next="idle until CEO approves (or rejects) via UI",
            context_briefing=await self._briefing_for(main_pm_agent_id, root_task_id),
        )

    async def complete(self, agent_id: UUID, task_id: UUID, notes: str) -> Envelope:
        """Dispatch to cell_pm_complete or main_pm_complete based on agent role."""
        agent = await self.task.agent_for(agent_id)
        if agent.role == "cell_pm":
            return await self.cell_pm_complete(agent_id, task_id, notes)
        if agent.role == "main_pm":
            return await self.main_pm_complete(agent_id, task_id, notes)
        return Envelope.not_authorized(
            message=f"role {agent.role} cannot complete tasks via this verb",
            remediate="only cell_pm and main_pm can call complete",
            context_briefing=await self._briefing_for(agent_id, task_id),
        )

    async def escalate_up(
        self, pm_agent_id: UUID, task_id: UUID, reason: str
    ) -> Envelope:
        """Escalate a task to the agent's escalation_target role."""
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")

        has_decision = await self.journal.has_decision_for_task(pm_agent_id, task_id)
        if not has_decision:
            from roboco.services.gateway.remediation import (
                hint_for_missing_journal_decision,
            )

            return Envelope.tracing_gap(
                missing=["journal:decision"],
                remediate=hint_for_missing_journal_decision(),
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )

        me = await self.task.agent_for(pm_agent_id)
        target_role = me.escalation_target
        if not target_role:
            return Envelope.invalid_state(
                message="no escalation target configured for your role",
                remediate="check agents_config.py for your role's escalation_target",
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )

        t = await self.task.escalate_up_to_role(
            pm_agent_id, task_id, target_role, reason
        )
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=f"escalated to {target_role}; idle until they respond",
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        )

    # --- Phase 4 (board) verbs ---

    async def escalate_to_ceo(
        self, agent_id: UUID, task_id: UUID, reason: str
    ) -> Envelope:
        """Phase 4: board agent_id escalates task_id to CEO with reason."""
        del agent_id, task_id, reason
        raise NotImplementedError("Phase 4")
