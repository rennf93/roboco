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

    async def pass_review(self, agent_id: UUID, task_id: UUID, notes: str) -> Envelope:
        """Phase 2: QA agent_id passes task_id with notes."""
        del agent_id, task_id, notes
        raise NotImplementedError("Phase 2")

    async def fail_review(
        self, agent_id: UUID, task_id: UUID, issues: list[str]
    ) -> Envelope:
        """Phase 2: QA agent_id fails task_id with issues."""
        del agent_id, task_id, issues
        raise NotImplementedError("Phase 2")

    # --- Phase 3 (documenter + PM) verbs ---

    async def claim_doc_task(self, agent_id: UUID, task_id: UUID) -> Envelope:
        """Phase 3: documenter agent_id claims documentation for task_id."""
        del agent_id, task_id
        raise NotImplementedError("Phase 3")

    async def i_documented(
        self,
        agent_id: UUID,
        task_id: UUID,
        notes: str,
        files: list[str],
    ) -> Envelope:
        """Phase 3: documenter completes docs with notes and files."""
        del agent_id, task_id, notes, files
        raise NotImplementedError("Phase 3")

    async def triage(self, agent_id: UUID) -> Envelope:
        """Phase 3: PM agent_id triages next task in queue."""
        del agent_id
        raise NotImplementedError("Phase 3")

    async def triage_all(self, agent_id: UUID) -> Envelope:
        """Phase 3: PM agent_id triages all waiting tasks."""
        del agent_id
        raise NotImplementedError("Phase 3")

    async def unblock(
        self, agent_id: UUID, task_id: UUID, *, restore: bool = True
    ) -> Envelope:
        """Phase 3: PM agent_id unblocks task_id; restore=True (default) returns
        task to its pre_block_state. restore=False legacy: dumps to in_progress.
        """
        del agent_id, task_id, restore  # Phase 3 implementation will use these
        raise NotImplementedError("Phase 3")

    async def complete(self, agent_id: UUID, task_id: UUID, notes: str) -> Envelope:
        """Phase 3: PM agent_id completes task_id with notes."""
        del agent_id, task_id, notes
        raise NotImplementedError("Phase 3")

    async def escalate_up(self, agent_id: UUID, task_id: UUID, reason: str) -> Envelope:
        """Phase 3: PM agent_id escalates task_id up with reason."""
        del agent_id, task_id, reason
        raise NotImplementedError("Phase 3")

    # --- Phase 4 (board) verbs ---

    async def escalate_to_ceo(
        self, agent_id: UUID, task_id: UUID, reason: str
    ) -> Envelope:
        """Phase 4: board agent_id escalates task_id to CEO with reason."""
        del agent_id, task_id, reason
        raise NotImplementedError("Phase 4")
