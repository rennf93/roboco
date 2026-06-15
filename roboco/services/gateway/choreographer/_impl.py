"""Choreographer — composes existing services into intent-verb sequences.

This module has interface signatures only in Phase 0. Each verb's full
implementation lands in its respective phase (Phase 1: dev verbs, Phase 2:
QA verbs, Phase 3: doc + PM verbs, Phase 4: board verbs).

The signatures are stable contracts that the MCP servers and the
/api/v1/flow/* endpoints will call into. Phase 0 wires the dependency
injection so later phases just fill in the bodies.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import UUID

import structlog

from roboco.exceptions import MergeConflictError
from roboco.foundation.policy import lifecycle as spec_module
from roboco.services.gateway.choreographer._verb_runner import VerbRunner
from roboco.services.gateway.claim_guards import (
    already_active_guard,
    paused_tasks_guard,
    unmet_dependency_guard,
)
from roboco.services.gateway.envelope import Envelope
from roboco.services.gateway.evidence_builder import (
    BriefingInputs,
    build_context_briefing,
    build_evidence_for_task,
    build_task_handoff,
)
from roboco.services.gateway.merge_chain import resolve_parent_branch
from roboco.services.gateway.remediation import (
    hint_for_evidence_not_inspected,
    hint_for_missing_doc_files,
    hint_for_missing_journal_decision,
    hint_for_missing_journal_learning,
    hint_for_missing_progress,
    hint_for_missing_qa_notes,
    hint_for_missing_reflect,
    hint_for_short_doc_notes,
    hint_for_unaddressed_acceptance_criteria,
)

logger = structlog.get_logger()

# Minimum character length enforced on rich_plan["approach"] by the PM
# sub-tasks gate. Must match the Pydantic min_length on
# IWillPlanRequest.approach. Raised 20→150: plans were vague
# because 20 chars is a one-liner; the approach + sub_tasks are also the
# progress checklist, so they must be substantive.
_PM_APPROACH_MIN_LEN = 150

# Each PM sub_task is a real work step (it becomes a delegate target AND a
# progress-checklist item). A title alone is not a plan — require a
# description that actually says what the step does.
_PM_SUBTASK_DESC_MIN_LEN = 60


def _thin_subtask_hint(sub_tasks: list[Any]) -> str | None:
    """Return a hint if any PM sub_task is title-only / thin.

    Each sub_task is a delegate target AND a progress-checklist item, so
    a title with no real description is not a plan. Returns None when
    every sub_task carries a title and a substantive description.
    """
    for i, st in enumerate(sub_tasks):
        if not isinstance(st, dict):
            return f"sub_task #{i + 1} must be an object {{title, description}}."
        title = str(st.get("title", "")).strip()
        desc = str(st.get("description") or "").strip()
        if not title:
            return f"sub_task #{i + 1} has no title."
        if len(desc) < _PM_SUBTASK_DESC_MIN_LEN:
            return (
                f"sub_task #{i + 1} ('{title[:40]}') description is too thin "
                f"({len(desc)} chars) — need >= {_PM_SUBTASK_DESC_MIN_LEN} "
                "characters describing what the step actually does."
            )
    return None


def _normalize_sub_task(st: dict[str, Any], order: int) -> dict[str, Any]:
    """Shape a sub_task entry to panel/src/types/index.ts::SubTask."""
    from uuid import uuid4 as _uuid4

    return {
        "id": str(st.get("id") or _uuid4()),
        "title": str(st.get("title", "")),
        "description": st.get("description") or None,
        "completed": bool(st.get("completed", False)),
        "order": order,
        "estimated_hours": st.get("estimated_hours"),
        "notes": st.get("notes"),
    }


def _normalize_risk(r: dict[str, Any]) -> dict[str, Any]:
    """Shape a risk entry to panel/src/types/index.ts (description/mitigation/severity).

    Accepts either {description, mitigation, severity?} (panel shape) or
    {risk, mitigation} (pre-gateway agent shape). ``severity`` always
    serializes to a non-None string — the TaskPlanResponse schema declares
    ``risks: list[dict[str, str]]`` and a None severity from an agent who
    omits the field triggers a panel-load 500 on every poll.
    """
    description = r.get("description") or r.get("risk") or ""
    severity_raw = r.get("severity")
    severity = str(severity_raw) if severity_raw not in (None, "") else "medium"
    return {
        "description": str(description),
        "mitigation": str(r.get("mitigation", "")),
        "severity": severity,
    }


def _normalize_open_question(q: Any) -> dict[str, Any] | None:
    """Shape an open_question entry to panel shape.

    Returns None for entries we can't interpret (e.g., None or a number).
    Accepts a bare string (the agent's short-form question), {question,
    answered, answer} (pre-gateway shape), or {question, answer,
    answered_by, answered_at} (panel shape).
    """
    if isinstance(q, str):
        return {
            "question": q,
            "answer": None,
            "answered_by": None,
            "answered_at": None,
        }
    if not isinstance(q, dict):
        return None
    return {
        "question": str(q.get("question", "")),
        "answer": q.get("answer"),
        "answered_by": q.get("answered_by"),
        "answered_at": q.get("answered_at"),
    }


def _build_panel_shaped_plan(
    plan_text: str, rich_plan: dict[str, Any]
) -> dict[str, Any]:
    """Build the Task.plan dict in the exact shape the panel UI consumes.

    Panel reference: panel/src/types/index.ts::TaskPlan. Each list entry is
    normalized so the panel renders without optional-field JS errors.
    """
    sub_tasks = [
        _normalize_sub_task(st, i)
        for i, st in enumerate(rich_plan.get("sub_tasks") or [])
        if isinstance(st, dict)
    ]
    risks = [
        _normalize_risk(r)
        for r in (rich_plan.get("risks") or [])
        if isinstance(r, dict)
    ]
    open_questions = [
        normalized
        for q in (rich_plan.get("open_questions") or [])
        if (normalized := _normalize_open_question(q)) is not None
    ]
    return {
        "text": plan_text,
        "approach": rich_plan.get("approach", ""),
        "sub_tasks": sub_tasks,
        "technical_considerations": rich_plan.get("technical_considerations", []),
        "risks": risks,
        "open_questions": open_questions,
    }


def _extract_original_developer(task: Any) -> str | None:
    """Pull the original_developer slug out of a task's quick_context, if any.

    The ``quick_context`` blob carries handoff hints written by prior
    actors; "original_developer:<slug>" is one such hint. Used by the
    spec's self-review precondition (a documenter who is also the
    original developer cannot self-doc).
    """
    qc = getattr(task, "quick_context", None) or ""
    marker = "original_developer:"
    if marker not in qc:
        return None
    tail = qc.split(marker, 1)[1].strip()
    if not tail:
        return None
    return tail.split()[0] or None


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
    # messaging is optional so existing callsites + tests that
    # don't exercise session propagation don't have to plumb it in. The
    # delegate() path uses it to thread parent sessions onto new subtasks.
    messaging: Any = None
    # Per-cell project routing for the delegate verb. Optional so existing
    # callsites / tests that don't exercise Product routing don't have to plumb
    # it in; when None, delegate falls back to parent-project inheritance.
    product: Any = None
    # Orchestrator access for the rate-limited i_am_blocked path.
    # Implements get_provider_for_agent(slug) -> str | None,
    # get_active_agent_slugs_for_provider(provider) -> list[str], and
    # async mark_waiting_long(slug, waiting_for, task_id, context).
    # Optional: when None the parking step is skipped (e.g. in unit tests
    # that don't need to verify orchestrator interactions).
    orchestrator: Any = None
    # StreamEventBus for publishing RATE_LIMIT_HIT events.
    # Optional so existing callsites that don't exercise the rate-limit path
    # don't have to plumb it in.
    stream_bus: Any = None


@dataclass(frozen=True)
class _ClaimPlanStartContext:
    """Bundle of fields shared by ``i_will_work_on`` / ``i_will_plan`` helpers.

    Both verbs compose the same (claim, set_plan, start) sequence and
    share gating + recovery branches; they only differ in role gate
    (DEV vs PM) and verb name on rejections / next_hint. Frozen so the
    helper sites can't mutate caller state and to keep PLR0913 (too
    many positional args) at bay.
    """

    agent_id: UUID
    task_id: UUID
    task: Any
    role_str: str
    briefing: dict[str, Any]
    plan: str | dict[str, Any] | None
    verb_name: str


@dataclass(frozen=True)
class _IAmDoneContext:
    """Bundle of fields the ``i_am_done`` helper sites all need.

    Frozen so the helper sites can't mutate caller state and to keep
    PLR0913 (too many positional args) at bay across the dispatcher
    body and its recovery branch.
    """

    agent_id: UUID
    task_id: UUID
    task: Any
    role_str: str
    briefing: dict[str, Any]
    notes: str


@dataclass(frozen=True)
class _ReassignedCtx:
    """Bundle of fields the ``_reassigned_rejection`` helper inspects.

    Shared between ``unclaim`` and ``resume``. Frozen so the helper site
    can't mutate caller state and
    to keep PLR0913 (too many positional args) at bay.
    """

    task: Any
    agent_id: UUID
    task_id: UUID
    role_str: str
    briefing: dict[str, Any]
    upstream_hint: str


@dataclass(frozen=True)
class DelegateInputs:
    """Bundle of fields the ``delegate`` verb receives from the route layer.

    Mirrors :data:`roboco.foundation.policy.task_completeness.TASK_AT_CREATE`:
    `task_type` and `nature` have no defaults — the v1 schema enforces both at
    the HTTP boundary, and defaulting here too would let direct
    callers (tests, internal code) silently pick `'code'`/`'technical'` and
    recreate the no-acceptance-criteria deadlock.

    Optional fields (`acceptance_criteria=None`, `nature=None`) survive the
    construction step and are then rejected by the gateway-side
    `task_completeness.check` before reaching `_create_subtask_from_inputs`:
    the rejection takes the form of `Envelope.incomplete_input`
    so the agent receives a structured field-by-field guide.
    """

    title: str
    description: str
    assigned_to: str
    team: str
    task_type: str
    nature: str | None = None
    acceptance_criteria: list[str] | None = None
    estimated_complexity: str = "medium"
    project_id: UUID | None = None


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

    @property
    def messaging(self) -> Any:
        return self._deps.messaging

    @property
    def product(self) -> Any:
        return self._deps.product

    @property
    def orchestrator(self) -> Any:
        return self._deps.orchestrator

    @property
    def stream_bus(self) -> Any:
        return self._deps.stream_bus

    async def _touch(self, task_id: UUID | None) -> None:
        """Best-effort heartbeat write; silent on missing task."""
        if task_id is not None:
            await self.task.heartbeat(task_id)

    async def _record_milestone_progress(
        self,
        task_id: UUID,
        agent_id: UUID,
        message: str,
        percentage: int | None = None,
    ) -> None:
        """Append a server-emitted progress entry on a lifecycle milestone.

        Agents call ``progress()`` inconsistently. Server-side
        auto-emit on natural milestones (open_pr, i_am_done) guarantees
        the panel + audit view always have entries at the major
        transitions, regardless of how chatty the agent is. Best-effort:
        a missing task_id or write failure must not break the verb path
        — progress is observability, not correctness.
        """
        with contextlib.suppress(Exception):
            await self.task.add_progress(
                task_id=task_id,
                agent_id=agent_id,
                message=message,
                percentage=percentage,
            )

    @staticmethod
    def _reassigned_rejection(
        ctx: _ReassignedCtx,
    ) -> Envelope | None:
        """Build the "task reassigned by upstream verb" rejection envelope.

        Shared between ``unclaim`` and ``resume``. The spec doesn't model
        "task got reassigned out from
        under you by an upstream verb" — when the spec gate accepts but
        ``task.assigned_to != agent_id``, this helper produces the
        envelope with the load-bearing "current owner" hint and
        verb-specific upstream remediate text. Returns ``None`` when the
        caller still owns the task.
        """
        task = ctx.task
        if task.assigned_to == ctx.agent_id:
            return None
        current_owner = (
            str(task.assigned_to) if task.assigned_to is not None else "<unassigned>"
        )
        return Envelope.not_authorized(
            message=(
                f"task {ctx.task_id} is no longer assigned to you "
                f"(current owner: {current_owner})"
            ),
            remediate=ctx.upstream_hint,
            context_briefing=ctx.briefing,
        ).with_introspection(task=task, role=ctx.role_str)

    async def _handle_pm_reentry(
        self,
        ctx: _ClaimPlanStartContext,
        t: Any,
        pm_agent_id: UUID,
        task_id: UUID,
        role_str: str,
        briefing: dict[str, Any],
    ) -> Envelope | None:
        """Handle two distinct re-entry contracts for i_will_plan.

        Idempotent heartbeat: the PM already owns the task in in_progress —
        touch the heartbeat and return OK without re-running the spec gate.
        This is the crash-recovery path: a PM container that respawns after a
        mid-run crash re-calls i_will_plan with thin args ("resume") and must
        receive OK so it can proceed from where it left off.

        Crash-recovery claim: task is stuck in claimed after a crash — skip
        re-claim (claimed is not a valid source for the claim transition) and
        run set_plan+start to complete the interrupted sequence.

        Returns None when neither condition applies, signalling the caller to
        continue to the normal claim-plan-start path. PLR0911 budget is the
        secondary reason this lives in a helper; the domain contract above is
        the primary one.
        """
        status = str(t.status)
        if status == "in_progress" and t.assigned_to == pm_agent_id:
            await self._touch(task_id)
            return Envelope.ok(
                status=status,
                task_id=str(task_id),
                next=spec_module._INTENT_VERBS["i_will_plan"].next_hint(t),
                context_briefing=briefing,
            ).with_introspection(task=t, role=role_str)
        if status == "claimed" and t.assigned_to == pm_agent_id:
            envelope = await self._resume_from_claimed(ctx)
            return await self._post_claim_journal_gate(
                "i_will_plan", pm_agent_id, task_id, envelope
            )
        return None

    async def _pm_sub_tasks_gate(
        self,
        *,
        role_str: str,
        rich_plan: dict[str, Any] | None,
        task: Any,
        agent_id: UUID,
        task_id: UUID,
        briefing: dict[str, Any],
    ) -> Envelope | None:
        """Plan-depth gate: PMs must supply a substantive approach + sub_tasks.

        Enforces both fields at the choreographer layer so direct service-layer
        callers (MCP server, test fixtures, orchestrator-internal Python) cannot
        persist a plan that bypassed the HTTP Pydantic boundary.

        A 20-char approach and title-only sub_tasks were "no
        effort" plans. approach must be >= _PM_APPROACH_MIN_LEN and every
        sub_task needs a real title + a description that says what the
        step does (it is both a delegate target and a progress-checklist
        item). Returns a rejection Envelope when the caller is a PM role
        and any field is absent/thin; returns None when the gate passed.
        """
        if role_str not in ("cell_pm", "main_pm"):
            return None
        missing: list[str] = []
        field_hints: dict[str, str] = {}
        approach_raw = (rich_plan or {}).get("approach", "")
        if len(str(approach_raw).strip()) < _PM_APPROACH_MIN_LEN:
            missing.append("approach")
            field_hints["approach"] = (
                f"approach must be a non-empty string of at least "
                f"{_PM_APPROACH_MIN_LEN} characters describing HOW you will "
                "decompose and route this task — not a one-liner."
            )
        sub_tasks = (rich_plan or {}).get("sub_tasks") or []
        if not sub_tasks:
            missing.append("sub_tasks")
            field_hints["sub_tasks"] = (
                "PMs must list at least one sub_task — a non-empty list of "
                "{title, description}. Each becomes a delegate target AND a "
                "progress-checklist item."
            )
        elif thin := _thin_subtask_hint(sub_tasks):
            missing.append("sub_tasks")
            field_hints["sub_tasks"] = thin
        if not missing:
            return None
        return await self._emit_rejection(
            Envelope.incomplete_input(
                missing=missing,
                field_hints=field_hints,
                remediate=(
                    "re-issue i_will_plan(task_id, plan, approach, "
                    "sub_tasks=[{'title': '...', 'description': '...'}, ...]) "
                    f"with approach >= {_PM_APPROACH_MIN_LEN} chars and every "
                    f"sub_task description >= {_PM_SUBTASK_DESC_MIN_LEN} chars."
                ),
                context_briefing=briefing,
            ).with_introspection(task=task, role=role_str),
            agent_id=agent_id,
            task_id=task_id,
            verb="i_will_plan",
        )

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
        confusing two distinct calls that share a correlation_id.
        """
        if env.error is None:
            return env
        # Refresh heartbeat on every rejection so an
        # agent stuck in a verb-rejection loop (e.g., tracing_gap while
        # retrying) does not look idle to the reaper. Best-effort: a
        # heartbeat failure must never alter the envelope returned to the
        # agent. _touch already guards task_id=None.
        try:
            await self._touch(task_id)
        except Exception as exc:
            logger.warning(
                "heartbeat touch failed on rejection", error=str(exc), verb=verb
            )
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

    @staticmethod
    def _claim_verb_hint(role: str, task: Any) -> str:
        """Role + status aware 'how to start this task' hint.

        give_me_work used to hard-code
        ``i_will_work_on(...)`` for every role/status. A documenter
        handed an awaiting_documentation task (or QA an awaiting_qa
        task) was told to call a dev verb it doesn't have — it looped.
        Map to the verb that actually claims the task for this role.
        """
        tid = str(getattr(task, "id", ""))
        status = str(getattr(task, "status", ""))
        if status == "awaiting_documentation":
            return f"call claim_doc_task(task_id='{tid}') to start"
        if status == "awaiting_qa":
            return f"call claim_review(task_id='{tid}') to start"
        if role in ("cell_pm", "main_pm", "product_owner", "head_marketing"):
            return f"call i_will_plan(task_id='{tid}', plan='<plan>') to start"
        return f"call i_will_work_on(task_id='{tid}', plan='<plan>') to start"

    async def _drop_dependency_held(self, tasks: list[Any]) -> list[Any]:
        """Drop pre-assigned PENDING tasks whose non-terminal dependencies are
        still unresolved.

        ``give_me_work``'s ``list_assigned_for_agent`` fallback includes PENDING
        rows with no dependency filter, so without this a held pre-assigned
        subtask (e.g. a frontend dev's task waiting on the UX/UI design) would
        still be offered and the agent only bounced at claim time. Mirrors the
        gate in ``TaskService.list_pending_for_agent`` and ``_run_claim_guards``.
        Only PENDING rows are gated — an already-claimed task is past the gate.
        """
        offerable: list[Any] = []
        for task in tasks:
            dep_ids = list(getattr(task, "dependency_ids", []) or [])
            if (
                str(task.status) == "pending"
                and dep_ids
                and await self._deps.task.unmet_dependency_ids(dep_ids)
            ):
                continue
            offerable.append(task)
        return offerable

    async def give_me_work(self, agent_id: UUID) -> Envelope:
        """Return the agent's most-actionable task or signal idle."""
        agent = await self._deps.task.agent_for(agent_id)
        role = str(agent.role) if agent is not None else "developer"
        # Pre-assigned pending tasks take priority. Smoke run 3 (2026-05-12)
        # showed agents missing tasks that were seeded with assigned_to=<them>
        # and status=pending because the earlier code only walked
        # list_assigned_for_agent (ordered by priority/updated_at — pending
        # could rank behind in_progress rows) and the PM path checked
        # awaiting_* queues but not the pre-assigned pending case.
        pre_assigned = await self._deps.task.list_pending_for_agent(agent_id)
        if pre_assigned:
            t = pre_assigned[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=self._claim_verb_hint(role, t),
                context_briefing=await self._briefing_for(agent_id, t.id, task=t),
            ).with_introspection(task=t, role=role)
        assigned = await self._drop_dependency_held(
            await self._deps.task.list_assigned_for_agent(agent_id)
        )
        if assigned:
            t = assigned[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=self._claim_verb_hint(role, t),
                context_briefing=await self._briefing_for(agent_id, t.id, task=t),
            ).with_introspection(task=t, role=role)
        paused = await self._deps.task.list_paused_for_agent(agent_id)
        if paused:
            t = paused[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=f"call resume(task_id='{t.id}') to continue paused work",
                context_briefing=await self._briefing_for(agent_id, t.id, task=t),
            ).with_introspection(task=t, role=role)
        return Envelope.ok(
            status="idle",
            task_id=None,
            next="call i_am_idle() — no work available",
            context_briefing=await self._briefing_for(agent_id, None),
        )

    async def _briefing_for(
        self, agent_id: UUID, task_id: UUID | None, *, task: Any | None = None
    ) -> dict[str, Any]:
        """Assemble context_briefing for agent_id, optionally scoped to task_id.

        ``task`` is the already-loaded row (every claim / give_me_work / done
        path holds it). The prior-work handoff is built only when it is passed —
        no extra fetch — so task-scoped error paths that carry only an id simply
        omit the digest rather than pay a redundant read for it.
        """
        repo = self._deps.evidence_repo
        task_handoff: dict[str, Any] | None = None
        if task_id is not None and task is not None:
            # Push the prior-work digest so a freshly spawned / respawned agent
            # resumes from the previous worker's PR + commits + journal rather
            # than re-exploring the codebase cold on every lifecycle hand-off.
            handoff_highlights = await repo.journal_highlights_for_task(task_id)
            task_handoff = build_task_handoff(task, handoff_highlights)
        inputs = BriefingInputs(
            unread_a2a=await repo.list_unread_a2a(agent_id),
            unread_mentions=await repo.list_unread_mentions(agent_id),
            pending_notifications=await repo.list_pending_notifications(agent_id),
            task_metadata_gaps=(
                await repo.task_metadata_gaps(task_id) if task_id else []
            ),
            recent_team_activity=await repo.recent_team_activity(agent_id),
            blockers_in_my_lane=await repo.blockers_in_lane(agent_id),
            task_handoff=task_handoff,
            company_goals=await repo.company_goals(),
        )
        return build_context_briefing(inputs)

    async def _run_claim_guards(
        self,
        *,
        agent_id: UUID,
        task: Any,
    ) -> Envelope | None:
        """Run concurrency-invariant claim guards. Returns rejection or None.

        Scope: only system-level concurrency invariants the lifecycle spec
        does NOT model. Role/state/task_type checks now route through
        ``spec.can_invoke_action`` (CLAIM_RULES + ActionSpec.allowed_task_types)
        in the verb's spec gate; the former role-typed and
        pm_cannot_execute_code guards have been deleted.

        Pre-gateway location: _helpers.py:124-204.
        """
        in_progress = await self.task.list_in_progress_for_agent(agent_id)
        if guard := already_active_guard(in_progress, task.id):
            return guard
        paused = await self.task.list_paused_for_agent(agent_id)
        if guard := paused_tasks_guard(paused):
            return guard
        dep_ids = list(task.dependency_ids)
        if dep_ids:
            unmet = await self.task.unmet_dependency_ids(dep_ids)
            if guard := unmet_dependency_guard(task, unmet):
                # Park the dependency-gated task back to pending so the
                # orchestrator stops respawning its assignee (the respawn loop
                # targets only claimed/in_progress) and the dispatch dependency
                # filter holds it until the upstream completes. No-op unless the
                # task is currently claimed/in_progress.
                await self.task.release_dependency_blocked_claim(task.id)
                return guard
        return None

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
        in submit_up, cell_pm_complete, main_pm_complete.
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

    def _verb_runner(self) -> VerbRunner:
        """Construct a VerbRunner bound to this Choreographer's services.

        Cheap to allocate; one per verb invocation keeps the runner
        stateless across requests.
        """
        return VerbRunner(task_service=self.task, git_service=self.git)

    async def _resume_from_claimed(
        self,
        ctx: _ClaimPlanStartContext,
    ) -> Envelope:
        """Recover from a stuck `claimed` state owned by the same agent.

        spec's composed `claim` action's source-statuses do not include
        CLAIMED, so the spec gate would reject re-claiming an
        already-owned task. This branch keeps the spec contract intact
        (we never call `claim` again) but lets the agent recover by
        running just set_plan (if a plan was supplied or stored) and
        start. Shared between ``i_will_work_on`` and ``i_will_plan`` —
        ``ctx.verb_name`` selects the verb-specific labels / next_hint.
        """
        agent_id = ctx.agent_id
        task_id = ctx.task_id
        t = ctx.task
        briefing = ctx.briefing
        role_str = ctx.role_str
        plan = ctx.plan
        verb_name = ctx.verb_name
        if not t.plan and not plan:
            return await self._emit_rejection(
                Envelope.tracing_gap(
                    missing=["plan"],
                    remediate=(
                        f"call {verb_name}(task_id='{task_id}',"
                        f" plan='<one-paragraph plan describing what you will do>')"
                    ),
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=agent_id,
                task_id=task_id,
                verb=verb_name,
            )
        # Concurrency guards still apply on resumption (paused / already-active
        # in another task).
        if guard := await self._run_claim_guards(
            agent_id=agent_id,
            task=t,
        ):
            return await self._emit_rejection(
                self._with_briefing(guard, briefing).with_introspection(
                    task=t, role=role_str
                ),
                agent_id=agent_id,
                task_id=task_id,
                verb=verb_name,
            )
        try:
            if plan and not t.plan:
                t = await self.task.set_plan(task_id, plan)
            t = await self.task.start(task_id, agent_id)
        except Exception as exc:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"verb runner failed: {exc}",
                    remediate="check workspace + retry; if persistent, escalate",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=agent_id,
                task_id=task_id,
                verb=verb_name,
            )
        if t is None:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"start failed for task {task_id}",
                    remediate=(
                        "task not in a startable state"
                        " (claimed/paused/needs_revision) or no plan recorded"
                    ),
                    context_briefing=briefing,
                ).with_introspection(task=None, role=role_str),
                agent_id=agent_id,
                task_id=task_id,
                verb=verb_name,
            )
        # Pre-gateway parity: ensure the WorkSession row
        # exists on the stuck-claimed recovery path too (same guarantee as
        # _claim_plan_start_run). Re-entry guard inside ensure_work_session.
        await self.task.ensure_work_session(task_id, agent_id)
        await self._touch(task_id)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS[verb_name].next_hint(t),
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)

    async def _claim_plan_start_gate(
        self,
        ctx: _ClaimPlanStartContext,
        role: spec_module.Role,
        spec_ctx: spec_module.Context,
    ) -> Envelope | None:
        """Run all gates for an ``i_will_work_on`` / ``i_will_plan`` call.

        Order: spec.can_invoke_intent -> behavioral claim guards
        (already_active / paused / unmet_dependency). Any rejection
        short-circuits with the appropriate envelope.

        Per-role claim authority (CLAIM_RULES) is enforced inside
        spec.can_invoke_action when action == "claim", called by
        can_invoke_intent, so no separate spec.can_claim call is needed.
        """
        t, briefing, role_str = ctx.task, ctx.briefing, ctx.role_str
        verb_name = ctx.verb_name
        decision = spec_module.can_invoke_intent(role, verb_name, t, spec_ctx)
        if not decision.allowed:
            return await self._emit_rejection(
                Envelope.from_decision(decision, briefing=briefing).with_introspection(
                    task=t, role=role_str
                ),
                agent_id=ctx.agent_id,
                task_id=ctx.task_id,
                verb=verb_name,
            )
        # Behavioral pre-flight guards the spec doesn't yet model:
        # - already_active: agent has another in_progress task elsewhere
        # - paused_tasks: agent has a paused task they should resume first
        # - unmet_dependency: an upstream dependency is still non-terminal
        # The role/state/task_type checks already passed via the spec gate
        # above. These migrate into spec.extra_preconditions in a later
        # task; until then, keep them imperative so concurrency invariants
        # stay enforced.
        if guard := await self._run_claim_guards(
            agent_id=ctx.agent_id,
            task=t,
        ):
            return await self._emit_rejection(
                self._with_briefing(guard, briefing).with_introspection(
                    task=t, role=role_str
                ),
                agent_id=ctx.agent_id,
                task_id=ctx.task_id,
                verb=verb_name,
            )
        return None

    async def _claim_plan_start_run(
        self, ctx: _ClaimPlanStartContext, agent: Any, spec_ctx: spec_module.Context
    ) -> Envelope:
        """Execute composed (claim, set_plan, start) via the verb runner.

        Caller has already validated all gates. Translates runner
        exceptions and ``None`` returns into invalid_state envelopes
        so the agent gets a remediation instead of a 500. Shared
        between ``i_will_work_on`` and ``i_will_plan``.
        """
        t, briefing, role_str = ctx.task, ctx.briefing, ctx.role_str
        verb_name = ctx.verb_name
        runner = self._verb_runner()
        try:
            t = await runner.run_intent(verb_name, t, agent, spec_ctx)
        except Exception as exc:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"verb runner failed: {exc}",
                    remediate="check workspace + retry; if persistent, escalate",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=ctx.agent_id,
                task_id=ctx.task_id,
                verb=verb_name,
            )
        if t is None:
            # A composed atomic action returned None (e.g. start() rejected
            # because of an ownership/state mismatch the spec gate could not
            # see). Surface as invalid_state so the agent gets a remediation
            # rather than a 500.
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"start failed for task {ctx.task_id}",
                    remediate=(
                        "task not in a startable state"
                        " (claimed/paused/needs_revision) or no plan recorded"
                    ),
                    context_briefing=briefing,
                ).with_introspection(task=None, role=role_str),
                agent_id=ctx.agent_id,
                task_id=ctx.task_id,
                verb=verb_name,
            )
        # Pre-gateway parity: create the WorkSession
        # row so downstream subsystems (panel, PR, merge chain) can track
        # this agent's per-task git activity. work_session_id stored on the
        # task; one WorkSession per (agent, task) claim cycle; re-entry
        # guard inside ensure_work_session prevents duplicate rows.
        await self.task.ensure_work_session(ctx.task_id, ctx.agent_id)
        await self._touch(ctx.task_id)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(ctx.task_id),
            next=spec_module._INTENT_VERBS[verb_name].next_hint(t),
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)

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
        the composed (claim, set_plan, start) sequence is wrapped in a
        savepoint by the runner so a mid-sequence failure rolls back
        the DB. Idempotent re-entry: a respawned dev re-calling on a
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
        briefing = await self._briefing_for(agent_id, task_id, task=t)
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
            ctx, t, agent_id, task_id, role_str, briefing
        ):
            return reentry
        return await self._fresh_dev_claim(
            ctx,
            role,
            spec_ctx,
            agent,
            rich_plan,
            role_str,
            t,
            agent_id,
            task_id,
            briefing,
        )

    async def _dev_reentry(
        self,
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

    @staticmethod
    def _with_briefing(env: Envelope, briefing: dict[str, Any]) -> Envelope:
        """Attach a context_briefing to an Envelope (mutate-and-return helper)."""
        env.context_briefing = briefing
        return env

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
                verb="open_pr",
            )
        spec_ctx = spec_module.Context(
            actor_id=agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            original_developer_slug=_extract_original_developer(t),
        )
        decision = spec_module.can_invoke_intent(role, "open_pr", t, spec_ctx)
        if not decision.allowed:
            return await self._emit_rejection(
                Envelope.from_decision(decision, briefing=briefing).with_introspection(
                    task=t, role=role_str
                ),
                agent_id=agent_id,
                task_id=task_id,
                verb="open_pr",
            )
        await self._touch(task_id)
        runner = self._verb_runner()
        try:
            await runner.run_intent("open_pr", t, agent, spec_ctx)
        except Exception as exc:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"verb runner failed: {exc}",
                    remediate="check workspace + retry; if persistent, escalate",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=agent_id,
                task_id=task_id,
                verb="open_pr",
            )
        return await self._open_pr_success_envelope(
            agent_id, task_id, t, briefing, role_str
        )

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

    async def i_am_done(self, agent_id: UUID, task_id: UUID, notes: str) -> Envelope:
        """Submit work for QA.

        Atomic: ``spec.can_invoke_intent`` runs first and enforces the
        intent's role membership and the ``PRECONDITION_OWNERSHIP`` /
        ``PRECONDITION_COMMITS`` extra preconditions BEFORE any state
        mutation. After the spec gate accepts, two additional gate sets
        run as defense-in-depth (the spec doesn't yet model them):

          - tracing-gate preconditions (progress entry, journal:reflect,
            acceptance criteria addressed)
          - field-level submit-qa gates (currently: PR open; commits and
            ownership are already covered by the spec extras above)

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
        spec_ctx = spec_module.Context(
            actor_id=agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            original_developer_slug=_extract_original_developer(t),
            notes=notes,
        )
        # Recovery re-entry: task already in `verifying` owned by the caller
        # (e.g. orchestrator restart between submit_verification and
        # submit_qa). The spec gate would reject because the first composed
        # action `submit_verification` requires source IN_PROGRESS. Run only
        # submit_qa via the runner-equivalent path, then continue with the
        # standard tracing/field gates beforehand.
        if str(t.status) == "verifying" and t.assigned_to == agent_id:
            return await self._i_am_done_resume_from_verifying(ctx)
        decision = spec_module.can_invoke_intent(role, "i_am_done", t, spec_ctx)
        if not decision.allowed:
            return await self._reject_i_am_done(
                ctx, Envelope.from_decision(decision, briefing=briefing)
            )
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
        if rejection := await self._check_tracing_gates(
            ctx.agent_id, ctx.task_id, ctx.task
        ):
            return await self._reject_i_am_done(ctx, rejection)
        if rejection := await self._check_submit_qa_field_gates(
            ctx.agent_id, ctx.task_id, ctx.task
        ):
            return await self._reject_i_am_done(ctx, rejection)
        if rejection := await self._ensure_branch_pushed(ctx):
            return await self._reject_i_am_done(ctx, rejection)
        if rejection := await self._check_quality_gate(ctx):
            return await self._reject_i_am_done(ctx, rejection)
        # Pre-gateway parity: persist per-criterion
        # status now that all gates have passed. The write runs AFTER the
        # verdict so it cannot change i_am_done's rejection behavior.
        await self._write_criteria_status(ctx.agent_id, ctx.task_id, ctx.task)
        return None

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
                    "again."
                ),
                context_briefing=ctx.briefing,
            )
        return None

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
        await self._notify_qa(ctx.agent_id, ctx.task_id, t)
        await self._touch(ctx.task_id)
        # Server-side milestone progress so the panel always
        # records the QA handoff regardless of agent's progress() habits.
        await self._record_milestone_progress(
            ctx.task_id,
            ctx.agent_id,
            "submitted for QA review",
            percentage=90,
        )
        return await self._build_i_am_done_ok(ctx.agent_id, ctx.task_id, t)

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
        await self._notify_qa(ctx.agent_id, ctx.task_id, t)
        await self._touch(ctx.task_id)
        return await self._build_i_am_done_ok(ctx.agent_id, ctx.task_id, t)

    async def _check_tracing_gates(
        self, agent_id: UUID, task_id: UUID, t: Any
    ) -> Envelope | None:
        """Run progress / reflect / acceptance-criteria / during-work tracing gates.

        The spec composes (submit_verification, submit_qa) for i_am_done and
        the auto-run submit_verification flips self_verified=True before
        submit_qa runs. SELF_VERIFIED therefore acts as a defense-in-depth
        backstop *after* the spec — checking it pre-flight would block the
        auto-verify path. It is filtered here and re-asserted by the spec
        action's own preconditions.
        """
        from roboco.foundation.policy import tracing as _tr

        has_reflect = await self.journal.has_reflect_for_task(agent_id, task_id)
        has_decision = await self.journal.has_decision_for_task(agent_id, task_id)
        has_learning = await self.journal.has_learning_for_task(agent_id, task_id)
        has_struggle = await self.journal.has_struggle_for_task(agent_id, task_id)
        during_work_count = sum([has_decision, has_learning, has_struggle])

        ctx = _tr.GateContext(
            journal_reflect_present=has_reflect,
            journal_decision_present=has_decision,
            journal_learning_present=has_learning,
            journal_struggle_present=has_struggle,
            journal_during_work_count=during_work_count,
        )
        requirements: list[_tr.Requirement] = [
            r
            for r in _tr.requirements_for("i_am_done")
            if r is not _tr.Requirement.SELF_VERIFIED
        ]
        result = _tr.check_requirements(
            task=t,
            requirements=requirements,
            ctx=ctx,
        )
        if result.passed:
            return None
        return await self._build_tracing_gap(agent_id, task_id, result.missing, task=t)

    async def _post_claim_journal_gate(
        self,
        verb: str,
        agent_id: UUID,
        task_id: UUID,
        envelope: Envelope,
    ) -> Envelope:
        """Apply the claim-time journal tracing gate AFTER a successful claim.

        Pre-gateway parity (spec §11 P1, P3): the (claim, set_plan, start)
        sequence is allowed to commit so the agent owns the task, then we
        verify the matching journal entry exists. If absent, the agent
        gets a tracing_gap with a remediation hint — they journal and
        retry the verb (idempotent re-entry shortcuts back to OK once the
        entry is present).

        If `envelope` is already an error (claim failed or the runner
        rejected), we pass it through untouched — no point demanding a
        journal note when the claim itself didn't stick.
        """
        if envelope.error is not None:
            return envelope
        t = await self.task.get(task_id)
        if t is None:
            return envelope
        gap = await self._check_claim_journal_at_claim(verb, agent_id, task_id, t)
        return gap if gap is not None else envelope

    async def _check_claim_journal_at_claim(
        self, verb: str, agent_id: UUID, task_id: UUID, t: Any
    ) -> Envelope | None:
        """Post-claim tracing gate for i_will_work_on / i_will_plan.

        Pre-gateway parity (spec §11 P1, P3): developers wrote a
        journal:note on every claim; PMs wrote a journal:decision on
        plan. The check runs AFTER the composed (claim, set_plan, start)
        sequence has succeeded — the claim itself stays. If the journal
        entry is missing, the agent receives a tracing_gap envelope and
        must journal then retry the verb (similar to how i_am_done's
        post-claim gates work).

        ``PLAN`` is filtered out of the required-set because the spec's
        composed action has already enforced PRECONDITION_PLAN before
        reaching this point — re-asserting it here would be redundant
        and produce a misleading hint when the only real failure is the
        missing journal entry.
        """
        from roboco.foundation.policy import tracing as _tr

        ctx = _tr.GateContext()
        if verb == "i_will_work_on":
            has_note = await self.journal.has_note_for_task(agent_id, task_id)
            ctx = _tr.GateContext(journal_note_at_claim_present=has_note)
        elif verb == "i_will_plan":
            has_decision = await self.journal.has_decision_for_task(agent_id, task_id)
            ctx = _tr.GateContext(journal_decision_present=has_decision)
        requirements: list[_tr.Requirement] = [
            r for r in _tr.requirements_for(verb) if r is not _tr.Requirement.PLAN
        ]
        result = _tr.check_requirements(
            task=t,
            requirements=requirements,
            ctx=ctx,
        )
        if result.passed:
            return None
        return await self._build_tracing_gap(agent_id, task_id, result.missing, task=t)

    async def _check_pm_decision_required(
        self, verb: str, agent_id: UUID, task_id: UUID, t: Any
    ) -> Envelope | None:
        """Standard PM-verb tracing gate driven by VERB_REQUIREMENTS.

        Used by ``unblock``, ``escalate_up``, ``escalate_to_ceo``, and
        ``delegate`` — each declares only ``JOURNAL_DECISION`` in the
        foundation table. Verbs requiring more (``complete``,
        ``submit_up``) use the verb-specific helpers below which thread
        the additional state (reflect, notes, subtasks) into GateContext.

        Pre-gateway parity: the gate requires the
        *most recent* journal:decision for (agent, task) to be no older
        than ``settings.pm_decision_window_seconds``. Older decisions are
        treated as missing so PMs write a fresh decision around each
        decision point rather than once at task creation.
        """
        from roboco.config import settings as _settings
        from roboco.foundation.policy import tracing as _tr

        # C8: recency-window only. Per-verb-group consumption tracking
        # (one decision satisfies exactly one delegate/unblock/escalate
        # call, then is consumed) is out of scope — Choreographer is
        # per-request so multi-call state would need a persistent store.
        latest = await self.journal.latest_decision_at(agent_id, task_id)
        window_seconds = _settings.pm_decision_window_seconds
        fresh = (
            latest is not None
            and (datetime.now(UTC) - latest).total_seconds() <= window_seconds
        )

        ctx = _tr.GateContext(journal_decision_present=fresh)
        result = _tr.check_requirements(
            task=t,
            requirements=list(_tr.requirements_for(verb)),
            ctx=ctx,
        )
        if result.passed:
            return None
        return await self._build_tracing_gap(agent_id, task_id, result.missing, task=t)

    async def _check_complete_gates(
        self, agent_id: UUID, task_id: UUID, notes: str
    ) -> Envelope | None:
        """Tracing gate for cell-PM and main-PM ``complete`` verbs.

        VERB_REQUIREMENTS["complete"] = JOURNAL_DECISION + JOURNAL_REFLECT
        + NOTES_MIN_CHARS. ``SUBTASKS_TERMINAL`` is enforced separately by
        ``_subtasks_not_terminal_envelope`` in the cell/main complete
        guards because that gate emits a richer remediation message
        listing the non-terminal subtasks; keeping it inline preserves
        that UX.
        """
        from types import SimpleNamespace

        from roboco.config import settings as _settings
        from roboco.foundation.policy import tracing as _tr

        has_decision = await self.journal.has_decision_for_task(agent_id, task_id)
        has_reflect = await self.journal.has_reflect_for_task(agent_id, task_id)
        task_view = SimpleNamespace(notes=notes)
        ctx = _tr.GateContext(
            journal_decision_present=has_decision,
            # A PM closing/submitting a task documents it in its *decision* note;
            # a separate *reflect* adds little for a coordination/review close and
            # is exactly the artifact weak-model PMs forget — looping on the
            # reflect gate until reaped (re-confirmed live 2026-06-10). Accept a
            # decision as satisfying reflect for the PM complete/submit_up close;
            # the gate still requires a decision + substantive notes, so the close
            # stays documented — only the redundant second-artifact demand drops.
            journal_reflect_present=has_reflect or has_decision,
            notes_min_chars=getattr(_settings, "notes_min_chars", 20),
        )
        result = _tr.check_requirements(
            task=task_view,
            requirements=list(_tr.requirements_for("complete")),
            ctx=ctx,
        )
        if result.passed:
            return None
        return await self._build_tracing_gap(agent_id, task_id, result.missing)

    async def _check_submit_up_gates(
        self, agent_id: UUID, task_id: UUID, notes: str
    ) -> Envelope | None:
        """Tracing gate for ``submit_up`` (cell PM bubble-up).

        VERB_REQUIREMENTS["submit_up"] = SUBTASKS_TERMINAL + JOURNAL_DECISION
        + JOURNAL_REFLECT + NOTES_MIN_CHARS. The notes value is threaded
        through a SimpleNamespace shim because the verb hasn't persisted
        it to the task yet. ``SUBTASKS_TERMINAL`` is filtered out here
        because the inline ``_subtasks_not_terminal_envelope`` that
        follows enumerates the non-terminal subtask ids — strictly richer
        remediation than the generic foundation hint.
        """
        from types import SimpleNamespace

        from roboco.config import settings as _settings
        from roboco.foundation.policy import tracing as _tr

        has_decision = await self.journal.has_decision_for_task(agent_id, task_id)
        has_reflect = await self.journal.has_reflect_for_task(agent_id, task_id)
        task_view = SimpleNamespace(notes=notes)
        ctx = _tr.GateContext(
            journal_decision_present=has_decision,
            # A PM closing/submitting a task documents it in its *decision* note;
            # a separate *reflect* adds little for a coordination/review close and
            # is exactly the artifact weak-model PMs forget — looping on the
            # reflect gate until reaped (re-confirmed live 2026-06-10). Accept a
            # decision as satisfying reflect for the PM complete/submit_up close;
            # the gate still requires a decision + substantive notes, so the close
            # stays documented — only the redundant second-artifact demand drops.
            journal_reflect_present=has_reflect or has_decision,
            notes_min_chars=getattr(_settings, "notes_min_chars", 20),
        )
        requirements: list[_tr.Requirement] = [
            r
            for r in _tr.requirements_for("submit_up")
            if r is not _tr.Requirement.SUBTASKS_TERMINAL
        ]
        result = _tr.check_requirements(
            task=task_view,
            requirements=requirements,
            ctx=ctx,
        )
        if result.passed:
            return None
        return await self._build_tracing_gap(agent_id, task_id, result.missing)

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
        """
        journal_highlights = await self.evidence_repo.journal_highlights_for_task(
            task_id
        )
        files_changed: list[str] = []
        if t.branch_name:
            files_changed = await self.git.list_changed_files(branch_name=t.branch_name)
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
            context_briefing=await self._briefing_for(agent_id, task_id, task=t),
        ).with_introspection(task=t, role=role)

    @staticmethod
    def _hint_for_missing_key(missing_key: str, task_id: UUID) -> str | None:
        """Map a single tracing-requirement key to its agent-facing hint.

        Returns ``None`` for keys that need composite handling (i.e.,
        ``acceptance_criterion:<name>``, which the caller batches into
        a single multi-criterion hint).
        """
        from roboco.config import settings as _roboco_settings

        tid = str(task_id)
        notes_min = getattr(_roboco_settings, "notes_min_chars", 20)
        simple_hints: dict[str, str] = {
            "progress>=1": hint_for_missing_progress(),
            "journal:reflect": hint_for_missing_reflect(task_id=tid),
            "journal:decision": hint_for_missing_journal_decision(),
            "qa_notes>=min": hint_for_missing_qa_notes(),
            "journal:learning": hint_for_missing_journal_learning(),
            "qa_evidence_inspected": hint_for_evidence_not_inspected(task_id=tid),
            "docs_notes>=min": hint_for_short_doc_notes(
                min_chars=_roboco_settings.docs_notes_min_chars
            ),
            "docs_files_non_empty": hint_for_missing_doc_files(),
            "journal:note_at_claim": (
                "pre-gateway parity P1: write a journal:note at claim. "
                f"Call note(scope='note', task_id='{tid}', "
                "text='<initial assessment>') describing your read of the "
                "task, then retry i_will_work_on."
            ),
            "journal:decision_at_claim": (
                "pre-gateway parity P3: PMs write a journal:decision on plan. "
                f"Call note(scope='decision', task_id='{tid}', "
                "text='<delegation rationale>') with your planning rationale, "
                "then retry i_will_plan."
            ),
            "notes>=min": (
                f"`notes` must be at least {notes_min} chars describing the "
                "merge / escalation rationale; pass a longer notes argument "
                "and retry."
            ),
            "subtasks_terminal": (
                "all subtasks must be in a terminal state (completed or "
                "cancelled) before this transition; wait for the closure "
                "dispatcher to bring you back when ready."
            ),
            "journal:during_work>=1": (
                "no journal:decision / :learning / :struggle entry exists "
                "for this task yet. Pre-gateway parity: developers write "
                "at least one work-progress journal entry before submit. "
                f"Call note(scope='decision'|'learning'|'struggle', "
                f"task_id='{tid}', text='<what you decided or learned>') "
                "with a substantive entry, then retry i_am_done. "
                "NOTE: scope='reflect' does NOT count for this requirement — "
                "reflect is the post-work summary; during_work demands "
                "an entry written while the work was happening."
            ),
            "journal:struggle": (
                f"call note(scope='struggle', task_id='{tid}', "
                "text='<what is blocking you>') with the blocker details, "
                "then retry."
            ),
            "commits>=1": (
                "no commits linked to this task yet. Use commit(message=...) "
                "to record your changes, then retry."
            ),
            "pr_open": (
                "no PR has been opened for this task. Call open_pr() to "
                "push the branch + open the PR, then retry."
            ),
            "self_verified": (
                "task has not been self-verified. i_am_done normally runs "
                "submit_verification automatically; if you see this gap, "
                "retry i_am_done after the previous call returned."
            ),
        }
        return simple_hints.get(missing_key)

    async def _build_tracing_gap(
        self,
        agent_id: UUID,
        task_id: UUID,
        missing: list[str],
        *,
        task: Any | None = None,
    ) -> Envelope:
        """Translate missing requirement keys into agent-facing hints.

        Multi-missing remediate uses a numbered list so the
        agent sees each requirement as a distinct step instead of a
        single semicolon-joined sentence the model parses as one
        instruction. Each missing key with no hint in
        ``_hint_for_missing_key`` still surfaces as a literal
        ``missing[]`` entry (defense-in-depth: the agent gets at least
        the key name even if no hint is registered).
        """
        hints: list[str] = []
        unaddressed: list[str] = []
        unhinted: list[str] = []
        for m in missing:
            if m.startswith("acceptance_criterion:"):
                unaddressed.append(m.split(":", 1)[1])
                continue
            hint = self._hint_for_missing_key(m, task_id)
            if hint is not None:
                hints.append(hint)
            else:
                unhinted.append(m)
        if unaddressed:
            hints.append(
                hint_for_unaddressed_acceptance_criteria(
                    criteria=unaddressed,
                    task_id=str(task_id),
                )
            )
        # Fallback hints for missing keys without a registered hint —
        # the agent at least sees the literal token instead of nothing.
        for token in unhinted:
            hints.append(
                f"requirement {token!r} not satisfied — see lifecycle docs "
                f"or escalate via i_am_blocked if you do not know how to "
                f"satisfy this."
            )
        if len(hints) <= 1:
            remediate = hints[0] if hints else ""
        else:
            numbered = "\n".join(f"{i + 1}. {h}" for i, h in enumerate(hints))
            remediate = (
                f"Multiple requirements missing — address ALL of the "
                f"following before retrying:\n{numbered}"
            )
        return Envelope.tracing_gap(
            missing=missing,
            remediate=remediate,
            context_briefing=await self._briefing_for(agent_id, task_id, task=task),
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

    @staticmethod
    def _build_struggle_body(
        reason: str, blocker_type: str | None, what_needed: str | None
    ) -> str:
        """Render the reason + optional Blocker Type / What Needed sections."""
        # Pre-gateway parity (G8 part b): typed blocker_type / what_needed
        # render as structured markdown blocks instead of a flat sentence.
        if not (blocker_type or what_needed):
            return reason
        parts = [reason.strip()] if reason.strip() else []
        if blocker_type:
            parts.append(f"## Blocker Type\n{blocker_type}")
        if what_needed:
            parts.append(f"## What Needed\n{what_needed}")
        return "\n\n".join(parts)

    async def _run_i_am_blocked_intent(
        self,
        agent_id: UUID,
        task_id: UUID,
        t: Any,
        agent: Any,
        spec_ctx: spec_module.Context,
        role_str: str,
        briefing: dict[str, Any],
    ) -> tuple[Any, Envelope | None]:
        """Dispatch (block,) via the verb runner; return (task, rejection)."""
        runner = self._verb_runner()
        try:
            updated = await runner.run_intent("i_am_blocked", t, agent, spec_ctx)
        except Exception as exc:
            return t, await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"verb runner failed: {exc}",
                    remediate="check workspace + retry; if persistent, escalate",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=agent_id,
                task_id=task_id,
                verb="i_am_blocked",
            )
        return updated, None

    @staticmethod
    def _parse_retry_after(what_needed: str | None) -> float | None:
        """Extract a retry-after seconds value from ``what_needed``, or None.

        Agents may embed the Retry-After seconds in the ``what_needed``
        field as a numeric string (e.g. ``"30"`` or ``"60.5"``). This
        helper tries to parse it; any non-numeric or absent value returns
        ``None``, which maps to the nullable ``retryAfterSeconds`` in the
        RATE_LIMIT_HIT event.
        """
        if what_needed is None:
            return None
        try:
            return float(what_needed.strip())
        except (ValueError, AttributeError):
            return None

    async def _handle_rate_limited_parking(
        self,
        agent_id: UUID,
        task_id: UUID,
        t: Any,
        agent: Any,
        role_str: str,
        briefing: dict[str, Any],
        what_needed: str | None,
    ) -> Envelope:
        """Rate-limited fast path: park agents, publish event, persist state.

        Called from ``i_am_blocked`` when ``reason == 'rate_limited'``.
        The task stays in its current status (``in_progress``) — no block
        transition occurs. Instead, every orchestrator-tracked active agent
        sharing the same provider as the calling agent is parked via
        ``mark_waiting_long(waiting_for='rate_limit_lifted')``.  A
        ``RATE_LIMIT_HIT`` event is published so downstream consumers (the
        orchestrator backpressure layer, the panel) can react.
        """
        from roboco.models.events import Event, EventType

        agent_slug: str | None = (
            getattr(agent, "slug", None) if agent is not None else None
        )

        provider: str = "unknown"
        affected_agents: list[str] = []

        orch = self.orchestrator
        if orch is not None and agent_slug is not None:
            with contextlib.suppress(Exception):
                prov = orch.get_provider_for_agent(agent_slug)
                if prov:
                    provider = prov
            with contextlib.suppress(Exception):
                affected_agents = list(
                    orch.get_active_agent_slugs_for_provider(provider)
                )
            for slug in affected_agents:
                with contextlib.suppress(Exception):
                    await orch.mark_waiting_long(
                        slug,
                        waiting_for="rate_limit_lifted",
                        task_id=str(task_id),
                        context={"provider": provider, "triggered_by": agent_slug},
                    )

        retry_after_seconds = self._parse_retry_after(what_needed)

        # Persist rate-limit state to Redis so downstream decide_spawn()
        # calls can gate new spawns for this provider.  Skipped when the
        # provider is "unknown" (orchestrator not wired or not tracking the
        # agent) to avoid polluting the tracker with meaningless keys.
        if provider != "unknown":
            with contextlib.suppress(Exception):
                from roboco.services.gateway.rate_limit_tracker import (
                    RateLimitStateTracker,
                )

                await RateLimitStateTracker(provider).activate(
                    retry_after=retry_after_seconds,
                    affected_agents=affected_agents,
                )

        bus = self.stream_bus
        if bus is not None:
            event = Event(
                type=EventType.RATE_LIMIT_HIT,
                data={
                    "provider": provider,
                    "affectedAgents": affected_agents,
                    "retryAfterSeconds": retry_after_seconds,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
                source_agent=str(agent_id),
            )
            with contextlib.suppress(Exception):
                await bus.publish(event)

        await self._touch(task_id)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=(
                "agent parked waiting for rate_limit_lifted; "
                "will be respawned when the limit clears"
            ),
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)

    async def i_am_blocked(
        self,
        agent_id: UUID,
        task_id: UUID,
        reason: str,
        blocker_type: str | None = None,
        what_needed: str | None = None,
    ) -> Envelope:
        """Escalate task_id and write a struggle journal entry; idle the agent.

        Atomic: ``spec.can_invoke_intent`` runs first and enforces role
        membership (developer/qa/documenter) and the source-status
        constraint of the composed ``block`` action (in_progress only).
        After the spec gate accepts, the journal:struggle entry is written
        from the verb body, then either:

        - ``reason == 'rate_limited'``: the task is **not** transitioned to
          ``blocked``; instead every active agent on the same provider is
          parked via ``mark_waiting_long(waiting_for='rate_limit_lifted')``
          and a ``RATE_LIMIT_HIT`` event is published.
        - any other reason: ``VerbRunner.run_intent("i_am_blocked", ...)``
          dispatches the ``(block,)`` atomic chain wrapped in a savepoint,
          transitioning the task to ``blocked``.
        """
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=agent_id,
                task_id=task_id,
                verb="i_am_blocked",
            )
        agent = await self.task.agent_for(agent_id)
        role_str = str(agent.role) if agent is not None else "developer"
        briefing = await self._briefing_for(agent_id, task_id, task=t)
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
                verb="i_am_blocked",
            )
        spec_ctx = spec_module.Context(
            actor_id=agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            original_developer_slug=_extract_original_developer(t),
            notes=reason,
        )
        decision = spec_module.can_invoke_intent(role, "i_am_blocked", t, spec_ctx)
        if not decision.allowed:
            return await self._emit_rejection(
                Envelope.from_decision(decision, briefing=briefing).with_introspection(
                    task=t, role=role_str
                ),
                agent_id=agent_id,
                task_id=task_id,
                verb="i_am_blocked",
            )
        # Journal:struggle is a side effect outside the lifecycle action; it
        # stays in the verb body, written before the runner dispatches `block`
        # so a later runner failure still leaves an audit trail.
        await self.journal.write_struggle(
            agent_id=agent_id,
            task_id=task_id,
            content=self._build_struggle_body(reason, blocker_type, what_needed),
        )

        # Rate-limited fast path: skip the block state transition and park
        # all affected agents instead.
        if reason.strip().lower() == "rate_limited":
            return await self._handle_rate_limited_parking(
                agent_id=agent_id,
                task_id=task_id,
                t=t,
                agent=agent,
                role_str=role_str,
                briefing=briefing,
                what_needed=what_needed,
            )

        t, rejection = await self._run_i_am_blocked_intent(
            agent_id, task_id, t, agent, spec_ctx, role_str, briefing
        )
        if rejection is not None:
            return rejection
        await self._touch(task_id)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS["i_am_blocked"].next_hint(t),
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)

    async def unclaim(self, agent_id: UUID, task_id: UUID) -> Envelope:
        """Voluntarily release a claimed/in_progress task back to pending.

        Audit J33 — ``_pending_assignment_guard`` already remediates with
        "or unclaim it first," but the verb didn't exist. This makes that
        promise true. The work-in-progress branch survives; only the claim
        is released so another agent (or the same one, fresh) can pick it
        up.

        Spec gate runs first (role membership only — unclaim's IntentSpec
        has ``composes=()``, so the spec does not enforce a source-status
        constraint). After the gate accepts, the reassignment-rejection
        branch (introduced in commit a5d358d) catches "task was reassigned
        out from under you by an upstream verb" — the spec doesn't model
        that case. Then the verb body owns dispatch via
        ``task.unclaim_for_agent`` because ``composes=()`` (no atomic action
        for the runner to run); the service-level None return surfaces as
        invalid_state when the status drifted between get and write.
        """
        t = await self.task.get(task_id)
        briefing = await self._briefing_for(agent_id, task_id, task=t)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=agent_id,
                task_id=task_id,
                verb="unclaim",
            )
        agent = await self.task.agent_for(agent_id)
        role_str = str(agent.role) if agent is not None else "developer"
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
                verb="unclaim",
            )
        spec_ctx = spec_module.Context(
            actor_id=agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            original_developer_slug=_extract_original_developer(t),
        )
        decision = spec_module.can_invoke_intent(role, "unclaim", t, spec_ctx)
        if not decision.allowed:
            return await self._emit_rejection(
                Envelope.from_decision(decision, briefing=briefing).with_introspection(
                    task=t, role=role_str
                ),
                agent_id=agent_id,
                task_id=task_id,
                verb="unclaim",
            )
        reassigned = self._reassigned_rejection(
            _ReassignedCtx(
                task=t,
                agent_id=agent_id,
                task_id=task_id,
                role_str=role_str,
                briefing=briefing,
                upstream_hint=(
                    "the task was reassigned by an upstream verb "
                    "(cell_pm_complete / main_pm_complete / unblock). "
                    "call give_me_work() to find your current work."
                ),
            )
        )
        if reassigned is not None:
            return await self._emit_rejection(
                reassigned, agent_id=agent_id, task_id=task_id, verb="unclaim"
            )
        # Verb body owns dispatch — unclaim's IntentSpec has composes=(),
        # so VerbRunner has no atomic action to run.
        after = await self.task.unclaim_for_agent(task_id, agent_id)
        if after is None:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"cannot unclaim from status {t.status}",
                    remediate=(
                        "only a task assigned to you in pending / claimed / "
                        "in_progress can be unclaimed"
                    ),
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
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
            next=spec_module._INTENT_VERBS["unclaim"].next_hint(after),
            context_briefing=briefing,
        ).with_introspection(task=after, role=role_str)

    @staticmethod
    def _validate_reassign(
        t: Any, agent_id: UUID, new_assignee: str
    ) -> Envelope | None:
        """Intra-cell guard for ``reassign`` (verb body owns it — composes=()).

        The task must be claimed/in_progress and in the caller's own cell, and
        ``new_assignee`` must be a developer of that same cell. Returns a
        rejection envelope, or None when the hand-off is allowed.
        """
        from roboco.agents_config import get_agent_role, get_agent_team
        from roboco.seeds.initial_data import AGENT_UUIDS

        caller_team = get_agent_team(str(agent_id))
        task_team = getattr(t.team, "value", t.team)
        status = str(getattr(t.status, "value", t.status))
        if status not in ("claimed", "in_progress"):
            return Envelope.invalid_state(
                message=f"cannot reassign a task in status {status!r}",
                remediate=(
                    "reassign only a claimed or in_progress task; review/terminal"
                    " states are owned by their lifecycle role"
                ),
                context_briefing={},
            )
        if task_team is None or task_team != caller_team:
            return Envelope.not_authorized(
                message=f"task team {task_team!r} is not your cell ({caller_team!r})",
                remediate="you can only reassign tasks inside your own cell",
                context_briefing={},
            )
        if new_assignee not in AGENT_UUIDS:
            return Envelope.invalid_state(
                message=f"unknown agent slug {new_assignee!r}",
                remediate=(
                    "new_assignee must be a developer slug in your cell, e.g. be-dev-2"
                ),
                context_briefing={},
            )
        if get_agent_role(new_assignee) != "developer":
            return Envelope.not_authorized(
                message=f"{new_assignee!r} is not a developer",
                remediate=(
                    "reassign hands work to a developer in your cell;"
                    " only dev slugs are valid"
                ),
                context_briefing={},
            )
        if get_agent_team(new_assignee) != caller_team:
            return Envelope.not_authorized(
                message=f"{new_assignee!r} is not in your cell ({caller_team!r})",
                remediate="reassign only to a developer in your own cell",
                context_briefing={},
            )
        return None

    async def reassign(
        self, agent_id: UUID, task_id: UUID, new_assignee: str
    ) -> Envelope:
        """A cell PM hands a claimed/in_progress task to another dev in its cell.

        Intra-cell only (see ``_validate_reassign``). The branch is keyed to the
        task, not the agent, so it survives — the new developer continues the
        work-in-progress and is respawned by the orchestrator.
        """
        from roboco.seeds.initial_data import AGENT_UUIDS

        t = await self.task.get(task_id)
        briefing = await self._briefing_for(agent_id, task_id, task=t)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=agent_id,
                task_id=task_id,
                verb="reassign",
            )
        agent = await self.task.agent_for(agent_id)
        role_str = str(agent.role) if agent is not None else "cell_pm"
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
                verb="reassign",
            )
        spec_ctx = spec_module.Context(
            actor_id=agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            original_developer_slug=_extract_original_developer(t),
        )
        decision = spec_module.can_invoke_intent(role, "reassign", t, spec_ctx)
        if not decision.allowed:
            return await self._emit_rejection(
                Envelope.from_decision(decision, briefing=briefing).with_introspection(
                    task=t, role=role_str
                ),
                agent_id=agent_id,
                task_id=task_id,
                verb="reassign",
            )
        guard = self._validate_reassign(t, agent_id, new_assignee)
        if guard is not None:
            return await self._emit_rejection(
                guard.with_introspection(task=t, role=role_str),
                agent_id=agent_id,
                task_id=task_id,
                verb="reassign",
            )
        after = await self.task.reassign_active_claim(
            task_id, UUID(AGENT_UUIDS[new_assignee])
        )
        if after is None:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"cannot reassign from status {t.status}",
                    remediate="only a claimed / in_progress task can be reassigned",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=agent_id,
                task_id=task_id,
                verb="reassign",
            )
        return Envelope.ok(
            status=str(after.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS["reassign"].next_hint(after),
            context_briefing=briefing,
        ).with_introspection(task=after, role=role_str)

    async def resume(self, agent_id: UUID, task_id: UUID) -> Envelope:
        """Resume a paused task this agent owns; transitions paused → in_progress.

        Audit J33 — ``i_am_idle`` auto-pauses owned in_progress tasks (so
        the closure dispatcher can wake the agent when subtasks finish),
        and the lifecycle table allows ``paused → in_progress``, but no
        verb exposed that transition to agents. ``i_will_work_on`` is
        explicitly limited to needs_revision/pending/claimed; overloading
        it would muddy state-machine intent. ``resume`` keeps it explicit.

        Spec gate runs first and enforces role membership plus the
        composed ``resume`` action's source-status constraint (PAUSED
        only). After the gate accepts, the reassignment-rejection branch
        catches "task was reassigned by an upstream verb" — the spec
        doesn't model that case, so the existing envelope text (preserved
        from commit a5d358d) is the load-bearing hint. Then
        ``VerbRunner.run_intent("resume", ...)`` dispatches the (resume,)
        atomic chain wrapped in a savepoint.
        """
        t = await self.task.get(task_id)
        briefing = await self._briefing_for(agent_id, task_id, task=t)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=agent_id,
                task_id=task_id,
                verb="resume",
            )
        agent = await self.task.agent_for(agent_id)
        role_str = str(agent.role) if agent is not None else "developer"
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
                verb="resume",
            )
        spec_ctx = spec_module.Context(
            actor_id=agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            original_developer_slug=_extract_original_developer(t),
        )
        decision = spec_module.can_invoke_intent(role, "resume", t, spec_ctx)
        if not decision.allowed:
            return await self._emit_rejection(
                Envelope.from_decision(decision, briefing=briefing).with_introspection(
                    task=t, role=role_str
                ),
                agent_id=agent_id,
                task_id=task_id,
                verb="resume",
            )
        reassigned = self._reassigned_rejection(
            _ReassignedCtx(
                task=t,
                agent_id=agent_id,
                task_id=task_id,
                role_str=role_str,
                briefing=briefing,
                upstream_hint=(
                    "the task was reassigned by an upstream verb. "
                    "call give_me_work() to find your current work."
                ),
            )
        )
        if reassigned is not None:
            return await self._emit_rejection(
                reassigned, agent_id=agent_id, task_id=task_id, verb="resume"
            )
        runner = self._verb_runner()
        runner_failure_msg: str | None = None
        try:
            after = await runner.run_intent("resume", t, agent, spec_ctx)
        except Exception as exc:
            after = None
            runner_failure_msg = f"verb runner failed: {exc}"
        if after is None:
            # Either the runner raised (recorded above) or the service
            # returned None despite the spec gate accepting (race: status
            # drifted between get and write). Both surface as invalid_state.
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=runner_failure_msg
                    or f"cannot resume from status {t.status} (drift)",
                    remediate="only paused tasks can be resumed",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=agent_id,
                task_id=task_id,
                verb="resume",
            )
        # Heartbeat — agent is back to active work after the resume.
        await self._touch(task_id)
        return Envelope.ok(
            status=str(after.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS["resume"].next_hint(after),
            context_briefing=briefing,
        ).with_introspection(task=after, role=role_str)

    async def i_am_idle(self, agent_id: UUID) -> Envelope:
        """Report no more work. Soft-block if there are unread A2As or @mentions.

        Before marking the agent idle:

        1. Bail with ``idle_with_unread`` when context_briefing has unread A2A
           or @mentions (must address those first).
        2. Refuse with INVALID_STATE if the agent has any pending tasks
           assigned but never claimed — they must call i_will_work_on (dev/qa/
           doc) or i_will_plan (pm) first. Board/advisory roles (product_owner,
           head_marketing, auditor) are exempt: they review without claiming.
           (Gate Set C, pre-gateway implicit via the orchestrator's
           auto-respawn.)
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
                    "clear your inbox, then retry i_am_idle(): read_messages()"
                    " for unread A2A, notify_ack() per @mention notification"
                ),
                context_briefing=briefing,
            )
        if guard := await self._pending_assignment_guard(agent_id, briefing):
            return await self._emit_rejection(
                guard, agent_id=agent_id, task_id=None, verb="i_am_idle"
            )
        if guard := await self._pm_unfinished_review_guard(agent_id, briefing):
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
        agent = await self.task.agent_for(agent_id)
        # Board/advisory roles (product_owner, head_marketing, auditor) review
        # and advise without ever claiming — they have no i_will_work_on /
        # i_will_plan verb. Their one-shot board dispatch is meant to leave the
        # coordination task pending for the CEO to reassign to Main PM, so they
        # must be allowed to idle after recording their review. Without this
        # they wedge: the gate would demand a claim verb the role does not have.
        if agent and agent.role in ("product_owner", "head_marketing", "auditor"):
            return None
        first = pending[0]
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

    async def _pm_unfinished_review_guard(
        self, agent_id: UUID, briefing: dict[str, Any]
    ) -> Envelope | None:
        """Refuse i_am_idle when a PM still owns a task awaiting its own review.

        A cell/main PM once tried to "send work back" by DMing the developer and
        going idle — but a DM changes no task state, so the task stayed
        awaiting_pm_review and the PM was just re-dispatched in a loop. A PM that
        owns an awaiting_pm_review task must act on it (complete to finish, or
        reassign/delegate to route it back) before it can idle.
        """
        agent = await self.task.agent_for(agent_id)
        if not agent or agent.role not in ("cell_pm", "main_pm"):
            return None
        assigned = await self.task.list_assigned_for_agent(agent_id)
        review = next(
            (t for t in assigned if str(t.status) == "awaiting_pm_review"), None
        )
        if review is None:
            return None
        return Envelope.invalid_state(
            message=(
                f"you still own task {review.id} awaiting your review; a DM does"
                " not route work, so idling just re-dispatches you."
            ),
            remediate=(
                "complete(task_id) to finish it, or reassign()/delegate() to"
                " send it back — then retry i_am_idle()."
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

        Pre-gateway parity: a synthetic checkpoint is
        written for each paused task so the panel's Checkpoints column reflects
        reality. Checkpoint failure is swallowed; it must never block the pause.
        """
        in_progress = await self.task.list_in_progress_for_agent(agent_id)
        paused_ids: list[str] = []
        for t in in_progress:
            await self.task.pause_for_agent(agent_id, t.id)
            paused_ids.append(str(t.id))
            await self._write_auto_pause_checkpoint(agent_id, t)
        return paused_ids

    async def _write_auto_pause_checkpoint(self, agent_id: UUID, task: Any) -> None:
        """Write a synthetic checkpoint for a task that was auto-paused on i_am_idle.

        Captures state-at-pause so the panel's
        Checkpoints column is never empty after an auto-pause. Agents that
        want an explicit checkpoint before idling can call note(scope='note',
        text='checkpoint: ...') first; this synthetic write covers the bare
        i_am_idle case which is what all current agents do.

        Failure is logged and swallowed — the pause already happened and the
        caller must not be affected by a checkpoint DB error.
        """
        try:
            commits = task.commits or []
            # commits may be hydrated as CommitRef objects or as plain dicts
            # (JSON column round-trip); the identifier field is `hash` (a stray
            # `sha` only ever appears on a gateway return value, never persisted).
            commit_refs = [
                (c.get("hash") or c.get("sha"))
                if isinstance(c, dict)
                else (getattr(c, "hash", None) or getattr(c, "sha", None))
                for c in commits[-3:]
            ]
            commit_refs = [ref for ref in commit_refs if ref]
            commit_count = len(commits)
            state_summary = f"auto-paused on i_am_idle (commits: {commit_count})"
            remaining_work = commit_refs if commit_refs else ["no commits yet"]
            await self.task.add_checkpoint(
                task_id=task.id,
                agent_id=agent_id,
                state_summary=state_summary,
                remaining_work=remaining_work,
            )
        except Exception:
            log = structlog.get_logger(__name__)
            log.warning(
                "auto_pause_checkpoint_failed",
                task_id=str(task.id),
                agent_id=str(agent_id),
            )

    # --- QA verbs moved to ``qa.py``. ---

    # --- Phase 3 (documenter + PM) verbs ---

    # claim_doc_task + i_documented moved to ``doc.py``.

    _RICH_PLAN_FIELDS: ClassVar[tuple[str, ...]] = (
        "approach",
        "sub_tasks",
        "technical_considerations",
        "risks",
        "open_questions",
    )

    @staticmethod
    def _resolve_effective_plan(
        plan: str, rich_plan: dict[str, Any] | None
    ) -> str | dict[str, Any]:
        """Use the panel-shaped dict when any rich field is populated.

        Requires `plan` (narrative paragraph) to be non-empty — the rich
        structure is layered on top, not a replacement. An empty paragraph
        falls through as ``""`` so the spec's plan-required precondition
        emits tracing_gap.
        """
        if not plan or not rich_plan:
            return plan
        if any(rich_plan.get(k) for k in Choreographer._RICH_PLAN_FIELDS):
            return _build_panel_shaped_plan(plan, rich_plan)
        return plan

    async def i_will_plan(
        self,
        pm_agent_id: UUID,
        task_id: UUID,
        plan: str,
        rich_plan: dict[str, Any] | None = None,
    ) -> Envelope:
        """PM mirror of i_will_work_on for parent tasks.

        Atomic: spec.can_invoke_intent runs before any state mutation;
        the composed (claim, set_plan, start) sequence is wrapped in a
        savepoint by the runner so a mid-sequence failure rolls back
        the DB.

        PM callers must supply ``approach`` (>= 20 chars) and a non-empty
        ``sub_tasks`` list inside ``rich_plan`` — these are enforced in
        ``_pm_sub_tasks_gate``. Developer callers may omit ``sub_tasks``
        (their plan is execution-shaped) but still need ``approach`` via
        the HTTP schema layer.

        Control flow: re-entry check → if not re-entry → sub_tasks gate
        → spec gate → claim+plan+start. The re-entry check must come first
        so a respawned PM calling with thin args ("resume", no sub_tasks)
        is short-circuited before the gate can reject them.
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
        role_str = str(agent.role) if agent is not None else "cell_pm"
        briefing = await self._briefing_for(pm_agent_id, task_id, task=t)
        try:
            role = spec_module.Role(role_str)
        except ValueError:
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message=f"unknown role '{role_str}'",
                    remediate="role is not declared in the lifecycle spec",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="i_will_plan",
            )
        effective_plan = self._resolve_effective_plan(plan, rich_plan)
        # spec_ctx carries the resolved (possibly-dict) plan so the
        # verb runner's set_plan handler persists the panel-shaped rich shape.
        # Passing the raw `plan` string here was the bug that left the Plan tab
        # empty: the runner uses spec_ctx, not _ClaimPlanStartContext.
        spec_ctx = spec_module.Context(
            plan=effective_plan,
            actor_id=pm_agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            original_developer_slug=_extract_original_developer(t),
        )
        ctx = _ClaimPlanStartContext(
            agent_id=pm_agent_id,
            task_id=task_id,
            task=t,
            role_str=role_str,
            briefing=briefing,
            plan=effective_plan,
            verb_name="i_will_plan",
        )
        # Re-entry check runs first — a respawned PM with thin args ("resume",
        # no sub_tasks) must short-circuit here before any gate.
        if reentry := await self._handle_pm_reentry(
            ctx, t, pm_agent_id, task_id, role_str, briefing
        ):
            return reentry
        # Lifecycle spec gate runs BEFORE the sub_tasks gate so wrong-state
        # cases (e.g., task in backlog/claimed/completed) return invalid_state
        # — the lifecycle's verdict — instead of being masked by the
        # PM-decomposition check. Parity test
        # `test_lifecycle_consumer_parity.py::test_i_will_plan_matches_spec`
        # asserts this order.
        if rejection := await self._claim_plan_start_gate(ctx, role, spec_ctx):
            return rejection
        # Spec gate passed; now enforce the verb-specific PM-decomposition
        # contract. PMs decompose; their plan MUST include approach + at
        # least one sub_task. Devs execute; sub_tasks may be empty.
        if rejection := await self._pm_sub_tasks_gate(
            role_str=role_str,
            rich_plan=rich_plan,
            task=t,
            agent_id=pm_agent_id,
            task_id=task_id,
            briefing=briefing,
        ):
            return rejection
        envelope = await self._claim_plan_start_run(ctx, agent, spec_ctx)
        return await self._post_claim_journal_gate(
            "i_will_plan", pm_agent_id, task_id, envelope
        )

    async def delegate(
        self,
        pm_agent_id: UUID,
        parent_task_id: UUID,
        inputs: DelegateInputs,
    ) -> Envelope:
        """Create a subtask under parent_task_id with delegation-chain validation.

        Atomic: spec.can_invoke_intent runs first for the role+state gate.
        Delegate-specific gates the spec doesn't model (chain validation,
        assignee-vs-task_type, enum coercion, parent-ownership, subtask
        cap) run after the spec gate. Main PM may delegate to a Cell PM
        slug; a Cell PM may delegate to its own team's developers. The
        atomic ``create_subtask`` action is special — its handler raises
        NotImplementedError because it requires DelegateInputs — so the
        verb body owns the dispatch to ``_create_subtask_from_inputs``.
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
        role_str = str(agent.role) if agent is not None else "cell_pm"
        briefing = await self._briefing_for(pm_agent_id, parent_task_id, task=parent)
        try:
            role = spec_module.Role(role_str)
        except ValueError:
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message=f"unknown role '{role_str}'",
                    remediate="role is not declared in the lifecycle spec",
                    context_briefing=briefing,
                ).with_introspection(task=parent, role=role_str),
                agent_id=pm_agent_id,
                task_id=parent_task_id,
                verb="delegate",
            )
        spec_ctx = spec_module.Context(
            actor_id=pm_agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            original_developer_slug=_extract_original_developer(parent),
        )
        decision = spec_module.can_invoke_intent(role, "delegate", parent, spec_ctx)
        if not decision.allowed:
            return await self._emit_rejection(
                Envelope.from_decision(decision, briefing=briefing).with_introspection(
                    task=parent, role=role_str
                ),
                agent_id=pm_agent_id,
                task_id=parent_task_id,
                verb="delegate",
            )
        # The foundation/policy/task_completeness gate runs BEFORE the
        # static/lifecycle guards. Auto-fill helpers patch unambiguous fields
        # (team-from-slug, priority-from-parent), then `check(TASK_AT_CREATE,
        # ...)` rejects under-filled payloads with `Envelope.incomplete_input`
        # — the spec §5.2.1 interrogation pattern. Defense-in-depth: the
        # service-layer raise still catches non-gateway callers.
        completeness_env = self._delegate_completeness_check(
            inputs, parent, briefing, role_str
        )
        if completeness_env is not None:
            return await self._emit_rejection(
                completeness_env,
                agent_id=pm_agent_id,
                task_id=parent_task_id,
                verb="delegate",
            )
        # Spec gate passed. Run delegate-specific guards the spec doesn't
        # model: tracing (journal:decision), chain validation, enum
        # coercion + assignee-vs-task_type, and parent-ownership/subtask-cap.
        guard = await self._delegate_extra_guards(
            pm_agent_id, parent_task_id, parent, role_str, inputs
        )
        if guard is not None:
            return await self._emit_rejection(
                guard.with_introspection(task=parent, role=role_str),
                agent_id=pm_agent_id,
                task_id=parent_task_id,
                verb="delegate",
            )
        return await self._create_subtask_and_envelope(
            pm_agent_id, parent, inputs, briefing, role_str
        )

    # Gate Set B subtask cap (pre-gateway implicit, made explicit here).
    # Soft warn at 8, hard block at 13. Cap enforced by ``_subtask_cap_guard``.
    _SUBTASK_HARD_CAP: int = 12

    async def _delegate_extra_guards(
        self,
        pm_agent_id: UUID,
        parent_task_id: UUID,
        parent: Any,
        role_str: str,
        inputs: DelegateInputs,
    ) -> Envelope | None:
        """Delegate-specific guards the spec doesn't model.

        Order: tracing (journal:decision per VERB_REQUIREMENTS) ->
        chain validation -> static (project_id, enum coercion,
        assignee-vs-task_type) -> lifecycle (parent ownership + subtask
        cap). Each returns an Envelope rejection or None to allow.

        The role-only check is no longer here — ``spec.can_invoke_intent``
        handles role+state in the verb body before this is called.
        """
        # Pre-gateway PM.md required journal:decision before each delegate.
        if env := await self._check_pm_decision_required(
            "delegate", pm_agent_id, parent_task_id, parent
        ):
            return env
        chain_error = self._validate_delegation_chain(role_str, inputs.assigned_to)
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
        if guard := await self._delegate_static_guards(
            pm_agent_id, parent_task_id, parent, inputs
        ):
            return guard
        # Sibling dedup: catch the PM-decomposition bug where the same
        # parent gets two subtasks for the same role + task_type
        # (observed on smoke run 2026-05-11: Main PM created two planning
        # tasks for be-pm; Cell PM created two code tasks for be-dev-1).
        if guard := await self._delegate_sibling_dedup_guard(parent_task_id, inputs):
            return guard
        # Split-before-claim: reject an egregiously-bundled code leaf so the PM
        # splits it before any dev can claim it.
        if guard := self._delegate_sizing_guard(inputs):
            return guard
        # Gate Set B: PARENT_NOT_CLAIMED + SUBTASK_CAP
        return await self._delegate_lifecycle_guards(
            pm_agent_id, parent_task_id, parent
        )

    _TERMINAL_STATUSES: ClassVar[frozenset[str]] = frozenset({"completed", "cancelled"})
    # Per-parent concurrency cap by spine task_type. `code` is capped at the
    # number of developers in a cell (2) so both can build independent units in
    # parallel; `planning` and `documentation` stay sequential (one at a time).
    _SPINE_TYPE_CAPS: ClassVar[dict[str, int]] = {
        "code": 2,
        "planning": 1,
        "documentation": 1,
    }
    # Split-before-claim sizing thresholds for a `code` leaf (by acceptance-
    # criteria count). Above the nudge count we flag a possible split in the
    # success envelope; above the hard count we reject so the PM splits the
    # bundle before any dev can claim it.
    _SIZING_NUDGE_AC_COUNT: ClassVar[int] = 5
    _SIZING_HARD_AC_COUNT: ClassVar[int] = 8

    @classmethod
    def _code_leaf_ac_count(cls, inputs: DelegateInputs) -> int | None:
        """Acceptance-criteria count for a ``code`` subtask, else None.

        Only ``code`` leaves are sized this way — ``planning`` briefs (main_pm
        → cell_pm) legitimately carry many criteria and are exempt.
        """
        if str(inputs.task_type or "") != "code":
            return None
        return len(inputs.acceptance_criteria or [])

    @classmethod
    def _delegate_sizing_guard(cls, inputs: DelegateInputs) -> Envelope | None:
        """Hard-block an egregiously-bundled code leaf (split-before-claim).

        A ``code`` subtask carrying more than ``_SIZING_HARD_AC_COUNT``
        acceptance criteria bundles too many independent concerns into one leaf:
        QA can't pass a partial, the dev re-touches unrelated parts, and criteria
        get dropped. Reject it so the PM splits the bundle before a dev ever
        claims it. Moderate bundling is allowed but flagged (see
        :meth:`_sizing_hint`).
        """
        ac_count = cls._code_leaf_ac_count(inputs)
        if ac_count is None or ac_count <= cls._SIZING_HARD_AC_COUNT:
            return None
        return Envelope.invalid_state(
            message=(
                f"code subtask bundles {ac_count} acceptance criteria — too many "
                f"independent concerns for one leaf "
                f"(cap {cls._SIZING_HARD_AC_COUNT})."
            ),
            remediate=(
                "Split this into smaller code subtasks before delegating — one "
                "concern each, 2-4 acceptance criteria per subtask. Hand "
                "independent concerns to both cell devs in parallel; sequence "
                "dependent ones. A bundled leaf drives multi-round QA failures "
                "and drops criteria."
            ),
            context_briefing={},
        )

    @classmethod
    def _sizing_hint(cls, inputs: DelegateInputs) -> str | None:
        """Soft nudge for a code leaf in the moderate sizing band.

        Fires above the nudge count and below the hard cap (the hard cap rejects
        outright). Surfaced in the delegate success envelope, never blocking.
        """
        ac_count = cls._code_leaf_ac_count(inputs)
        if ac_count is None or ac_count <= cls._SIZING_NUDGE_AC_COUNT:
            return None
        return (
            f"This code subtask carries {ac_count} acceptance criteria. If they "
            "span more than one independent concern, consider splitting it so "
            "each concern is its own subtask — and hand independents to both "
            "devs in parallel. (Allowed, just flagged.)"
        )

    @staticmethod
    def _spine_type_dup_envelope(
        new_type: str, sibling: Any, sib_assignee: str, cap: int = 1
    ) -> Envelope:
        """Rule-1 rejection: spine-type concurrency cap hit."""
        capacity = (
            f"The {new_type!r} spine is sequential — only one non-terminal at a time."
            if cap == 1
            else (
                f"The {new_type!r} spine is capped at {cap} concurrent "
                f"(one per cell developer) and both are already in flight."
            )
        )
        parallel_hint = (
            "If the work is genuinely parallel (two independent modules), "
            "split this parent into two sibling parents instead of two code "
            "subtasks under one parent.\n\n"
            if cap == 1
            else (
                f"You may run up to {cap} code subtasks at once (one per dev) "
                "when they touch independent files; a further one must wait for "
                "an in-flight sibling to finish.\n\n"
            )
        )
        return Envelope.invalid_state(
            message=(
                f"parent already has {cap} non-terminal "
                f"task_type={new_type!r} subtask(s) "
                f"(e.g. {sibling.id}, assigned_to={sib_assignee!r}, "
                f"status={sibling.status}). {capacity}"
            ),
            remediate=(
                "Drive an existing sibling to completion / cancel it before "
                "delegating another of the same type. "
                + parallel_hint
                + "**DO NOT work around this by delegating again with a "
                "different task_type** (e.g. 'documentation' or "
                "'research' as a 'verification' subtask). The lifecycle "
                "handles QA, documentation, and PM-review automatically "
                "after the code subtask finishes — you do not create "
                "auxiliary subtasks for those roles. Call i_am_idle() "
                "now and wait for an existing child to come back."
            ),
            context_briefing={},
        )

    @staticmethod
    def _same_assignee_dup_envelope(
        new_type: str, new_assignee: str, sibling: Any
    ) -> Envelope:
        """Rule-2 rejection: same assignee already owns same task_type."""
        return Envelope.invalid_state(
            message=(
                f"sibling subtask already assigned to "
                f"{new_assignee!r} with task_type={new_type!r}: "
                f"id={sibling.id} status={sibling.status}"
            ),
            remediate=(
                "Either drive the existing sibling to completion / "
                "cancel it, or split this work into a subtask of "
                "the existing sibling rather than a new sibling."
            ),
            context_briefing={},
        )

    @staticmethod
    def _is_cross_team_planning(new_type: str, new_team: str, sib_team: str) -> bool:
        """Planning subtasks on different teams are NOT
        over-decomposition — main_pm fans planning out to per-cell PMs.
        Both teams must be non-empty so an empty-team escape hatch can't
        bypass the cap defensively.
        """
        return (
            new_type == "planning"
            and bool(new_team)
            and bool(sib_team)
            and new_team != sib_team
        )

    @classmethod
    def _sibling_cap_envelope(
        cls,
        siblings: list[Any],
        new_type: str,
        new_team: str,
        new_assignee: str,
    ) -> Envelope | None:
        """Apply Rule-2 (same-assignee) then Rule-1 (spine concurrency cap).

        Rule 2: a PM never gives one agent two non-terminal subtasks of the
        same type under one parent.

        Rule 1: a parent may hold at most ``_SPINE_TYPE_CAPS[type]`` non-terminal
        subtasks of a spine type. ``code`` is capped at 2 (one per cell dev) so
        both developers can build independent units in parallel; ``planning``
        and ``documentation`` stay at 1. ``planning`` on a different team does
        not count toward the cap — that's main_pm's legitimate cross-cell fanout
        (see :meth:`_is_cross_team_planning`).
        """
        live = [
            s
            for s in siblings
            if str(getattr(s, "status", "")) not in cls._TERMINAL_STATUSES
        ]
        return cls._same_assignee_rejection(
            live, new_type, new_assignee
        ) or cls._spine_cap_rejection(live, new_type, new_team)

    @classmethod
    def _same_assignee_rejection(
        cls, live: list[Any], new_type: str, new_assignee: str
    ) -> Envelope | None:
        """Rule 2: a PM never gives one agent two same-type subtasks per parent."""
        if not new_assignee:
            return None
        for sibling in live:
            if (
                str(getattr(sibling, "assigned_to", "") or "") == new_assignee
                and str(getattr(sibling, "task_type", "")) == new_type
            ):
                return cls._same_assignee_dup_envelope(new_type, new_assignee, sibling)
        return None

    @classmethod
    def _spine_cap_rejection(
        cls, live: list[Any], new_type: str, new_team: str
    ) -> Envelope | None:
        """Rule 1: at most ``_SPINE_TYPE_CAPS[type]`` non-terminal same-type."""
        cap = cls._SPINE_TYPE_CAPS.get(new_type)
        if cap is None:
            return None
        same_spine = [
            s
            for s in live
            if str(getattr(s, "task_type", "")) == new_type
            and not cls._is_cross_team_planning(
                new_type, new_team, str(getattr(s, "team", "") or "")
            )
        ]
        if len(same_spine) < cap:
            return None
        blocker = same_spine[0]
        return cls._spine_type_dup_envelope(
            new_type, blocker, str(getattr(blocker, "assigned_to", "") or ""), cap=cap
        )

    async def _delegate_sibling_dedup_guard(
        self,
        parent_task_id: UUID,
        inputs: DelegateInputs,
    ) -> Envelope | None:
        """Block PM-decomposition over-spread (the smoke-run runaway pattern).

        Two rules, both rooted in bounded per-parent concurrency:

        1. **Same-type concurrency cap**: a parent may hold at most
           ``_SPINE_TYPE_CAPS[type]`` non-terminal subtasks of a spine type —
           ``code`` is capped at 2 (one per cell developer, so both build
           independent units in parallel); ``planning`` and ``documentation``
           stay at 1. Exception: ``planning`` subtasks on different teams do
           not count toward the cap — that's main_pm's legitimate cross-cell
           fanout.
        2. **Same-assignee same-type** (fallback): a PM never delegates two
           subtasks of the same type to the same agent under the same parent
           (so the two parallel ``code`` slots always go to different devs).

        Both rules surface an existing sibling id so the PM can finish
        or cancel it instead of guessing.
        """
        siblings = await self.task.get_subtasks(parent_task_id)
        return self._sibling_cap_envelope(
            list(siblings),
            str(inputs.task_type or ""),
            str(inputs.team or ""),
            str(inputs.assigned_to or ""),
        )

    async def _delegate_static_guards(
        self,
        pm_agent_id: UUID,
        parent_task_id: UUID,
        parent: Any,
        inputs: DelegateInputs,
    ) -> Envelope | None:
        """project_id / enum guards. Pure data-shape checks.

        The slug-validity check used to live here, but
        `_validate_delegation_chain` runs first (in `_delegate_extra_guards`)
        and rejects any slug outside the allowed delegation targets —
        which is a strict subset of `AGENT_UUIDS` — so any AGENT_UUIDS
        check here was unreachable.
        """
        if parent.project_id is None and getattr(parent, "product_id", None) is None:
            return Envelope.invalid_state(
                message="parent task has neither a project_id nor a product_id",
                remediate=(
                    "the parent must have a project (single repo) or a product "
                    "(cell->project map) so subtasks can resolve a repo"
                ),
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
        if type_error := self._validate_assignee_task_type(
            inputs.assigned_to, inputs.task_type
        ):
            return Envelope.invalid_state(
                message=type_error,
                remediate=self._assignee_task_type_remediate(inputs.assigned_to),
                context_briefing=await self._briefing_for(pm_agent_id, parent_task_id),
            )
        if str(inputs.task_type) == "documentation":
            # The lifecycle auto-handles documentation. After a
            # `code` subtask passes QA it transitions to
            # awaiting_documentation and a *documenter* is spawned
            # automatically. A PM-created `documentation` subtask assigned
            # to a developer can never be spawned (dev-dispatch rejects
            # role/task_type mismatch) — it becomes a permanent orphan
            # that deadlocks submit_up (all subtasks must be terminal).
            # The spine-cap is per-type so this slips past the
            # code-vs-code dedup; reject it explicitly here.
            return Envelope.invalid_state(
                message=(
                    "task_type='documentation' subtasks are not PM-"
                    "delegatable: the lifecycle creates the documentation "
                    "phase automatically after the code subtask passes QA."
                ),
                remediate=(
                    "Delegate ONLY the code subtask (task_type='code'). "
                    "Once it passes QA the gateway transitions it to "
                    "awaiting_documentation and spawns a documenter for "
                    "you — do NOT create a separate documentation subtask "
                    "or assign docs to a developer. Re-issue delegate(...) "
                    "with task_type='code' and i_am_idle when done."
                ),
                context_briefing=await self._briefing_for(pm_agent_id, parent_task_id),
            )
        return None

    _CELL_PM_SLUGS: ClassVar[frozenset[str]] = frozenset({"be-pm", "fe-pm", "ux-pm"})

    @staticmethod
    def _validate_assignee_task_type(assigned_to: str, task_type: str) -> str | None:
        """Reject role-vs-type misclassifications.

        Rules:
        - delegating to a Cell PM requires
          ``task_type='planning'``. Cell PMs decompose; they don't execute.
        - (2026-05-11 smoke): delegating to a Developer requires
          ``task_type in {'code', 'documentation', 'research'}``. Devs
          implement. Planning/design/administrative belong to PMs/board.
          The 'research' allowance covers genuine spike work (try a
          library, prototype an approach) — NOT coordination/handoff,
          which is PM work.
        - (#7): the UX/UI cell's developers ARE its designers — for a
          DEVELOPER on ``Team.UX_UI`` ``task_type='design'`` is legitimate
          cell work (mockups, specs, design assets committed to the repo),
          so ux-dev-1/ux-dev-2 also accept ``design``. Backend/frontend
          devs still cannot be handed ``design`` — that routing belongs to
          the UX cell. The orchestrator already dispatches a developer for a
          ``design`` task (``_dev_dispatch_role_matches`` returns True), so
          this does not create the orphan that blocks ``documentation``.
        - Delegating to a QA requires ``task_type='code'`` (their work is
          to review PRs of code changes).
        - Delegating to a Documenter requires ``task_type='documentation'``.
        """
        from roboco.foundation.identity import AGENTS, Role, Team

        if assigned_to in Choreographer._CELL_PM_SLUGS and task_type != "planning":
            return (
                f"task_type={task_type!r} is invalid for assignee {assigned_to!r}: "
                f"Cell PMs own planning tasks, not code/documentation/etc."
            )
        agent = AGENTS.get(assigned_to)
        if agent is None:
            return None
        if agent.role is Role.DEVELOPER:
            dev_err = Choreographer._developer_task_type_error(
                assigned_to, agent.team is Team.UX_UI, task_type
            )
            if dev_err is not None:
                return dev_err
        if agent.role is Role.QA and task_type != "code":
            return (
                f"task_type={task_type!r} is invalid for assignee {assigned_to!r}: "
                f"QA reviews code PRs — task_type must be 'code'."
            )
        if agent.role is Role.DOCUMENTER and task_type != "documentation":
            return (
                f"task_type={task_type!r} is invalid for assignee {assigned_to!r}: "
                f"Documenters write documentation — task_type must be "
                f"'documentation'."
            )
        return None

    @staticmethod
    def _developer_task_type_error(
        assigned_to: str, is_ux_dev: bool, task_type: str
    ) -> str | None:
        """Reject a developer's task_type. UX-cell developers additionally
        accept 'design' (mockups/specs/design assets are their cell work)."""
        allowed = {"code", "documentation", "research"}
        owned = "code/documentation/research"
        design_clause = "; design belongs to the UX cell."
        if is_ux_dev:
            allowed.add("design")
            owned = "code/documentation/research/design"
            design_clause = "."
        if task_type in allowed:
            return None
        return (
            f"task_type={task_type!r} is invalid for assignee "
            f"{assigned_to!r}: Developers own {owned}. Coordination, "
            f"planning, and administrative work belong to PMs{design_clause}"
        )

    @staticmethod
    def _assignee_task_type_remediate(assigned_to: str) -> str:
        """Per-assignee-class remediation for an assignee-vs-task_type reject.

        The reject `message` is already case-specific; this gives the PM the
        right *next call* for the kind of assignee it just mis-typed instead
        of a one-size-fits-all 'pass planning to a Cell PM' hint.
        """
        from roboco.foundation.identity import AGENTS, Role, Team

        if assigned_to in Choreographer._CELL_PM_SLUGS:
            return (
                "Cell PMs (be-pm/fe-pm/ux-pm) own PLANNING tasks — they "
                "decompose the slice and delegate code work to devs. Pass "
                "task_type='planning' when delegating to a Cell PM."
            )
        agent = AGENTS.get(assigned_to)
        if agent is not None and agent.role is Role.DEVELOPER:
            if agent.team is Team.UX_UI:
                return (
                    "UX developers take task_type in "
                    "{'code','documentation','research','design'}. Use "
                    "'design' for mockups/specs/design-asset work, 'code' "
                    "for implementation."
                )
            return (
                "Developers take task_type in "
                "{'code','documentation','research'}. Use 'code' for "
                "implementation; planning/administrative work stays with you "
                "(the PM), and design work is routed to the UX cell."
            )
        return (
            "Match task_type to the assignee's role: 'code' for devs/QA, "
            "'documentation' for documenters, 'planning' for Cell PMs."
        )

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

    def _delegate_completeness_check(
        self,
        inputs: DelegateInputs,
        parent: Any,
        briefing: dict[str, Any],
        role_str: str,
    ) -> Envelope | None:
        """The foundation/policy/task_completeness gate for delegate.

        Auto-fills unambiguous fields (team-from-slug, priority-from-parent)
        without overwriting explicit values, then runs `check(TASK_AT_CREATE,
        ...)` against the payload. Returns:

        - ``None`` when every TASK_AT_CREATE requirement is satisfied (the
          verb continues into the static/lifecycle guards).
        - ``Envelope.incomplete_input`` (with `with_introspection` applied)
          when any field is missing — the agent gets a structured
          field-by-field guide (spec §5.2.1 interrogation pattern).

        The `acceptance_criteria=inputs.acceptance_criteria or []` collapse
        at `_create_subtask_from_inputs` was removed alongside this gate,
        so under-filled payloads now hit the service-layer raise
        instead of being silently substituted. This method is the
        gateway-side defense; the service raise is defense-in-depth for
        non-gateway callers.
        """
        from types import SimpleNamespace

        from roboco.foundation.policy import task_completeness as tc

        payload: dict[str, Any] = {
            "title": inputs.title,
            "description": inputs.description,
            "assigned_to": inputs.assigned_to,
            "team": inputs.team,
            "task_type": inputs.task_type,
            "nature": inputs.nature,
            "estimated_complexity": inputs.estimated_complexity,
            "acceptance_criteria": inputs.acceptance_criteria,
        }
        # Auto-fill (spec §5.2.1 (a)) — never overwrites explicit values.
        # team-from-slug is harmless when the caller already supplied team;
        # priority-from-parent records `__priority_inherited=True` for
        # post-create journal:note (best-effort observability).
        payload = tc.fill_team_from_assignee(payload)
        payload = tc.fill_priority_from_parent(payload, parent)
        completeness_input = SimpleNamespace(
            **{k: v for k, v in payload.items() if not k.startswith("__")}
        )
        result = tc.check(tc.TASK_AT_CREATE, completeness_input)
        if result.passed:
            return None
        return Envelope.incomplete_input(
            missing=result.missing,
            field_hints=result.field_hints,
            remediate=(
                "re-issue delegate(...) with these fields filled: "
                f"{', '.join(result.missing)}. Each field's required shape "
                "is in `field_hints`."
            ),
            context_briefing=briefing,
        ).with_introspection(task=parent, role=role_str)

    async def _create_subtask_and_envelope(
        self,
        pm_agent_id: UUID,
        parent: Any,
        inputs: DelegateInputs,
        briefing: dict[str, Any],
        role_str: str,
    ) -> Envelope:
        """Run subtask creation and translate completeness raises into envelopes.

        The defensive raises inside `_create_subtask_from_inputs`
        catch under-filled payloads that slipped past the gateway gate. Without
        this translator they surface as Starlette 500s — which means the agent
        never sees `field_hints`, retries indefinitely, and looks like a
        runaway. Converting to `Envelope.incomplete_input` here closes that
        loop so the agent gets the same interrogation-pattern reply it would
        have gotten from the upfront completeness check.
        """
        from roboco.foundation.policy.task_completeness import TaskCompletenessError

        parent_task_id = parent.id
        try:
            new_task = await self._create_subtask_from_inputs(
                pm_agent_id, parent_task_id, parent, inputs
            )
        except TaskCompletenessError as exc:
            return await self._emit_rejection(
                Envelope.incomplete_input(
                    missing=exc.missing,
                    field_hints=exc.field_hints,
                    remediate=(
                        "re-issue delegate(...) with corrected fields: "
                        f"{', '.join(exc.missing)}. Each field's required "
                        "shape is in `field_hints`."
                    ),
                    context_briefing=briefing,
                ).with_introspection(task=parent, role=role_str),
                agent_id=pm_agent_id,
                task_id=parent_task_id,
                verb="delegate",
            )
        await self._wire_ux_frontend_dependency(new_task, parent)
        hint = self._sizing_hint(inputs)
        if hint is not None:
            briefing = {**briefing, "sizing_hint": hint}
        return Envelope.ok(
            status="created",
            task_id=str(new_task.id),
            next=spec_module._INTENT_VERBS["delegate"].next_hint(new_task),
            context_briefing=briefing,
        ).with_introspection(task=new_task, role=role_str)

    @staticmethod
    def _team_value(value: Any) -> str:
        return value.value if hasattr(value, "value") else str(value)

    async def _wire_ux_frontend_dependency(self, new_task: Any, parent: Any) -> None:
        """Cross-cell sequencing: in a product fan-out the implementation cells
        (FRONTEND and BACKEND) depend on the UX/UI cell task — UX design defines
        the screens and API contracts both cells build against, so it is upstream
        of implementation. Wires the dependency in either delegation order. A
        dev/code subtask delegated under a cell task that is itself still waiting
        on that dependency inherits it, so the developer is held until UX is done
        instead of coding ahead of the design. Best-effort: never breaks delegate.
        """
        if parent is None or getattr(parent, "product_id", None) is None:
            return
        from roboco.foundation.identity import Team

        nt_team = self._team_value(new_task.team)
        try:
            # A subtask of a cell task that is waiting on another cell must wait
            # too — propagate the parent's unmet cross-cell dependencies down.
            await self.task.inherit_unmet_dependencies(new_task.id, parent.id)
            if nt_team == Team.FRONTEND.value:
                await self._depend_frontend_on_ux(new_task, parent.id)
            elif nt_team == Team.BACKEND.value:
                await self._depend_backend_on_ux(new_task, parent.id)
            elif nt_team == Team.UX_UI.value:
                await self._depend_pending_frontends_on_ux(new_task, parent.id)
                await self._depend_pending_backends_on_ux(new_task, parent.id)
        except Exception as exc:
            logger.warning(
                "cross-cell UX->implementation sequencing wiring failed",
                error=str(exc),
                parent_task_id=str(getattr(parent, "id", None)),
            )

    async def _depend_frontend_on_ux(self, fe_task: Any, parent_id: Any) -> None:
        """Make a new FRONTEND cell task wait on its non-terminal UX/UI sibling."""
        from roboco.foundation.identity import Team
        from roboco.models.base import TaskStatus

        terminal = {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
        siblings = await self.task.get_subtasks(parent_id)
        ux = next(
            (
                s
                for s in siblings
                if self._team_value(s.team) == Team.UX_UI.value
                and s.id != fe_task.id
                and s.status not in terminal
            ),
            None,
        )
        if ux is not None:
            await self.task.add_dependency(fe_task.id, ux.id)
            await self.task.set_sequence(
                fe_task.id, (getattr(ux, "sequence", 0) or 0) + 1
            )

    async def _depend_backend_on_ux(self, be_task: Any, parent_id: Any) -> None:
        """Make a new BACKEND cell task wait on its non-terminal UX/UI sibling."""
        from roboco.foundation.identity import Team
        from roboco.models.base import TaskStatus

        terminal = {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
        siblings = await self.task.get_subtasks(parent_id)
        ux = next(
            (
                s
                for s in siblings
                if self._team_value(s.team) == Team.UX_UI.value
                and s.id != be_task.id
                and s.status not in terminal
            ),
            None,
        )
        if ux is not None:
            await self.task.add_dependency(be_task.id, ux.id)
            await self.task.set_sequence(
                be_task.id, (getattr(ux, "sequence", 0) or 0) + 1
            )

    async def _depend_pending_frontends_on_ux(
        self, ux_task: Any, parent_id: Any
    ) -> None:
        """Retro-wire not-yet-started FRONTEND siblings onto a new UX/UI task."""
        from roboco.foundation.identity import Team
        from roboco.models.base import TaskStatus

        not_started = {TaskStatus.BACKLOG, TaskStatus.PENDING}
        ux_sequence = (getattr(ux_task, "sequence", 0) or 0) + 1
        siblings = await self.task.get_subtasks(parent_id)
        for fe in siblings:
            if (
                self._team_value(fe.team) == Team.FRONTEND.value
                and fe.id != ux_task.id
                and fe.status in not_started
            ):
                await self.task.add_dependency(fe.id, ux_task.id)
                await self.task.set_sequence(fe.id, ux_sequence)

    async def _depend_pending_backends_on_ux(
        self, ux_task: Any, parent_id: Any
    ) -> None:
        """Retro-wire not-yet-started BACKEND siblings onto a new UX/UI task."""
        from roboco.foundation.identity import Team
        from roboco.models.base import TaskStatus

        not_started = {TaskStatus.BACKLOG, TaskStatus.PENDING}
        ux_sequence = (getattr(ux_task, "sequence", 0) or 0) + 1
        siblings = await self.task.get_subtasks(parent_id)
        for be in siblings:
            if (
                self._team_value(be.team) == Team.BACKEND.value
                and be.id != ux_task.id
                and be.status in not_started
            ):
                await self.task.add_dependency(be.id, ux_task.id)
                await self.task.set_sequence(be.id, ux_sequence)

    async def _resolve_subtask_project(
        self, parent: Any, inputs: DelegateInputs
    ) -> UUID:
        """Resolve the project a delegated subtask lands in.

        Priority: explicit inputs.project_id -> the parent's Product map for
        this cell -> the parent's own project. Raises TaskCompletenessError only
        for a fan-out parent (product, no own project) whose product has no
        mapping for this cell — i.e. the subtask would have no repo to land in.
        """
        if inputs.project_id is not None:
            return inputs.project_id
        parent_product_id = getattr(parent, "product_id", None)
        if self.product is not None and parent_product_id is not None:
            mapped = await self.product.project_for(parent_product_id, inputs.team)
            if mapped is not None:
                return UUID(str(mapped))
        if parent.project_id is not None:
            return UUID(str(parent.project_id))
        from roboco.foundation.policy.task_completeness import TaskCompletenessError

        raise TaskCompletenessError(
            missing=["project_id"],
            field_hints={
                "project_id": (
                    f"no project for team {inputs.team!r}: add a "
                    f"{inputs.team}->project mapping to the parent's product, or "
                    "pass an explicit project_id on delegate"
                )
            },
            message=f"cannot resolve a project for the {inputs.team} subtask",
        )

    async def _create_subtask_from_inputs(
        self,
        pm_agent_id: UUID,
        parent_task_id: UUID,
        parent: Any,
        inputs: DelegateInputs,
    ) -> Any:
        """Resolve enums + AGENT_UUIDS slug and call TaskService.create_subtask.

        By contract, callers (the `delegate` verb body) MUST run
        `_delegate_completeness_check` first, so `inputs.acceptance_criteria`
        and `inputs.nature` are guaranteed non-None / non-empty here. The
        defensive `TaskCompletenessError` raises preserve correctness if
        a future caller bypasses the gateway path — defense-in-depth in
        line with the service-layer raise.
        """
        from roboco.foundation.policy.task_completeness import TaskCompletenessError
        from roboco.models.base import TaskNature
        from roboco.models.task import TaskCreateRequest
        from roboco.seeds.initial_data import AGENT_UUIDS

        team_enum, type_enum, complexity_enum = self._resolve_delegate_enums(inputs)
        assignee_id = UUID(AGENT_UUIDS[inputs.assigned_to])
        # The `or []` collapse was removed. The gateway runs
        # `_delegate_completeness_check` BEFORE this helper, so empty/None
        # acceptance_criteria here means a non-gateway caller bypassed the
        # check. Raise so the service-layer raise can attach the
        # field hints — never silently substitute.
        if not inputs.acceptance_criteria:
            raise TaskCompletenessError(
                missing=["acceptance_criteria"],
                field_hints={
                    "acceptance_criteria": (
                        "non-empty list[str]; each item describes a verifiable outcome"
                    )
                },
                message=(
                    "_create_subtask_from_inputs called with empty "
                    "acceptance_criteria — completeness check must run first"
                ),
            )
        if inputs.nature is None:
            raise TaskCompletenessError(
                missing=["nature"],
                field_hints={
                    "nature": "one of: technical | non_technical",
                },
                message=(
                    "_create_subtask_from_inputs called with no nature — "
                    "completeness check must run first"
                ),
            )
        try:
            nature_enum = TaskNature(inputs.nature)
        except ValueError as exc:
            raise TaskCompletenessError(
                missing=["nature"],
                field_hints={"nature": "one of: technical | non_technical"},
                message=f"invalid nature {inputs.nature!r}: {exc}",
            ) from exc
        resolved_project_id = await self._resolve_subtask_project(parent, inputs)
        req = TaskCreateRequest(
            title=inputs.title,
            description=inputs.description,
            acceptance_criteria=inputs.acceptance_criteria,
            team=team_enum,
            created_by=pm_agent_id,
            project_id=resolved_project_id,
            product_id=getattr(parent, "product_id", None),
            parent_task_id=parent_task_id,
            assigned_to=assignee_id,
            task_type=type_enum,
            nature=nature_enum,
            estimated_complexity=complexity_enum,
        )
        new_task = await self.task.create_subtask(req)
        # Assign a distinct ordinal within the parent's siblings so the merge
        # order is deterministic. Within-cell siblings were all left at the
        # default sequence 0 — which is why two leaf PRs raced into the same
        # cell branch and the second wedged. Each new sibling takes the next
        # ordinal (the count of pre-existing siblings).
        siblings = await self.task.get_subtasks(parent_task_id)
        next_seq = len([s for s in siblings if s.id != new_task.id])
        await self.task.set_sequence(new_task.id, next_seq)
        # Thread the parent's existing session links onto the
        # new subtask so the assigned agent (dev/qa/doc) lands in the
        # group chat the PM has already been talking in. Pre-gateway
        # parity — sessions were wired to the whole tree at creation
        # time; the gateway path created subtasks one-by-one and forgot
        # this step. Idempotent on re-runs; no-op when no parent session.
        if self.messaging is not None:
            await self.messaging.propagate_sessions_to_subtask(
                parent_task_id=parent_task_id,
                subtask_id=new_task.id,
                added_by=pm_agent_id,
            )
        return new_task

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

        Spec gate runs first and enforces role membership (cell_pm only)
        plus the composed ``submit_pm_review`` action's source-status
        constraint (IN_PROGRESS only). After the gate accepts, the
        verb-specific ``_submit_up_guard`` runs the rest of the
        preflight checks the spec doesn't model: ownership, notes
        length, journal:decision presence, subtasks-terminal, branch
        present. Then ``VerbRunner.run_intent("submit_up", ...)``
        dispatches the (submit_pm_review,) atomic chain plus the
        (create_pr,) side effect inside a savepoint. After the runner
        returns, the task is handed off to the Main PM (reassign + a2a).
        """
        t = await self.task.get(task_id)
        briefing = await self._briefing_for(pm_agent_id, task_id, task=t)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="submit_up",
            )
        agent = await self.task.agent_for(pm_agent_id)
        role_str = str(agent.role) if agent is not None else "cell_pm"
        try:
            role = spec_module.Role(role_str)
        except ValueError:
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message=f"unknown role '{role_str}'",
                    remediate="role is not declared in the lifecycle spec",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="submit_up",
            )
        spec_ctx = spec_module.Context(
            actor_id=pm_agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            original_developer_slug=_extract_original_developer(t),
            notes=notes,
        )
        decision = spec_module.can_invoke_intent(role, "submit_up", t, spec_ctx)
        if not decision.allowed:
            return await self._emit_rejection(
                Envelope.from_decision(decision, briefing=briefing).with_introspection(
                    task=t, role=role_str
                ),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="submit_up",
            )

        # Verb-specific preflight: ownership + notes-length + journal:decision
        # + subtasks-terminal + branch-present. None of these are modelled by
        # the spec yet — keep them in the verb body.
        guard = await self._submit_up_guard(pm_agent_id, task_id, t, notes)
        if guard is not None:
            guard.with_introspection(task=t, role=role_str)
            return await self._emit_rejection(
                guard,
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="submit_up",
            )

        outcome = await self._submit_up_run_intent(
            t, agent, spec_ctx, briefing, role_str
        )
        if isinstance(outcome, Envelope):
            return await self._emit_rejection(
                outcome,
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="submit_up",
            )
        t = outcome
        # Do NOT hand the cell task to Main PM. The cell PM owns cell
        # completion — it stays assigned to the cell PM, which is respawned to
        # `complete` the task (merging the cell→root PR). Main PM only
        # completes the ROOT (root→master + escalate-to-CEO).
        # `_maybe_advance_parent_to_pm_review` already keeps it on the cell PM.
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS["submit_up"].next_hint(t),
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)

    async def _submit_up_run_intent(
        self,
        t: Any,
        agent: Any,
        spec_ctx: spec_module.Context,
        briefing: dict[str, Any],
        role_str: str,
    ) -> Any:
        """Dispatch the submit_up composition through VerbRunner.

        Returns the post-composition task on success, or an
        ``invalid_state`` Envelope when the runner raises or the
        underlying ``submit_pm_review`` returns ``None``.
        """
        runner = self._verb_runner()
        try:
            after = await runner.run_intent("submit_up", t, agent, spec_ctx)
        except Exception as exc:
            return Envelope.invalid_state(
                message=f"verb runner failed: {exc}",
                remediate="check workspace + retry; if persistent, escalate",
                context_briefing=briefing,
            ).with_introspection(task=t, role=role_str)
        if after is None:
            return Envelope.invalid_state(
                message="could not transition to awaiting_pm_review",
                remediate="check task state — must be in_progress with PR ready",
                context_briefing=briefing,
            )
        return after

    async def _submit_up_guard(
        self, pm_agent_id: UUID, task_id: UUID, t: Any, notes: str
    ) -> Envelope | None:
        """Return a rejection Envelope if any submit_up precondition fails."""
        ownership = await self._submit_up_ownership_guard(
            pm_agent_id, task_id, t, notes
        )
        if ownership is not None:
            return ownership
        return await self._submit_up_state_guard(pm_agent_id, task_id, t, notes)

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
        self, pm_agent_id: UUID, task_id: UUID, t: Any, notes: str
    ) -> Envelope | None:
        """Journal + subtask-closure + branch guards for submit_up.

        Tracing gates (journal:decision, journal:reflect, notes>=min,
        subtasks_terminal) are evaluated by ``_check_submit_up_gates``
        which consumes ``VERB_REQUIREMENTS["submit_up"]``. The
        ``_subtasks_not_terminal_envelope`` call is kept as a fallback
        because its remediation enumerates the non-terminal subtask ids
        — strictly richer than the foundation hint.
        """
        if env := await self._check_submit_up_gates(pm_agent_id, task_id, notes):
            return env
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

    async def pm_give_me_work(self, pm_agent_id: UUID) -> Envelope:
        """Return the PM's first assigned task in any active status, or idle.

        Mirrors the developer's give_me_work but does not filter to dev-only
        statuses — PMs care about all assigned tasks (planning, paused, in
        progress, awaiting_pm_review).

        Pre-assigned pending tasks are checked first.
        Smoke run 3 showed Main PM getting idle even though c7935d2c was
        pending and assigned_to=main-pm because list_assigned_for_agent
        ordered by priority/updated_at and could rank a pre-assigned pending
        task below other active rows; the pre-assigned pending check now
        wins unconditionally.
        """
        # Pre-assigned pending tasks take priority over everything else.
        pre_assigned = await self.task.list_pending_for_agent(pm_agent_id)
        if pre_assigned:
            t = pre_assigned[0]
            await self._touch(t.id)
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=self._pm_next_hint(str(t.status), t.id),
                context_briefing=await self._briefing_for(pm_agent_id, t.id, task=t),
            )
        assigned = await self.task.list_assigned_for_agent(pm_agent_id)
        if assigned:
            t = assigned[0]
            await self._touch(t.id)
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=self._pm_next_hint(str(t.status), t.id),
                context_briefing=await self._briefing_for(pm_agent_id, t.id, task=t),
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
                context_briefing=await self._briefing_for(pm_agent_id, t.id, task=t),
            )
        awaiting = await self.task.list_awaiting_pm_review_for_team(pm.team)
        if awaiting:
            t = awaiting[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=f"review and complete(task_id='{t.id}')",
                context_briefing=await self._briefing_for(pm_agent_id, t.id, task=t),
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
                context_briefing=await self._briefing_for(pm_agent_id, t.id, task=t),
            )
        awaiting = await self.task.list_awaiting_main_pm_all()
        if awaiting:
            t = awaiting[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=f"complete(task_id='{t.id}') opens master PR + escalates to CEO",
                context_briefing=await self._briefing_for(pm_agent_id, t.id, task=t),
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

        # A dependency block must not be cleared by hand. It auto-clears via
        # _unblock_dependents the moment its last dependency reaches a terminal
        # state; forcing it now would let the dependent proceed without the
        # upstream's work (e.g. a frontend task built before its UX design lands).
        dep_ids = list(t.dependency_ids or [])
        unmet = await self.task.unmet_dependency_ids(dep_ids) if dep_ids else []
        if unmet:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=(
                        f"task {task_id} still depends on {len(unmet)} "
                        "unfinished task(s); a dependency block clears on its "
                        "own once the upstream work completes"
                    ),
                    remediate=(
                        "don't force this — let the dependency finish; the task "
                        "auto-unblocks the moment its last dependency reaches "
                        "completed/cancelled"
                    ),
                    context_briefing=await self._briefing_for(pm_agent_id, task_id),
                ).with_introspection(task=t, role=role),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="unblock",
            )

        if env := await self._check_pm_decision_required(
            "unblock", pm_agent_id, task_id, t
        ):
            return await self._emit_rejection(
                env.with_introspection(task=t, role=role),
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

    async def _own_review_hint(self, pm_agent_id: UUID, exclude_task_id: UUID) -> str:
        """Remediate suffix naming the PM's OWN task ready to complete.

        A PM looped firing complete/unblock at the
        wrong (parent) task_id while its own leaf sat at
        ``awaiting_pm_review``, never named in any rejection — minimax
        never found the one correct call. Surface it explicitly.
        Best-effort: never raises into the rejection path.
        """
        try:
            owned = await self.task.list_by_assignee(pm_agent_id)
        except Exception:
            return ""
        ready = [
            str(o.id)
            for o in owned
            if str(o.status) == "awaiting_pm_review" and o.id != exclude_task_id
        ]
        if not ready:
            return ""
        tid = ready[0]
        return (
            f" You OWN task {tid} which is awaiting_pm_review and ready to "
            f"finish — call complete(task_id='{tid}', notes='...') on THAT "
            "task, not this one."
        )

    async def _cell_pm_complete_guard(
        self, pm_agent_id: UUID, task_id: UUID, t: Any, notes: str
    ) -> Envelope | None:
        """Return a rejection Envelope if pre-merge guards fail; else None.

        Tracing gates (journal:decision, journal:reflect, notes>=min) are
        evaluated by ``_check_complete_gates`` which consumes
        ``VERB_REQUIREMENTS["complete"]``. The
        ``_subtasks_not_terminal_envelope`` call is kept inline because
        its remediation enumerates the non-terminal subtask ids — strictly
        richer than the foundation hint.
        """
        if t.assigned_to != pm_agent_id:
            return Envelope.not_authorized(
                message="not assigned to you",
                remediate=(
                    "claim the task or wait for it to be assigned."
                    + await self._own_review_hint(pm_agent_id, task_id)
                ),
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )
        if str(t.status) != "awaiting_pm_review":
            return Envelope.invalid_state(
                message=(
                    f"task {task_id} is in {t.status}, expected awaiting_pm_review"
                ),
                remediate=(
                    "this task is not ready for completion."
                    + await self._own_review_hint(pm_agent_id, task_id)
                ),
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )
        if env := await self._check_complete_gates(pm_agent_id, task_id, notes):
            return env
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
        guard = await self._cell_pm_complete_guard(pm_agent_id, task_id, t, notes)
        if guard is not None:
            guard.with_introspection(task=t, role="cell_pm")
            return await self._emit_rejection(
                guard,
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="cell_pm_complete",
            )
        # Resolve the merge target from the PARENT task's real
        # branch_name. For a leaf this is the cell branch (same team — no
        # change); for a cell task it is the root branch (feature/main_pm/…),
        # which parent_branch_for would have mis-derived as feature/<cellteam>/…
        target = await resolve_parent_branch(t, self.task)
        try:
            merge_result = await self.git.pr_merge(
                t.pr_number, target=target, actor_agent_id=pm_agent_id
            )
        except MergeConflictError as exc:
            # A sibling landed overlapping work first, so this PR can't merge.
            # Resolve it (rebase / close-superseded / escalate) instead of
            # letting the failure re-block the task and respawn the PM forever.
            return await self._resolve_merge_conflict_on_complete(
                pm_agent_id, task_id, t, target, notes, exc
            )
        return await self._finalize_cell_complete(
            pm_agent_id, task_id, t, notes, merge_result.get("merge_commit_sha")
        )

    async def _finalize_cell_complete(
        self,
        pm_agent_id: UUID,
        task_id: UUID,
        t: Any,
        notes: str,
        merge_commit: str | None,
    ) -> Envelope:
        """Mark the leaf completed and propagate the completion to its parent."""
        leaf_parent_id = t.parent_task_id
        leaf_team = t.team
        t = await self.task.cell_pm_complete(
            pm_agent_id,
            task_id,
            notes,
            merge_commit=merge_commit,
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
            next=spec_module._INTENT_VERBS["complete"].next_hint(t),
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        ).with_introspection(task=t, role="cell_pm")

    async def _resolve_merge_conflict_on_complete(
        self,
        pm_agent_id: UUID,
        task_id: UUID,
        t: Any,
        target: str,
        notes: str,
        exc: MergeConflictError,
    ) -> Envelope:
        """Resolve a leaf PR that couldn't merge because a sibling landed first.

        Rebase the branch onto the current base and act on the outcome rather
        than failing (which re-blocks the task and respawns the PM forever):

        - ``rebased``     — the branch now integrates cleanly; retry the merge.
        - ``superseded``  — every change is already in the base via the sibling;
          close the dead PR and complete the task without a redundant merge.
        - ``conflicts`` / ``unknown`` — a human must resolve; escalate to the
          CEO (``awaiting_ceo_approval``) so the task leaves the agent loop.
        """
        rebase = await self.git.rebase_pr_for_task(
            t.pr_number, actor_agent_id=pm_agent_id
        )
        status = rebase.get("status")
        if status == "rebased":
            merge_result = await self.git.pr_merge(
                t.pr_number, target=target, actor_agent_id=pm_agent_id
            )
            return await self._finalize_cell_complete(
                pm_agent_id, task_id, t, notes, merge_result.get("merge_commit_sha")
            )
        if status == "superseded":
            await self.git.close_pull_request(
                t.pr_number,
                comment=(
                    "Closed as superseded: every change on this branch is "
                    "already present in the base via a sibling PR that merged "
                    "first. Completing the task without a redundant merge."
                ),
                actor_agent_id=pm_agent_id,
            )
            return await self._finalize_cell_complete(
                pm_agent_id, task_id, t, notes, None
            )
        return await self._escalate_merge_conflict_to_ceo(
            pm_agent_id, task_id, t, rebase, exc
        )

    async def _escalate_merge_conflict_to_ceo(
        self,
        pm_agent_id: UUID,
        task_id: UUID,
        t: Any,
        rebase: dict[str, Any],
        exc: MergeConflictError,
    ) -> Envelope:
        """Route an unresolvable PR conflict to the CEO; never loop on it.

        Moves the task to ``awaiting_ceo_approval`` (admin override — the leaf
        has no in-band edge there) so it leaves agent dispatch, and best-effort
        alerts the CEO with the conflicting files.
        """
        from roboco.models.base import TaskStatus

        files = rebase.get("files") or []
        logger.info(
            "merge conflict escalated to CEO",
            task_id=str(task_id),
            conflicting_files=len(files),
            rebase_status=rebase.get("status"),
            merge_error=str(exc),
        )
        await self.task.admin_set_status(
            task_id,
            TaskStatus.AWAITING_CEO_APPROVAL,
            actor_id=pm_agent_id,
            actor_role="cell_pm",
        )
        await self._notify_ceo_merge_conflict(task_id, files)
        t = await self.task.get(task_id)
        detail = f" ({len(files)} conflicting file(s))" if files else ""
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=(
                "the PR has merge conflicts that could not be resolved "
                f"automatically{detail}; escalated to the CEO. A developer can "
                "rebase the branch, or the CEO can close the PR if superseded."
            ),
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        ).with_introspection(task=t, role="cell_pm")

    async def _notify_ceo_merge_conflict(self, task_id: UUID, files: list[str]) -> None:
        """Best-effort CEO alert for a wedged merge conflict; never raises."""
        from roboco.services.notification import NotificationService

        try:
            await NotificationService().send_stuck_agent_notification(
                task_id=str(task_id),
                agent_slug="cell_pm",
                task_status="awaiting_ceo_approval",
                to_agent="ceo",
            )
        except Exception:
            logger.warning(
                "failed to send CEO merge-conflict notification",
                task_id=str(task_id),
                conflicting_files=len(files),
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
        self, main_pm_agent_id: UUID, root_task_id: UUID, t: Any, notes: str
    ) -> Envelope | None:
        """Return a rejection Envelope if pre-escalation guards fail; else None.

        Tracing gates (journal:decision, journal:reflect, notes>=min) are
        evaluated by ``_check_complete_gates`` which consumes
        ``VERB_REQUIREMENTS["complete"]``. The
        ``_subtasks_not_terminal_envelope`` call is kept inline because
        its remediation enumerates the non-terminal subtask ids — strictly
        richer than the foundation hint.
        """
        if t.assigned_to != main_pm_agent_id:
            return Envelope.not_authorized(
                message="not assigned to you",
                remediate=(
                    "wait for assignment or claim."
                    + await self._own_review_hint(main_pm_agent_id, root_task_id)
                ),
                context_briefing=await self._briefing_for(
                    main_pm_agent_id, root_task_id
                ),
            )
        # Accept in_progress too. A root resumed from paused (its
        # subtasks all done) sits in in_progress — there is no submit_up for
        # roots to move it to awaiting_pm_review. main_pm_complete itself
        # opens the root→master PR and walks it through awaiting_pm_review
        # before escalating; the CEO is the root's reviewer.
        if str(t.status) not in ("awaiting_pm_review", "in_progress"):
            return Envelope.invalid_state(
                message=(
                    f"task {root_task_id} is in {t.status}, expected"
                    " awaiting_pm_review or in_progress"
                ),
                remediate=(
                    "this task is not ready for main-PM completion."
                    + await self._own_review_hint(main_pm_agent_id, root_task_id)
                ),
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
                    "cell PM should complete this task; main PM only"
                    " completes root tasks."
                    + await self._own_review_hint(main_pm_agent_id, root_task_id)
                ),
                context_briefing=await self._briefing_for(
                    main_pm_agent_id, root_task_id
                ),
            )
        if env := await self._check_complete_gates(
            main_pm_agent_id, root_task_id, notes
        ):
            return env
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
        guard = await self._main_pm_complete_guard(
            main_pm_agent_id, root_task_id, t, notes
        )
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

        # escalate_to_ceo requires source=awaiting_pm_review, but a root
        # resumed from paused is in_progress and nothing else moves it there
        # (submit_up is cell-PM-only). The root→master PR now exists, so walk
        # the root through awaiting_pm_review here. Uses the TaskService
        # transition directly (no gateway team-match) — submit_pm_review's
        # gates (in_progress + branch + pr_created + subtasks terminal) all
        # hold at this point.
        refreshed = await self.task.get(root_task_id)
        if refreshed is not None and str(refreshed.status) == "in_progress":
            advanced = await self.task.submit_pm_review(
                main_pm_agent_id, root_task_id, notes
            )
            if advanced is None:
                return await self._emit_rejection(
                    Envelope.invalid_state(
                        message=(
                            "could not move root to awaiting_pm_review for CEO"
                            " escalation"
                        ),
                        remediate=(
                            "ensure the root→master PR is open and all subtasks"
                            " are terminal, then retry complete"
                        ),
                        context_briefing=await self._briefing_for(
                            main_pm_agent_id, root_task_id
                        ),
                    ).with_introspection(task=refreshed, role="main_pm"),
                    agent_id=main_pm_agent_id,
                    task_id=root_task_id,
                    verb="main_pm_complete",
                )

        # Use kwargs — service signature is (task_id, agent_role="cell_pm",
        # notes=None). Positional was passing agent_id as task_id and the
        # actual task_id as agent_role.
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
            next=spec_module._INTENT_VERBS["complete"].next_hint(t),
            context_briefing=await self._briefing_for(main_pm_agent_id, root_task_id),
        ).with_introspection(task=t, role="main_pm")

    async def complete(self, agent_id: UUID, task_id: UUID, notes: str) -> Envelope:
        """Dispatch to cell_pm_complete or main_pm_complete based on agent role.

        Spec gate runs first (role membership + composed ``complete``
        action's source-status constraint, AWAITING_PM_REVIEW only).
        Both rejections (role not in spec._PM_ROLES, status not awaiting_pm_review)
        flow through ``spec.can_invoke_intent`` and surface as the
        spec-supplied rejection_kind. After the gate accepts, the
        verb body owns dispatch — ``complete`` has two divergent
        runtime paths (Cell PM merges leaf into parent branch; Main PM
        opens master PR + escalates to CEO) that can't be expressed as
        a single VerbRunner composition. Each lower-level method keeps
        its own pre-flight guards (``_cell_pm_complete_guard`` /
        ``_main_pm_complete_guard``) — those model journal:decision
        presence, subtasks-terminal, and PR-mergeability checks the
        spec doesn't model yet.
        """
        t = await self.task.get(task_id)
        briefing = await self._briefing_for(agent_id, task_id, task=t)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=agent_id,
                task_id=task_id,
                verb="complete",
            )
        agent = await self.task.agent_for(agent_id)
        role_str = str(agent.role) if agent is not None else "developer"
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
                verb="complete",
            )
        spec_ctx = spec_module.Context(
            actor_id=agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            original_developer_slug=_extract_original_developer(t),
        )
        decision = spec_module.can_invoke_intent(role, "complete", t, spec_ctx)
        if not decision.allowed:
            return await self._emit_rejection(
                Envelope.from_decision(decision, briefing=briefing).with_introspection(
                    task=t, role=role_str
                ),
                agent_id=agent_id,
                task_id=task_id,
                verb="complete",
            )
        # Spec gate passed — role is CELL_PM or MAIN_PM, status is
        # AWAITING_PM_REVIEW. Verb body owns dispatch from here.
        if role_str == "cell_pm":
            return await self.cell_pm_complete(agent_id, task_id, notes)
        # role_str == "main_pm" — spec._PM_ROLES has only these two members.
        return await self.main_pm_complete(agent_id, task_id, notes)

    async def escalate_up(
        self, pm_agent_id: UUID, task_id: UUID, reason: str
    ) -> Envelope:
        """Escalate a task to the agent's escalation_target role.

        Spec gate runs first and enforces role membership (cell_pm or
        main_pm only — escalate_up's IntentSpec has ``composes=()``, so
        the spec does not enforce a source-status constraint). After the
        gate accepts, the verb-specific preflight guards stay:
        ``journal:decision`` presence (the spec doesn't model journal
        side effects) and ``escalation_target`` configuration on the
        actor's agent record (also out of the spec's scope). Then the
        verb body owns dispatch via ``task.escalate(...)`` because
        ``composes=()`` (no atomic action for the runner to run).
        """
        t = await self.task.get(task_id)
        briefing = await self._briefing_for(pm_agent_id, task_id, task=t)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="escalate_up",
            )
        me = await self.task.agent_for(pm_agent_id)
        role_str = str(me.role) if me is not None else "cell_pm"
        try:
            role = spec_module.Role(role_str)
        except ValueError:
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message=f"unknown role '{role_str}'",
                    remediate="role is not declared in the lifecycle spec",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="escalate_up",
            )
        spec_ctx = spec_module.Context(
            actor_id=pm_agent_id,
            actor_slug=getattr(me, "slug", None) if me is not None else None,
            original_developer_slug=_extract_original_developer(t),
            notes=reason,
        )
        decision = spec_module.can_invoke_intent(role, "escalate_up", t, spec_ctx)
        if not decision.allowed:
            return await self._emit_rejection(
                Envelope.from_decision(decision, briefing=briefing).with_introspection(
                    task=t, role=role_str
                ),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="escalate_up",
            )

        preflight = await self._escalate_up_preflight(
            pm_agent_id, t, me, briefing, role_str
        )
        if preflight is not None:
            return await self._emit_rejection(
                preflight,
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="escalate_up",
            )

        # Verb body owns dispatch — escalate_up's IntentSpec has
        # composes=(), so VerbRunner has no atomic action to run.
        target_slug = me.escalation_target if me else None
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
                    context_briefing=briefing,
                ),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="escalate_up",
            )
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=f"escalated to {target_slug}; idle until they respond",
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)

    async def _escalate_up_preflight(
        self,
        pm_agent_id: UUID,
        t: Any,
        me: Any,
        briefing: dict[str, Any],
        role_str: str,
    ) -> Envelope | None:
        """Verb-specific preflight gates for escalate_up.

        Returns a rejection envelope when the gate fires; ``None`` to
        proceed. The spec doesn't model journal side effects or agent
        metadata (escalation_target slug), so these gates stay in the
        verb body. The journal-decision check is delegated to
        ``_check_pm_decision_required`` which consumes
        ``VERB_REQUIREMENTS["escalate_up"]``.
        """
        if env := await self._check_pm_decision_required(
            "escalate_up", pm_agent_id, t.id, t
        ):
            return env.with_introspection(task=t, role=role_str)
        target_slug = me.escalation_target if me else None
        if not target_slug:
            return Envelope.invalid_state(
                message="no escalation target configured for your role",
                remediate="check agents_config.py ESCALATION_CHAIN for your slug",
                context_briefing=briefing,
            ).with_introspection(task=t, role=role_str)
        return None

    # --- Phase 4 (board) verbs ---

    async def _escalate_did_not_apply(
        self,
        *,
        runner_error: str | None,
        task: Any,
        role_str: str,
        briefing: dict[str, Any],
        agent_id: UUID,
        task_id: UUID,
    ) -> Envelope:
        """Rejection for an escalate_to_ceo the runner did not apply.

        ``runner_error`` set → the run_intent savepoint raised; otherwise the
        service declined (task not in awaiting_pm_review). Either way a clean
        invalid_state, never the unhandled ``None.status`` 500.
        """
        if runner_error is not None:
            message = f"verb runner failed: {runner_error}"
            remediate = "check workspace + retry; if persistent, escalate"
        else:
            message = (
                "escalate_to_ceo did not apply — the task is not in a state"
                " that escalates to the CEO (needs awaiting_pm_review)"
            )
            remediate = (
                "resolve or re-route the task; only awaiting_pm_review tasks"
                " escalate to the CEO"
            )
        return await self._emit_rejection(
            Envelope.invalid_state(
                message=message,
                remediate=remediate,
                context_briefing=briefing,
            ).with_introspection(task=task, role=role_str),
            agent_id=agent_id,
            task_id=task_id,
            verb="escalate_to_ceo",
        )

    async def escalate_to_ceo(
        self, agent_id: UUID, task_id: UUID, reason: str
    ) -> Envelope:
        """Board/Main PM escalates task_id to CEO with reason.

        Spec gate runs first and enforces role membership (main_pm,
        product_owner, head_marketing) plus the composed
        ``escalate_to_ceo`` action's source-status constraint
        (AWAITING_PM_REVIEW only). After the gate accepts, the
        verb-specific preflight guard stays: ``journal:decision``
        presence (the spec doesn't model journal side effects). Then
        ``VerbRunner.run_intent("escalate_to_ceo", ...)`` dispatches the
        (escalate_to_ceo,) atomic chain wrapped in a savepoint. After
        the runner returns, the task is reassigned to None — the CEO
        acts via the UI, not as a spawnable agent (mirrors
        main_pm_complete).
        """
        t = await self.task.get(task_id)
        briefing = await self._briefing_for(agent_id, task_id, task=t)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=agent_id,
                task_id=task_id,
                verb="escalate_to_ceo",
            )
        me = await self.task.agent_for(agent_id)
        role_str = str(me.role) if me is not None else "main_pm"
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
                verb="escalate_to_ceo",
            )
        spec_ctx = spec_module.Context(
            actor_id=agent_id,
            actor_slug=getattr(me, "slug", None) if me is not None else None,
            original_developer_slug=_extract_original_developer(t),
            notes=reason,
        )
        decision = spec_module.can_invoke_intent(role, "escalate_to_ceo", t, spec_ctx)
        if not decision.allowed:
            return await self._emit_rejection(
                Envelope.from_decision(decision, briefing=briefing).with_introspection(
                    task=t, role=role_str
                ),
                agent_id=agent_id,
                task_id=task_id,
                verb="escalate_to_ceo",
            )

        # Verb-specific preflight: journal:decision presence (out of spec scope).
        # Delegates to _check_pm_decision_required which consumes
        # VERB_REQUIREMENTS["escalate_to_ceo"].
        if env := await self._check_pm_decision_required(
            "escalate_to_ceo", agent_id, task_id, t
        ):
            return await self._emit_rejection(
                env.with_introspection(task=t, role=role_str),
                agent_id=agent_id,
                task_id=task_id,
                verb="escalate_to_ceo",
            )

        runner = self._verb_runner()
        runner_error: str | None = None
        try:
            updated = await runner.run_intent("escalate_to_ceo", t, me, spec_ctx)
        except Exception as exc:
            updated, runner_error = None, str(exc)
        # run_intent returns None when the service declines the escalation (e.g. a
        # board agent escalating a task not in awaiting_pm_review). Without this
        # guard the OK path below dereferenced ``None.status`` — an unhandled 500
        # (the board's escalate-from-blocked crash loop).
        if updated is None:
            return await self._escalate_did_not_apply(
                runner_error=runner_error,
                task=t,
                role_str=role_str,
                briefing=briefing,
                agent_id=agent_id,
                task_id=task_id,
            )
        t = updated
        # Same as main_pm_complete: CEO acts via UI, not as a spawnable agent.
        await self.task.reassign(task_id, None)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS["escalate_to_ceo"].next_hint(t),
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)

    # board_triage + auditor_triage moved to ``board.py`` as the first
    # per-role mixin extraction. The Choreographer class is
    # composed in ``__init__.py`` from BoardMixin + the rest of this
    # _impl. Methods now resolve via Python's MRO.
