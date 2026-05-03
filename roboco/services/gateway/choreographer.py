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
from typing import Any
from uuid import UUID

import structlog

from roboco.config import settings
from roboco.services.gateway.claim_guards import (
    already_active_guard,
    paused_tasks_guard,
    pm_cannot_execute_code_guard,
    role_typed_claim_guard,
    sibling_sequence_guard,
)
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

logger = structlog.get_logger()


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


@dataclass(frozen=True)
class DelegateInputs:
    """Bundle of fields the ``delegate`` verb receives from the route layer."""

    title: str
    description: str
    assigned_to: str
    team: str
    task_type: str = "code"
    acceptance_criteria: list[str] | None = None
    estimated_complexity: str = "medium"


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

    async def _touch(self, task_id: UUID | None) -> None:
        """Best-effort heartbeat write; silent on missing task."""
        if task_id is not None:
            await self.task.heartbeat(task_id)

    async def _emit_rejection(
        self,
        env: Envelope,
        *,
        agent_id: UUID,
        task_id: UUID | None,
        verb: str,
    ) -> Envelope:
        """Audit-log a rejection envelope; pass through unchanged on success.

        Idempotent on success envelopes: the early `env.error is None`
        return is the only fast path. Audit writes are best-effort —
        failures must NEVER block the verb (the agent's response is the
        contract; the audit row is observability-only).
        """
        if env.error is None:
            return env
        try:
            await self.audit.log_event(
                event_type="gateway.rejected",
                agent_id=agent_id,
                task_id=task_id,
                details={
                    "verb": verb,
                    "reason": env.error,
                    "message": env.message,
                    "missing": env.missing or [],
                },
            )
        except Exception as exc:
            # Audit is best-effort: it must NEVER block the verb. The agent's
            # response is the contract; the audit row is observability-only.
            logger.warning("audit.log_event failed", error=str(exc), verb=verb)
        return env

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

    async def _run_claim_guards(
        self,
        *,
        agent_id: UUID,
        task: Any,
        skip_role_typed: bool = False,
        skip_pm_code: bool = False,
        skip_sequence: bool = False,
    ) -> Envelope | None:
        """Run claim-time guards (Gate Set A). Returns rejection or None.

        Pre-gateway location: _helpers.py:124-204 + claim.py:121-180.

        Optional skip flags isolate guards that don't apply to a given verb:
        - skip_role_typed: i_will_plan/claim_review/claim_doc_task have their
          own role checks; only i_will_work_on uses role_typed_claim_guard.
        - skip_pm_code: claim_review/claim_doc_task call sites cannot be PMs
          to begin with; pm_cannot_execute_code is meaningless there.
        - skip_sequence: some verbs (resumption of an already-claimed task)
          do not need to re-validate sibling order.
        """
        agent = await self.task.agent_for(agent_id)
        role = agent.role if agent is not None else "developer"
        task_type = str(getattr(task, "task_type", "code") or "code")

        if not skip_pm_code and (
            guard := pm_cannot_execute_code_guard(role, task_type)
        ):
            return guard
        if not skip_role_typed and (guard := role_typed_claim_guard(role, task_type)):
            return guard
        in_progress = await self.task.list_in_progress_for_agent(agent_id)
        if guard := already_active_guard(in_progress, task.id):
            return guard
        paused = await self.task.list_paused_for_agent(agent_id)
        if guard := paused_tasks_guard(paused):
            return guard
        if not skip_sequence:
            siblings = await self._fetch_siblings(task)
            if guard := sibling_sequence_guard(task, siblings):
                return guard
        return None

    async def _fetch_siblings(self, task: Any) -> list[Any]:
        """Fetch sibling tasks for the sequence-order guard.

        Returns ``[]`` when the task has no parent (root task) so the
        guard short-circuits. Otherwise returns the parent's subtasks via
        ``TaskService.get_subtasks``.
        """
        parent_id = getattr(task, "parent_task_id", None)
        if parent_id is None:
            return []
        siblings: list[Any] = await self.task.get_subtasks(parent_id)
        return siblings

    async def _non_terminal_subtask_ids(self, parent_task_id: UUID) -> str:
        """Return a human-readable comma-separated list of non-terminal subtasks.

        Used by Gate Set F closure-time guards to name exactly which
        subtasks are blocking parent completion.
        """
        terminal = {"completed", "cancelled"}
        subtasks: list[Any] = await self.task.get_subtasks(parent_task_id)
        non_terminal = [s for s in subtasks if str(s.status) not in terminal]
        if not non_terminal:
            return "(none — query out-of-sync, retry)"
        # Format: "<id> (<status>)"
        return ", ".join(f"{s.id} ({s.status})" for s in non_terminal)

    async def i_will_work_on(
        self, agent_id: UUID, task_id: UUID, plan: str | None = None
    ) -> Envelope:
        """Claim/start/recover any actionable state of agent_id's task_id."""
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=agent_id,
                task_id=task_id,
                verb="i_will_work_on",
            )
        status = str(t.status)
        briefing = await self._briefing_for(agent_id, task_id)

        if status == "needs_revision":
            # Resumption after QA rejection: agent already owned the task,
            # role-typed claim already passed at original claim time.
            if t.assigned_to != agent_id:
                t = await self.task.claim(task_id, agent_id)
            t = await self.task.start(task_id, agent_id)
        elif status == "pending":
            # Fresh claim — run all claim-time gates BEFORE mutating state.
            if guard := await self._run_claim_guards(agent_id=agent_id, task=t):
                return await self._emit_rejection(
                    self._with_briefing(guard, briefing),
                    agent_id=agent_id,
                    task_id=task_id,
                    verb="i_will_work_on",
                )
            if t.assigned_to is None or t.assigned_to != agent_id:
                t = await self.task.claim(task_id, agent_id)
            if not t.plan and not plan:
                remediate = (
                    f"call i_will_work_on(task_id='{task_id}',"
                    f" plan='<one-paragraph plan describing what you will do>')"
                )
                return await self._emit_rejection(
                    Envelope.tracing_gap(
                        missing=["plan"],
                        remediate=remediate,
                        context_briefing=briefing,
                    ),
                    agent_id=agent_id,
                    task_id=task_id,
                    verb="i_will_work_on",
                )
            if plan:
                t = await self.task.set_plan(task_id, plan)
            t = await self.task.start(task_id, agent_id)
        elif status == "claimed" and t.assigned_to == agent_id:
            # Resumption: skip sibling-sequence (already passed at claim).
            # Still enforce already_active/paused so concurrent claims fail.
            guard = await self._run_claim_guards(
                agent_id=agent_id, task=t, skip_sequence=True
            )
            if guard:
                return await self._emit_rejection(
                    self._with_briefing(guard, briefing),
                    agent_id=agent_id,
                    task_id=task_id,
                    verb="i_will_work_on",
                )
            t = await self.task.start(task_id, agent_id)
        else:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"task {task_id} is in {status}; cannot start work",
                    remediate="call give_me_work() to find an actionable task",
                    context_briefing=briefing,
                ),
                agent_id=agent_id,
                task_id=task_id,
                verb="i_will_work_on",
            )

        await self._touch(task_id)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=(
                "edit + commit; call i_have_committed when ready,"
                " or i_am_done when finished"
            ),
            context_briefing=briefing,
        )

    @staticmethod
    def _with_briefing(env: Envelope, briefing: dict[str, Any]) -> Envelope:
        """Attach a context_briefing to an Envelope (mutate-and-return helper)."""
        env.context_briefing = briefing
        return env

    async def i_have_committed(self, agent_id: UUID, message: str) -> Envelope:
        """Record that the dev made a commit; auto-creates progress entry."""
        t = await self.task.get_active_task_for_agent(agent_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message="no active task for this agent",
                    remediate=(
                        "call give_me_work() then i_will_work_on(task_id, plan)"
                    ),
                    context_briefing=await self._briefing_for(agent_id, None),
                ),
                agent_id=agent_id,
                task_id=None,
                verb="i_have_committed",
            )
        if not t.plan:
            no_plan_remediate = (
                f"plan must be set first;"
                f" call i_will_work_on(task_id='{t.id}', plan='...')"
            )
            return await self._emit_rejection(
                Envelope.tracing_gap(
                    missing=["plan"],
                    remediate=no_plan_remediate,
                    context_briefing=await self._briefing_for(agent_id, t.id),
                ),
                agent_id=agent_id,
                task_id=t.id,
                verb="i_have_committed",
            )
        await self.task.add_progress(t.id, agent_id, message)
        await self._touch(t.id)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(t.id),
            next="continue working, or i_am_done when finished",
            context_briefing=await self._briefing_for(agent_id, t.id),
        )

    async def submit_for_qa(self, agent_id: UUID, task_id: UUID) -> Envelope:
        """Push the dev's branch and open a PR. Does NOT submit for QA itself —
        the dev calls ``i_am_done`` after this verb returns success.

        Gate E made ``i_am_done`` strict: it requires ``pr_number`` set. The
        catch-up shortcut lives off the dev manifest, so before this verb
        existed devs had no escape from the NO_PR rejection. ``submit_for_qa``
        is the explicit push + open-PR step, leaving ``i_am_done`` to do the
        strict submit.

        Pre-flight: caller must own the task, have committed at least once,
        and not already have a PR open. If a PR is already open, this verb
        is idempotent — it points the dev at ``i_am_done``.
        """
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=agent_id,
                task_id=task_id,
                verb="submit_for_qa",
            )
        briefing = await self._briefing_for(agent_id, task_id)
        if t.assigned_to != agent_id:
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message=f"task {task_id} is not assigned to you",
                    remediate="call give_me_work() to find your work",
                    context_briefing=briefing,
                ),
                agent_id=agent_id,
                task_id=task_id,
                verb="submit_for_qa",
            )
        if not t.commits:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message="no commits on this task yet",
                    remediate=(
                        "commit at least one change before submitting for QA — "
                        "call commit(message='<subject>')"
                    ),
                    context_briefing=briefing,
                ),
                agent_id=agent_id,
                task_id=task_id,
                verb="submit_for_qa",
            )
        if t.pr_number is not None:
            return Envelope.ok(
                status=str(t.status),
                task_id=str(task_id),
                next=(
                    f"PR #{t.pr_number} already open; call "
                    f"i_am_done(task_id, notes='...') when self-verified"
                ),
                context_briefing=briefing,
            )

        await self._touch(task_id)
        await self.git.push_branch(t.branch_name)
        parent = parent_branch_for(t.branch_name)
        pr = await self.git.create_pr(t.branch_name, parent=parent, is_root_pr=False)

        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=(
                f"PR #{pr['pr_number']} opened; call "
                f"i_am_done(task_id, notes='...') when self-verified"
            ),
            context_briefing=briefing,
        )

    async def i_am_done(self, agent_id: UUID, task_id: UUID, notes: str) -> Envelope:
        """Submit work for QA — strict path.

        Pre-gateway, the route layer enforced four field-level gates
        (NOT_SELF_VERIFIED, NO_COMMITS, NO_PR, NO_PROGRESS) before
        transitioning verifying → awaiting_qa. The strict gateway path
        re-enforces those exactly: dev MUST have already committed,
        pushed, opened a PR, reported progress, and self-verified before
        i_am_done can submit.

        For the smart-catch-up convenience that auto-runs the chain
        on the dev's behalf, see ``i_am_done_with_catchup``.
        """
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=agent_id,
                task_id=task_id,
                verb="i_am_done",
            )
        if t.assigned_to != agent_id:
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message="not assigned to you",
                    remediate="claim it via i_will_work_on(task_id) first",
                    context_briefing=await self._briefing_for(agent_id, task_id),
                ),
                agent_id=agent_id,
                task_id=task_id,
                verb="i_am_done",
            )

        # 1. Tracing-gate preconditions (progress / reflect / acceptance)
        if rejection := await self._check_tracing_gates(agent_id, task_id, t):
            return await self._emit_rejection(
                rejection, agent_id=agent_id, task_id=task_id, verb="i_am_done"
            )

        # 2. Field-level gates (Gate Set E) — strict.
        if rejection := await self._check_submit_qa_field_gates(agent_id, task_id, t):
            return await self._emit_rejection(
                rejection, agent_id=agent_id, task_id=task_id, verb="i_am_done"
            )

        # 3. Submit (no catch-up).
        submitted = await self.task.submit_qa(agent_id, task_id, notes)
        if submitted is not None:
            t = submitted
        await self._notify_qa(agent_id, task_id, t)
        await self._touch(task_id)
        return await self._build_i_am_done_ok(agent_id, task_id, t)

    async def i_am_done_with_catchup(
        self, agent_id: UUID, task_id: UUID, notes: str
    ) -> Envelope:
        """Submit work for QA — opt-in smart catch-up.

        Same tracing-gate preconditions as ``i_am_done``, but auto-runs
        the verify / push / PR / submit_qa chain on the dev's behalf
        instead of refusing on missing fields. Use this when the dev
        explicitly wants the gateway to drive the closure path.

        Pre-gateway behavior: dev had to call each step manually. The
        catch-up convenience exists for backward compat with workflows
        that rely on the implicit chain.
        """
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=agent_id,
                task_id=task_id,
                verb="i_am_done_with_catchup",
            )
        if t.assigned_to != agent_id:
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message="not assigned to you",
                    remediate="claim it via i_will_work_on(task_id) first",
                    context_briefing=await self._briefing_for(agent_id, task_id),
                ),
                agent_id=agent_id,
                task_id=task_id,
                verb="i_am_done_with_catchup",
            )
        if rejection := await self._check_tracing_gates(agent_id, task_id, t):
            return await self._emit_rejection(
                rejection,
                agent_id=agent_id,
                task_id=task_id,
                verb="i_am_done_with_catchup",
            )
        t = await self._run_catch_up(agent_id, task_id, t, notes)
        await self._notify_qa(agent_id, task_id, t)
        return await self._build_i_am_done_ok(agent_id, task_id, t)

    async def _check_tracing_gates(
        self, agent_id: UUID, task_id: UUID, t: Any
    ) -> Envelope | None:
        """Run progress / reflect / acceptance-criteria tracing gates."""
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
        if gate.passed:
            return None
        return await self._build_tracing_gap(agent_id, task_id, gate.missing)

    async def _check_submit_qa_field_gates(
        self, agent_id: UUID, task_id: UUID, t: Any
    ) -> Envelope | None:
        """Gate Set E field-level gates restored from tasks.py:903-940 at 0c3d15a.

        Each missing field becomes its own tracing_gap entry with the
        matching pre-gateway error code.
        """
        missing: list[str] = []
        hints: list[str] = []
        if not t.self_verified:
            missing.append("NOT_SELF_VERIFIED")
            hints.append(
                "call commit(message=...) to add a self-verified commit first;"
                " self_verified is set automatically when you commit on this"
                " task's branch"
            )
        if not t.commits:
            missing.append("NO_COMMITS")
            hints.append(
                "no commits on this task yet — call commit(message='<subject>')"
                " before i_am_done"
            )
        if t.pr_number is None:
            missing.append("NO_PR")
            hints.append(
                "no PR open — push your branch and open a PR before"
                " i_am_done (or call i_am_done_with_catchup to do it auto)"
            )
        if not missing:
            return None
        return Envelope.tracing_gap(
            missing=missing,
            remediate=" ; ".join(hints),
            context_briefing=await self._briefing_for(agent_id, task_id),
        )

    async def _build_i_am_done_ok(
        self, agent_id: UUID, task_id: UUID, t: Any
    ) -> Envelope:
        """Assemble the success envelope for i_am_done / _with_catchup."""
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
            await self.git.push_branch(t.branch_name)

        if t.pr_number is None:
            parent = parent_branch_for(t.branch_name)
            await self.git.create_pr(t.branch_name, parent=parent, is_root_pr=False)
            t = await self.task.get(task_id)  # refresh after PR creation

        return await self.task.submit_qa(agent_id, task_id, notes)

    async def _notify_qa(self, agent_id: UUID, task_id: UUID, t: Any) -> None:
        """Reassign + A2A-notify the QA agent for this task's team.

        ``submit_qa`` clears ``assigned_to`` to None. We then explicitly
        reassign to the QA agent so the orchestrator's per-agent task
        polling spawns QA (not the dev again) for the next stage.
        """
        qa_agent = await self.task.qa_agent_for_team(t.team)
        if qa_agent is not None:
            await self.task.reassign(task_id, qa_agent.id)
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
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=agent_id,
                task_id=task_id,
                verb="i_am_blocked",
            )
        await self.journal.write_struggle(
            agent_id=agent_id, task_id=task_id, content=reason
        )
        t = await self.task.escalate(agent_id, task_id, reason)
        await self._touch(task_id)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next="idle — PM will resolve and notify",
            context_briefing=await self._briefing_for(agent_id, task_id),
        )

    async def i_am_idle(self, agent_id: UUID) -> Envelope:
        """Report no more work. Soft-block if there are unread A2As or @mentions.

        Before marking the agent idle:

        1. Bail with ``idle_with_unread`` when context_briefing has unread A2A
           or @mentions (must address those first).
        2. Refuse with INVALID_STATE if the agent has any pending tasks
           assigned but never claimed — they must call i_will_work_on (dev/qa/
           doc) or i_will_plan (pm) first. (Gate Set C, pre-gateway implicit
           via the orchestrator's auto-respawn.)
        3. Auto-pause every in_progress task this agent owns so the
           orchestrator's PM-closure dispatcher can wake them when subtasks
           finish, instead of leaving the parent stuck at ``in_progress``
           forever.
        """
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
        if guard := await self._pending_assignment_guard(agent_id, briefing):
            return await self._emit_rejection(
                guard, agent_id=agent_id, task_id=None, verb="i_am_idle"
            )
        await self._auto_pause_in_progress_tasks(agent_id)
        await self.task.mark_agent_idle(agent_id)
        return Envelope.ok(
            status="idle",
            task_id=None,
            next="container will shut down",
            context_briefing=briefing,
        )

    async def _pending_assignment_guard(
        self, agent_id: UUID, briefing: dict[str, Any]
    ) -> Envelope | None:
        """Refuse i_am_idle when caller owns any pending (unclaimed) task.

        Pre-gateway: the orchestrator would respawn the agent after
        i_am_idle if they still owned pending work, leading to a tight
        respawn loop. Now an explicit refusal lets the agent fix it via
        i_will_work_on or i_will_plan before exiting.
        """
        assigned = await self.task.list_assigned_for_agent(agent_id)
        pending = [t for t in assigned if str(t.status) == "pending"]
        if not pending:
            return None
        first = pending[0]
        agent = await self.task.agent_for(agent_id)
        verb = (
            "i_will_plan"
            if agent and agent.role in ("cell_pm", "main_pm")
            else ("i_will_work_on")
        )
        return Envelope.invalid_state(
            message=(
                f"You have task {first.id} assigned but never claimed; "
                "cannot idle until claimed or unclaimed."
            ),
            remediate=(
                f"call {verb}(task_id='{first.id}') to start work, or"
                " unclaim it first; then retry i_am_idle"
            ),
            context_briefing=briefing,
        )

    async def _auto_pause_in_progress_tasks(self, agent_id: UUID) -> None:
        """Pause every in_progress task assigned to this agent.

        Restores the pre-Phase-4 auto-pause behavior: a PM that called
        i_will_plan and is now idle leaves the parent at ``paused`` so the
        closure dispatcher knows to respawn it when subtasks complete.
        """
        in_progress = await self.task.list_in_progress_for_agent(agent_id)
        for t in in_progress:
            await self.task.pause_for_agent(agent_id, t.id)

    # --- Phase 2 (QA) verbs ---

    async def claim_review(self, qa_agent_id: UUID, task_id: UUID) -> Envelope:
        """QA agent claims task in awaiting_qa for review.

        The response includes evidence (pr_url, pr_number, commits, files_changed,
        journal_highlights, acceptance_criteria_status) INLINE so the QA agent
        cannot miss the PR data. Marks `qa_evidence_inspected=true` automatically.
        """
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="claim_review",
            )
        if str(t.status) != "awaiting_qa":
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=(
                        f"task {task_id} is in {t.status}, "
                        "expected awaiting_qa for review"
                    ),
                    remediate="call give_me_work() to find an actionable QA task",
                    context_briefing=await self._briefing_for(qa_agent_id, task_id),
                ),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="claim_review",
            )

        # Gate Set A: ALREADY_ACTIVE / PAUSED_TASKS_EXIST guard QA from
        # juggling reviews while their previous in_progress task is open.
        # role_typed/PM-code skipped: QA verb only ever fires for QA role.
        # sequence skipped: QA reviews are siblings on a different axis.
        guard = await self._run_claim_guards(
            agent_id=qa_agent_id,
            task=t,
            skip_role_typed=True,
            skip_pm_code=True,
            skip_sequence=True,
        )
        if guard:
            return await self._emit_rejection(
                self._with_briefing(
                    guard, await self._briefing_for(qa_agent_id, task_id)
                ),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="claim_review",
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
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="pass_review",
            )
        if t.assigned_to != qa_agent_id:
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message="not assigned to you",
                    remediate="claim it via claim_review(task_id) first",
                    context_briefing=await self._briefing_for(qa_agent_id, task_id),
                ),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="pass_review",
            )

        has_learning = await self.journal.has_learning_for_task(qa_agent_id, task_id)
        missing = self._check_qa_pass_gates(
            notes=notes,
            has_learning=has_learning,
            evidence_inspected=t.qa_evidence_inspected,
        )
        if missing:
            return await self._emit_rejection(
                self._qa_tracing_gap(
                    missing, task_id, await self._briefing_for(qa_agent_id, task_id)
                ),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="pass_review",
            )

        t = await self.task.qa_pass(qa_agent_id, task_id, notes)

        # qa_pass clears assigned_to to None; reassign to the team's
        # documenter so the orchestrator spawns the right agent next.
        doc_agent = await self.task.documenter_for_team(t.team)
        if doc_agent is not None:
            await self.task.reassign(task_id, doc_agent.id)
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
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="fail_review",
            )
        if t.assigned_to != qa_agent_id:
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message="not assigned to you",
                    remediate="claim it via claim_review(task_id) first",
                    context_briefing=await self._briefing_for(qa_agent_id, task_id),
                ),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="fail_review",
            )
        if not issues:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message="fail_review requires at least one issue",
                    remediate="pass issues=['<concrete actionable issue>', ...]",
                    context_briefing=await self._briefing_for(qa_agent_id, task_id),
                ),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="fail_review",
            )

        has_learning = await self.journal.has_learning_for_task(qa_agent_id, task_id)
        notes = "Issues:\n" + "\n".join(f"- {issue}" for issue in issues)
        missing = self._check_qa_pass_gates(
            notes=notes,
            has_learning=has_learning,
            evidence_inspected=t.qa_evidence_inspected,
        )
        if missing:
            return await self._emit_rejection(
                self._qa_tracing_gap(
                    missing, task_id, await self._briefing_for(qa_agent_id, task_id)
                ),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="fail_review",
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
                ),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="claim_doc_task",
            )

        # Gate Set A: ALREADY_ACTIVE / PAUSED_TASKS_EXIST. The doc verb only
        # ever fires for documenter role; PM-code and role-typed claim guards
        # are skipped. Sequence guard is also irrelevant here.
        guard = await self._run_claim_guards(
            agent_id=doc_agent_id,
            task=t,
            skip_role_typed=True,
            skip_pm_code=True,
            skip_sequence=True,
        )
        if guard:
            return await self._emit_rejection(
                self._with_briefing(
                    guard, await self._briefing_for(doc_agent_id, task_id)
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
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="i_documented",
            )
        if t.assigned_to != doc_agent_id:
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message="not assigned to you",
                    remediate="claim it via claim_doc_task(task_id) first",
                    context_briefing=await self._briefing_for(doc_agent_id, task_id),
                ),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="i_documented",
            )
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
                ),
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
                ),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="i_documented",
            )
        t = await self.task.docs_complete(
            doc_agent_id, task_id, notes=notes, files=files
        )
        # docs_complete may have promoted the task to awaiting_pm_review and
        # already routed it to the PM up the parent chain
        # (_maybe_advance_to_pm_review). Explicitly reassign anyway to the
        # cell PM for this team — guarantees a respawn target even when the
        # parent-chain heuristic returns None or picks the wrong PM.
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
        )

    async def _i_will_plan_preflight(
        self, pm_agent_id: UUID, task_id: UUID, t: Any, plan: str
    ) -> Envelope | None:
        """Run i_will_plan's role / status / plan / claim guards. None = pass."""
        agent = await self.task.agent_for(pm_agent_id)
        if agent is None or agent.role not in ("cell_pm", "main_pm"):
            return Envelope.not_authorized(
                message="only cell_pm or main_pm may call i_will_plan",
                remediate="this verb is reserved for PMs",
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )
        if str(t.status) != "pending":
            return Envelope.invalid_state(
                message=f"task {task_id} is in {t.status}, expected pending",
                remediate="call give_me_work() to find a pending task to plan",
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )
        if not plan or not plan.strip():
            return Envelope.tracing_gap(
                missing=["plan"],
                remediate=(
                    f"call i_will_plan(task_id='{task_id}',"
                    " plan='<one-paragraph plan describing the breakdown>')"
                ),
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )
        # Gate Set A: PM_CANNOT_EXECUTE_CODE — cell_pm/main_pm can only plan
        # non-code tasks. role_typed_claim_guard is skipped here because
        # i_will_plan only services PM roles, which fall into the PM-code
        # branch. ALREADY_ACTIVE/PAUSED still apply.
        guard = await self._run_claim_guards(
            agent_id=pm_agent_id, task=t, skip_role_typed=True
        )
        if guard:
            return self._with_briefing(
                guard, await self._briefing_for(pm_agent_id, task_id)
            )
        return None

    async def i_will_plan(
        self, pm_agent_id: UUID, task_id: UUID, plan: str
    ) -> Envelope:
        """PM mirror of i_will_work_on for parent tasks.

        Transitions a pending task owned (or claimable by) this PM into
        in_progress with the supplied plan. Required before a PM can call
        ``delegate`` to spawn subtasks.
        """
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="i_will_plan",
            )
        rejection = await self._i_will_plan_preflight(pm_agent_id, task_id, t, plan)
        if rejection is not None:
            return await self._emit_rejection(
                rejection,
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="i_will_plan",
            )

        if t.assigned_to is None or t.assigned_to != pm_agent_id:
            t = await self.task.claim(task_id, pm_agent_id)
            if t is None:
                return await self._emit_rejection(
                    Envelope.invalid_state(
                        message="claim failed",
                        remediate="task may already be claimed by another agent",
                        context_briefing=await self._briefing_for(pm_agent_id, task_id),
                    ),
                    agent_id=pm_agent_id,
                    task_id=task_id,
                    verb="i_will_plan",
                )
        await self.task.set_plan(task_id, plan)
        t = await self.task.start(task_id, pm_agent_id)
        await self._touch(task_id)
        return Envelope.ok(
            status=str(t.status) if t else "in_progress",
            task_id=str(task_id),
            next=(
                "delegate(parent_task_id, title, description, assigned_to, team)"
                " for each subtask, then i_am_idle"
            ),
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        )

    async def delegate(
        self,
        pm_agent_id: UUID,
        parent_task_id: UUID,
        inputs: DelegateInputs,
    ) -> Envelope:
        """Create a subtask under parent_task_id with delegation-chain validation.

        Main PM may delegate to a Cell PM slug; a Cell PM may delegate to
        its own team's developers. Anything else is rejected with an
        explicit hint about the chain.
        """
        parent = await self.task.get(parent_task_id)
        if parent is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {parent_task_id} not found"),
                agent_id=pm_agent_id,
                task_id=parent_task_id,
                verb="delegate",
            )
        agent = await self.task.agent_for(pm_agent_id)
        guard = await self._delegate_guard(
            pm_agent_id, parent_task_id, parent, agent, inputs
        )
        if guard is not None:
            return await self._emit_rejection(
                guard,
                agent_id=pm_agent_id,
                task_id=parent_task_id,
                verb="delegate",
            )

        new_task = await self._create_subtask_from_inputs(
            pm_agent_id, parent_task_id, parent, inputs
        )
        return Envelope.ok(
            status="created",
            task_id=str(new_task.id),
            next="continue delegating subtasks, or i_am_idle when done",
            context_briefing=await self._briefing_for(pm_agent_id, parent_task_id),
        )

    # Gate Set B subtask cap (pre-gateway implicit, made explicit here).
    # Soft warn at 8, hard block at 13. Cap enforced by ``_subtask_cap_guard``.
    _SUBTASK_HARD_CAP: int = 12

    async def _delegate_guard(
        self,
        pm_agent_id: UUID,
        parent_task_id: UUID,
        parent: Any,
        agent: Any,
        inputs: DelegateInputs,
    ) -> Envelope | None:
        """Return rejection Envelope if a delegate precondition fails; else None."""
        if guard := await self._delegate_role_guards(
            pm_agent_id, parent_task_id, agent, inputs
        ):
            return guard
        if guard := await self._delegate_static_guards(
            pm_agent_id, parent_task_id, parent, inputs
        ):
            return guard
        # Gate Set B: PARENT_NOT_CLAIMED + SUBTASK_CAP
        return await self._delegate_lifecycle_guards(
            pm_agent_id, parent_task_id, parent
        )

    async def _delegate_role_guards(
        self,
        pm_agent_id: UUID,
        parent_task_id: UUID,
        agent: Any,
        inputs: DelegateInputs,
    ) -> Envelope | None:
        """Role + delegation-chain guards (the original two)."""
        if agent is None or agent.role not in ("cell_pm", "main_pm"):
            return Envelope.not_authorized(
                message="only cell_pm or main_pm may delegate",
                remediate="this verb is reserved for PMs",
                context_briefing=await self._briefing_for(pm_agent_id, parent_task_id),
            )
        chain_error = self._validate_delegation_chain(agent.role, inputs.assigned_to)
        if chain_error is not None:
            return Envelope.not_authorized(
                message=chain_error,
                remediate=(
                    "Main PM delegates to be-pm/fe-pm/ux-pm. "
                    "Cell PM delegates to its own team's devs (e.g. backend "
                    "PM -> be-dev-1/be-dev-2)."
                ),
                context_briefing=await self._briefing_for(pm_agent_id, parent_task_id),
            )
        return None

    async def _delegate_static_guards(
        self,
        pm_agent_id: UUID,
        parent_task_id: UUID,
        parent: Any,
        inputs: DelegateInputs,
    ) -> Envelope | None:
        """Slug / project_id / enum guards. Pure data-shape checks."""
        from roboco.seeds.initial_data import AGENT_UUIDS

        if inputs.assigned_to not in AGENT_UUIDS:
            return Envelope.invalid_state(
                message=f"unknown agent slug: {inputs.assigned_to!r}",
                remediate=f"valid slugs: {sorted(AGENT_UUIDS)}",
                context_briefing=await self._briefing_for(pm_agent_id, parent_task_id),
            )
        if parent.project_id is None:
            return Envelope.invalid_state(
                message="parent task has no project_id",
                remediate="parent task must have a project to inherit",
                context_briefing=await self._briefing_for(pm_agent_id, parent_task_id),
            )
        try:
            self._resolve_delegate_enums(inputs)
        except ValueError as exc:
            return Envelope.invalid_state(
                message=f"invalid enum value: {exc}",
                remediate="check team/task_type/estimated_complexity",
                context_briefing=await self._briefing_for(pm_agent_id, parent_task_id),
            )
        return None

    async def _delegate_lifecycle_guards(
        self,
        pm_agent_id: UUID,
        parent_task_id: UUID,
        parent: Any,
    ) -> Envelope | None:
        """Gate Set B: PARENT_NOT_CLAIMED + SUBTASK_CAP.

        Pre-gateway, the orchestrator enforced both implicitly: a PM only
        ever called task_create after the orchestrator spawned them
        post-claim, and naturally never created more than a handful of
        subtasks in one spawn cycle.

        With the gateway exposing ``delegate`` as a first-class verb,
        these gates must be explicit.
        """
        if str(parent.status) != "in_progress":
            return Envelope.invalid_state(
                message=(
                    f"parent task {parent_task_id} is in {parent.status}; "
                    "must be in_progress to accept subtasks"
                ),
                remediate=(
                    f"call i_will_plan(parent_task_id='{parent_task_id}',"
                    " plan='...') before delegating subtasks"
                ),
                context_briefing=await self._briefing_for(pm_agent_id, parent_task_id),
            )
        if parent.assigned_to != pm_agent_id:
            return Envelope.not_authorized(
                message=(
                    f"parent task {parent_task_id} is assigned to "
                    f"{parent.assigned_to}, not you"
                ),
                remediate=(
                    f"call i_will_plan(parent_task_id='{parent_task_id}',"
                    " plan='...') to claim before delegating subtasks"
                ),
                context_briefing=await self._briefing_for(pm_agent_id, parent_task_id),
            )
        existing = await self.task.get_subtasks(parent_task_id)
        if len(existing) >= self._SUBTASK_HARD_CAP:
            return Envelope.invalid_state(
                message=(
                    f"parent already has {len(existing)} subtasks; "
                    f"cap is {self._SUBTASK_HARD_CAP}"
                ),
                remediate=(
                    "consolidate or split into a separate parent task before"
                    " adding more subtasks"
                ),
                context_briefing=await self._briefing_for(pm_agent_id, parent_task_id),
            )
        return None

    @staticmethod
    def _resolve_delegate_enums(inputs: DelegateInputs) -> tuple[Any, Any, Any]:
        """Convert string inputs to Team/TaskType/Complexity enums.

        Raises ValueError if any string is not a member of its enum.
        """
        from roboco.models.base import Complexity, TaskType, Team

        return (
            Team(inputs.team),
            TaskType(inputs.task_type),
            Complexity(inputs.estimated_complexity),
        )

    async def _create_subtask_from_inputs(
        self,
        pm_agent_id: UUID,
        parent_task_id: UUID,
        parent: Any,
        inputs: DelegateInputs,
    ) -> Any:
        """Resolve enums + AGENT_UUIDS slug and call TaskService.create_subtask."""
        from roboco.models.task import TaskCreateRequest
        from roboco.seeds.initial_data import AGENT_UUIDS

        team_enum, type_enum, complexity_enum = self._resolve_delegate_enums(inputs)
        assignee_id = UUID(AGENT_UUIDS[inputs.assigned_to])
        req = TaskCreateRequest(
            title=inputs.title,
            description=inputs.description,
            acceptance_criteria=inputs.acceptance_criteria or [],
            team=team_enum,
            created_by=pm_agent_id,
            project_id=UUID(str(parent.project_id)),
            parent_task_id=parent_task_id,
            assigned_to=assignee_id,
            task_type=type_enum,
            estimated_complexity=complexity_enum,
        )
        return await self.task.create_subtask(req)

    @staticmethod
    def _validate_delegation_chain(pm_role: str, target_slug: str) -> str | None:
        """Return error string if delegation chain is invalid; else None."""
        cell_pm_targets = {
            "cell_pm": {
                "backend": {"be-dev-1", "be-dev-2"},
                "frontend": {"fe-dev-1", "fe-dev-2"},
                "ux_ui": {"ux-dev-1", "ux-dev-2"},
            },
        }
        main_pm_targets = {"be-pm", "fe-pm", "ux-pm"}

        if pm_role == "main_pm":
            if target_slug not in main_pm_targets:
                return (
                    f"main_pm cannot delegate to {target_slug!r}; allowed: "
                    f"{sorted(main_pm_targets)}"
                )
            return None
        if pm_role == "cell_pm":
            allowed_for_any_team: set[str] = set()
            for team_targets in cell_pm_targets["cell_pm"].values():
                allowed_for_any_team |= team_targets
            if target_slug not in allowed_for_any_team:
                return (
                    f"cell_pm cannot delegate to {target_slug!r}; allowed: "
                    f"{sorted(allowed_for_any_team)}"
                )
            return None
        return f"role {pm_role!r} cannot delegate"

    async def submit_up(self, pm_agent_id: UUID, task_id: UUID, notes: str) -> Envelope:
        """Cell PM bubbles a finished cell-scope task up to the Main PM.

        Opens a cell-level PR into the parent (Main PM) branch, transitions
        the task to ``awaiting_pm_review``, and reassigns to the Main PM.
        Required preconditions: caller owns the task, all subtasks
        terminal, journal:decision logged, notes >= 20 chars.
        """
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="submit_up",
            )
        guard = await self._submit_up_guard(pm_agent_id, task_id, t, notes)
        if guard is not None:
            return await self._emit_rejection(
                guard,
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="submit_up",
            )

        parent_branch = parent_branch_for(t.branch_name)
        await self.git.create_pr(t.branch_name, parent=parent_branch, is_root_pr=False)
        t = await self.task.submit_pm_review(pm_agent_id, task_id, notes)
        if t is None:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message="could not transition to awaiting_pm_review",
                    remediate="check task state — must be in_progress with PR ready",
                    context_briefing=await self._briefing_for(pm_agent_id, task_id),
                ),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="submit_up",
            )
        await self._handoff_to_main_pm(pm_agent_id, task_id)
        return Envelope.ok(
            status="awaiting_pm_review",
            task_id=str(task_id),
            next="idle until Main PM reviews",
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        )

    async def _submit_up_guard(
        self, pm_agent_id: UUID, task_id: UUID, t: Any, notes: str
    ) -> Envelope | None:
        """Return a rejection Envelope if any submit_up precondition fails."""
        ownership = await self._submit_up_ownership_guard(
            pm_agent_id, task_id, t, notes
        )
        if ownership is not None:
            return ownership
        return await self._submit_up_state_guard(pm_agent_id, task_id, t)

    async def _submit_up_ownership_guard(
        self, pm_agent_id: UUID, task_id: UUID, t: Any, notes: str
    ) -> Envelope | None:
        """Role + assignment + notes-length guards for submit_up."""
        from roboco.config import settings as roboco_settings

        agent = await self.task.agent_for(pm_agent_id)
        if agent is None or agent.role != "cell_pm":
            return Envelope.not_authorized(
                message="submit_up is reserved for cell_pm",
                remediate="main_pm should call complete on root tasks instead",
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )
        if t.assigned_to != pm_agent_id:
            return Envelope.not_authorized(
                message="not assigned to you",
                remediate="claim the task or wait for assignment",
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )
        if not notes or len(notes) < roboco_settings.docs_notes_min_chars:
            return Envelope.tracing_gap(
                missing=["notes>=min"],
                remediate=(
                    "submit_up requires substantive notes describing the"
                    " cell's contribution (>= 20 chars)."
                ),
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )
        return None

    async def _submit_up_state_guard(
        self, pm_agent_id: UUID, task_id: UUID, t: Any
    ) -> Envelope | None:
        """Journal + subtask-closure + branch guards for submit_up."""
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
        if not await self.task.all_subtasks_terminal(task_id):
            non_terminal = await self._non_terminal_subtask_ids(task_id)
            return Envelope.tracing_gap(
                missing=["subtasks not all terminal"],
                remediate=(
                    "all subtasks must be in completed/cancelled before"
                    " bubbling up. Non-terminal subtasks: " + non_terminal
                ),
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )
        if not t.branch_name:
            return Envelope.invalid_state(
                message="task has no branch; cannot open cell-level PR",
                remediate="cell PMs must claim+plan their parent task first",
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )
        return None

    async def _handoff_to_main_pm(self, pm_agent_id: UUID, task_id: UUID) -> None:
        """Reassign the task to the Main PM and A2A-notify the handoff."""
        main_pm = await self.task.main_pm_agent()
        if main_pm is None:
            return
        main_pm_uuid = UUID(str(main_pm.id))
        await self.task.reassign(task_id, main_pm_uuid)
        await self.a2a.send(
            from_agent=pm_agent_id,
            to_agent=main_pm_uuid,
            skill="task_management",
            task_id=task_id,
            body=f"Cell scope complete for {task_id}. Ready for Main PM review.",
        )

    async def pm_give_me_work(self, pm_agent_id: UUID) -> Envelope:
        """Return the PM's first assigned task in any active status, or idle.

        Mirrors the developer's give_me_work but does not filter to dev-only
        statuses — PMs care about all assigned tasks (planning, paused, in
        progress, awaiting_pm_review).
        """
        assigned = await self.task.list_assigned_for_agent(pm_agent_id)
        if assigned:
            t = assigned[0]
            await self._touch(t.id)
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=self._pm_next_hint(str(t.status), t.id),
                context_briefing=await self._briefing_for(pm_agent_id, t.id),
            )
        return Envelope.ok(
            status="idle",
            task_id=None,
            next="no assigned work — call triage() or i_am_idle()",
            context_briefing=await self._briefing_for(pm_agent_id, None),
        )

    @staticmethod
    def _pm_next_hint(status: str, task_id: Any) -> str:
        """Compose a status-aware next hint for PMs."""
        if status == "pending":
            return f"call i_will_plan(task_id='{task_id}', plan='...') to start"
        if status == "paused":
            return (
                f"check subtasks; when terminal, call complete(task_id='{task_id}')"
                " or submit_up()"
            )
        if status == "blocked":
            return f"investigate then unblock(task_id='{task_id}')"
        if status == "awaiting_pm_review":
            return f"review and complete(task_id='{task_id}')"
        return f"act on task {task_id} — current status: {status}"

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
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="unblock",
            )
        if str(t.status) != "blocked":
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"task {task_id} is in {t.status}, expected blocked",
                    remediate=(
                        "this task is not blocked; call triage() to find blocked tasks"
                    ),
                    context_briefing=await self._briefing_for(pm_agent_id, task_id),
                ),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="unblock",
            )

        has_decision = await self.journal.has_decision_for_task(pm_agent_id, task_id)
        if not has_decision:
            from roboco.services.gateway.remediation import (
                hint_for_missing_journal_decision,
            )

            return await self._emit_rejection(
                Envelope.tracing_gap(
                    missing=["journal:decision"],
                    remediate=hint_for_missing_journal_decision(),
                    context_briefing=await self._briefing_for(pm_agent_id, task_id),
                ),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="unblock",
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
            non_terminal = await self._non_terminal_subtask_ids(task_id)
            return Envelope.tracing_gap(
                missing=["subtasks not all terminal"],
                remediate=(
                    "all subtasks must be in completed/cancelled before"
                    " completing parent. Non-terminal subtasks: " + non_terminal
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
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="cell_pm_complete",
            )
        guard = await self._cell_pm_complete_guard(pm_agent_id, task_id, t)
        if guard is not None:
            return await self._emit_rejection(
                guard,
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="cell_pm_complete",
            )
        target = parent_branch_for(t.branch_name)
        merge_result = await self.git.pr_merge(t.pr_number, target=target)
        leaf_parent_id = t.parent_task_id
        leaf_team = t.team
        t = await self.task.cell_pm_complete(
            pm_agent_id,
            task_id,
            notes,
            merge_commit=merge_result.get("merge_commit_sha"),
        )
        # Now that the leaf is completed, propagate the completion up to the
        # parent task: if the parent's subtasks are all terminal, hand the
        # parent off to the cell_pm for that team so it gets respawned for
        # the next stage (cell-PM PR merge or main-PM hand-off).
        if leaf_parent_id is not None:
            await self._maybe_advance_parent_to_pm_review(leaf_parent_id, leaf_team)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=f"merged into {target}; triage() for next item",
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        )

    async def _maybe_advance_parent_to_pm_review(
        self, parent_task_id: UUID, leaf_team: Any
    ) -> None:
        """Promote a parent task to awaiting_pm_review once all subtasks finish.

        Walks up from the just-completed leaf. If every direct subtask of
        the parent is terminal, set the parent's ``assigned_to`` to the cell
        PM for the parent's team so the orchestrator spawns the right PM
        for the next stage (review/merge/escalate). Status itself is left
        untouched here — the cell PM transitions it via ``complete``.
        """
        parent = await self.task.get(parent_task_id)
        if parent is None:
            return
        all_terminal = await self.task.all_subtasks_terminal(parent_task_id)
        if not all_terminal:
            return
        team = parent.team or leaf_team
        if team is None:
            return
        pm_agent = await self.task.cell_pm_for_team(team)
        if pm_agent is None:
            return
        await self.task.reassign(parent_task_id, pm_agent.id)

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
            non_terminal = await self._non_terminal_subtask_ids(root_task_id)
            return Envelope.tracing_gap(
                missing=["subtasks not all terminal"],
                remediate=(
                    "all subtasks must be in completed/cancelled state. "
                    "Non-terminal subtasks: " + non_terminal
                ),
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
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {root_task_id} not found"),
                agent_id=main_pm_agent_id,
                task_id=root_task_id,
                verb="main_pm_complete",
            )
        guard = await self._main_pm_complete_guard(main_pm_agent_id, root_task_id, t)
        if guard is not None:
            return await self._emit_rejection(
                guard,
                agent_id=main_pm_agent_id,
                task_id=root_task_id,
                verb="main_pm_complete",
            )

        needs_pr = t.pr_number is None
        if not needs_pr:
            current_target = await self.git.pr_target(t.pr_number)
            needs_pr = current_target != "master"
        if needs_pr:
            await self.git.create_pr(t.branch_name, parent="master", is_root_pr=True)

        t = await self.task.escalate_to_ceo(main_pm_agent_id, root_task_id, notes)
        # CEO acts via the UI, not as an agent the orchestrator spawns. Clear
        # ``assigned_to`` so no agent gets respawned to chase this task while
        # it sits in awaiting_ceo_approval.
        await self.task.reassign(root_task_id, None)
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
        return await self._emit_rejection(
            Envelope.not_authorized(
                message=f"role {agent.role} cannot complete tasks via this verb",
                remediate="only cell_pm and main_pm can call complete",
                context_briefing=await self._briefing_for(agent_id, task_id),
            ),
            agent_id=agent_id,
            task_id=task_id,
            verb="complete",
        )

    async def escalate_up(
        self, pm_agent_id: UUID, task_id: UUID, reason: str
    ) -> Envelope:
        """Escalate a task to the agent's escalation_target role."""
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="escalate_up",
            )

        has_decision = await self.journal.has_decision_for_task(pm_agent_id, task_id)
        if not has_decision:
            from roboco.services.gateway.remediation import (
                hint_for_missing_journal_decision,
            )

            return await self._emit_rejection(
                Envelope.tracing_gap(
                    missing=["journal:decision"],
                    remediate=hint_for_missing_journal_decision(),
                    context_briefing=await self._briefing_for(pm_agent_id, task_id),
                ),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="escalate_up",
            )

        me = await self.task.agent_for(pm_agent_id)
        target_slug = me.escalation_target if me else None
        if not target_slug:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message="no escalation target configured for your role",
                    remediate="check agents_config.py ESCALATION_CHAIN for your slug",
                    context_briefing=await self._briefing_for(pm_agent_id, task_id),
                ),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="escalate_up",
            )

        t = await self.task.escalate(pm_agent_id, task_id, reason)
        if t is None:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=(
                        f"could not escalate task {task_id} to {target_slug}: "
                        "target agent not found or task missing"
                    ),
                    remediate=(
                        f"verify {target_slug} exists in agents table and that the "
                        "task is still present"
                    ),
                    context_briefing=await self._briefing_for(pm_agent_id, task_id),
                ),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="escalate_up",
            )
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=f"escalated to {target_slug}; idle until they respond",
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        )

    # --- Phase 4 (board) verbs ---

    async def escalate_to_ceo(
        self, agent_id: UUID, task_id: UUID, reason: str
    ) -> Envelope:
        """Board/Main PM escalates task_id to CEO with reason."""
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=agent_id,
                task_id=task_id,
                verb="escalate_to_ceo",
            )
        me = await self.task.agent_for(agent_id)
        if me.role not in ("main_pm", "product_owner", "head_marketing"):
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message=f"role {me.role} cannot escalate to CEO directly",
                    remediate="use escalate_up() to go through your escalation chain",
                    context_briefing=await self._briefing_for(agent_id, task_id),
                ),
                agent_id=agent_id,
                task_id=task_id,
                verb="escalate_to_ceo",
            )
        if str(t.status) != "awaiting_pm_review":
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=(
                        f"task {task_id} is in {t.status}, expected awaiting_pm_review"
                    ),
                    remediate="this task is not at the gate for CEO approval",
                    context_briefing=await self._briefing_for(agent_id, task_id),
                ),
                agent_id=agent_id,
                task_id=task_id,
                verb="escalate_to_ceo",
            )
        has_decision = await self.journal.has_decision_for_task(agent_id, task_id)
        if not has_decision:
            from roboco.services.gateway.remediation import (
                hint_for_missing_journal_decision,
            )

            return await self._emit_rejection(
                Envelope.tracing_gap(
                    missing=["journal:decision"],
                    remediate=hint_for_missing_journal_decision(),
                    context_briefing=await self._briefing_for(agent_id, task_id),
                ),
                agent_id=agent_id,
                task_id=task_id,
                verb="escalate_to_ceo",
            )
        t = await self.task.escalate_to_ceo(task_id, agent_role=me.role, notes=reason)
        # Same as main_pm_complete: CEO acts via UI, not as a spawnable agent.
        await self.task.reassign(task_id, None)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next="idle until CEO acts via UI",
            context_briefing=await self._briefing_for(agent_id, task_id),
        )

    async def board_triage(self, board_agent_id: UUID) -> Envelope:
        """Phase 4: Board triage — next strategic root task awaiting PM review."""
        strategic = await self.task.list_strategic_for_board()
        if strategic:
            t = strategic[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=(
                    f"review and call escalate_to_ceo(task_id='{t.id}', reason=...)"
                    " or i_am_idle"
                ),
                context_briefing=await self._briefing_for(board_agent_id, t.id),
            )
        return Envelope.ok(
            status="idle",
            task_id=None,
            next="no strategic-review work — i_am_idle",
            context_briefing=await self._briefing_for(board_agent_id, None),
        )

    async def auditor_triage(self, auditor_agent_id: UUID) -> Envelope:
        """Phase 4: Auditor triage — surfaces anomalies (long-running blocked, etc.)."""
        anomalies = await self.task.list_long_running_blocked()
        if anomalies:
            t = anomalies[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=(
                    "log a reflect-note observing the anomaly via "
                    f"note(scope='reflect', task_id='{t.id}', text='...')"
                ),
                context_briefing=await self._briefing_for(auditor_agent_id, t.id),
            )
        return Envelope.ok(
            status="idle",
            task_id=None,
            next="no anomalies — i_am_idle",
            context_briefing=await self._briefing_for(auditor_agent_id, None),
        )
