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
    """Bundle of fields the ``delegate`` verb receives from the route layer.

    `task_type` has no default — the v2 schema enforces this at the HTTP
    boundary, but defaulting here too would let direct callers (tests,
    other internal code) silently pick 'code' and recreate the
    2026-05-08 deadlock.
    """

    title: str
    description: str
    assigned_to: str
    team: str
    task_type: str
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

        Introspection (`current_state` + `valid_next_verbs`) is applied
        at the call site via `Envelope.with_introspection(task, role)`
        rather than here — this keeps the rejection path's signature
        narrow and lets the helper stay framework-clean.

        Stashes ``correlation_id`` from the structlog contextvars (bound
        by ``CorrelationIdMiddleware`` for the inbound request) and a
        per-attempt id into the audit row's ``details`` JSONB. The
        attempt_id is unique per rejection event so post-mortem queries
        can group "all attempts on task X within a window" without
        confusing two distinct calls that share a correlation_id (audit
        P2-7/D-N).
        """
        if env.error is None:
            return env
        from uuid import uuid4 as _uuid4

        details: dict[str, Any] = {
            "verb": verb,
            "reason": env.error,
            "message": env.message,
            "missing": env.missing or [],
            "attempt_id": str(_uuid4()),
        }
        cid = structlog.contextvars.get_contextvars().get("correlation_id")
        if cid is not None:
            details["correlation_id"] = cid
        try:
            await self.audit.log_event(
                event_type="gateway.rejected",
                agent_id=agent_id,
                task_id=task_id,
                details=details,
            )
        except Exception as exc:
            # Audit is best-effort: it must NEVER block the verb. The agent's
            # response is the contract; the audit row is observability-only.
            logger.warning("audit.log_event failed", error=str(exc), verb=verb)
        return env

    # --- Phase 1 (developer) verbs ---

    async def give_me_work(self, agent_id: UUID) -> Envelope:
        """Return the agent's most-actionable task or signal idle."""
        agent = await self._deps.task.agent_for(agent_id)
        role = str(agent.role) if agent is not None else "developer"
        assigned = await self._deps.task.list_assigned_for_agent(agent_id)
        if assigned:
            t = assigned[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=f"call i_will_work_on(task_id='{t.id}', plan='<plan>') to start",
                context_briefing=await self._briefing_for(agent_id, t.id),
            ).with_introspection(task=t, role=role)
        paused = await self._deps.task.list_paused_for_agent(agent_id)
        if paused:
            t = paused[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=f"call resume(task_id='{t.id}') to continue paused work",
                context_briefing=await self._briefing_for(agent_id, t.id),
            ).with_introspection(task=t, role=role)
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

    @staticmethod
    def _run_role_guards(
        role: str, task_type: str, *, skip_pm_code: bool, skip_role_typed: bool
    ) -> Envelope | None:
        """Sync role-based guards (pm_cannot_execute_code, role_typed)."""
        if not skip_pm_code and (
            guard := pm_cannot_execute_code_guard(role, task_type)
        ):
            return guard
        if not skip_role_typed and (guard := role_typed_claim_guard(role, task_type)):
            return guard
        return None

    async def _run_claim_concurrency_guards(
        self, agent_id: UUID, task: Any, *, skip_sequence: bool
    ) -> Envelope | None:
        """Async concurrency-based guards (already_active, paused, sequence)."""
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
        task_type = str(task.task_type)

        if guard := self._run_role_guards(
            role,
            task_type,
            skip_pm_code=skip_pm_code,
            skip_role_typed=skip_role_typed,
        ):
            return guard
        return await self._run_claim_concurrency_guards(
            agent_id, task, skip_sequence=skip_sequence
        )

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

    async def _subtasks_not_terminal_envelope(
        self,
        agent_id: UUID,
        task_id: UUID,
        *,
        context_phrase: str,
    ) -> Envelope | None:
        """Return a tracing_gap rejection if any subtask of ``task_id`` is non-terminal.

        Centralizes the closure-time "all subtasks terminal" gate that fires
        in submit_up, cell_pm_complete, main_pm_complete (audit P2-3/D-15).
        ``context_phrase`` lets each caller name the action being blocked
        (e.g., "bubbling up", "completing parent").
        """
        if await self.task.all_subtasks_terminal(task_id):
            return None
        non_terminal = await self._non_terminal_subtask_ids(task_id)
        return Envelope.tracing_gap(
            missing=["subtasks not all terminal"],
            remediate=(
                f"all subtasks must be in completed/cancelled before"
                f" {context_phrase}. Non-terminal subtasks: {non_terminal}"
            ),
            context_briefing=await self._briefing_for(agent_id, task_id),
        )

    async def _i_will_work_on_pending(
        self,
        agent_id: UUID,
        task_id: UUID,
        t: Any,
        plan: str | None,
        briefing: dict[str, Any],
    ) -> tuple[Envelope | None, Any]:
        """Pending-branch dispatch for i_will_work_on. Atomic: validates
        the plan precondition BEFORE calling claim() so a missing-plan
        rejection doesn't leave the task in `claimed` with no plan.

        Pre-fix (2026-05-09 smoke Bug A): claim() ran first, then the
        plan check failed → task was stuck in `claimed` because the
        `_i_will_work_on_claimed` branch (the natural retry path) had
        no plan-recovery logic. Now ordering is: guards → plan check →
        claim → set_plan → start.
        """
        if guard := await self._run_claim_guards(agent_id=agent_id, task=t):
            return self._with_briefing(guard, briefing), t
        # Plan precondition BEFORE any state mutation. Atomic invariant.
        if not t.plan and not plan:
            return Envelope.tracing_gap(
                missing=["plan"],
                remediate=(
                    f"call i_will_work_on(task_id='{task_id}',"
                    f" plan='<one-paragraph plan describing what you will do>')"
                ),
                context_briefing=briefing,
            ), t
        # claim() transitions pending → claimed; idempotent for same assignee.
        # Branch creation runs inside _finalize_claim and rolls back on
        # failure (audit P0-7 / S-01); we surface the failure as an envelope
        # so the agent gets remediate instead of a 500.
        try:
            t = await self.task.claim(task_id, agent_id)
        except Exception as exc:
            return Envelope.invalid_state(
                message=f"claim failed during finalization: {exc}",
                remediate=(
                    "branch or workspace setup failed; the claim was rolled"
                    " back. Check workspace + token, then retry"
                    " i_will_work_on(task_id, plan)."
                ),
                context_briefing=briefing,
            ), None
        if t is None:
            return Envelope.invalid_state(
                message="claim failed",
                remediate="task may already be claimed by another agent",
                context_briefing=briefing,
            ), t
        if plan:
            t = await self.task.set_plan(task_id, plan)
        t = await self.task.start(task_id, agent_id)
        if t is None:
            return self._start_failed_envelope(task_id, briefing), t
        return None, t

    @staticmethod
    def _start_failed_envelope(task_id: UUID, briefing: dict[str, Any]) -> Envelope:
        """Rejection envelope when ``task.start()`` returns None.

        ``start()`` returns None on invalid status, ownership mismatch, or
        missing plan. Surface the failure rather than fall through to an
        OK envelope that dereferences ``None.status``.
        """
        return Envelope.invalid_state(
            message=f"start failed for task {task_id}",
            remediate=(
                "task not in a startable state"
                " (claimed/paused/needs_revision) or no plan recorded"
            ),
            context_briefing=briefing,
        )

    async def _i_will_work_on_needs_revision(
        self,
        agent_id: UUID,
        task_id: UUID,
        t: Any,
        briefing: dict[str, Any],
    ) -> tuple[Envelope | None, Any]:
        """needs_revision branch for i_will_work_on. Returns (rejection|None, task)."""
        if t.assigned_to != agent_id:
            t = await self.task.claim(task_id, agent_id)
            if t is None:
                return Envelope.invalid_state(
                    message="claim failed",
                    remediate="task may already be claimed by another agent",
                    context_briefing=briefing,
                ), None
        t = await self.task.start(task_id, agent_id)
        if t is None:
            return self._start_failed_envelope(task_id, briefing), None
        return None, t

    async def _i_will_work_on_claimed(
        self,
        agent_id: UUID,
        task_id: UUID,
        t: Any,
        plan: str | None,
        briefing: dict[str, Any],
    ) -> tuple[Envelope | None, Any]:
        """claimed branch for i_will_work_on. Returns (rejection|None, task).

        Recovery path (Bug A from 2026-05-09 smoke): if the task is in
        `claimed` without a plan (e.g. orchestrator restart, prior
        partial-claim race) and the caller now supplies one, set it
        before start() instead of failing with "no plan recorded". If
        the task still has no plan and none is supplied, surface the
        same tracing_gap shape `_i_will_work_on_pending` uses.
        """
        guard = await self._run_claim_guards(
            agent_id=agent_id, task=t, skip_sequence=True
        )
        if guard:
            return self._with_briefing(guard, briefing), None
        if not t.plan and not plan:
            return Envelope.tracing_gap(
                missing=["plan"],
                remediate=(
                    f"call i_will_work_on(task_id='{task_id}',"
                    f" plan='<one-paragraph plan describing what you will do>')"
                ),
                context_briefing=briefing,
            ), None
        if plan and not t.plan:
            t = await self.task.set_plan(task_id, plan)
        t = await self.task.start(task_id, agent_id)
        if t is None:
            return self._start_failed_envelope(task_id, briefing), None
        return None, t

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
        agent = await self.task.agent_for(agent_id)
        role = str(agent.role) if agent is not None else "developer"
        status = str(t.status)
        briefing = await self._briefing_for(agent_id, task_id)

        rejection: Envelope | None = None
        if status == "needs_revision":
            rejection, t = await self._i_will_work_on_needs_revision(
                agent_id, task_id, t, briefing
            )
        elif status == "pending":
            rejection, t = await self._i_will_work_on_pending(
                agent_id, task_id, t, plan, briefing
            )
        elif status == "claimed" and t.assigned_to == agent_id:
            rejection, t = await self._i_will_work_on_claimed(
                agent_id, task_id, t, plan, briefing
            )
        elif status == "in_progress" and t.assigned_to == agent_id:
            # Idempotent re-entry: respawned dev re-calling i_will_work_on
            # on a task they already own in_progress. Skip start() (would
            # reject — wrong source state) but fall through to the OK
            # envelope at the end. Heartbeat fires there too, refreshing
            # reaper activity.
            pass
        else:
            rejection = Envelope.invalid_state(
                message=f"task {task_id} is in {status}; cannot start work",
                remediate="call give_me_work() to find an actionable task",
                context_briefing=briefing,
            )

        if rejection is not None:
            rejection.with_introspection(task=t, role=role)
            return await self._emit_rejection(
                rejection,
                agent_id=agent_id,
                task_id=task_id,
                verb="i_will_work_on",
            )

        await self._touch(task_id)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=(
                "edit + commit(message) for each meaningful change,"
                " then open_pr(task_id) and i_am_done(task_id)"
            ),
            context_briefing=briefing,
        ).with_introspection(task=t, role=role)

    @staticmethod
    def _with_briefing(env: Envelope, briefing: dict[str, Any]) -> Envelope:
        """Attach a context_briefing to an Envelope (mutate-and-return helper)."""
        env.context_briefing = briefing
        return env

    async def open_pr(self, agent_id: UUID, task_id: UUID) -> Envelope:
        """Push the dev's branch and open a PR.

        Atomic: validates ALL preconditions (assignee, commits,
        no-prior-PR) BEFORE running any git side effects. If any check
        fails, no PR is opened. After success, the dev calls
        ``i_am_done`` to actually transition the task to awaiting_qa.

        Renamed from ``submit_for_qa`` (2026-05-08): the old name
        suggested this verb advanced the lifecycle, but it only opens
        the PR. Agents misread the name, called it expecting a QA
        handoff, then never called i_am_done — orphaning PRs (e.g.
        PR #12 in the smoke-test trace).

        Idempotent on re-call: if a PR is already open, returns OK
        pointing the dev at ``i_am_done`` without opening another.
        """
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=agent_id,
                task_id=task_id,
                verb="open_pr",
            )
        briefing = await self._briefing_for(agent_id, task_id)
        agent = await self.task.agent_for(agent_id)
        role = str(agent.role) if agent is not None else "developer"
        if t.assigned_to != agent_id:
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message=f"task {task_id} is not assigned to you",
                    remediate="call give_me_work() to find your work",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role),
                agent_id=agent_id,
                task_id=task_id,
                verb="open_pr",
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
                ).with_introspection(task=t, role=role),
                agent_id=agent_id,
                task_id=task_id,
                verb="open_pr",
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
            ).with_introspection(task=t, role=role)

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
        ).with_introspection(task=t, role=role)

    async def i_am_done(self, agent_id: UUID, task_id: UUID, notes: str) -> Envelope:
        """Submit work for QA.

        Preconditions enforced by gates:
          - tracing: progress entry, journal:reflect, acceptance criteria
          - field-level: at least one commit, PR open
        The dev must have called ``commit()`` (do_server) at least once and
        ``open_pr(task_id)`` to push + open the PR. Calling i_am_done
        is the dev's explicit attestation that the work is complete; it
        auto-runs the in_progress → verifying transition (which seeds
        ``self_verified``) and then verifying → awaiting_qa.

        The previous strict path required a separate ``submit_for_verification``
        verb that wasn't on any manifest, making i_am_done unreachable
        (audit D-08). Removed that requirement; the act of calling i_am_done
        IS the self-verification.
        """
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=agent_id,
                task_id=task_id,
                verb="i_am_done",
            )
        agent = await self.task.agent_for(agent_id)
        role = str(agent.role) if agent is not None else "developer"
        if t.assigned_to != agent_id:
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message="not assigned to you",
                    remediate="claim it via i_will_work_on(task_id) first",
                    context_briefing=await self._briefing_for(agent_id, task_id),
                ).with_introspection(task=t, role=role),
                agent_id=agent_id,
                task_id=task_id,
                verb="i_am_done",
            )

        # 1. Tracing-gate preconditions (progress / reflect / acceptance)
        if rejection := await self._check_tracing_gates(agent_id, task_id, t):
            rejection.with_introspection(task=t, role=role)
            return await self._emit_rejection(
                rejection, agent_id=agent_id, task_id=task_id, verb="i_am_done"
            )

        # 2. Field-level gates (Gate Set E) — commits + PR (self_verified
        # auto-set in step 3, so it's not a precondition the dev must satisfy).
        if rejection := await self._check_submit_qa_field_gates(agent_id, task_id, t):
            rejection.with_introspection(task=t, role=role)
            return await self._emit_rejection(
                rejection, agent_id=agent_id, task_id=task_id, verb="i_am_done"
            )

        # 3. Auto-run in_progress → verifying (sets self_verified) if needed.
        if str(t.status) == "in_progress":
            verified = await self.task.submit_verification(agent_id, task_id, notes)
            if verified is not None:
                t = verified

        # 4. Submit verifying → awaiting_qa.
        submitted = await self.task.submit_qa(agent_id, task_id, notes)
        if submitted is not None:
            t = submitted
        await self._notify_qa(agent_id, task_id, t)
        await self._touch(task_id)
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
        # NOTE: self_verified is no longer a precondition — i_am_done auto-runs
        # the in_progress → verifying transition which sets it. The previous
        # NOT_SELF_VERIFIED gate required a separate submit_for_verification
        # verb that wasn't on any manifest (audit D-08).
        if not t.commits:
            missing.append("NO_COMMITS")
            hints.append(
                "no commits on this task yet — call commit(message='<subject>')"
                " before i_am_done"
            )
        if t.pr_number is None:
            missing.append("NO_PR")
            hints.append(
                "no PR open — call open_pr(task_id) to push your"
                " branch and open the PR, then retry i_am_done"
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
        agent = await self.task.agent_for(agent_id)
        role = str(agent.role) if agent is not None else "developer"
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next="idle until QA responds",
            evidence=evidence.as_dict(),
            context_briefing=await self._briefing_for(agent_id, task_id),
        ).with_introspection(task=t, role=role)

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

        Reads from either ``skills`` (gateway view, list of dicts with
        ``id`` keys) or ``capabilities`` (SQLAlchemy AgentTable, list of
        strings). The DB-side AgentTable has no ``skills`` attribute,
        so a naive ``target_agent.skills`` raises AttributeError on
        production agents (audit D-06). Falls back to the first entry
        in ``preference`` when no match is found.
        """
        skills_attr = getattr(target_agent, "skills", None)
        capabilities_attr = getattr(target_agent, "capabilities", None)
        raw = skills_attr if skills_attr is not None else capabilities_attr
        have: set[str] = set()
        for s in raw or []:
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
        agent = await self.task.agent_for(agent_id)
        role = str(agent.role) if agent is not None else "developer"
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
        ).with_introspection(task=t, role=role)

    async def unclaim(self, agent_id: UUID, task_id: UUID) -> Envelope:
        """Voluntarily release a claimed/in_progress task back to pending.

        Audit J33 — ``_pending_assignment_guard`` already remediates with
        "or unclaim it first," but the verb didn't exist. This makes that
        promise true. The work-in-progress branch survives; only the claim
        is released so another agent (or the same one, fresh) can pick it
        up. State and authorization checks live here; the DB write itself
        is in ``TaskService.unclaim_for_agent``.
        """
        t = await self.task.get(task_id)
        briefing = await self._briefing_for(agent_id, task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=agent_id,
                task_id=task_id,
                verb="unclaim",
            )
        agent = await self.task.agent_for(agent_id)
        role = str(agent.role) if agent is not None else "developer"
        if t.assigned_to != agent_id:
            # The task was reassigned out from under this agent — most
            # commonly by an upstream verb that legitimately changed
            # ownership (cell_pm_complete propagating to the parent,
            # main_pm_complete clearing assigned_to to None when
            # escalating to CEO, or a PM unblocking with restore=True).
            # The agent's local state is stale; tell it concretely.
            current_owner = (
                str(t.assigned_to) if t.assigned_to is not None else "<unassigned>"
            )
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message=(
                        f"task {task_id} is no longer assigned to you "
                        f"(current owner: {current_owner})"
                    ),
                    remediate=(
                        "the task was reassigned by an upstream verb "
                        "(cell_pm_complete / main_pm_complete / unblock). "
                        "call give_me_work() to find your current work."
                    ),
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role),
                agent_id=agent_id,
                task_id=task_id,
                verb="unclaim",
            )
        after = await self.task.unclaim_for_agent(task_id, agent_id)
        if after is None:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"cannot unclaim from status {t.status}",
                    remediate="only claimed/in_progress tasks can be unclaimed",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role),
                agent_id=agent_id,
                task_id=task_id,
                verb="unclaim",
            )
        # Deliberately no _touch — unclaim clears assigned_to, so there is
        # no claimant heartbeat to refresh. (Asymmetric with `resume` by
        # design: resume keeps the same claimant active and does heartbeat.)
        return Envelope.ok(
            status=str(after.status),
            task_id=str(task_id),
            next="task returned to pending; another agent (or you, fresh) can claim",
            context_briefing=briefing,
        ).with_introspection(task=after, role=role)

    async def resume(self, agent_id: UUID, task_id: UUID) -> Envelope:
        """Resume a paused task this agent owns; transitions paused → in_progress.

        Audit J33 — ``i_am_idle`` auto-pauses owned in_progress tasks (so
        the closure dispatcher can wake the agent when subtasks finish),
        and the lifecycle table allows ``paused → in_progress``, but no
        verb exposed that transition to agents. ``i_will_work_on`` is
        explicitly limited to needs_revision/pending/claimed; overloading
        it would muddy state-machine intent. ``resume`` keeps it explicit.

        State and authorization checks live here; the DB write itself is
        in ``TaskService.resume_for_agent``.
        """
        t = await self.task.get(task_id)
        briefing = await self._briefing_for(agent_id, task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=agent_id,
                task_id=task_id,
                verb="resume",
            )
        agent = await self.task.agent_for(agent_id)
        role = str(agent.role) if agent is not None else "developer"
        if t.assigned_to != agent_id:
            # See unclaim's matching branch for the rationale: the task
            # was reassigned by an upstream verb. Surface the actual
            # current owner so the agent can stop looping on a stale
            # task_id and call give_me_work() instead.
            current_owner = (
                str(t.assigned_to) if t.assigned_to is not None else "<unassigned>"
            )
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message=(
                        f"task {task_id} is no longer assigned to you "
                        f"(current owner: {current_owner})"
                    ),
                    remediate=(
                        "the task was reassigned by an upstream verb. "
                        "call give_me_work() to find your current work."
                    ),
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role),
                agent_id=agent_id,
                task_id=task_id,
                verb="resume",
            )
        after = await self.task.resume_for_agent(task_id, agent_id)
        if after is None:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"cannot resume from status {t.status}",
                    remediate="only paused tasks can be resumed",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role),
                agent_id=agent_id,
                task_id=task_id,
                verb="resume",
            )
        # Heartbeat — agent is back to active work after the resume.
        await self._touch(task_id)
        return Envelope.ok(
            status=str(after.status),
            task_id=str(task_id),
            next="resumed; continue working — call commit() when ready",
            context_briefing=briefing,
        ).with_introspection(task=after, role=role)

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
        paused_ids = await self._auto_pause_in_progress_tasks(agent_id)
        await self.task.mark_agent_idle(agent_id)
        if paused_ids:
            # Tell the agent how to come back to these tasks. Without this,
            # an agent respawned for a paused task has no signal that
            # `resume(task_id)` is the way back in.
            joined = ", ".join(f"resume(task_id='{tid}')" for tid in paused_ids)
            next_msg = (
                "container will shut down; on respawn, "
                f"call {joined} to continue paused work"
            )
        else:
            next_msg = "container will shut down"
        return Envelope.ok(
            status="idle",
            task_id=None,
            next=next_msg,
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

    async def _auto_pause_in_progress_tasks(self, agent_id: UUID) -> list[str]:
        """Pause every in_progress task assigned to this agent.

        Restores the pre-Phase-4 auto-pause behavior: a PM that called
        i_will_plan and is now idle leaves the parent at ``paused`` so the
        closure dispatcher knows to respawn it when subtasks complete.

        Returns the list of task IDs that were paused (as strings) so
        ``i_am_idle`` can tell the agent which ``resume(task_id)`` calls
        await it on the next respawn. Empty list when nothing was active.
        """
        in_progress = await self.task.list_in_progress_for_agent(agent_id)
        paused_ids: list[str] = []
        for t in in_progress:
            await self.task.pause_for_agent(agent_id, t.id)
            paused_ids.append(str(t.id))
        return paused_ids

    # --- Phase 2 (QA) verbs moved to ``qa.py`` (audit P2-2). ---

    # --- Phase 3 (documenter + PM) verbs ---

    # claim_doc_task + i_documented moved to ``doc.py`` (audit P2-2).

    async def _i_will_plan_preflight(
        self, pm_agent_id: UUID, task_id: UUID, t: Any, plan: str
    ) -> Envelope | None:
        """Run i_will_plan's role / status / plan / claim guards. None = pass.

        Idempotent on re-entry: if the caller already owns the task in
        claimed/in_progress (their previous spawn moved it forward), the
        verb returns OK with current state instead of rejecting. Without
        this, a respawned PM hits 'task in in_progress, expected pending'
        and loops until the reaper drops the claim back to pending —
        producing the cycle smoke 2026-05-04 captured.
        """
        agent = await self.task.agent_for(pm_agent_id)
        role = str(agent.role) if agent is not None else ""
        # Role-only gate: i_will_plan is principle-level reserved for PMs.
        # The status check below ((pending → in_progress) is a separate gate
        # whose rejection must surface as `invalid_state`, not
        # `not_authorized` — agents react to those two errors differently.
        if role not in ("cell_pm", "main_pm"):
            return Envelope.not_authorized(
                message="only cell_pm or main_pm may call i_will_plan",
                remediate="this verb is reserved for PMs",
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            ).with_introspection(task=t, role=role)
        status = str(t.status)
        if status != "pending":
            # Idempotent re-entry: caller already owns this task in a
            # post-claim state. Don't reject; the i_will_plan body will
            # short-circuit on the same condition and return OK.
            if status in ("claimed", "in_progress") and t.assigned_to == pm_agent_id:
                return None
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
        # Gate Set A: ALREADY_ACTIVE / PAUSED guards only.
        #
        # `pm_cannot_execute_code_guard` is INTENTIONALLY skipped here:
        # PMs PLAN code-typed parent tasks all the time (they decompose
        # the work into developer-claimable subtasks via delegate).
        # The "PMs cannot execute code" rule belongs to the EXECUTION
        # verb (`i_will_work_on`), not the PLANNING verb (`i_will_plan`).
        # Pre-fix this guard fired on i_will_plan and deadlocked any
        # code-typed parent task — see the 2026-05-08 smoke-test trace.
        #
        # `role_typed_claim_guard` is also skipped because i_will_plan
        # services only PM roles which aren't in its allow-table.
        guard = await self._run_claim_guards(
            agent_id=pm_agent_id,
            task=t,
            skip_role_typed=True,
            skip_pm_code=True,
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
        agent = await self.task.agent_for(pm_agent_id)
        role = str(agent.role) if agent is not None else "cell_pm"
        rejection = await self._i_will_plan_preflight(pm_agent_id, task_id, t, plan)
        if rejection is not None:
            rejection.with_introspection(task=t, role=role)
            return await self._emit_rejection(
                rejection,
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="i_will_plan",
            )

        # Idempotent re-entry: respawned PM that already owns this task in
        # claimed/in_progress short-circuits with the current state. Touch
        # the heartbeat so the reaper sees fresh activity, then return OK
        # pointing at delegate as the next call. Without this short-circuit
        # the body would reach start() — which rejects because status is
        # not 'claimed' on the in_progress branch — and emit a misleading
        # invalid_state envelope.
        status = str(t.status)
        if status in ("claimed", "in_progress") and t.assigned_to == pm_agent_id:
            await self._touch(task_id)
            return Envelope.ok(
                status=status,
                task_id=str(task_id),
                next=(
                    "task already claimed; delegate(parent_task_id, ...) for"
                    " each subtask, then i_am_idle"
                ),
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            ).with_introspection(task=t, role=role)

        # Always call claim() when status is pending — even if the PM is
        # already in assigned_to (CEO pre-assigns root tasks at creation).
        # claim() transitions pending → claimed; without that, start() below
        # would refuse the claimed → in_progress transition and silently
        # return None, leaving the task stuck in pending under a misleading
        # OK envelope. claim() is idempotent for the same assignee.
        if str(t.status) == "pending":
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
        if t is None:
            # start() returns None on invalid status / ownership / missing
            # plan. Surface the failure instead of pretending success.
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"start failed for task {task_id}",
                    remediate=(
                        "task not in a startable state"
                        " (claimed/paused/needs_revision) or no plan recorded"
                    ),
                    context_briefing=await self._briefing_for(pm_agent_id, task_id),
                ),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="i_will_plan",
            )
        await self._touch(task_id)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=(
                "delegate(parent_task_id, title, description, assigned_to, team)"
                " for each subtask, then i_am_idle"
            ),
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        ).with_introspection(task=t, role=role)

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
        role = str(agent.role) if agent is not None else "cell_pm"
        guard = await self._delegate_guard(
            pm_agent_id, parent_task_id, parent, agent, inputs
        )
        if guard is not None:
            guard.with_introspection(task=parent, role=role)
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
        ).with_introspection(task=new_task, role=role)

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
            role = str(agent.role) if agent is not None else ""
            return guard.with_introspection(task=parent, role=role)
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
        """Role + delegation-chain guards (the original two).

        Role gate stays role-only here (not via is_verb_allowed) — the
        parent-status check is a separate gate that must surface as
        `invalid_state`, not `not_authorized`. See _i_will_plan_preflight
        for the same rationale.
        """
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
        """project_id / enum guards. Pure data-shape checks.

        The slug-validity check used to live here, but `_delegate_role_guards`
        runs first and `_validate_delegation_chain` rejects any slug outside
        the allowed delegation targets — which is a strict subset of
        `AGENT_UUIDS` — so any AGENT_UUIDS check here was unreachable.
        """
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
        agent = await self.task.agent_for(pm_agent_id)
        role = str(agent.role) if agent is not None else "cell_pm"
        guard = await self._submit_up_guard(pm_agent_id, task_id, t, notes)
        if guard is not None:
            guard.with_introspection(task=t, role=role)
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
        ).with_introspection(task=t, role=role)

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
        if env := await self._subtasks_not_terminal_envelope(
            pm_agent_id, task_id, context_phrase="bubbling up"
        ):
            return env
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
        agent = await self.task.agent_for(pm_agent_id)
        role = str(agent.role) if agent is not None else "cell_pm"
        if str(t.status) != "blocked":
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"task {task_id} is in {t.status}, expected blocked",
                    remediate=(
                        "this task is not blocked; call triage() to find blocked tasks"
                    ),
                    context_briefing=await self._briefing_for(pm_agent_id, task_id),
                ).with_introspection(task=t, role=role),
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
                ).with_introspection(task=t, role=role),
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
        ).with_introspection(task=t, role=role)

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
        if env := await self._subtasks_not_terminal_envelope(
            pm_agent_id, task_id, context_phrase="completing parent"
        ):
            return env
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
            guard.with_introspection(task=t, role="cell_pm")
            return await self._emit_rejection(
                guard,
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="cell_pm_complete",
            )
        target = parent_branch_for(t.branch_name)
        merge_result = await self.git.pr_merge(
            t.pr_number, target=target, actor_agent_id=pm_agent_id
        )
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
        ).with_introspection(task=t, role="cell_pm")

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
        if env := await self._subtasks_not_terminal_envelope(
            main_pm_agent_id, root_task_id, context_phrase="escalating to CEO"
        ):
            return env
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
            guard.with_introspection(task=t, role="main_pm")
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

        # Use kwargs — service signature is (task_id, agent_role="cell_pm",
        # notes=None). Positional was passing agent_id as task_id and the
        # actual task_id as agent_role (audit D-07).
        t = await self.task.escalate_to_ceo(
            task_id=root_task_id, agent_role="main_pm", notes=notes
        )
        # CEO acts via the UI, not as an agent the orchestrator spawns. Clear
        # ``assigned_to`` so no agent gets respawned to chase this task while
        # it sits in awaiting_ceo_approval.
        await self.task.reassign(root_task_id, None)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(root_task_id),
            next="idle until CEO approves (or rejects) via UI",
            context_briefing=await self._briefing_for(main_pm_agent_id, root_task_id),
        ).with_introspection(task=t, role="main_pm")

    async def complete(self, agent_id: UUID, task_id: UUID, notes: str) -> Envelope:
        """Dispatch to cell_pm_complete or main_pm_complete based on agent role."""
        agent = await self.task.agent_for(agent_id)
        if agent.role == "cell_pm":
            return await self.cell_pm_complete(agent_id, task_id, notes)
        if agent.role == "main_pm":
            return await self.main_pm_complete(agent_id, task_id, notes)
        t = await self.task.get(task_id)
        rejection = Envelope.not_authorized(
            message=f"role {agent.role} cannot complete tasks via this verb",
            remediate="only cell_pm and main_pm can call complete",
            context_briefing=await self._briefing_for(agent_id, task_id),
        )
        if t is not None:
            rejection.with_introspection(task=t, role=str(agent.role))
        return await self._emit_rejection(
            rejection,
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
        me = await self.task.agent_for(pm_agent_id)
        role = str(me.role) if me is not None else "cell_pm"

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
                ).with_introspection(task=t, role=role),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="escalate_up",
            )

        target_slug = me.escalation_target if me else None
        if not target_slug:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message="no escalation target configured for your role",
                    remediate="check agents_config.py ESCALATION_CHAIN for your slug",
                    context_briefing=await self._briefing_for(pm_agent_id, task_id),
                ).with_introspection(task=t, role=role),
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
        ).with_introspection(task=t, role=role)

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
        role = str(me.role) if me is not None else "main_pm"
        if me.role not in ("main_pm", "product_owner", "head_marketing"):
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message=f"role {me.role} cannot escalate to CEO directly",
                    remediate="use escalate_up() to go through your escalation chain",
                    context_briefing=await self._briefing_for(agent_id, task_id),
                ).with_introspection(task=t, role=role),
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
                ).with_introspection(task=t, role=role),
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
                ).with_introspection(task=t, role=role),
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
        ).with_introspection(task=t, role=role)

    # board_triage + auditor_triage moved to ``board.py`` as the first
    # per-role mixin extraction (audit P2-2). The Choreographer class is
    # composed in ``__init__.py`` from BoardMixin + the rest of this
    # _impl. Methods now resolve via Python's MRO.
