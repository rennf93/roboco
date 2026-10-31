"""Developer verbs: give_me_work, i_will_work_on, open_pr, and i_am_done with
the checks its gate chain runs.

Role mixins composed onto ``Choreographer`` in ``__init__.py``."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from roboco.foundation.policy import lifecycle as spec_module
from roboco.foundation.policy.content import (
    markers,
)
from roboco.services.gateway.choreographer import findings as findings_lib
from roboco.services.gateway.choreographer._impl import (
    _PM_APPROACH_MIN_LEN,
    _ClaimPlanStartContext,
    _extract_original_developer,
    _IAmDoneContext,
    _ReassignedCtx,
    _thin_subtask_hint,
)
from roboco.services.gateway.choreographer._protocol import actor_context_fields
from roboco.services.gateway.choreographer.evidence_legs import (
    LegBudget,
    run_bounded_leg,
)
from roboco.services.gateway.envelope import Envelope
from roboco.services.gateway.evidence_builder import (
    build_evidence_for_task,
)
from roboco.services.gateway.merge_chain import (
    find_topology_issue,
)
from roboco.services.repositories.review_findings import (
    STATUS_OPEN,
    ReviewFindingsRepository,
)

if TYPE_CHECKING:
    from uuid import UUID

    from roboco.services.gateway.choreographer._protocol import ChoreographerHelpers

    _Base = ChoreographerHelpers
else:
    _Base = object

logger = structlog.get_logger()


class GiveMeWorkMixin(_Base):
    """``give_me_work``: the next task an agent may claim, its briefing and
    claim-verb hint, and dropping a candidate held by an unmet dependency or
    an earlier same-lane sibling."""

    @staticmethod
    def _claim_verb_hint(role: str, task: Any) -> str:
        """Role + status aware 'how to start this task' hint.

        give_me_work used to hard-code
        ``i_will_work_on(...)`` for every role/status. A documenter
        handed an awaiting_documentation task (or QA an awaiting_qa
        task) was told to call a dev verb it doesn't have — it looped.
        Map to the verb that actually claims the task for this role.

        awaiting_pm_review is a review-queue state, not a re-plan state: a
        PM offered its own already-gated task here must be steered to
        complete/request_changes, never i_will_plan (i_will_plan legally
        re-claiming from this status used to reset the task and loop the
        submit_up -> pr_pass -> awaiting_pm_review cycle forever).
        """
        tid = str(getattr(task, "id", ""))
        status = str(getattr(task, "status", ""))
        if status == "awaiting_documentation":
            return f"call claim_doc_task(task_id='{tid}') to start"
        if status == "awaiting_qa":
            return f"call claim_review(task_id='{tid}') to start"
        if status == "awaiting_pm_review" and role in ("cell_pm", "main_pm"):
            return (
                f"this task already passed the PR-review gate — call"
                f" complete(task_id='{tid}') to merge, or"
                f" request_changes(task_id='{tid}', findings=[...]) to bounce"
                " it back; do NOT call i_will_plan"
            )
        if role in ("cell_pm", "main_pm", "product_owner", "head_marketing"):
            return f"call i_will_plan(task_id='{tid}', plan='<plan>') to start"
        return f"call i_will_work_on(task_id='{tid}', plan='<plan>') to start"

    async def _drop_dependency_held(self, tasks: list[Any]) -> list[Any]:
        """Drop PENDING/NEEDS_REVISION tasks the claim gate would refuse
        right now — an unmet dependency, a same-parent sequence hold, or an
        earlier non-terminal sibling holding the dev's lane.

        ``give_me_work``'s ``list_assigned_for_agent`` fallback spans every
        active status with no hold filter, so without this a held task
        (e.g. a frontend dev's subtask waiting on the UX/UI design, or a
        needs_revision reclaim behind a lower-sequence sibling delegated
        after the first claim) was still offered here and only bounced at
        claim time — the give_me_work/i_will_work_on offer-then-reject loop
        the 2026-07-24 incident hit on the needs_revision path.
        ``is_pending_claim_blocked`` wraps the dependency + sequence half of
        what ``TaskService.claim()`` enforces; the lane half
        (``has_earlier_incomplete_code_sibling``, enforced by the
        choreographer's ``_lane_claim_guard``) is checked here too — without
        it the pre-assigned branch dropped a lane-held leaf only for the
        assigned-row fallback to re-offer the same leaf (2026-09-28 churn
        scan). ``is True`` / ``is not True`` (not bare truthy checks) keep
        this inert under partial test mocks (an unstubbed AsyncMock method
        returns a truthy mock object, not a real bool) — mirrors
        ``_pending_not_lane_held``'s identical mock-safety guard.
        Scoped to PENDING and NEEDS_REVISION — the only statuses the claim
        guards re-read; every other status already passed them at an earlier
        claim.
        """
        offerable: list[Any] = []
        for task in tasks:
            if str(task.status) in (
                "pending",
                "needs_revision",
            ):
                if await self._deps.task.is_pending_claim_blocked(task.id) is True:
                    continue
                if (
                    await self._deps.task.has_earlier_incomplete_code_sibling(task)
                    is True
                ):
                    continue
            offerable.append(task)
        return offerable

    async def _work_envelope(self, agent_id: UUID, t: Any, role: str) -> Envelope:
        """Shared 'claim this task' envelope for the bounced/pre-assigned/
        assigned give_me_work branches (identical shape, different source
        list)."""
        return Envelope.ok(
            status=str(t.status),
            task_id=str(t.id),
            next=self._claim_verb_hint(role, t),
            context_briefing=await self._briefing_for(
                agent_id, t.id, task=t, full=True
            ),
        ).with_introspection(task=t, role=role)

    async def give_me_work(self, agent_id: UUID) -> Envelope:
        """Return the agent's most-actionable task or signal idle."""
        agent = await self._deps.task.agent_for(agent_id)
        role = str(agent.role) if agent is not None else "developer"
        # Bounced work first: a needs_revision task carries open findings a
        # reviewer is waiting on, so newly seeded pending work must not
        # starve it (2026-08-24: be-dev-1 held 3 bounced tasks all day
        # behind a stream of freshly pre-assigned pending ones).
        assigned_rows = await self._deps.task.list_assigned_for_agent(agent_id)
        bounced = await self._drop_access_denied(
            await self._drop_dependency_held(
                [t for t in assigned_rows if str(t.status) == "needs_revision"]
            ),
            agent_id,
        )
        if bounced:
            return await self._work_envelope(agent_id, bounced[0], role)
        # Pre-assigned pending tasks take priority. Smoke run 3 (2026-05-12)
        # showed agents missing tasks that were seeded with assigned_to=<them>
        # and status=pending because the earlier code only walked
        # list_assigned_for_agent (ordered by priority/updated_at — pending
        # could rank behind in_progress rows) and the PM path checked
        # awaiting_* queues but not the pre-assigned pending case.
        pre_assigned = await self._drop_access_denied(
            await self._pending_not_lane_held(
                await self._deps.task.list_pending_for_agent(agent_id)
            ),
            agent_id,
        )
        if pre_assigned:
            return await self._work_envelope(agent_id, pre_assigned[0], role)
        assigned = await self._drop_access_denied(
            await self._drop_dependency_held(assigned_rows), agent_id
        )
        if assigned:
            # B39 mid-work staleness advisory (default-off): the dev just
            # re-surfaceed its in-progress branch; this is the natural
            # touchpoint to warn about a moved base. Best-effort, may only
            # add a notification, never blocks the work envelope.
            await self._branch_staleness_advisory(agent_id, assigned[0])
            return await self._work_envelope(agent_id, assigned[0], role)
        paused = await self._deps.task.list_paused_for_agent(agent_id)
        if paused:
            t = paused[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=f"call resume(task_id='{t.id}') to continue paused work",
                context_briefing=await self._briefing_for(
                    agent_id, t.id, task=t, full=True
                ),
            ).with_introspection(task=t, role=role)
        return Envelope.ok(
            status="idle",
            task_id=None,
            next="call i_am_idle() — no work available",
            context_briefing=await self._briefing_for(agent_id, None),
        )

    async def _pending_not_lane_held(self, pending: list[Any]) -> list[Any]:
        """Drop a dev's pending code leaves that are waiting behind an earlier
        non-terminal sibling in the same dev's lane (per-dev sequenced queues).

        Those leaves are spawned by the orchestrator when the lane clears, so
        they must not pin the dev to idle. ``is not True`` keeps this inert under
        partial test mocks (an AsyncMock returns a truthy stub, not a real bool)
        — only a leaf the service positively confirms is lane-held is dropped.
        """
        live: list[Any] = []
        for t in pending:
            if await self.task.has_earlier_incomplete_code_sibling(t) is not True:
                live.append(t)
        return live


class DevClaimMixin(_Base):
    """``i_will_work_on``: a developer's claim, plan and start, re-entering a
    task it already holds or claiming a fresh one behind the plan-field gate."""

    @staticmethod
    def _build_rich_plan(
        plan: str | None,
        steps: list[dict[str, Any]] | None,
        technical_considerations: list[str] | None,
        risks: list[dict[str, Any]] | None,
        open_questions: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Assemble the panel-shaped rich plan from a dev's inputs."""
        return {
            "approach": plan or "",
            "sub_tasks": steps or [],
            "technical_considerations": technical_considerations or [],
            "risks": risks or [],
            "open_questions": open_questions or [],
        }

    async def i_will_work_on(
        self,
        *,
        agent_id: UUID,
        task_id: UUID,
        plan: str | None = None,
        steps: list[dict[str, Any]] | None = None,
        technical_considerations: list[str] | None = None,
        risks: list[dict[str, Any]] | None = None,
        open_questions: list[dict[str, Any]] | None = None,
    ) -> Envelope:
        """Claim a task and start work on it.

        Atomic: spec.can_invoke_intent runs before any state mutation;
        the composed (claim, set_plan) sequence is wrapped in a
        savepoint by the runner so a mid-sequence failure rolls back
        the DB; start follows the branch provisioning.
        Idempotent re-entry: a respawned dev re-calling on a
        task they already own in_progress just refreshes the heartbeat.

        ``steps`` is the developer's execution checklist (same
        SubTask shape as a PM's sub_tasks). Persisted into
        ``task.plan.sub_tasks`` via the panel-shaped path so it renders
        identically AND feeds plan-driven progress. A developer
        on a fresh claim must supply substantive steps —
        ``_dev_steps_gate`` enforces depth; the re-entry / recovery
        paths short-circuit before the gate so a respawned dev is never
        re-blocked for steps it already submitted.
        """
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=agent_id,
                task_id=task_id,
                verb="i_will_work_on",
            )
        agent = await self.task.agent_for(agent_id)
        role_str = str(agent.role) if agent is not None else "developer"
        briefing = await self._briefing_for(agent_id, task_id, task=t, full=True)
        try:
            role = spec_module.Role(role_str)
        except ValueError:
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message=f"unknown role '{role_str}'",
                    remediate="role is not declared in the lifecycle spec",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=agent_id,
                task_id=task_id,
                verb="i_will_work_on",
            )
        # Full parity: a dev authors the same rich plan a PM does. The
        # dev's `plan` doubles as the Approach; `steps` become sub_tasks. Built
        # via the panel-shaped path so the Plan tab renders identically and
        # feeds progress. With no rich fields (re-entry/recovery) this
        # falls through to unchanged string behaviour.
        rich_plan = self._build_rich_plan(
            plan, steps, technical_considerations, risks, open_questions
        )
        effective_plan: str | dict[str, Any] | None = self._resolve_effective_plan(
            plan or "", rich_plan
        )
        spec_ctx = spec_module.Context(
            plan=effective_plan,
            actor_id=agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            agent_team=str(agent.team) if agent is not None and agent.team else None,
            original_developer_slug=_extract_original_developer(t),
        )
        ctx = _ClaimPlanStartContext(
            agent_id=agent_id,
            task_id=task_id,
            task=t,
            role_str=role_str,
            briefing=briefing,
            plan=effective_plan,
            verb_name="i_will_work_on",
        )
        if reentry := await self._dev_reentry(
            ctx=ctx,
            t=t,
            agent_id=agent_id,
            task_id=task_id,
            role_str=role_str,
            briefing=briefing,
        ):
            return reentry
        return await self._fresh_dev_claim(
            ctx=ctx,
            role=role,
            spec_ctx=spec_ctx,
            agent=agent,
            rich_plan=rich_plan,
            role_str=role_str,
            t=t,
            agent_id=agent_id,
            task_id=task_id,
            briefing=briefing,
        )

    async def _dev_reentry(
        self,
        *,
        ctx: _ClaimPlanStartContext,
        t: Any,
        agent_id: UUID,
        task_id: UUID,
        role_str: str,
        briefing: dict[str, Any],
    ) -> Envelope | None:
        """Re-entry short-circuits for i_will_work_on (extracted to keep
        i_will_work_on under the cyclomatic-complexity gate; mirrors
        _handle_pm_reentry). Returns an Envelope to short-circuit, or None
        to fall through to the fresh-claim path.
        """
        # Idempotent re-entry: agent already owns the task in_progress.
        # Short-circuit before the spec gate (in_progress is not a source
        # state for the composed `claim` action).
        if str(t.status) == "in_progress" and t.assigned_to == agent_id:
            await self._touch(task_id)
            return Envelope.ok(
                status=str(t.status),
                task_id=str(task_id),
                next=spec_module._INTENT_VERBS["i_will_work_on"].next_hint(t),
                context_briefing=briefing,
            ).with_introspection(task=t, role=role_str)
        # Recovery re-entry: task stuck in `claimed` (orchestrator restart
        # or partial-claim race) and the agent already owns it. The spec
        # `claim` source-statuses exclude CLAIMED, so run only set_plan +
        # start.
        if str(t.status) == "claimed" and t.assigned_to == agent_id:
            envelope = await self._resume_from_claimed(ctx)
            return await self._post_claim_journal_gate(
                "i_will_work_on", agent_id, task_id, envelope
            )
        return None

    async def _fresh_dev_claim(
        self,
        *,
        ctx: _ClaimPlanStartContext,
        role: Any,
        spec_ctx: Any,
        agent: Any,
        rich_plan: dict[str, Any],
        role_str: str,
        t: Any,
        agent_id: UUID,
        task_id: UUID,
        briefing: dict[str, Any],
    ) -> Envelope:
        """Fresh (non-re-entry) i_will_work_on tail: spec gate → dev-plan
        gate → claim/plan/start → post-claim journal gate. Extracted so
        i_will_work_on stays within the return-count budget; the dev-plan
        gate mirrors _pm_sub_tasks_gate's placement (after the spec gate).
        """
        if rejection := await self._claim_plan_start_gate(ctx, role, spec_ctx):
            return rejection
        if rejection := await self._dev_plan_gate(
            role_str=role_str,
            rich_plan=rich_plan,
            task=t,
            agent_id=agent_id,
            task_id=task_id,
            briefing=briefing,
        ):
            return rejection
        envelope = await self._claim_plan_start_run(ctx, agent, spec_ctx)
        return await self._post_claim_journal_gate(
            "i_will_work_on", agent_id, task_id, envelope
        )

    @staticmethod
    def _dev_plan_field_gaps(rich_plan: dict[str, Any]) -> dict[str, str]:
        """Collect missing/thin rich-plan fields for a fresh dev claim.

        Full parity with PMs: approach (the dev's `plan`, >= min chars),
        substantive sub_tasks (the `steps` checklist), technical_considerations
        and risks. open_questions stay optional. Returns {field: hint}.
        """
        gaps: dict[str, str] = {}
        approach = str(rich_plan.get("approach") or "").strip()
        if len(approach) < _PM_APPROACH_MIN_LEN:
            gaps["plan"] = (
                f"plan must be >= {_PM_APPROACH_MIN_LEN} chars describing HOW "
                "you will implement this (it is the plan's Approach)."
            )
        steps = rich_plan.get("sub_tasks") or []
        if not steps:
            gaps["steps"] = (
                "a non-empty execution checklist — list of {title, "
                "description}; each step is also a progress-checklist item."
            )
        elif thin := _thin_subtask_hint(steps):
            gaps["steps"] = thin
        if not rich_plan.get("technical_considerations"):
            gaps["technical_considerations"] = (
                "list >= 1 architectural / library / approach note (strings)."
            )
        if not rich_plan.get("risks"):
            gaps["risks"] = (
                "list >= 1 {risk, mitigation} entry — what could go wrong and "
                "how you'll handle it."
            )
        return gaps

    async def _dev_plan_gate(
        self,
        *,
        role_str: str,
        rich_plan: dict[str, Any],
        task: Any,
        agent_id: UUID,
        task_id: UUID,
        briefing: dict[str, Any],
    ) -> Envelope | None:
        """A developer's FRESH claim must author the same rich plan a PM does,
        so the task's Plan tab is fully populated for audit/tracing. Enforces
        approach + steps + technical_considerations + risks (open_questions
        optional). Non-developer callers and re-entry are unaffected — the
        re-entry/recovery paths return before this is reached. Returns a
        rejection Envelope when the plan is thin; None when it passes.
        """
        if role_str != "developer":
            return None
        gaps = self._dev_plan_field_gaps(rich_plan)
        if not gaps:
            return None
        return await self._emit_rejection(
            Envelope.incomplete_input(
                missing=sorted(gaps),
                field_hints=gaps,
                remediate=(
                    "re-issue i_will_work_on(task_id, plan='<how, >= "
                    f"{_PM_APPROACH_MIN_LEN} chars>', "
                    "steps=[{'title': '...', 'description': '...'}, ...], "
                    "technical_considerations=['...'], "
                    "risks=[{'risk': '...', 'mitigation': '...'}]) — the same "
                    "rich plan a PM authors, so your task's Plan tab is filled."
                ),
                context_briefing=briefing,
            ).with_introspection(task=task, role=role_str),
            agent_id=agent_id,
            task_id=task_id,
            verb="i_will_work_on",
        )


class OpenPRMixin(_Base):
    """``open_pr``: the ownership and branch-topology preflight, the push and
    create-PR run with its success and failure envelopes, and the
    branch-pushed check ``i_am_done``'s gate shares."""

    async def open_pr(self, agent_id: UUID, task_id: UUID) -> Envelope:
        """Push the dev's branch and open a PR.

        Atomic: spec.can_invoke_intent runs first and enforces ALL
        preconditions (PRECONDITION_OWNERSHIP, PRECONDITION_COMMITS,
        PRECONDITION_NO_PR) BEFORE any git side effect. If any check
        fails, no PR is opened. After success, the dev calls
        ``i_am_done`` to actually transition the task to awaiting_qa.

        Renamed from ``submit_for_qa`` (2026-05-08): the old name
        suggested this verb advanced the lifecycle, but it only opens
        the PR. Agents misread the name, called it expecting a QA
        handoff, then never called i_am_done — orphaning open PRs.

        Idempotent on re-call: if the caller already owns the task and
        a PR is already open, return OK pointing at ``i_am_done`` rather
        than the spec's ``tracing_gap`` for ``no_prior_pr``. Two calls
        in a row should not surface a misleading "open a PR" hint.
        """
        # Serialize concurrent open_pr on the SAME task across the
        # idempotent-guard fetch -> runner -> milestone-emit critical section.
        # The guard reads t.pr_number from an unlocked fetch; without a lock,
        # two concurrent same-task open_pr calls (the alive-but-unresponsive
        # respawn race) both fetch pr_number=None, both pass the guard, both
        # run the runner (create_pr's GitHub 422 'already exists' path ensures
        # only one PR — no double PR), and both reach _open_pr_success_envelope
        # -> _record_milestone_progress -> a double-emitted 70% "opened PR #N"
        # progress entry (the audit/milestone view double-counts one PR-open).
        # The per-task transaction-scoped advisory lock is acquired BEFORE the
        # fetch so the second concurrent call blocks until the first commits,
        # then its fetch sees the first's committed pr_number and the guard
        # short-circuits without re-emitting the milestone. Per-task (not
        # per-agent) — the single-active-task guard means concurrent open_pr
        # on the same task is purely the respawn-race bug case, never a
        # legitimate-concurrency regression.
        await self.task.acquire_task_lock(task_id)
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=agent_id,
                task_id=task_id,
                verb="open_pr",
            )
        briefing = await self._briefing_for(agent_id, task_id, task=t)
        agent = await self.task.agent_for(agent_id)
        role_str = str(agent.role) if agent is not None else "developer"
        # Idempotent re-entry: caller owns the task and a PR is already
        # open. The spec would otherwise reject with PRECONDITION_NO_PR
        # tracing_gap, but agents calling open_pr twice should get the
        # existing PR's i_am_done hint, not a misleading "open a PR" remediate.
        if t.pr_number is not None and t.assigned_to == agent_id:
            return Envelope.ok(
                status=str(t.status),
                task_id=str(task_id),
                next=(
                    f"PR #{t.pr_number} already open; call "
                    f"i_am_done(task_id, notes='...') when self-verified"
                ),
                context_briefing=briefing,
            ).with_introspection(task=t, role=role_str)
        spec_ctx = spec_module.Context(
            actor_id=agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            agent_team=str(agent.team) if agent is not None and agent.team else None,
            original_developer_slug=_extract_original_developer(t),
        )
        if rejection := await self._open_pr_preflight_rejection(
            agent_id=agent_id,
            task_id=task_id,
            t=t,
            role_str=role_str,
            briefing=briefing,
            spec_ctx=spec_ctx,
        ):
            return await self._emit_rejection(
                rejection, agent_id=agent_id, task_id=task_id, verb="open_pr"
            )
        await self._touch(task_id)
        runner = self._verb_runner()
        try:
            await runner.run_intent("open_pr", t, agent, spec_ctx)
        except Exception as exc:
            return await self._emit_rejection(
                self._open_pr_failure_env(exc, t, briefing, role_str),
                agent_id=agent_id,
                task_id=task_id,
                verb="open_pr",
            )
        return await self._open_pr_success_envelope(
            agent_id, task_id, t, briefing, role_str
        )

    async def _open_pr_preflight_rejection(
        self,
        *,
        agent_id: UUID,
        task_id: UUID,
        t: Any,
        role_str: str,
        briefing: dict[str, Any],
        spec_ctx: Any,
    ) -> Envelope | None:
        """Role + reassignment + spec-gate + topology rejection for open_pr.

        A stale/superseded agent (task reassigned away) is steered to
        give_me_work with a clear not_authorized BEFORE the spec gate would
        emit an owns_task tracing_gap the agent retries forever (same pattern
        as i_am_done / resume).
        """
        try:
            role = spec_module.Role(role_str)
        except ValueError:
            return Envelope.not_authorized(
                message=f"unknown role '{role_str}'",
                remediate="role is not declared in the lifecycle spec",
                context_briefing=briefing,
            ).with_introspection(task=t, role=role_str)
        reassigned = self._reassigned_rejection(
            _ReassignedCtx(
                task=t,
                agent_id=agent_id,
                task_id=task_id,
                role_str=role_str,
                briefing=briefing,
                upstream_hint=(
                    "this task was reassigned away from you; you are no longer "
                    "its owner. Call give_me_work() to find your current task, "
                    "or i_am_idle() if there is none. Do NOT retry open_pr."
                ),
            )
        )
        if reassigned is not None:
            return reassigned
        decision = spec_module.can_invoke_intent(role, "open_pr", t, spec_ctx)
        if not decision.allowed:
            return Envelope.from_decision(
                decision, briefing=briefing
            ).with_introspection(task=t, role=role_str)
        return await self._open_pr_topology_rejection(t, briefing, role_str)

    async def _open_pr_topology_rejection(
        self, t: Any, briefing: dict[str, Any], role_str: str
    ) -> Envelope | None:
        """Refuse BEFORE create_pr runs when the task's branch/parent
        topology is already incoherent (or None).

        Catches the 5612b225/PR #856 incident class at the earliest possible
        point instead of only at complete()/submit_root, after all
        implementation and review has already been spent: a parented task
        whose branch/PR is anchored to the integration branch instead of its
        parent's own branch, or a parentless root whose branch encodes a
        nested hierarchy (it was detached from a coordination ancestor
        during a restructure). The existing terminal-verb CEO_ONLY head-
        branch routing (``_maybe_route_head_branch_to_ceo``) is untouched —
        this is an additional, earlier catch, not a replacement.

        Fail-open on a ``find_topology_issue`` exception (its DB calls can
        raise) — mirrors ``_behind_base_gate``'s posture — so a transient
        error cannot wedge ``open_pr`` on a check that only ever refuses.
        """
        try:
            issue = await find_topology_issue(t, self.task)
        except Exception as exc:
            logger.warning("topology_check_skip", task_id=str(t.id), error=str(exc))
            return None
        if issue is None:
            return None
        return Envelope.invalid_state(
            message=issue.message,
            remediate=(
                f"do not open this PR yet — expected base branch is "
                f"'{issue.expected_base}'. {issue.repair}"
            ),
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)

    @staticmethod
    def _open_pr_failure_env(
        exc: Exception, t: Any, briefing: dict[str, Any], role_str: str
    ) -> Envelope:
        """Map an open_pr runner failure to its rejection envelope.

        Empty-diff subtask: a branch with zero commits relative to its base
        makes GitHub 422 with "No commits between ...". open_pr can never
        succeed here (an overlapping-decomposition leaf whose work was
        delivered by the parent), so a generic invalid_state "retry" loops the
        dev forever (15x on one task in a single run). Steer to a terminal
        i_am_blocked hand-off so the PM completes/cancels the redundant leaf;
        every other failure keeps the generic retry hint.
        """
        if "No commits between" in str(exc):
            return Envelope.invalid_state(
                message=(
                    "this branch has no commits relative to its base — there is "
                    "nothing to open a PR for"
                ),
                remediate=(
                    "do NOT retry open_pr — your branch has no diff, so this "
                    "leaf's work was almost certainly delivered by its parent "
                    "task. Call i_am_blocked(reason='no commits to PR — work "
                    "delivered via parent') so your PM can complete or cancel "
                    "this redundant leaf."
                ),
                context_briefing=briefing,
            ).with_introspection(task=t, role=role_str)
        return Envelope.invalid_state(
            message=f"verb runner failed: {exc}",
            remediate="check workspace + retry; if persistent, escalate",
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)

    async def _open_pr_success_envelope(
        self,
        agent_id: UUID,
        task_id: UUID,
        t: Any,
        briefing: dict[str, Any],
        role_str: str,
    ) -> Envelope:
        """Refresh the task, auto-emit milestone progress, build the OK envelope.

        git_service.create_pr writes pr_number / pr_url onto the task row;
        the runner doesn't bubble that update back so we re-fetch.
        Milestone progress fires server-side so the panel + audit
        log always show "opened PR #N" regardless of agent chattiness.
        """
        refreshed = await self.task.get(task_id)
        t = refreshed if refreshed is not None else t
        if t.pr_number is not None:
            await self._record_milestone_progress(
                task_id,
                agent_id,
                f"opened PR #{t.pr_number}",
                percentage=70,
            )
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS["open_pr"].next_hint(t),
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)

    async def _ensure_branch_pushed(self, ctx: _IAmDoneContext) -> Envelope | None:
        """Push the task branch to origin before it reaches awaiting_qa.

        QA reviews the remote PR branch. A fix committed during a revision
        cycle lives only in the developer's local workspace until pushed —
        without this, QA re-reviews the stale remote and fails the same task
        every cycle (a non-converging loop). Idempotent: a no-op when nothing
        is unpushed, so first-submit (already pushed by open_pr) is unaffected.
        """
        try:
            await self.git.push_task_branch(ctx.agent_id, ctx.task_id)
        except Exception as exc:
            return Envelope.invalid_state(
                message=f"could not push your branch to origin: {exc}",
                remediate=(
                    "your latest commits are local-only and QA reviews the "
                    "pushed PR branch. resolve the push error (often a "
                    "transient network / fetch timeout) and call i_am_done "
                    "again. if it says your branch is behind its remote "
                    "counterpart, call sync_branch() to rebase + force-push. "
                    "if sync_branch reports a dirty workspace, commit(...) "
                    "(omit files to stage everything) THEN sync_branch() "
                    "THEN i_am_done() again. still stuck? unclaim() releases "
                    "the task back to the pool."
                ),
                context_briefing=ctx.briefing,
            )
        return None


class IAmDoneMixin(_Base):
    """``i_am_done``: the developer's submit to QA, its ordered gate chain, the
    resume of a run left in verifying, the success envelope and the QA
    notice."""

    async def i_am_done(
        self,
        agent_id: UUID,
        task_id: UUID,
        notes: str,
        resolved_findings: list[dict[str, Any]] | None = None,
    ) -> Envelope:
        """Submit work for QA.

        Atomic: ``spec.can_invoke_intent`` runs first and enforces the
        intent's role membership and the ``PRECONDITION_OWNERSHIP`` /
        ``PRECONDITION_COMMITS`` extra preconditions BEFORE any state
        mutation. After the spec gate accepts, two additional gate sets
        run as defense-in-depth (the spec doesn't yet model them):

          - tracing-gate preconditions (progress entry, journal:reflect,
            acceptance criteria addressed, revision-findings-ledger
            addressed)
          - field-level submit-qa gates (currently: PR open; commits and
            ownership are already covered by the spec extras above)

        ``resolved_findings`` ({finding_id, commit?, note?} entries) marks
        open ledger rows ``addressed`` BEFORE the tracing gate runs (in
        ``_i_am_done_gate``, shared by both the fresh path and the
        resume-from-verifying recovery path below) — a write-then-gate
        pattern mirroring ``dev_notes``: apply the resolution, then check
        whether the task's open-findings count is now zero.

        Once all gates pass, ``VerbRunner.run_intent("i_am_done", ...)``
        dispatches the (submit_verification, submit_qa) atomic chain
        wrapped in a savepoint so a mid-sequence failure rolls back the
        DB. Recovery re-entry: a task already in ``verifying`` owned by
        the caller has its first composed action (submit_verification,
        source IN_PROGRESS) rejected by the spec gate. We short-circuit
        before the spec gate and run only ``submit_qa`` — the spec
        doesn't model partial-progress recovery, so that branch lives
        in the verb body.

        The previous strict path required a separate ``submit_for_verification``
        verb that wasn't on any manifest, making i_am_done unreachable.
        Removed that requirement; the act of calling i_am_done
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
        role_str = str(agent.role) if agent is not None else "developer"
        briefing = await self._briefing_for(agent_id, task_id, task=t)
        ctx = _IAmDoneContext(
            agent_id=agent_id,
            task_id=task_id,
            task=t,
            role_str=role_str,
            briefing=briefing,
            notes=notes,
            resolved_findings=resolved_findings,
        )
        try:
            role = spec_module.Role(role_str)
        except ValueError:
            return await self._reject_i_am_done(
                ctx,
                Envelope.not_authorized(
                    message=f"unknown role '{role_str}'",
                    remediate="role is not declared in the lifecycle spec",
                    context_briefing=briefing,
                ),
            )
        actor_slug, agent_team = actor_context_fields(agent)
        spec_ctx = spec_module.Context(
            actor_id=agent_id,
            actor_slug=actor_slug,
            agent_team=agent_team,
            original_developer_slug=_extract_original_developer(t),
            notes=notes,
        )
        # Pre-spec-gate short-circuits (verifying-resume recovery + the
        # work-already-done fast path), folded into one helper to keep i_am_done
        # within its return-count budget (mirrors _fresh_dev_claim's extraction).
        if dispatched := await self._i_am_done_pre_gate_dispatch(ctx, t, agent_id):
            return dispatched
        # Stale/superseded agent: the task was reassigned out from under this
        # agent (PM reassign / reaper unclaim / escalation redirect) while its
        # container kept running. Without this, the spec gate's
        # PRECONDITION_OWNERSHIP emits an owns_task *tracing_gap*, which the
        # agent reads as a fixable precondition and retries i_am_done forever
        # (41 such loops in one run). A clear "no longer yours" not_authorized
        # steers to give_me_work — terminal for this agent. (resume/unclaim
        # already do this; i_am_done did not.) Folded into the soup/spec return
        # below to stay within the return-count budget.
        reassigned = self._reassigned_rejection(
            _ReassignedCtx(
                task=t,
                agent_id=agent_id,
                task_id=task_id,
                role_str=role_str,
                briefing=briefing,
                upstream_hint=(
                    "this task was reassigned away from you; you are no longer "
                    "its owner. Call give_me_work() to find your current task, "
                    "or i_am_idle() if there is none. Do NOT retry i_am_done."
                ),
            )
        )
        # i_am_done notes is optional and supplementary (the real summary lives
        # in commits + journal:reflect), so guard it lightly — a banned token
        # ('wip'/'x') is soup, but a terse real word like 'done' is fine.
        soup = self._free_text_soup(checks=(("notes", notes, 4),))
        decision = spec_module.can_invoke_intent(role, "i_am_done", t, spec_ctx)
        if env := (reassigned or self._soup_or_decision_env(soup, decision, briefing)):
            return await self._reject_i_am_done(ctx, env)
        if gate_rejection := await self._i_am_done_gate(ctx):
            return gate_rejection
        return await self._i_am_done_run(ctx, agent, spec_ctx)

    async def _reject_i_am_done(self, ctx: _IAmDoneContext, env: Envelope) -> Envelope:
        """Stamp introspection + emit audit row for an i_am_done rejection."""
        env.with_introspection(task=ctx.task, role=ctx.role_str)
        return await self._emit_rejection(
            env, agent_id=ctx.agent_id, task_id=ctx.task_id, verb="i_am_done"
        )

    async def _i_am_done_gate(self, ctx: _IAmDoneContext) -> Envelope | None:
        """Run defense-in-depth tracing + field-level gates the spec doesn't model.

        Also pushes the branch to origin so a task cannot reach awaiting_qa
        with commits that exist only in the developer's local workspace.
        Returns the rejection envelope if any gate fails; None on pass. Shared
        by the normal and resume-from-verifying paths so both push.
        """
        await self._apply_resolved_findings(ctx)
        guards = (
            lambda: self._check_tracing_gates(ctx.agent_id, ctx.task_id, ctx.task),
            lambda: self._check_submit_qa_field_gates(
                ctx.agent_id, ctx.task_id, ctx.task
            ),
            lambda: self._ensure_branch_pushed(ctx),
            lambda: self._behind_base_gate(ctx),
            lambda: self._check_quality_gate(ctx),
            lambda: self._toolchain_broken_guard(ctx.agent_id, ctx.task),
            lambda: self._conventions_gate(ctx),
        )
        for guard in guards:
            if rejection := await guard():
                return await self._reject_i_am_done(ctx, rejection)
        # Pre-gateway parity: persist per-criterion
        # status now that all gates have passed. The write runs AFTER the
        # verdict so it cannot change i_am_done's rejection behavior.
        await self._write_criteria_status(ctx.agent_id, ctx.task_id, ctx.task)
        return None

    async def _i_am_done_run(
        self, ctx: _IAmDoneContext, agent: Any, spec_ctx: spec_module.Context
    ) -> Envelope:
        """Dispatch the spec-composed (submit_verification, submit_qa) chain."""
        runner = self._verb_runner()
        try:
            t = await runner.run_intent("i_am_done", ctx.task, agent, spec_ctx)
        except Exception as exc:
            return await self._reject_i_am_done(
                ctx,
                Envelope.invalid_state(
                    message=f"verb runner failed: {exc}",
                    remediate="check workspace + retry; if persistent, escalate",
                    context_briefing=ctx.briefing,
                ),
            )
        notify_warning = await self._notify_qa(ctx.agent_id, ctx.task_id, t)
        await self._touch(ctx.task_id)
        # Server-side milestone progress so the panel always
        # records the QA handoff regardless of agent's progress() habits.
        await self._record_milestone_progress(
            ctx.task_id,
            ctx.agent_id,
            "submitted for QA review",
            percentage=90,
        )
        env = await self._build_i_am_done_ok(ctx.agent_id, ctx.task_id, t)
        if notify_warning:
            env.warning = notify_warning
        return env

    async def _i_am_done_resume_from_verifying(self, ctx: _IAmDoneContext) -> Envelope:
        """Recovery path: task is already in `verifying` owned by caller.

        The spec's i_am_done composes (submit_verification, submit_qa) and
        the runner dispatches the FIRST action; submit_verification's
        source_status is IN_PROGRESS so a `verifying` task hits invalid_state
        through the spec gate. Run submit_qa directly, plus the same tracing
        + field-level gates the standard path enforces.
        """
        if gate_rejection := await self._i_am_done_gate(ctx):
            return gate_rejection
        try:
            submitted = await self.task.submit_qa(ctx.agent_id, ctx.task_id, ctx.notes)
        except Exception as exc:
            return await self._reject_i_am_done(
                ctx,
                Envelope.invalid_state(
                    message=f"submit_qa failed: {exc}",
                    remediate="check workspace + retry; if persistent, escalate",
                    context_briefing=ctx.briefing,
                ),
            )
        t = submitted if submitted is not None else ctx.task
        notify_warning = await self._notify_qa(ctx.agent_id, ctx.task_id, t)
        await self._touch(ctx.task_id)
        env = await self._build_i_am_done_ok(ctx.agent_id, ctx.task_id, t)
        if notify_warning:
            env.warning = notify_warning
        return env

    async def _i_am_done_pre_gate_dispatch(
        self, ctx: _IAmDoneContext, t: Any, agent_id: UUID
    ) -> Envelope | None:
        """Pre-spec-gate short-circuits, folded to keep i_am_done within its
        return-count budget:

        1. verifying-resume recovery (task already ``verifying`` owned by the
           caller — orchestrator restart between submit_verification and
           submit_qa; the spec gate would reject the first composed action).
        2. the possibilities-matrix fast path (work already done).

        Returns the dispatched envelope, or None to fall through to the
        standard spec/soup/gate path.
        """
        if str(t.status) == "verifying" and t.assigned_to == agent_id:
            return await self._i_am_done_resume_from_verifying(ctx)
        return await self._maybe_i_am_done_fast_path(ctx, t, agent_id)

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
        # verb that wasn't on any manifest.
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
            context_briefing=await self._briefing_for(agent_id, task_id, task=t),
        )

    async def _build_i_am_done_ok(
        self, agent_id: UUID, task_id: UUID, t: Any
    ) -> Envelope:
        """Assemble the success envelope for i_am_done / _with_catchup.

        files_changed sourced from git (authoritative) so the
        i_am_done envelope shows the same file list QA / docs / PMs will
        see — independent of legacy ``add_files_modified`` plumbing.

        Runs strictly AFTER the composed transition already committed (the
        caller already ran submit_verification/submit_qa) — this is purely
        informational, not a gate — so the list_changed_files leg runs
        bounded via ``run_bounded_leg``: a timeout must not strand the
        dev's already-succeeded submit behind a hung response.
        """
        from roboco.config import settings as _settings

        journal_highlights = await self.evidence_repo.journal_highlights_for_task(
            task_id
        )
        files_changed: list[str] = []
        evidence_gaps: list[str] = []
        if t.branch_name:
            budget = LegBudget(_settings.evidence_assembly_timeout_seconds)
            files_changed = await run_bounded_leg(
                # Forward the submitting agent so the workspace resolves from
                # ITS clone (same rule as _do_push_branch): by this point the
                # transition already reassigned the task to QA, and a
                # no-actor fallback lands on the cleared assignee or a
                # workspace that was never cloned (found by the e2e devops
                # delegation arc).
                self.git.list_changed_files(
                    branch_name=t.branch_name, actor_agent_id=agent_id
                ),
                default=[],
                budget=budget,
                leg="files_changed",
                hint="review the PR diff on GitHub directly",
                task_id=task_id,
                gaps=evidence_gaps,
            )
        # Normally empty here — i_am_done's FINDINGS_ADDRESSED gate already
        # required every open finding resolved before this point — but wired
        # in for consistency with every other evidence call site.
        open_findings = await findings_lib.open_findings_for_task(
            self.task.session, task_id
        )
        evidence = build_evidence_for_task(
            t,
            journal_highlights=journal_highlights,
            files_changed=files_changed,
            revision_findings=open_findings,
            evidence_gaps=evidence_gaps,
        )
        agent = await self.task.agent_for(agent_id)
        role = str(agent.role) if agent is not None else "developer"
        await self._teardown_sandbox_best_effort(agent_id)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next="idle until QA responds",
            evidence=evidence.as_dict(),
            context_briefing=await self._briefing_for(agent_id, task_id, task=t),
        ).with_introspection(task=t, role=role)

    async def _notify_qa(self, agent_id: UUID, task_id: UUID, t: Any) -> str | None:
        """Reassign + A2A-notify the QA agent for this task's team.

        ``submit_qa`` clears ``assigned_to`` to None. We then explicitly
        reassign to the QA agent so the orchestrator's per-agent task
        polling spawns QA (not the dev again) for the next stage.

        The submit-qa transition is already committed by the time this
        runs, so a raise here (``A2AService.send`` is NOT best-effort —
        it raises on policy denial, missing-agent lookup, participant
        validation, and transient DB errors) must not escape and blow up
        the verb path. Degrades to a warning string instead, mirroring
        ``_pass_review_documenter_handoff`` (qa.py) and the ``pr_fail``
        loop-closer (pr_gate.py) — the two existing precedents for this
        exact shape.
        """
        qa_agent = await self.task.qa_agent_for_team(t.team)
        if qa_agent is None:
            return None
        try:
            # Savepoint: reassign()'s flush would otherwise poison the
            # shared session on a mid-flush failure — the response commit
            # (DbCommitMiddleware) reuses it right after this returns.
            async with self.task.session.begin_nested():
                await self.task.reassign(task_id, qa_agent.id)
                skill = self._resolve_skill(qa_agent, ["code_review", "qa_review"])
                await self.a2a.send(
                    from_agent=agent_id,
                    to_agent=qa_agent.id,
                    skill=skill,
                    task_id=task_id,
                    body=f"Ready for review. PR: {t.pr_url}",
                )
        except Exception as exc:
            # The savepoint rollback on ANY exception here — not just a DB
            # error, e.g. a2a.send failing — fully expires every attribute
            # of `t` (the same identity-map object reassign() mutated
            # inside the block). The caller reads t.status / with_introspection(t)
            # building the envelope right after this returns, so a refresh
            # failure here means the DB is genuinely broken; let it raise —
            # that 500 is honest, unlike silently returning a warning off a
            # task object that will itself blow up on read.
            await self.task.session.refresh(t)
            logger.warning(
                "notify_qa side-effect failed - transition committed, "
                "handoff did not fire",
                task_id=str(task_id),
                recipient=str(qa_agent.id),
                error_type=type(exc).__name__,
                error=repr(exc),
            )
            return (
                f"submit-qa transition committed but the QA handoff to "
                f"{qa_agent.id} failed ({type(exc).__name__}: {exc!r}). "
                f"Re-issue the notification via dm."
            )
        return None

    def _resolve_skill(self, target_agent: Any, preference: list[str]) -> str:
        """Pick first skill in preference list that target_agent has.

        Reads from either ``skills`` (gateway view, list of dicts with
        ``id`` keys) or ``capabilities`` (SQLAlchemy AgentTable, list of
        strings). The DB-side AgentTable has no ``skills`` attribute,
        so a naive ``target_agent.skills`` raises AttributeError on
        production agents. Falls back to the first entry
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


class DoneChecksMixin(_Base):
    """The ``i_am_done`` fast path for work that already looks done, and the
    checks it shares with the gate: open findings, criteria addressed, the
    quality gate, and the acceptance-criteria status the gate records."""

    async def _check_quality_gate(self, ctx: _IAmDoneContext) -> Envelope | None:
        """Run the project's fast quality gate (lint + typecheck) in the dev's
        workspace before the task reaches QA, so a red gate is caught at the
        dev's desk instead of in QA review or CI. The full test suite stays on
        CI. Fail-open: a gate-infrastructure error (missing workspace or
        toolchain) is logged and never blocks the submit; only an actual check
        failure blocks.
        """
        try:
            result = await self.git.run_pre_submit_quality_gate(ctx.agent_id, ctx.task)
        except Exception as exc:
            logger.warning(
                "quality_gate_skipped", task_id=str(ctx.task_id), error=str(exc)
            )
            return None
        if result.passed:
            return None
        return Envelope.invalid_state(
            message=f"quality gate failed before QA — {result.summary}",
            remediate=(
                "Fix these in your workspace, commit, and call i_am_done again "
                "— QA reviews working code, not a red gate:\n\n" + result.output_excerpt
            ),
            context_briefing=ctx.briefing,
        )

    async def _fast_path_quality_verdict(
        self, ctx: _IAmDoneContext
    ) -> tuple[Envelope | None, bool]:
        """Quality posture for the work-already-done fast path.

        Trusts the PR's CI-green signal — the same signal the downstream
        ``pr_pass`` gate trusts (``_ci_status_guard``) — and skips the local
        workspace gate when CI is green. A repo with no CI configured (or an
        unresolvable lookup) has no CI signal, so the local gate is the only
        signal and runs. A CI ``failure`` is a known-bad build: refuse the fast
        path rather than shipping red, even before the local gate runs.

        Returns ``(rejection|None, ran_local)``. ``ran_local`` lets the caller
        distinguish "the local gate already decided" from "CI green, gate
        skipped" so it never double-gates.
        """
        # ``_resolve_ci_status`` lives on ``PRGatePassMixin``.
        status = await self._resolve_ci_status(ctx.task_id, ctx.task)
        if status is not None:
            state = status.get("state")
            if state == "success":
                return None, False
            if state == "failure":
                names = (
                    ", ".join(status.get("failing_checks") or [])
                    or "one or more checks"
                )
                return (
                    Envelope.invalid_state(
                        message=(
                            f"fast path refused — PR CI is failing ({names}); "
                            "QA reviews working code, not a red build"
                        ),
                        remediate=(
                            "fix the failing CI checks, commit, and call i_am_done "
                            "again — or run the standard path (leave "
                            "possibilities_matrix off)"
                        ),
                        context_briefing=ctx.briefing,
                    ),
                    False,
                )
        # No CI signal (no_ci_configured / pending / pending_not_scheduled /
        # error / unresolvable): the local gate is the only signal — run it.
        return await self._check_quality_gate(ctx), True

    @staticmethod
    def _extract_first_commit_sha(t: Any) -> str | None:
        """Read the first commit hash off the task, dict or model alike."""
        commits: list[Any] = list(getattr(t, "commits", []) or [])
        if not commits:
            return None
        first = commits[0]
        if isinstance(first, dict):
            return first.get("hash") or first.get("sha")
        return getattr(first, "hash", None) or getattr(first, "sha", None)

    @staticmethod
    def _already_addressed_criteria(existing_status: list[dict[str, Any]]) -> set[str]:
        """Criteria already carrying a non-empty referencing_artifact_id."""
        return {
            s["criterion"]
            for s in existing_status
            if isinstance(s, dict) and s.get("referencing_artifact_id")
        }

    @staticmethod
    def _find_existing_entry(
        existing_status: list[dict[str, Any]], criterion: str
    ) -> dict[str, Any] | None:
        """First existing entry whose criterion key matches; None otherwise."""
        for entry in existing_status:
            if isinstance(entry, dict) and entry.get("criterion") == criterion:
                return entry
        return None

    @staticmethod
    def _new_criterion_entry(
        criterion: str, has_reflect: bool, first_commit_sha: str | None, now_iso: str
    ) -> dict[str, Any]:
        """Build the per-criterion status row for an unaddressed criterion."""
        addressed = has_reflect or first_commit_sha is not None
        if not addressed:
            artifact_ref: str | None = None
        else:
            artifact_ref = first_commit_sha if first_commit_sha else "reflect-note"
        return {
            "criterion": criterion,
            "addressed": addressed,
            "artifact_ref": artifact_ref,
            "checked_at": now_iso,
        }

    async def _write_criteria_status(
        self, agent_id: UUID, task_id: UUID, t: Any
    ) -> None:
        """Persist per-criterion addressing status to task.acceptance_criteria_status.

        Pre-gateway parity: the i_am_done gate uses
        journal:reflect as a blanket addressing artifact when it is present
        (one reflect note covers all criteria — spec §9 item 1). We surface
        that decision as a structured per-criterion list so the panel and
        audit log can render per-criterion checkmarks.

        Already-addressed entries (those already carrying a non-empty
        referencing_artifact_id) are preserved as-is. Only criteria not yet
        addressed receive a new entry. The artifact_ref for newly-addressed
        criteria is the first commit sha if one exists, otherwise "reflect-note".
        """
        criteria: list[str] = list(getattr(t, "acceptance_criteria", []) or [])
        if not criteria:
            return

        existing_status: list[dict[str, Any]] = list(
            getattr(t, "acceptance_criteria_status", []) or []
        )
        already_addressed = self._already_addressed_criteria(existing_status)
        if already_addressed >= set(criteria):
            return

        first_commit_sha = self._extract_first_commit_sha(t)
        has_reflect = await self.journal.has_reflect_for_task(agent_id, task_id)
        now_iso = datetime.now(UTC).isoformat()

        new_status: list[dict[str, Any]] = []
        for criterion in criteria:
            if criterion in already_addressed:
                preserved = self._find_existing_entry(existing_status, criterion)
                if preserved is not None:
                    new_status.append(preserved)
                continue
            new_status.append(
                self._new_criterion_entry(
                    criterion, has_reflect, first_commit_sha, now_iso
                )
            )

        await self.task.set_acceptance_criteria_status(task_id, new_status)

    async def _maybe_i_am_done_fast_path(
        self, ctx: _IAmDoneContext, t: Any, agent_id: UUID
    ) -> Envelope | None:
        """Possibilities-matrix fast-path gate: returns the fast-path envelope
        when the flag is armed, the task is claimed/in_progress and owned by
        the caller, and ``_work_appears_done`` holds; None to fall through to
        the standard i_am_done path. Extracted from i_am_done to keep that
        verb within its return-count budget. ``assigned_to`` is asserted here
        so a reassigned agent cannot fast-path a task that is no longer theirs.
        """
        from roboco.config import settings as _settings

        if not _settings.possibilities_matrix_enabled:
            return None
        if str(t.status) not in ("claimed", "in_progress"):
            return None
        if t.assigned_to != agent_id:
            return None
        if not await self._work_appears_done(t):
            return None
        return await self._i_am_done_fast_path(ctx)

    async def _fast_path_rejection(self, ctx: _IAmDoneContext) -> Any:
        """The fast path's ordered rejection cascade; None means proceed.

        Order matters: notes first (cheap, no writes), then the video
        render-preview check (cheap, no writes — a video task cannot fast-
        path around looking at the rendered artifact), then finding
        resolution + guards, then the open-findings re-check, then the
        quality verdict with its toolchain backstop.
        """
        if soup := self._soup_reason(ctx.notes, "notes", 4):
            return soup
        if getattr(
            ctx.task, "source", None
        ) == markers.VIDEO_TASK_SOURCE and not markers.get_render_preview(ctx.task):
            return await self._build_tracing_gap(
                ctx.agent_id, ctx.task_id, ["render_preview"], task=ctx.task
            )
        await self._apply_resolved_findings(ctx)
        guards = (
            lambda: self._check_submit_qa_field_gates(
                ctx.agent_id, ctx.task_id, ctx.task
            ),
            # Push-first, mirroring the standard gate: _behind_base_gate reads
            # origin, which must already reflect the pushed head.
            lambda: self._ensure_branch_pushed(ctx),
            lambda: self._behind_base_gate(ctx),
            lambda: self._conventions_gate(ctx),
        )
        for guard in guards:
            if rejection := await guard():
                return rejection
        # FINDINGS_ADDRESSED re-checked post-resolution: a resolved_findings entry
        # in THIS call may have closed the open set; anything still open blocks.
        if await self._open_finding_ids(ctx.task_id):
            return Envelope.invalid_state(
                message="fast path blocked — open findings remain on the ledger",
                remediate=(
                    "name every open finding in resolved_findings "
                    "({finding_id, note}), or leave possibilities_matrix off "
                    "and run the standard i_am_done path"
                ),
                context_briefing=ctx.briefing,
            )
        rejection, ran_local = await self._fast_path_quality_verdict(ctx)
        # The local gate ran (no CI signal) without a toolchain that can
        # execute it is a hollow pass — pair the two like the standard gate
        # does. Skipped when CI-green already decided (no local run at all).
        if rejection is None and ran_local:
            rejection = await self._toolchain_broken_guard(ctx.agent_id, ctx.task)
        return rejection

    async def _i_am_done_fast_path(self, ctx: _IAmDoneContext) -> Envelope:
        """Work-already-done fast path: slimmed gates + direct transition chain.

        Skips the turn-costly gates the standard ``_i_am_done_gate`` runs: the
        retroactive rich-plan gate (``start`` without ``set_plan`` — a done task
        has no plan to author), the journal tracing gates (``notes`` is the
        reflect/handoff substitute, recorded as a progress entry by
        ``submit_verification`` / ``submit_qa``), and the local quality gate
        (the CI-green proxy in ``_fast_path_quality_verdict``). Keeps the
        non-negotiable gates the predicate already asserts, plus conventions
        (leaf dev tasks skip ``awaiting_pr_review`` so the conventions check
        must not also be skipped here — it is tree-sitter, sub-second, 0 turns
        when green). Since ``notes`` is the sole compensating control for the
        skipped journal gates, it is enforced here directly (empty AND soup —
        unlike the standard path's soup-only check, which skips an empty value
        because presence there is gated by the journal requirements this path
        doesn't run).
        """
        if rejection := await self._fast_path_rejection(ctx):
            return await self._reject_i_am_done(ctx, rejection)
        try:
            if str(ctx.task.status) == "claimed":
                await self.task.start(ctx.task_id, ctx.agent_id, "developer")
            await self.task.submit_verification(ctx.agent_id, ctx.task_id, ctx.notes)
            submitted = await self.task.submit_qa(ctx.agent_id, ctx.task_id, ctx.notes)
        except Exception as exc:
            return await self._reject_i_am_done(
                ctx,
                Envelope.invalid_state(
                    message=f"fast-path transition failed: {exc}",
                    remediate="check workspace + retry; if persistent, call i_am_idle",
                    context_briefing=ctx.briefing,
                ),
            )
        t = submitted if submitted is not None else ctx.task
        notify_warning = await self._notify_qa(ctx.agent_id, ctx.task_id, t)
        await self._touch(ctx.task_id)
        await self._record_milestone_progress(
            ctx.task_id, ctx.agent_id, "submitted for QA review", percentage=90
        )
        env = await self._build_i_am_done_ok(ctx.agent_id, ctx.task_id, t)
        if notify_warning:
            env.warning = notify_warning
        return env

    async def _open_finding_ids(self, task_id: UUID) -> tuple[str, ...]:
        """8-char ids of the task's still-OPEN revision-ledger findings.

        Read AFTER ``_apply_resolved_findings`` has already run for this
        call (see ``_i_am_done_gate``), so a resolution supplied in THIS
        i_am_done call is already reflected. Empty for a task with no
        findings at all — the FINDINGS_ADDRESSED gate is then trivially
        satisfied. Fails OPEN (treats a lookup failure as "nothing open")
        on any error — a transient ledger-read hiccup must never turn a new
        guardrail into an i_am_done outage, mirroring every other
        best-effort DB/git lookup in this module (e.g. ``_resolve_ci_status``).
        """
        try:
            repo = ReviewFindingsRepository(self.task.session)
            open_rows = await repo.list_for_task(task_id, status=STATUS_OPEN)
        except Exception:
            logger.warning(
                "open_finding_ids lookup failed — failing open", task_id=str(task_id)
            )
            return ()
        return tuple(str(row.id)[:8] for row in open_rows)

    async def _work_appears_done(self, t: Any) -> bool:
        """True when a task's work is provably already done — the gate for the
        possibilities-matrix fast path. status in {claimed, in_progress,
        verifying} + >=1 commit + PR open + every acceptance criterion
        addressed + no open findings. Ownership is NOT checked here (the
        fast-path branch asserts ``assigned_to`` so a reassigned agent cannot
        fast-path a task that is no longer theirs).

        A criterion counts as addressed if its per-criterion row carries a
        non-empty artifact reference OR an explicit ``addressed`` flag. The
        writer (``_new_criterion_entry``) stores ``artifact_ref`` while the
        canonical reader (``_already_addressed_criteria``) looks for
        ``referencing_artifact_id`` — a latent key drift masked today because
        the standard AC gate passes via the ``journal_reflect_present``
        blanket. The fast path skips that blanket (it skips the reflect gate),
        so this predicate reads BOTH keys plus ``addressed`` to see real data
        rather than ship dead code.
        """
        if str(t.status) not in ("claimed", "in_progress", "verifying"):
            return False
        if not getattr(t, "commits", None):
            return False
        if not (getattr(t, "pr_created", False) or getattr(t, "pr_number", None)):
            return False
        if not self._all_criteria_addressed(t):
            return False
        return not await self._open_finding_ids(t.id)

    @staticmethod
    def _all_criteria_addressed(t: Any) -> bool:
        """Every acceptance criterion has a per-criterion row marked addressed.

        A criterion counts as addressed if its row carries a non-empty
        artifact reference OR an explicit ``addressed`` flag. The writer
        (``_new_criterion_entry``) stores ``artifact_ref`` while the canonical
        reader (``_already_addressed_criteria``) looks for
        ``referencing_artifact_id`` — a latent key drift masked today because
        the standard AC gate passes via the ``journal_reflect_present``
        blanket. The fast path skips that blanket (it skips the reflect gate),
        so this reads BOTH keys plus ``addressed`` to see real data rather
        than ship dead code. A task with no criteria is trivially covered.
        """
        criteria = list(getattr(t, "acceptance_criteria", []) or [])
        if not criteria:
            return True
        rows = list(getattr(t, "acceptance_criteria_status", []) or [])
        addressed: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            if not (
                row.get("referencing_artifact_id")
                or row.get("artifact_ref")
                or row.get("addressed")
            ):
                continue
            crit = row.get("criterion")
            if crit:
                addressed.add(str(crit))
        return addressed >= set(criteria)
