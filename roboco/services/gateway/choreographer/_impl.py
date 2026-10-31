"""Choreographer — composes existing services into intent-verb sequences.

This module has interface signatures only in Phase 0. Each verb's full
implementation lands in its respective phase (Phase 1: dev verbs, Phase 2:
QA verbs, Phase 3: doc + PM verbs, Phase 4: board verbs).

The signatures are stable contracts that the MCP servers and the
/api/v1/flow/* endpoints will call into. Phase 0 wires the dependency
injection so later phases just fill in the bodies.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar, Literal
from uuid import UUID

import structlog

from roboco.foundation.identity import Team, slug_for_uuid_or_none
from roboco.foundation.policy import lifecycle as spec_module
from roboco.foundation.policy.batch import is_batch_root_subtask
from roboco.foundation.policy.content import (
    markers,
)
from roboco.foundation.policy.content.validators import reject_trivial
from roboco.services.gateway.choreographer import findings as findings_lib
from roboco.services.gateway.choreographer._protocol import actor_context_fields
from roboco.services.gateway.choreographer._verb_runner import VerbRunner
from roboco.services.gateway.choreographer.collision import build_collision_context
from roboco.services.gateway.choreographer.evidence_legs import (
    LegBudget,
    run_bounded_leg,
)
from roboco.services.gateway.claim_guards import (
    agent_access_denied_guard,
    already_active_guard,
    paused_tasks_guard,
    project_budget_exceeded_guard,
    sequence_held_guard,
    unmet_dependency_guard,
)
from roboco.services.gateway.envelope import Envelope
from roboco.services.gateway.evidence_builder import (
    BriefingInputs,
    build_context_briefing,
    build_task_handoff,
    shape_memory_query,
)
from roboco.services.gateway.merge_chain import (
    resolve_parent_branch,
)
from roboco.services.gateway.remediation import (
    hint_for_evidence_not_inspected,
    hint_for_missing_doc_files,
    hint_for_missing_journal_decision,
    hint_for_missing_journal_learning,
    hint_for_missing_progress,
    hint_for_missing_qa_notes,
    hint_for_missing_reflect,
    hint_for_open_findings,
    hint_for_render_preview,
    hint_for_short_dev_notes,
    hint_for_short_doc_notes,
    hint_for_short_pr_reviewer_notes,
    hint_for_short_quick_context,
    hint_for_unaddressed_acceptance_criteria,
)
from roboco.services.gateway.role_config import role_carries_notify_ack
from roboco.services.repositories.review_findings import (
    ReviewFindingsRepository,
)

logger = structlog.get_logger()

if TYPE_CHECKING:
    from roboco.services.gateway.choreographer._protocol import ChoreographerHelpers

    _Base = ChoreographerHelpers
else:
    _Base = object

# _ensure_pm_decision's result: "fresh" (a recent decision already satisfies
# the window, nothing written), "wrote" (recorded now), "transient_failure"
# (the write raised WITH a non-empty rationale in hand — a DB-contention
# lock-timeout, not a real absence), "absent" (no rationale AND the window
# check never ran — legacy default for every call site that hasn't been
# threaded through yet). Every PM-decision gate helper accepts this as an
# optional param so "transient_failure" can satisfy the gate for THIS call
# (the rationale is in the verb payload; the write is a convenience, not the
# substance) without changing every verb's own public signature.
PmDecisionOutcome = Literal["fresh", "wrote", "transient_failure", "absent"]

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

# Per-sub_task ceilings — mirror IWillPlanRequest.SubTaskCreate so direct
# service-layer callers (MCP, fixtures) can't bypass the Pydantic boundary
# with a 5000-char description (the bloat defect: descriptions dominated by
# over-long sub-task prose).
_PM_SUBTASK_TITLE_MAX_LEN = 200
_PM_SUBTASK_DESC_MAX_LEN = 600

# Over-decomposition cap (the 2026-07-07 task-quality defect): a plan with
# more than this many sub_tasks should be split into sibling coordination
# tasks, not one giant plan.
_PM_SUBTASKS_MAX = 7

# unblock's flip breaker: alert the CEO once a task has flip-flopped
# block/unblock this many times (live incident: 10 flips, 43 spawns with no
# forward progress before anyone noticed).
_BLOCK_FLIP_NOTIFY_THRESHOLD = 3

# unblock's oscillation breaker: past the (higher) notify threshold above, a
# cycle that keeps repeating with NO progress between rounds (see
# markers.bump_oscillation_strikes) is force-blocked for a human instead of
# restored again — the notify threshold alone only alerts, it never stops the
# respawns (live incident: an escalate_up/unblock cycle split across two
# agents' per-(agent, task) respawn counters, neither of which accrued at the
# cycle's real rate, burned spawns for hours with the CEO alert already fired
# and ignored/unactioned).
_OSCILLATION_TRIP_THRESHOLD = 5


def _touch_globs(t: Any) -> list[str]:
    """``t.intends_to_touch`` defensively coerced to ``list[str]`` (the
    decisions pilots use the declared scope globs as best-effort state; a
    missing or malformed field degrades to no scope, never a crash)."""
    val = getattr(t, "intends_to_touch", None)
    return [str(f) for f in val] if isinstance(val, list) else []


def _thin_subtask_hint(sub_tasks: list[Any]) -> str | None:
    """Return a hint if any PM sub_task is title-only / thin / over-long.

    Each sub_task is a delegate target AND a progress-checklist item, so
    a title with no real description is not a plan. Also caps title +
    description length so a sub_task can't bloat the plan. Returns None
    when every sub_task carries a title and a substantive description.
    """
    for i, st in enumerate(sub_tasks):
        if not isinstance(st, dict):
            return f"sub_task #{i + 1} must be an object {{title, description}}."
        title = str(st.get("title", "")).strip()
        desc = str(st.get("description") or "").strip()
        if not title:
            return f"sub_task #{i + 1} has no title."
        if len(title) > _PM_SUBTASK_TITLE_MAX_LEN:
            return (
                f"sub_task #{i + 1} ('{title[:40]}') title is too long "
                f"({len(title)} chars) — keep titles <= "
                f"{_PM_SUBTASK_TITLE_MAX_LEN} chars."
            )
        if len(desc) < _PM_SUBTASK_DESC_MIN_LEN:
            return (
                f"sub_task #{i + 1} ('{title[:40]}') description is too thin "
                f"({len(desc)} chars) — need >= {_PM_SUBTASK_DESC_MIN_LEN} "
                "characters describing what the step actually does."
            )
        if len(desc) > _PM_SUBTASK_DESC_MAX_LEN:
            return (
                f"sub_task #{i + 1} ('{title[:40]}') description is too long "
                f"({len(desc)} chars) — keep descriptions <= "
                f"{_PM_SUBTASK_DESC_MAX_LEN} chars; split if larger."
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
    return markers.get_original_developer(task)


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
    # Per-cell project routing for the delegate verb. Optional so existing
    # callsites / tests that don't exercise Product routing don't have to plumb
    # it in; when None, delegate falls back to parent-project inheritance.
    product: Any = None
    # ProjectService for the agent-access claim guard
    # (check_agent_access — the allowed_agents restriction). Optional so
    # existing callsites / tests that don't plumb it keep the guard inert.
    project: Any = None
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

    Both verbs run the same claim, set_plan, provision, start sequence and
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
    resolved_findings: list[dict[str, Any]] | None = None


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
    # Parent AC ids this subtask is responsible for — the decomposition coverage
    # link. Empty/None means the child covers no specific parent criteria yet.
    covers_parent_criteria: list[str] | None = None
    # Dev-task collision surface (multi-level sequencing — edge kind 3). The
    # cell PM states what each dev task touches so the choreographer can run
    # SequencingService and wire the dev-task collision DAG. Optional: a
    # delegate without surfaces joins no collision edges (parallel).
    intends_to_touch: list[str] | None = None
    adds_migration: bool = False
    touches_shared: bool = False
    # Explicit dependency override — wired verbatim as dependency_ids on the
    # created dev task (an edge the surface rules would miss).
    depends_on: list[UUID] | None = None


class BaseChoreographer(_Base):
    """Composes existing services into intent-verb sequences.

    Constructor takes a ``ChoreographerDeps`` bundle (DI). Verb methods are
    async. Each returns a standardized Envelope. The package's
    ``Choreographer`` composes the role mixins onto this class; under
    ``TYPE_CHECKING`` it inherits ``ChoreographerHelpers`` like every mixin,
    so a call into a mixin method is typed here too.

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
    def product(self) -> Any:
        return self._deps.product

    @property
    def project(self) -> Any:
        return self._deps.project

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

    # --- Phase 1 (developer) verbs ---

    # PM coordinator roles plan + delegate many roots in parallel; the actual
    # work then runs in the delegated children/cells, not in the PM's own hands.
    # So the single-active-task concurrency invariants (already_active / paused)
    # that keep a *developer* to one task at a time must NOT gate a coordinator
    # — only a genuine upstream sequence dependency (unmet_dependency) may hold a
    # PM's root back. Without this exemption a PM that claimed one root could
    # never plan a second and thrashed between its claimed roots, respawning
    # forever and burning tokens for zero progress.
    _COORDINATOR_ROLES: ClassVar[frozenset[str]] = frozenset({"main_pm", "cell_pm"})

    def _verb_runner(self) -> VerbRunner:
        """Construct a VerbRunner bound to this Choreographer's services.

        Cheap to allocate; one per verb invocation keeps the runner
        stateless across requests.
        """
        return VerbRunner(task_service=self.task, git_service=self.git)

    # --- QA verbs moved to ``qa.py``. ---

    # --- Phase 3 (documenter + PM) verbs ---

    # claim_doc_task + i_documented moved to ``doc.py``.

    # Proactive nudge surfaced alongside ``parent_ac_coverage`` in the
    # planning briefing and the delegate success envelope, ahead of any
    # rejection — delegate() enforces covers_parent_criteria on every call
    # once the parent has acceptance criteria, it is NOT deferred to
    # i_am_idle's separate, opt-in unclaimed-parent-acs self-check (a PM
    # reading only that gate's docs can otherwise assume the mapping is
    # optional and loop on the delegate-time rejection for hours).
    _COVERS_PARENT_CRITERIA_HINT: ClassVar[str] = (
        "every delegate() call under this parent must pass "
        "covers_parent_criteria=[<one or more ids from parent_ac_coverage>] "
        "— it is enforced right now, on this call, not deferred to i_am_idle."
    )

    _TERMINAL_STATUSES: ClassVar[frozenset[str]] = frozenset({"completed", "cancelled"})

    # --- Phase 4 (board) verbs ---

    # board_triage + auditor_triage moved to ``board.py`` as the first
    # per-role mixin extraction. The Choreographer class is
    # composed in ``__init__.py`` from BoardMixin + the rest of this
    # _impl. Methods now resolve via Python's MRO.


class RejectionMixin(_Base):
    """The rejection envelopes every verb shares: the audited rejection with its
    sandbox teardown, the free-text soup guard, the reassigned-task and
    runner-failure envelopes, and attaching a briefing."""

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
        if env.remediate:
            # Conventions-gate rejections carry the file:line violation
            # listing ONLY here — without it the audit row is unactionable.
            details["remediate"] = env.remediate
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

    async def _teardown_sandbox_best_effort(self, agent_id: UUID) -> None:
        """Release the caller's request_sandbox sidecar on successful exit.

        Called from the six verbs whose success means the caller's
        engagement with its work has ended (i_am_done, unclaim, i_am_idle,
        pass_review/fail_review, i_documented) — no shared success-emit
        path spans all six (they live across three mixin files, each
        building its own ``Envelope.ok`` at its own site), so this is
        called once at each verb's existing success point rather than
        duplicated teardown logic. No orchestrator (e2e harness, startup)
        is a silent no-op; any other failure is logged, never raised —
        the container-removal teardown + janitor sweep remain the backstop.
        """
        orch = self.orchestrator
        if orch is None:
            return
        from roboco.agents_config import _resolve_to_slug

        try:
            await orch.release_sandbox(_resolve_to_slug(str(agent_id)))
        except Exception as exc:
            logger.warning(
                "sandbox_release_failed", agent_id=str(agent_id), error=str(exc)
            )

    @classmethod
    def _free_text_soup(
        cls, checks: tuple[tuple[str, Any, int], ...]
    ) -> Envelope | None:
        """Return a bare ``invalid_state`` envelope for the first soupy field.

        ``checks`` is a tuple of ``(field_name, value, min_chars)``. A value
        that is ``None`` or empty/whitespace is skipped — presence is gated
        elsewhere; this rejects *filler* in text the agent actually supplied
        (``wip``, ``asdf``, ``tbd``, ``...``). A list value has each item
        checked. The returned envelope carries no introspection or audit row:
        the caller folds it into its existing rejection ``return`` so the soup
        check adds no extra return (verbs stay under the complexity bound), and
        the agent always gets a remediable envelope, never a 422 (which would
        trip the do-server circuit breaker).
        """
        for name, value, min_chars in checks:
            items = value if isinstance(value, list) else [value]
            for idx, item in enumerate(items):
                if item is None or not str(item).strip():
                    continue
                label = f"{name}[{idx}]" if isinstance(value, list) else name
                env = cls._soup_reason(str(item), label, min_chars)
                if env is not None:
                    return env
        return None

    async def _guard_free_text(
        self,
        *,
        checks: tuple[tuple[str, Any, int], ...],
        task: Any,
        agent_id: UUID,
        role_str: str,
        verb: str,
    ) -> Envelope | None:
        """Emit-on-soup wrapper over :meth:`_free_text_soup`.

        For verbs that have return-count headroom: stamps introspection, audits
        via ``_emit_rejection``, and returns the rejection (or ``None`` clean).
        Verbs already at the return bound call ``_free_text_soup`` directly and
        fold the result into an existing rejection return instead.
        """
        env = self._free_text_soup(checks)
        if env is None:
            return None
        return await self._emit_rejection(
            env.with_introspection(task=task, role=role_str),
            agent_id=agent_id,
            task_id=getattr(task, "id", None),
            verb=verb,
        )

    @staticmethod
    def _soup_reason(value: str, field: str, min_chars: int) -> Envelope | None:
        """Build an ``invalid_state`` envelope when ``value`` is filler, else None."""
        try:
            reject_trivial(value, field=field, min_chars=min_chars)
        except ValueError as exc:
            return Envelope.invalid_state(
                message=str(exc),
                remediate=(
                    f"write a substantive {field} (>={min_chars} chars, no filler "
                    "like 'asdf'/'wip'/'tbd'/'...'); state what actually happened."
                ),
                context_briefing={},
            )
        return None

    @staticmethod
    def _soup_or_decision_env(
        soup: Envelope | None, decision: Any, briefing: dict[str, Any]
    ) -> Envelope | None:
        """Pick the rejection envelope: soup first, then the spec decision.

        Lets a verb fold the free-text soup check into its existing
        spec-gate rejection ``return`` with a single branch — the two
        fallback ``or``s live here, keeping the verb body under the
        cyclomatic bound. Returns ``None`` when neither rejects; the caller
        stamps introspection + emits.
        """
        if soup is not None:
            return soup
        if not decision.allowed:
            return Envelope.from_decision(decision, briefing=briefing)
        return None

    @staticmethod
    def _runner_failed_envelope(
        exc: Exception, t: Any, briefing: Any, role_str: str
    ) -> Envelope:
        """Shared 'verb runner failed' rejection for a claim/plan/start
        sequence: the same shape whether the failure came from inside the
        composed savepoint (``run_intent``) or from ``provision_claim``'s
        post-savepoint branch creation."""
        return Envelope.invalid_state(
            message=f"verb runner failed: {exc}",
            remediate="check workspace + retry; if persistent, escalate",
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)

    @staticmethod
    def _step_failed_envelope(
        step: str, ctx: _ClaimPlanStartContext, briefing: Any, role_str: str
    ) -> Envelope:
        """Rejection for a claim/plan/start step that returned ``None``."""
        return Envelope.invalid_state(
            message=f"{step} failed for task {ctx.task_id}",
            remediate=(
                "task not in a startable state"
                " (claimed/paused/needs_revision) or no plan recorded"
            ),
            context_briefing=briefing,
        ).with_introspection(task=None, role=role_str)

    @staticmethod
    def _with_briefing(env: Envelope, briefing: dict[str, Any]) -> Envelope:
        """Attach a context_briefing to an Envelope (mutate-and-return helper)."""
        env.context_briefing = briefing
        return env


class BriefingMixin(_Base):
    """The ``context_briefing`` every verb returns: the collision context,
    company goals, and the heavy claim-time sections with institutional
    memory, its retrieval log and lesson pruning."""

    async def _briefing_for(
        self,
        agent_id: UUID,
        task_id: UUID | None,
        *,
        task: Any | None = None,
        include_ac_coverage: bool = False,
        full: bool = False,
        include_company_goals: bool = False,
    ) -> dict[str, Any]:
        """Assemble context_briefing for agent_id, optionally scoped to task_id.

        ``full`` gates the heavy, verb-invariant sections (company_goals,
        recent_team_activity, blockers_in_my_lane, task_handoff,
        institutional_memory). Only context-acquisition verbs (give_me_work /
        claim / plan / resume / triage) pass ``full=True``; every other verb —
        progress, transition and rejection envelopes included — gets the slim
        signals-only briefing (unread a2a/mentions/notifications + metadata
        gaps). The agent already holds the heavy context from its claim, and
        every extra copy is re-read at cache-read price on all later turns.

        ``include_company_goals`` is a narrow, cheap-only opt-in for callers
        that want the charter (north_star/brand_voice/…) without paying for
        the rest of ``full``'s heavy sections (team activity, blockers, an
        institutional-memory RAG search). ``company_goals`` is a single
        capped-singleton lookup, so this stays safe on a low-cardinality path
        like ``board_triage``'s idle branch — hit when the Product Owner /
        Head of Marketing's one-shot roadmap / feature-spotlight exploration
        spawn finds no strategic root to review, which is not a "strategic
        root" itself so the ``full=True`` branch never fires for it. A no-op
        when ``full`` is already True (company_goals is already fetched).

        ``task`` is the already-loaded row (every claim / give_me_work / done
        path holds it). The prior-work handoff is built only when it is passed —
        no extra fetch — so task-scoped error paths that carry only an id simply
        omit the digest rather than pay a redundant read for it.

        ``include_ac_coverage`` is set only on PM decomposition touchpoints
        (give_me_work / i_will_plan / delegate / submit_up) so the PM sees, per
        parent criterion, what is still unclaimed and can pass
        ``covers_parent_criteria`` on delegate. Off everywhere else so a leaf's
        own criteria never surface as bogus "unclaimed" noise to a developer.
        It is functional (not bulk), so it stays independent of ``full``.
        """
        repo = self._deps.evidence_repo
        heavy = (
            await self._heavy_briefing_sections(agent_id, task_id, task) if full else {}
        )
        company_goals = await self._resolve_company_goals(
            heavy, full=full, include_company_goals=include_company_goals
        )
        inputs = BriefingInputs(
            unread_a2a=await repo.list_unread_a2a(agent_id),
            unread_mentions=await repo.list_unread_mentions(agent_id),
            pending_notifications=await repo.list_pending_notifications(agent_id),
            task_metadata_gaps=(
                await repo.task_metadata_gaps(task_id) if task_id else []
            ),
            recent_team_activity=heavy.get("recent_team_activity", []),
            blockers_in_my_lane=heavy.get("blockers_in_my_lane", []),
            task_handoff=heavy.get("task_handoff"),
            company_goals=company_goals,
        )
        briefing = build_context_briefing(inputs)
        memory_block = heavy.get("institutional_memory")
        if memory_block is not None:
            # "What the company already knows about work like this" — distilled
            # lessons + approved playbooks, pushed so the agent never has to ask.
            # The block always carries ``institutional_memory_status`` so an agent
            # can tell "searched, nothing" (below_floor / empty) from "search broke"
            # (error) from "subsystem off" (disabled); lessons is empty unless ok.
            briefing = {**briefing, "institutional_memory": memory_block}
        # The collision map: surfaced siblings (same parent) that would collide
        # with this task. A PM planning a batch root sees the other
        # root-subtasks' declared surfaces; a dev claiming a leaf sees its
        # cell-sibling overlaps. No actual files at plan/claim time, so drift is
        # omitted here (the QA/gate envelopes carry it). Best-effort — a failure
        # omits the block, never breaks the briefing.
        briefing = await self._with_collision_briefing(briefing, full, task)
        if include_ac_coverage and task_id is not None:
            coverage = await self.task.parent_ac_coverage(task_id)
            if coverage:
                # Compact decomposition view: surface each parent criterion's id
                # so the PM can map subtasks to it via covers_parent_criteria,
                # and call out the ones still unclaimed. Mirrors how sizing_hint
                # is merged ad-hoc — context_briefing is free-form dict[str, Any].
                briefing = {
                    **briefing,
                    "parent_ac_coverage": coverage,
                    "unclaimed_parent_acs": [
                        c["id"] for c in coverage if not c["claimed"]
                    ],
                    "delegate_hint": self._COVERS_PARENT_CRITERIA_HINT,
                }
        return briefing

    async def _collision_context_for(
        self, t: Any, *, actual_files: list[str] | None = None
    ) -> list[dict[str, Any]] | None:
        """The collision map for ``t`` against its surfaced siblings — one
        indexed ``get_subtasks(parent_task_id)`` query + the pure builder.

        ``None`` for a root (no parent) or a fetch failure (best-effort: the
        briefing / envelope omit the block rather than break). The QA and
        PR-gate evidence builders call the pure ``build_collision_context``
        directly (they hold the real touched files); this helper covers the
        planning/claim briefing path, which has no actual files yet.
        """
        parent_id = getattr(t, "parent_task_id", None)
        if not parent_id:
            return None
        try:
            siblings = await self.task.get_subtasks(parent_id)
            return build_collision_context(
                task=t, siblings=siblings, actual_files=actual_files
            )
        except Exception:
            # Best-effort: omit on any fetch/build failure
            return None

    async def _with_collision_briefing(
        self, briefing: dict[str, Any], full: bool, task: Any
    ) -> dict[str, Any]:
        """Merge the collision block into ``briefing`` when the planning/claim
        path warrants it (``full`` + a real task). Keeps the branch count out of
        ``_briefing_for`` — the collision fetch + merge live here."""
        if not full or task is None:
            return briefing
        collision = await self._collision_context_for(task)
        if not collision:
            return briefing
        return {**briefing, "collision_context": collision}

    async def _resolve_company_goals(
        self,
        heavy: dict[str, Any],
        *,
        full: bool,
        include_company_goals: bool,
    ) -> dict[str, Any] | None:
        """The briefing's company_goals section — split out of ``_briefing_for``
        to keep its complexity down.

        ``full=True`` already resolved it into ``heavy`` (one query, via
        ``_heavy_briefing_sections``); otherwise a standalone cheap fetch runs
        only under the narrower ``include_company_goals`` opt-in. Never both —
        no double query when a caller somehow sets both.
        """
        goals: dict[str, Any] | None
        if full:
            goals = heavy.get("company_goals")
        elif include_company_goals:
            goals = await self._deps.evidence_repo.company_goals()
        else:
            goals = None
        return goals

    async def _heavy_briefing_sections(
        self, agent_id: UUID, task_id: UUID | None, task: Any | None
    ) -> dict[str, Any]:
        """The full-briefing-only sections (see ``_briefing_for``'s docstring).

        The prior-work handoff is built only when the loaded ``task`` row is
        passed — no extra fetch — so task-scoped error paths that carry only an
        id simply omit the digest rather than pay a redundant read for it.
        """
        repo = self._deps.evidence_repo
        task_handoff: dict[str, Any] | None = None
        if task_id is not None and task is not None:
            # Push the prior-work digest so a freshly spawned / respawned agent
            # resumes from the previous worker's PR + commits + journal rather
            # than re-exploring the codebase cold on every lifecycle hand-off.
            handoff_highlights = await repo.journal_highlights_for_task(
                task_id, include_ancestors=True
            )
            open_findings = await findings_lib.open_findings_for_task(
                self.task.session, task_id
            )
            parent_context = await repo.ancestor_context_for_task(task_id)
            task_handoff = build_task_handoff(
                task, handoff_highlights, open_findings, parent_context
            )
        return {
            "recent_team_activity": await repo.recent_team_activity(agent_id),
            "blockers_in_my_lane": await repo.blockers_in_lane(agent_id),
            "task_handoff": task_handoff,
            "company_goals": await repo.company_goals(),
            "institutional_memory": await self._institutional_memory(agent_id, task),
        }

    async def _institutional_memory(
        self, agent_id: UUID, task: Any | None
    ) -> dict[str, Any]:
        """Org-memory keystone: top-K relevant past lessons/playbooks for this
        claim, role-shaped and relevance-floored. Empty unless
        ``org_memory_enabled`` and a task is in hand (nothing to query on
        otherwise). Best-effort — never breaks the briefing.

        Returns ``{"status": ..., "lessons": [...]}`` where status is one of
        ``disabled`` (subsystem off / no task — ponytail: both mean no search ran),
        ``error`` (search raised), ``timeout`` (searched, but the memory search
        didn't answer in time), ``empty`` (search yielded nothing),
        ``below_floor`` (searched, nothing met the floor), ``ok`` (lessons
        injected). Lessons is empty unless status is ``ok`` — the status is
        additive, the injection behavior is unchanged."""
        from roboco.config import settings as _settings

        if not _settings.org_memory_enabled or task is None:
            return {"status": "disabled", "lessons": []}
        agent = await self.task.agent_for(agent_id)
        role = str(agent.role) if agent is not None else ""
        title = str(getattr(task, "title", "") or "")
        raw_type = getattr(task, "task_type", None)
        if raw_type is None:
            task_type = ""
        elif hasattr(raw_type, "value"):
            task_type = str(raw_type.value)
        else:
            task_type = str(raw_type)
        query = shape_memory_query(role, title, task_type)
        try:
            result = await asyncio.wait_for(
                self._deps.evidence_repo.similar_memory(
                    query=query,
                    top_k=_settings.org_memory_top_k,
                    min_score=_settings.org_memory_min_score,
                ),
                timeout=_settings.institutional_memory_timeout_seconds,
            )
        except TimeoutError:
            logger.warning(
                "institutional_memory_timeout",
                agent_id=str(agent_id),
                task_id=str(getattr(task, "id", "")),
            )
            return {"status": "timeout", "lessons": []}
        items = list(result.get("items", []))
        status = str(result.get("status", "error"))
        if status == "ok":
            # Retrieval tracking (spec 12.1 Wave 3 instrumentation): what
            # institutional memory was injected, for which task/agent.
            await self._log_memory_retrievals(agent_id, task, items)
            # B41 lesson_prune (decisions pilot, default-off): score each
            # retrieved lesson's applicability and prune the inapplicable
            # ones from the INJECTED list only. The retrieval query, the
            # floor, and the add path are untouched; off/shadow/below-
            # floor injects every lesson exactly as before.
            items = await self._prune_institutional_memory(task, items)
        return {
            "status": status,
            "lessons": items,
        }

    async def _log_memory_retrievals(
        self, agent_id: UUID, task: Any | None, items: list[Any]
    ) -> None:
        """Retrieval tracking (spec 12.1 Wave 3 instrumentation): persist
        what institutional memory was injected, for which task/agent - the
        evidence future knowledge-family graders grade against.
        Best-effort: never breaks the briefing. Runs in its OWN committed
        session so the log survives the verb's transaction outcome."""
        if not items:
            return
        try:
            from roboco.db.base import get_session_factory
            from roboco.db.tables import MemoryRetrievalLogTable

            now = datetime.now(UTC)
            rows = [
                MemoryRetrievalLogTable(
                    source=str(item.get("source") or "")[:500],
                    task_id=task.id if task is not None else None,
                    agent_slug=slug_for_uuid_or_none(agent_id),
                    retrieved_at=now,
                )
                for item in items
            ]
            rows = [row for row in rows if row.source]
            if not rows:
                return
            factory = get_session_factory()
            async with factory() as db:
                db.add_all(rows)
                await db.commit()
        except Exception:
            logger.debug(
                "memory retrieval log write failed (best-effort)",
                agent_id=str(agent_id),
            )

    async def _prune_institutional_memory(
        self, task: Any, items: list[Any]
    ) -> list[Any]:
        """B41 lesson_prune scoring lane (decisions pilot, default-off).

        Title-shape retrieval injected wholesale misses file-scoped
        lessons and injects noise; this scores each lesson against
        (task description + plan + files-touched) and drops the ones
        below ``LESSON_PRUNE_KEEP_FLOOR`` from the injected list. MAY
        ONLY PRUNE: the search that fed it is never changed and lessons
        are never added. Fail-open in the safe direction: any pilot
        absence (off, shadow, no verdict, error) returns the full list.
        """
        if not items:
            return items
        kept = await self._lesson_prune_verdict(task, items)
        return kept if kept is not None else items

    async def _lesson_prune_verdict(
        self, task: Any, items: list[Any]
    ) -> list[Any] | None:
        """One lesson_prune scoring call over (description, plan, files).
        None means keep the full list (pilot off/shadow/error/no verdict):
        fail-open in the safe direction."""
        try:
            from roboco.services.decisions.pilots import PilotMode, pilot_mode

            mode = await pilot_mode(self.task.session, "lesson_prune")
        except Exception as exc:
            logger.warning(
                "lesson_prune_skip",
                task_id=str(getattr(task, "id", "")),
                error=str(exc),
            )
            return None
        if mode is PilotMode.OFF:
            return None
        from roboco.services.decisions.pilots_gateway import lesson_prune

        plan_text = str(getattr(task, "plan", "") or "")
        rich_plan = getattr(task, "rich_plan", None)
        if not plan_text and isinstance(rich_plan, dict):
            plan_text = str(rich_plan.get("approach") or "")
        try:
            return await lesson_prune(
                self.task.session,
                task_id=str(getattr(task, "id", "")),
                lessons=items,
                task_description=str(getattr(task, "description", "") or ""),
                plan=plan_text,
                files_touched=_touch_globs(task),
            )
        except Exception as exc:
            logger.warning(
                "lesson_prune_skip",
                task_id=str(getattr(task, "id", "")),
                error=str(exc),
            )
            return None


class ClaimGuardsMixin(_Base):
    """The guards every claim verb runs: already-active and paused, sequence
    and dependency holds, the lane barrier, the project budget, and project
    access with its DevOps and org-wide exemptions."""

    # Org-wide actors exempt from the project access rule, straight from the
    # spec's _ORG_WIDE_ROLES. Devops is subtracted: its cross-cell grant is
    # flag-gated in _devops_lane_exempt, and unconditional membership here
    # would silently un-gate it.
    _ORG_WIDE_EXEMPT_ROLES: ClassVar[frozenset[str]] = frozenset(
        role.value for role in spec_module._ORG_WIDE_ROLES - {spec_module.Role.DEVOPS}
    )

    async def _run_claim_guards(
        self,
        *,
        agent_id: UUID,
        task: Any,
        role_str: str | None = None,
        skip_dev_guards: bool = False,
        check_project_budget: bool = False,
        check_agent_access: bool = False,
    ) -> Envelope | None:
        """Run concurrency-invariant claim guards. Returns rejection or None.

        Scope: only system-level concurrency invariants the lifecycle spec
        does NOT model. Role/state/task_type checks now route through
        ``spec.can_invoke_action`` (CLAIM_RULES + ActionSpec.allowed_task_types)
        in the verb's spec gate; the former role-typed and
        pm_cannot_execute_code guards have been deleted.

        ``role_str`` selects whether the single-active-task guards apply: a PM
        coordinator (``_COORDINATOR_ROLES``) is exempt from ``already_active`` /
        ``paused`` (it holds many roots in parallel by design) but the sequence
        guard ``unmet_dependency`` still applies to everyone. A ``None`` role
        keeps the full guards (safe default for non-PM callers).

        ``skip_dev_guards`` skips the dev-only guards (``already_active`` /
        ``paused`` / ``_lane_claim_guard``) for a non-transitioning inspection
        claim — a pr_reviewer claiming an awaiting_pr_review gate task does not
        start work, so the single-active-task / code-lane invariants that gate a
        developer starting a code task do not apply (the dependency guard still
        runs). See ``claim_gate_review`` (#192).

        ``check_project_budget`` (default False — explicit opt-in, not
        inferred from role/verb) scopes the project monthly-budget guard to
        genuinely work-STARTING claims: ``i_will_work_on`` / ``i_will_plan``
        pass True. A project at cap still has non-negotiable in-flight work
        to finish — QA's ``claim_review``, the PR gate's
        ``claim_gate_review``, ``claim_doc_task``, and inbound
        ``claim_pr_review`` all pass False (the default) so reviewing/
        documenting/merging what's already been paid for never wedges behind
        an exhausted cap whose incremental cost is negligible next to the
        sunk spend.

        ``check_agent_access`` (same default-False work-STARTING opt-in)
        scopes the project agent-access guard to the same two call sites —
        it is the enforcement chokepoint for the project's
        ``allowed_agents`` restriction: a same-cell agent not on the list
        is refused before any task-status mutation.

        Pre-gateway location: _helpers.py:124-204.
        """
        if not skip_dev_guards and role_str not in self._COORDINATOR_ROLES:
            in_progress = await self.task.list_in_progress_for_agent(agent_id)
            if guard := already_active_guard(in_progress, task.id):
                return guard
            paused = await self.task.list_paused_for_agent(agent_id)
            if guard := paused_tasks_guard(paused, task.id):
                return guard
        if guard := await self._sequencing_claim_guard(task):
            return guard
        if guard := await self._opt_in_claim_guards(
            task,
            agent_id=agent_id,
            check_project_budget=check_project_budget,
            check_agent_access=check_agent_access,
        ):
            return guard
        if skip_dev_guards:
            return None
        return await self._lane_claim_guard(task)

    async def _opt_in_claim_guards(
        self,
        task: Any,
        *,
        agent_id: UUID,
        check_project_budget: bool,
        check_agent_access: bool,
    ) -> Envelope | None:
        """The two opt-in, work-STARTING-only guards in one call — the
        project monthly-budget guard and the project agent-access guard.
        Extracted from ``_run_claim_guards`` (xenon return-count budget).
        """
        if check_project_budget and (
            guard := await self._project_budget_claim_guard(task)
        ):
            return guard
        if check_agent_access and (
            guard := await self._agent_access_claim_guard(task, agent_id)
        ):
            return guard
        return None

    async def _agent_access_claim_guard(
        self, task: Any, agent_id: UUID
    ) -> Envelope | None:
        """Refuse claim when the project's access rule denies the agent.

        Enforces ``ProjectService.check_agent_access`` — the services-layer
        rule (project exists, assigned cell matches, optional
        ``allowed_agents`` list; None = whole cell passes) — at the
        agent-to-project grant chokepoint, so a restriction set via
        POST /projects/{id}/access/{agent_id} is actually enforced. The
        deny decision comes solely from that rule; this helper only
        resolves its inputs. Two carve-outs run before the rule — the
        flag-gated devops lane and the unconditional org-wide roles
        (``_ORG_WIDE_EXEMPT_ROLES``). Inert when the deps lack a project
        service (existing tests), the task has no project (branchless
        coordination root), or the agent view carries no usable team —
        mirroring ``_sequence_claim_guard``'s mock-safety guard.
        """
        if self._deps.project is None:
            return None
        project = getattr(task, "project", None)
        if project is None or getattr(project, "id", None) is None:
            return None
        agent = await self.task.agent_for(agent_id)
        if agent is None or not isinstance(getattr(agent, "team", None), str):
            return None
        denied, reason = await self._project_access_denied(task, agent_id, agent)
        if not denied:
            return None
        return agent_access_denied_guard(
            task, project.id, agent_id, False, reason=reason
        )

    async def _project_access_denied(
        self, task: Any, agent_id: UUID, agent: Any
    ) -> tuple[bool, str]:
        """The single project-access verdict, shared by the claim guard and
        the give_me_work / pm_give_me_work offer pre-filter so the two can
        never drift (the offer-then-reject lesson: the drop predicate wraps
        the EXACT claim-time refusal).

        Returns ``(denied, reason)`` with reason "assigned_cell" (a
        cross-cell actor the rule refuses) or "allowed_agents" (a same-cell
        agent left off the list). Exempt or unverifiable cases return
        (False, "unknown"): no project service, branchless task, non-string
        or invalid team, the flag-gated devops lane, or an org-wide role.
        """
        project = getattr(task, "project", None)
        if project is None or getattr(project, "id", None) is None:
            return False, "unknown"
        # The assignee passthrough shares the exempt-roles short-circuit: an
        # explicit assignment IS authorization. The coordinator that set
        # ``assigned_to`` deliberately routed this work to this agent, so a
        # cross-cell slice (panel work for the frontend cell on the
        # backend-assigned roboco-api project) must not be refused at
        # claim/offer time. Without this carve-out the assignment wedged:
        # fe-pm's i_will_plan was refused not_authorized four times, it
        # escalated to main-pm five times, blocked all three roots itself,
        # and the whole frontend cell starved (2026-09-27). This passes only
        # the task's own assignee — the POST /access grant surface is
        # unchanged and still cannot widen an assigned cell.
        if (
            self._devops_lane_exempt(agent)
            or self._org_wide_exempt(agent)
            or getattr(task, "assigned_to", None) == agent_id
        ):
            return False, "unknown"
        team = agent.team
        if not isinstance(team, str):
            return False, "unknown"
        try:
            team_enum = Team(team)
        except ValueError:
            return False, "unknown"
        has_access = await self.project.check_agent_access(
            project.id, agent_id, team_enum
        )
        if has_access:
            return False, "unknown"
        reason = (
            "allowed_agents"
            if getattr(project, "assigned_cell", None) == team
            else "assigned_cell"
        )
        return True, reason

    async def _drop_access_denied(self, tasks: list[Any], agent_id: UUID) -> list[Any]:
        """give_me_work / pm_give_me_work mirror of the claim-time
        project-access guard — the offer half of the offer-then-reject
        class. Without it an agent pre-assigned a task on a project whose
        access rule refuses them was offered the task forever:
        give_me_work said i_will_work_on, the claim guard refused
        not_authorized, and the loop only ended at the in-container circuit
        breaker (the 2026-07-24 offer-then-reject incident's access-rule
        flavor; live 2026-09-27 in its coordinator shape). Shares
        ``_project_access_denied`` with the claim guard so the two cannot
        drift. Scoped to PENDING / NEEDS_REVISION — the statuses a fresh
        claim would transition; an in_progress row survives a mid-work
        revocation so the agent keeps seeing the task it holds. Inert
        without a project service or a usable agent view (mirroring the
        guard's mock-safety), and org-wide / flag-on-devops actors are
        exempt exactly as at claim time.
        """
        if not tasks or self._deps.project is None:
            return tasks
        agent = await self.task.agent_for(agent_id)
        if agent is None or not isinstance(getattr(agent, "team", None), str):
            return tasks
        if self._devops_lane_exempt(agent) or self._org_wide_exempt(agent):
            return tasks
        offerable: list[Any] = []
        for t in tasks:
            if (
                str(getattr(t, "status", "")) in ("pending", "needs_revision")
                and (await self._project_access_denied(t, agent_id, agent))[0]
            ):
                continue
            offerable.append(t)
        return offerable

    @staticmethod
    def _devops_lane_exempt(agent: Any) -> bool:
        """Flag-gated devops exemption from the project access rule.

        The floating devops author works ANY project's clone, so the
        project-level assigned-cell rule must exempt it exactly like
        TaskService's team-level exemption does (found by the e2e devops
        gate arc: the task-level carve-out alone wedged every
        delegation-lane claim). Off => the cell rule applies — nothing
        spawns devops-1 with the flag off anyway.
        """
        raw_role = getattr(agent, "role", None)
        if raw_role is None:
            return False
        role = raw_role.value if hasattr(raw_role, "value") else raw_role
        if role != spec_module.Role.DEVOPS.value:
            return False
        from roboco.config import settings

        return settings.devops_enabled

    @staticmethod
    def _org_wide_exempt(agent: Any) -> bool:
        """Unconditional org-wide exemption from the project access rule.

        The Main PM owns coordination roots on every cell's project and the
        board roles plan across cells by design — lifecycle's
        ``_ORG_WIDE_ROLES`` is the single source (the spec team-match gate
        and TaskService's claim-time management-role exemption already
        carve them out; the chokepoint guard just missed them, wedging
        every root claim behind the assigned-cell rule — live 2026-09-27:
        main-pm unable to start any cell-project root). Devops is handled
        by ``_devops_lane_exempt`` instead: its grant is flag-gated.
        """
        raw_role = getattr(agent, "role", None)
        if raw_role is None:
            return False
        role = raw_role.value if hasattr(raw_role, "value") else raw_role
        return role in ClaimGuardsMixin._ORG_WIDE_EXEMPT_ROLES

    async def _sequencing_claim_guard(self, task: Any) -> Envelope | None:
        """Both halves of the claim-time sequencing bar in one call — an
        unmet dependency or a same-parent sequence hold — mirroring
        ``TaskService._claim_blocked_by_sequencing``'s own composition.
        Collapsed into one ``_run_claim_guards`` return so the xenon
        return-statement budget holds.
        """
        if guard := await self._dependency_claim_guard(task):
            return guard
        return await self._sequence_claim_guard(task)

    async def _sequence_claim_guard(self, task: Any) -> Envelope | None:
        """Refuse claim while a same-parent sibling with a lower sequence is
        still non-terminal — surfaced as a clean, named ``sequence_held``
        BEFORE the composed claim verb runs (see ``sequence_held_guard``).

        Runs for every ``_run_claim_guards`` caller (``i_will_work_on`` /
        ``i_will_plan``, both the fresh-claim and stuck-``claimed``-recovery
        paths), so both the PENDING and NEEDS_REVISION reclaim routes agree
        with ``TaskService.claim()``'s own sequence bar instead of it
        surfacing deep inside the verb runner as a misdiagnosed
        "concurrent transition". ``isinstance(..., str)`` keeps this inert
        under partial test mocks (an unstubbed AsyncMock method returns a
        truthy mock object, not ``None`` or a real string) — mirrors
        ``_pending_not_lane_held``'s identical mock-safety guard.
        """
        blocked_by = await self.task.sequence_hold_reason(task)
        if not isinstance(blocked_by, str):
            return None
        return sequence_held_guard(task, blocked_by)

    async def _dependency_claim_guard(self, task: Any) -> Envelope | None:
        """Refuse claim while the task has non-terminal dependencies.

        Extracted from ``_run_claim_guards`` (xenon return-count budget).
        """
        dep_ids = list(task.dependency_ids or [])
        if not dep_ids:
            return None
        unmet = await self.task.unmet_dependency_ids(dep_ids)
        guard = unmet_dependency_guard(task, unmet)
        if guard is None:
            return None
        # Re-check before mutating: the read above is an unlocked SELECT, and
        # an upstream dependency may have reached a terminal state
        # (completed/cancelled) in the microseconds between that read and now.
        # Dependencies are monotonic — unmet -> met only, terminal states never
        # reopen — so a fresh read that now finds them met stays met, and the
        # task can proceed. Releasing it anyway would needlessly clear its
        # branch + abandon its WorkSession and bounce the assignee, only for
        # the dependency-completion re-dispatch to re-dispatch + re-claim it a
        # moment later. Skip the release and let the caller proceed (return
        # None). The cross-task residual window (upstream completes between
        # this re-check and the release below) is not closable by a row lock
        # on the dependent — but the re-check narrows the window from [first
        # read -> release] to [re-check -> release] and, in the common case,
        # the first read already sees met (no guard).
        fresh_unmet = await self.task.unmet_dependency_ids(dep_ids)
        if not fresh_unmet:
            return None
        # Still unmet — park the dependency-gated task back to pending so the
        # orchestrator stops respawning its assignee (the respawn loop targets
        # only claimed/in_progress) and the dispatch dependency filter holds it
        # until the upstream completes. No-op unless the task is currently
        # claimed/in_progress.
        await self.task.release_dependency_blocked_claim(task.id)
        return guard

    async def _project_budget_claim_guard(self, task: Any) -> Envelope | None:
        """Refuse claim once the task's project has spent its monthly cap.

        Inert unless ``ROBOCO_TASK_BUDGETS_ENABLED`` is on AND the task's
        project has ``monthly_budget_usd`` set — a task with no project (a
        branchless coordination root) or a project with no cap never even
        reaches the spend query.
        """
        from roboco.config import settings as _settings

        if not _settings.task_budgets_enabled:
            return None
        project = getattr(task, "project", None)
        if project is None:
            return None
        monthly_budget_usd = getattr(project, "monthly_budget_usd", None)
        if monthly_budget_usd is None:
            return None
        month_spend_usd = await self.task.project_month_spend_usd(project.id)
        return project_budget_exceeded_guard(task, monthly_budget_usd, month_spend_usd)

    async def _budget_unblock_guard(self, t: Any) -> Envelope | None:
        """Refuse ``unblock`` on a budget-blocked task while still over cap.

        Inert unless ``ROBOCO_TASK_BUDGETS_ENABLED`` is on AND the task
        carries the ``BUDGET_BLOCKED`` marker (stamped by the orchestrator's
        task-budget sweep at breach time — see
        ``AgentOrchestrator._handle_task_budget_breach``); a task blocked for
        any other reason (dependency, manual escalation) never reaches the
        spend query. Clears the marker on success so a later, unrelated block
        on the same task is never mistaken for a stale budget breach.
        """
        from roboco.config import settings as _settings
        from roboco.foundation.policy.agent_loop import effective_task_budget_usd

        if not _settings.task_budgets_enabled or not markers.is_budget_blocked(t):
            return None
        spend_usd = await self.task.task_spend_usd(t.id)
        cap_usd = effective_task_budget_usd(t)
        # No explicit budget = no cap: the breach that stamped the marker was
        # since resolved by clearing the budget field — unblock proceeds.
        if cap_usd is not None and spend_usd >= cap_usd:
            return Envelope.invalid_state(
                message=(
                    f"task {t.id} is still over its cost budget: "
                    f"${cap_usd:,.2f} cap, ${spend_usd:,.2f} spent."
                ),
                remediate=(
                    "raise the task's Budget (USD) field (or the project's "
                    "Monthly Budget if that's the actual cap) before calling "
                    "unblock again"
                ),
            )
        markers.clear_budget_blocked(t)
        return None

    async def _lane_claim_guard(self, task: Any) -> Envelope | None:
        """Refuse a code leaf behind an earlier open same-assignee sibling.

        The out-of-order start wedge: a later PR cut from a base that predates
        the earlier sibling's unmerged changes. CODE-only predicate ->
        coordinator PMs are inert. Fail-closed on lookup error so a DB hiccup
        never lets an out-of-order claim through.
        """
        try:
            lane_held = await self.task.has_earlier_incomplete_code_sibling(task)
        except Exception:
            return Envelope.invalid_state(
                message=(
                    f"lane order check failed for task {task.id}; retry give_me_work."
                ),
                remediate="call give_me_work() to re-fetch available work",
            )
        if lane_held is not True:
            return None
        await self.task.release_dependency_blocked_claim(task.id)
        return Envelope.invalid_state(
            message=(
                f"task {task.id} waits behind an earlier open task in your "
                "code lane; start that one first."
            ),
            remediate="call give_me_work() to pick up the earlier task",
        )


class ClaimPlanMixin(_Base):
    """The claim-plan-start sequence ``i_will_work_on`` and ``i_will_plan``
    share (spec gate, claim lock, guards, run), the post-claim journal gate,
    the effective plan, and the PM's ``i_will_plan`` with its plan gates."""

    async def _handle_pm_reentry(
        self,
        *,
        ctx: _ClaimPlanStartContext,
        t: Any,
        pm_agent_id: UUID,
        task_id: UUID,
        role_str: str,
        briefing: dict[str, Any],
    ) -> Envelope | None:
        """Handle three distinct re-entry contracts for i_will_plan.

        Idempotent heartbeat: the PM already owns the task in in_progress —
        touch the heartbeat and return OK without re-running the spec gate.
        This is the crash-recovery path: a PM container that respawns after a
        mid-run crash re-calls i_will_plan with thin args ("resume") and must
        receive OK so it can proceed from where it left off.

        Crash-recovery claim: task is stuck in claimed after a crash — skip
        re-claim (claimed is not a valid source for the claim transition) and
        run set_plan+start to complete the interrupted sequence.

        Review-queue steering: the task already passed the in-path PR gate and
        is sitting in awaiting_pm_review awaiting this PM's merge decision. A
        respawned PM re-offered this task by give_me_work used to call
        i_will_plan on it, and CLAIM_RULES used to let that legally re-claim
        it — running the full composed (claim, set_plan, start) sequence and
        resetting the task to in_progress, which re-ran submit_up -> pr_pass
        -> awaiting_pm_review forever (one production task looped 11 cycles /
        37 spawns in 4h before this was caught). Steer to complete /
        request_changes instead, with NO claim and NO status change.

        Returns None when none of the three conditions applies, signalling the
        caller to continue to the normal claim-plan-start path. PLR0911 budget
        is the secondary reason this lives in a helper; the domain contract
        above is the primary one.
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
        if status == "awaiting_pm_review" and t.assigned_to == pm_agent_id:
            return Envelope.ok(
                status=status,
                task_id=str(task_id),
                next=(
                    "this task already passed the PR-review gate and is"
                    " awaiting your merge decision — do NOT re-plan or"
                    " re-submit it. Call complete(task_id) to merge the"
                    " assembled PR, or request_changes(task_id,"
                    " findings=[...]) to bounce it back with concrete"
                    " findings."
                ),
                context_briefing=briefing,
            ).with_introspection(task=t, role=role_str)
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
        elif len(sub_tasks) > _PM_SUBTASKS_MAX:
            # Over-decomposition cap (the 2026-07-07 task-quality defect):
            # a plan with >7 sub_tasks is one giant plan that should be split
            # into sibling coordination tasks. Per-item title/description
            # bounds are in _thin_subtask_hint above. We deliberately do NOT
            # ban sub_tasks on code tasks (PMs legitimately plan code-typed
            # parents into dev subtasks — the 2026-05-08 rule,
            # test_cell_pm_can_plan_code_typed_parent_via_i_will_plan) nor
            # require >=2 on roots (a single-cell root → one cell task is a
            # legitimate pass-through, test_pm_can_plan_non_code_parent) —
            # both judgment calls the gate can't make without false positives.
            missing.append("sub_tasks")
            field_hints["sub_tasks"] = (
                f"too many sub_tasks ({len(sub_tasks)}) — decompose into "
                f"at most {_PM_SUBTASKS_MAX}. If the work is genuinely "
                "larger, split it into sibling coordination tasks instead "
                "of one giant plan."
            )
        if not missing:
            return await self._plan_quality_gate(
                role_str=role_str,
                rich_plan=rich_plan,
                task=task,
                agent_id=agent_id,
                task_id=task_id,
                briefing=briefing,
            )
        return await self._emit_rejection(
            Envelope.incomplete_input(
                missing=missing,
                field_hints=field_hints,
                remediate=(
                    "re-issue i_will_plan(task_id, plan, approach, "
                    "sub_tasks=[{'title': '...', 'description': '...'}, ...]) "
                    f"with approach >= {_PM_APPROACH_MIN_LEN} and <= 800 "
                    f"chars, every sub_task description >= "
                    f"{_PM_SUBTASK_DESC_MIN_LEN} and <= "
                    f"{_PM_SUBTASK_DESC_MAX_LEN} chars, and at most "
                    f"{_PM_SUBTASKS_MAX} sub_tasks."
                ),
                context_briefing=briefing,
            ).with_introspection(task=task, role=role_str),
            agent_id=agent_id,
            task_id=task_id,
            verb="i_will_plan",
        )

    async def _plan_quality_gate(
        self,
        *,
        role_str: str,
        rich_plan: dict[str, Any] | None,
        task: Any,
        agent_id: UUID,
        task_id: UUID,
        briefing: dict[str, Any],
    ) -> Envelope | None:
        """B1 plan_quality scoring lane (decisions pilot, default-off).

        Runs ONLY after the structural counts passed: the pilot scores
        whether the decomposition is actually adequate (garbage padded to
        the 150-char minimum is invisible to counts). STRICTER-ONLY by
        construction - a LOW confident score may reject a plan the
        structural check accepted, but no verdict can ever accept a plan
        the structural check rejected (that path already returned above).
        Fail-open: pilot off/shadow/below-floor/down returns None and the
        gate behaves exactly as before.
        """
        try:
            from roboco.services.decisions.pilots import PilotMode, pilot_mode

            mode = await pilot_mode(self.task.session, "plan_quality")
        except Exception as exc:
            logger.warning("plan_quality_skip", task_id=str(task_id), error=str(exc))
            return None
        if mode is PilotMode.OFF:
            return None
        verdict = await self._plan_quality_verdict(rich_plan, task, task_id)
        if not verdict or not verdict.get("inadequate"):
            return None
        score = verdict.get("score")
        confidence = verdict.get("confidence")
        return await self._emit_rejection(
            Envelope.incomplete_input(
                missing=["plan_quality"],
                field_hints={
                    "plan_quality": (
                        "the plan passed the structural minimums but the "
                        f"plan-quality rubric scored it inadequate "
                        f"(score {score}/2, confidence {confidence}): the "
                        "approach reads as padded prose and the sub_tasks do "
                        "not decompose the work into real, distinct steps."
                    )
                },
                remediate=(
                    "rewrite the approach to describe HOW you will decompose "
                    "and route this work (no filler prose) and give every "
                    "sub_task a substantive, distinct description, then "
                    "re-issue i_will_plan(task_id, plan, approach, "
                    "sub_tasks=[{'title': '...', 'description': '...'}, ...])."
                ),
                context_briefing=briefing,
            ).with_introspection(task=task, role=role_str),
            agent_id=agent_id,
            task_id=task_id,
            verb="i_will_plan",
        )

    async def _plan_quality_verdict(
        self, rich_plan: dict[str, Any] | None, task: Any, task_id: UUID
    ) -> Any:
        """One plan_quality rubric call over the plan's approach and
        sub_tasks. None on failure (fail-open: the gate passes)."""
        from roboco.services.decisions.pilots_gateway import plan_quality

        approach = str((rich_plan or {}).get("approach") or "").strip()
        sub_tasks = list((rich_plan or {}).get("sub_tasks") or [])
        try:
            return await plan_quality(
                self.task.session,
                task_id=str(task_id),
                task_title=str(getattr(task, "title", "") or ""),
                approach=approach,
                sub_tasks=sub_tasks,
            )
        except Exception as exc:
            logger.warning("plan_quality_skip", task_id=str(task_id), error=str(exc))
            return None

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
        # in another task). check_project_budget=True: resuming i_will_work_on
        # / i_will_plan is still a work-STARTING claim. check_agent_access
        # likewise — the grant chokepoint for the project's allowed_agents
        # restriction.
        if guard := await self._run_claim_guards(
            agent_id=agent_id,
            task=t,
            role_str=role_str,
            check_project_budget=True,
            check_agent_access=True,
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
        A PM recovering a NEEDS_REVISION coordination task is scoped by
        give_me_work routing (it only ever offers an agent its OWN assigned
        tasks), exactly as developer leaf-revisions are — not by a gateway-only
        ownership gate, which would diverge from the spec (the parity invariant
        requires gateway authorization == spec authorization).
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
        #
        # The agent-wide guards below read the agent's OTHER tasks via plain
        # (unlocked) SELECTs, and claim()'s FOR UPDATE locks only the TARGET
        # row — neither serializes two concurrent claims by the SAME agent on
        # TWO DIFFERENT pending tasks. A transaction-scoped advisory lock keyed
        # by agent_id, acquired here BEFORE the guard reads and held until the
        # request transaction commits, makes the second concurrent claim's read
        # see the first's committed in_progress task and get rejected. The
        # in-process asyncio.Lock is lost on orchestrator-restart split-brain,
        # so this is the only DB-level guarantee of the one-task-per-agent
        # invariant. Coordinators (cell_pm / main_pm) are exempt — matching the
        # already_active/paused guard exemption below — so a PM can still plan +
        # delegate many roots in parallel (the PM coordinator concurrency
        # feature). A hash collision only causes benign false serialization.
        if role_str not in self._COORDINATOR_ROLES:
            await self.task.acquire_claim_lock(ctx.agent_id)
        if guard := await self._run_claim_guards(
            agent_id=ctx.agent_id,
            task=t,
            role_str=role_str,
            check_project_budget=True,
            check_agent_access=True,
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
        """Execute composed (claim, set_plan) via the verb runner, then
        provision the claim (branch, upstream-base inherit, work session)
        outside any savepoint, then start the task.

        Caller has already validated all gates. Translates runner
        exceptions and ``None`` returns into invalid_state envelopes
        so the agent gets a remediation instead of a 500. Shared
        between ``i_will_work_on`` and ``i_will_plan``.

        The verb runner's ``_do_claim`` calls ``claim(provision=False)``
        inside ``_run_composed_actions``'s savepoint: claim/set_plan land
        or roll back together as one unit, with no branch creation inside
        that savepoint. ``start`` runs only after ``provision_claim``: the
        claimed -> in_progress transition carries a branch gate that
        refuses a branchless task, so it cannot sit inside a savepoint the
        branch commit has to follow (the E2E smoke caught the opposite
        order as "Cannot start work: no branch assigned").
        Once ``run_intent`` returns successfully,
        ``TaskService.provision_claim`` commits that unit durably
        (releasing the claim row lock) BEFORE its own network git work,
        with no savepoint left to undo it on failure; that commit lives
        inside ``provision_claim`` itself, not here, so this method never
        touches ``self.task.session`` directly (every gateway unit test
        mocks ``self.task`` as a whole service and would otherwise need
        its own raw-session plumbing for a call this method has no other
        reason to make). A provisioning failure durably reverts the
        claim/plan fields (``TaskService``'s own revert-and-commit,
        the same revert a branch-creation failure always triggered) and
        surfaces the same "verb runner failed" rejection a savepoint
        failure above already does.
        """
        t, briefing, role_str = ctx.task, ctx.briefing, ctx.role_str
        verb_name = ctx.verb_name
        runner = self._verb_runner()
        try:
            t = await runner.run_intent(verb_name, t, agent, spec_ctx)
        except Exception as exc:
            return await self._emit_rejection(
                self._runner_failed_envelope(exc, t, briefing, role_str),
                agent_id=ctx.agent_id,
                task_id=ctx.task_id,
                verb=verb_name,
            )
        if t is None:
            # A composed atomic action returned None (an ownership/state
            # mismatch the spec gate could not see). Surface as invalid_state
            # so the agent gets a remediation rather than a 500.
            return await self._emit_rejection(
                self._step_failed_envelope("claim/set_plan", ctx, briefing, role_str),
                agent_id=ctx.agent_id,
                task_id=ctx.task_id,
                verb=verb_name,
            )
        try:
            await self.task.provision_claim(ctx.task_id, ctx.agent_id)
            # claimed -> in_progress needs the branch provision_claim just
            # committed, so start runs here, never inside the savepoint.
            t = await self.task.start(ctx.task_id, ctx.agent_id)
        except Exception as exc:
            return await self._emit_rejection(
                self._runner_failed_envelope(exc, t, briefing, role_str),
                agent_id=ctx.agent_id,
                task_id=ctx.task_id,
                verb=verb_name,
            )
        if t is None:
            return await self._emit_rejection(
                self._step_failed_envelope("start", ctx, briefing, role_str),
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

    async def _post_claim_journal_gate(
        self,
        verb: str,
        agent_id: UUID,
        task_id: UUID,
        envelope: Envelope,
    ) -> Envelope:
        """Apply the claim-time journal tracing gate AFTER a successful claim.

        Pre-gateway parity (spec §11 P1, P3): the claim, set_plan, start
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
        if verb == "i_will_plan":
            # Write-then-gate (see _ensure_pm_decision): the plan i_will_plan
            # just persisted IS the PM's planning rationale. The prompted order
            # note(scope='decision') -> i_will_plan could never work, since
            # note refuses a write before the claim exists, and PMs idled on
            # the resulting tracing_gap instead (live 2026-10-28: main-pm
            # paused four fresh roots in ten minutes this way).
            await self._ensure_pm_decision(
                agent_id, task_id, self._planning_rationale(t)
            )
        gap = await self._check_claim_journal_at_claim(verb, agent_id, task_id, t)
        return gap if gap is not None else envelope

    @staticmethod
    def _planning_rationale(t: Any) -> str | None:
        """The PM's planning rationale off the task's persisted plan: a rich
        plan's ``approach``, else a plain plan's ``text``."""
        plan = getattr(t, "plan", None)
        if not isinstance(plan, dict):
            return None
        rationale = plan.get("approach") or plan.get("text")
        return str(rationale) if rationale else None

    async def _check_claim_journal_at_claim(
        self, verb: str, agent_id: UUID, task_id: UUID, t: Any
    ) -> Envelope | None:
        """Post-claim tracing gate for i_will_work_on / i_will_plan.

        Pre-gateway parity (spec §11 P1, P3): developers wrote a
        journal:note on every claim; PMs wrote a journal:decision on
        plan. The check runs AFTER the claim, set_plan, start
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
        if any(rich_plan.get(k) for k in ClaimPlanMixin._RICH_PLAN_FIELDS):
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
        the composed (claim, set_plan) sequence is wrapped in a
        savepoint by the runner so a mid-sequence failure rolls back
        the DB; start follows the branch provisioning.

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
        briefing = await self._briefing_for(
            pm_agent_id, task_id, task=t, include_ac_coverage=True, full=True
        )
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
            agent_team=str(agent.team) if agent is not None and agent.team else None,
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
            ctx=ctx,
            t=t,
            pm_agent_id=pm_agent_id,
            task_id=task_id,
            role_str=role_str,
            briefing=briefing,
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


class BranchSyncMixin(_Base):
    """Keeping a branch current: ``sync_branch``, the behind-base gate,
    freshening an assembled branch before a submit, and the mid-work
    staleness notice."""

    async def _assembled_submit_guards(
        self, t: Any, task_id: UUID, verb: str
    ) -> tuple[Envelope | None, int | None]:
        """Assembly integrity (#11) then behind-base auto-sync (B2) for the
        assembled PM submits. Base resolution fails open (a malformed parent
        ref must not strand the submit — the merge layer keeps its checks).

        Returns ``(rejection_envelope, ahead)`` — see
        ``_freshen_assembled_branch`` for what ``ahead`` means and when it is
        ``None``. The caller threads a non-``None`` ``ahead`` into
        ``VerbRunner.run_intent`` so the PR-waiver check reuses this same
        probe's fetch instead of running its own.
        """
        guard = await self._assembly_integrity_guard(t, verb=verb)
        if guard is not None:
            return guard, None
        try:
            base_branch = await resolve_parent_branch(t, self.task)
        except Exception as exc:
            logger.warning(
                "assembled_freshen_base_skip",
                task_id=str(task_id),
                error=str(exc),
            )
            base_branch = ""
        return await self._freshen_assembled_branch(
            t, base_branch=base_branch, verb=verb
        )

    async def _assembly_integrity_guard(self, t: Any, *, verb: str) -> Envelope | None:
        """Refuse the assembled submit when a completed child's work is missing.

        Live incident #11: a completed revert subtask's commit never landed on
        the assembled cell branch, so the review gate re-flagged the exact
        violation the revert fixed. Patch-equivalence check (rebase-safe);
        fail-open on git errors — the review gate remains the backstop.
        """
        if not getattr(t, "branch_name", None):
            return None
        try:
            missing = list(await self.git.unmerged_child_commits(t) or [])
        except Exception as exc:
            logger.warning("assembly_integrity_skip", task_id=str(t.id), error=str(exc))
            return None
        if not missing:
            return None
        listing = "; ".join(
            f"{m['title']} ({m['task_id']}, {m['unmerged']} commit(s))" for m in missing
        )
        return Envelope.invalid_state(
            message=(
                f"{verb} refused: completed subtask work is MISSING from the "
                f"assembled branch — {listing}"
            ),
            remediate=(
                "merge each listed child's branch into the assembled branch "
                "(their completion recorded a merge that is not reflected on "
                "origin), then re-submit. Submitting now re-reviews a diff "
                "that silently drops finished work."
            ),
            context_briefing={},
        )

    async def _freshen_assembled_branch(
        self, t: Any, *, base_branch: str, verb: str
    ) -> tuple[Envelope | None, int | None]:
        """Behind-base auto-sync for the assembled PM submits (B2).

        Re-submitting a cell/root head whose base moved re-reviews stale work
        and ping-pongs needs_revision ↔ awaiting_pr_review (live 2026-07-02).
        Every child is terminal at submit time, so rebasing the assembled
        branch onto its base is safe; ``sync_task_branch`` pushes only the
        HEAD branch (master/main are never written). Fail-open on probe/sync
        errors — the PR/merge layer keeps its own behind checks — but a rebase
        CONFLICT is a hard reject naming the files, and a DIVERGED branch (the
        PM's own clone and origin each carry unique commits) is likewise a
        hard reject, so the PM routes a conflict-resolution revision instead
        of re-submitting blind.

        Returns ``(rejection_envelope, ahead)``. ``ahead`` is this probe's own
        ``is_behind_base`` ahead-count, handed back to the caller so the
        downstream PR-waiver check (``VerbRunner._maybe_waive_pr_creation``)
        can reuse it instead of a second ``git fetch origin`` for the same
        branch/base pair — but only when it is still trustworthy: ``None`` no
        branch/base to probe, the probe itself failed, OR a rebase ran. A
        rebase moves the branch tip onto a new base and can drop now-empty
        commits, so the pre-rebase ahead count is not guaranteed to match the
        post-rebase branch — the waiver check must re-fetch fresh in that
        case (the one deliberate second fetch this fix leaves in place).
        """
        if not getattr(t, "branch_name", None) or not base_branch:
            return None, None
        try:
            behind, ahead = await self.git.is_behind_base(t, base_branch=base_branch)
        except Exception as exc:
            logger.warning("assembled_freshen_skip", task_id=str(t.id), error=str(exc))
            return None, None
        if behind <= 0:
            return None, ahead
        try:
            result = await self.git.sync_task_branch(t, base_branch=base_branch)
        except Exception as exc:
            logger.warning(
                "assembled_freshen_sync_failed", task_id=str(t.id), error=str(exc)
            )
            return None, None
        reject = self._freshen_rejection_for(
            result, behind=behind, base_branch=base_branch, verb=verb
        )
        if reject is not None:
            return reject, None
        logger.info(
            "assembled_branch_freshened",
            task_id=str(t.id),
            verb=verb,
            base_branch=base_branch,
            behind=behind,
            status=result.get("status"),
        )
        # A rebase just ran — see the ahead-staleness note in the docstring
        # above. Return None so the caller's downstream waiver check fetches
        # its own fresh ahead-count against the just-rebased branch.
        return None, None

    @staticmethod
    def _freshen_rejection_for(
        result: dict[str, Any], *, behind: int, base_branch: str, verb: str
    ) -> Envelope | None:
        """Hard-reject shapes for ``_freshen_assembled_branch``'s sync result.

        Conflicts and divergence both mean the auto-sync could not safely
        reconcile the branch on its own — extracted so the caller's
        return-statement count stays under the PLR0911 budget (mirrors
        ``_sync_branch_preflight_rejection``).
        """
        status = result.get("status")
        if status == "conflicts":
            files = ", ".join(result.get("files") or []) or "unknown files"
            return Envelope.invalid_state(
                message=(
                    f"{verb} refused: the assembled branch was {behind} "
                    f"commit(s) behind its base '{base_branch}' and the "
                    f"auto-rebase hit conflicts in: {files}"
                ),
                remediate=(
                    "the base moved under this branch and the conflict needs a "
                    "human-quality merge: delegate a conflict-resolution "
                    "revision for the listed files, then re-submit. Do NOT "
                    "re-submit unchanged — the review gate will fail the same "
                    "stale diff again."
                ),
                context_briefing={},
            )
        if status == "diverged":
            return Envelope.invalid_state(
                message=(
                    f"{verb} refused: the assembled branch has DIVERGED from "
                    f"its origin copy ({result.get('local_only', '?')} "
                    f"local-only commit(s), {result.get('origin_only', '?')} "
                    "origin-only commit(s)) — this workspace and origin each "
                    "carry work the other lacks"
                ),
                remediate=(
                    "a human must reconcile the two histories by hand (fetch, "
                    "inspect both tips, merge or rebase deliberately) before "
                    "re-submitting — auto-sync refuses to guess which side to "
                    "keep"
                ),
                context_briefing={},
            )
        return None

    async def _behind_base_gate(self, ctx: _IAmDoneContext) -> Envelope | None:
        """Refuse i_am_done when the task branch has fallen behind its base.

        A sibling's PR merged into the parent branch while this dev worked →
        the head lacks that merged work, so the assembled PR can't merge
        cleanly and a sibling's changes go missing from this branch (the
        2026-06-27 out-of-order dev-task break). The dev must ``sync_branch``
        (the gate-level rebase verb) before submitting. Fail-open on git /
        base-resolution error so a flaky fetch can't strand a task at the
        submit gate — the merge layer has its own behind checks. Skipped for
        branchless coordination roots (no ``branch_name``) and when the base
        resolves to a protected branch (master/main) or a ``-``-prefixed ref.
        Runs after ``_ensure_branch_pushed`` so origin reflects the pushed head.
        """
        t = ctx.task
        if not getattr(t, "branch_name", None):
            return None
        try:
            base_branch = await resolve_parent_branch(t, self.task)
        except Exception as exc:
            logger.warning("behind_base_skip", task_id=str(ctx.task_id), error=str(exc))
            return None
        if (
            not base_branch
            or base_branch.startswith("-")
            or base_branch in ("master", "main")
        ):
            return None
        try:
            behind, _ahead = await self.git.is_behind_base(
                t, base_branch=base_branch, actor_agent_id=ctx.agent_id
            )
        except Exception as exc:
            logger.warning("behind_base_skip", task_id=str(ctx.task_id), error=str(exc))
            return None
        if behind > 0:
            return Envelope.invalid_state(
                message=(
                    f"your branch is {behind} commit(s) behind its base "
                    f"'{base_branch}' — a sibling's PR merged into the parent "
                    f"branch while you worked; your branch is missing that work"
                ),
                remediate=(
                    "call sync_branch(task_id) to rebase your branch onto its base "
                    "through the gate, then i_am_done again. do NOT submit a branch "
                    "that is behind its base — the PR cannot merge cleanly"
                ),
                context_briefing=ctx.briefing,
            )
        return None

    async def _branch_staleness_advisory(self, agent_id: UUID, t: Any) -> None:
        """B39 branch_staleness mid-work advisory (decisions pilot,
        default-off).

        ``_behind_base_gate`` only fires at submit, fail-open; the
        2026-06-27 incident class (a sibling's merge leaving the branch
        N behind) otherwise surfaces hours later. This mid-work path
        reuses the gate's exact behind-count/base-resolution data and
        asks the pilot whether the developer should hear about it NOW.
        MAY ONLY ADD a notification to the dev: it never blocks, never
        rebases, and below confidence it does nothing. Fail-open on any
        git/base error (the submit gate remains the backstop).
        """
        if not getattr(t, "branch_name", None):
            return
        state = await self._branch_staleness_state(agent_id, t)
        if state is None:
            return
        base_branch, behind, ahead = state
        from roboco.services.decisions.pilots_gateway import branch_staleness

        try:
            verdict = await branch_staleness(
                self.task.session,
                task_id=str(t.id),
                base_branch=base_branch,
                behind=behind,
                ahead=ahead,
                changed_files=_touch_globs(t),
            )
        except Exception as exc:
            logger.warning("branch_staleness_skip", task_id=str(t.id), error=str(exc))
            return
        if verdict is None:
            return
        await self._notify_branch_stale(agent_id, t, base_branch, behind, verdict)

    async def _branch_staleness_state(
        self, agent_id: UUID, t: Any
    ) -> tuple[str, int, int] | None:
        """The gate's behind-count data for the B39 advisory: a resolved
        non-protected base plus ``(behind, ahead)``, or None when the
        branch is fresh, the base is protected, resolution fails, or the
        pilot mode gate says stay out (fail-open, mirrors
        ``_behind_base_gate``'s skip posture)."""
        try:
            from roboco.services.decisions.pilots import PilotMode, pilot_mode

            mode = await pilot_mode(self.task.session, "branch_staleness")
        except Exception:
            return None
        if mode is PilotMode.OFF:
            return None
        try:
            base_branch = await resolve_parent_branch(t, self.task)
            protected = (
                not base_branch
                or base_branch.startswith("-")
                or base_branch in ("master", "main")
            )
            if not protected:
                behind, ahead = await self.git.is_behind_base(
                    t, base_branch=base_branch, actor_agent_id=agent_id
                )
                if behind > 0:
                    return base_branch, int(behind), int(ahead)
        except Exception as exc:
            logger.warning("branch_staleness_skip", task_id=str(t.id), error=str(exc))
        return None

    async def _notify_branch_stale(
        self,
        agent_id: UUID,
        t: Any,
        base_branch: str,
        behind: int,
        verdict: dict[str, Any],
    ) -> None:
        """The additive action of B39: an ack-required note TO THE DEV
        about the moved base. Best-effort; never blocks give_me_work."""
        try:
            from roboco.services.notification import NotificationService

            agent = await self.task.agent_for(agent_id)
            to_slug = str(getattr(agent, "slug", "") or "") or str(agent_id)
            risk = verdict.get("risk_score")
            confidence = verdict.get("confidence")
            body = (
                f"[decisions] Branch staleness: your branch on task "
                f"{str(t.id)[:8]} is {behind} commit(s) behind its base "
                f"'{base_branch}' and the new base commits carry conflict "
                f"risk (risk {risk}/2, confidence {confidence}). Consider "
                "sync_branch(task_id=...) mid-work instead of discovering "
                "it at the submit gate."
            )
            await NotificationService().send_ack_notification(
                from_agent="system",
                to_agent=to_slug,
                body=body,
                task_id=str(t.id),
            )
        except Exception as exc:
            logger.warning(
                "failed to send branch-staleness notification",
                task_id=str(getattr(t, "id", "")),
                error=str(exc),
            )

    async def sync_branch(
        self, agent_id: UUID, task_id: UUID, stash: bool = False
    ) -> Envelope:
        """Rebase the caller's task branch onto its current base THROUGH the gate.

        Raw shell git is denied to agents (the ``Bash(git:*)`` base deny), so a
        developer whose branch fell behind its base — a sibling's PR merged
        into the parent branch while they worked — had no gate-level rebase,
        only the CEO/PM-only ``/rebase`` HTTP route. ``sync_branch`` is that
        gate verb: it resolves the task's base via
        ``merge_chain.resolve_parent_branch``, guards against rebasing into a
        protected branch, then rebases + force-pushes (with-lease) via
        ``GitService.sync_task_branch``. Git-only (no DB state change), like
        ``open_pr``; the spec gate checks role + ownership, then the handler
        guards branch + base, then runs the git op. Conflicts abort the rebase
        (no force-push) and return the conflicted files — resolve by hand,
        commit, then sync_branch again.

        ``stash=True`` auto-stashes uncommitted changes (tracked + untracked)
        before rebasing and pops them back after, instead of refusing
        DIRTY_WORKSPACE — the dev-facing dead end where the prescribed fix
        (stage/commit) required a raw ``git`` agents are denied.
        """
        t = await self.task.get(task_id)
        briefing = await self._briefing_for(agent_id, task_id, task=t)
        agent = await self.task.agent_for(agent_id)
        role_str = str(agent.role) if agent is not None else "developer"
        # Preflight: not_found / unknown-role / spec-gate / no-branch /
        # protected-base. Returns the rejection (None when all clear) AND the
        # resolved base_branch the git op needs (empty when rejected).
        rejection, base_branch = await self._sync_branch_preflight_rejection(
            agent_id=agent_id,
            task_id=task_id,
            t=t,
            agent=agent,
            role_str=role_str,
            briefing=briefing,
        )
        if rejection is not None:
            return await self._emit_rejection(
                rejection, agent_id=agent_id, task_id=task_id, verb="sync_branch"
            )
        # sync_task_branch is git-only (fetch/checkout/reset or rebase) and can
        # chown-walk the workspace for tens of seconds on a slow volume. Close
        # out the read-only preflight transaction first so this request's
        # session sits outside a transaction while the git op runs, instead of
        # holding it open (and its connection idle-in-transaction) the whole
        # time.
        await self.task.session.commit()
        try:
            result = await self.git.sync_task_branch(
                t, base_branch=base_branch, actor_agent_id=agent_id, stash=stash
            )
        except Exception as exc:
            dirty = "DIRTY_WORKSPACE" in str(exc)
            remediate = (
                "your workspace has uncommitted changes; call"
                " sync_branch(stash=True) to auto-stash, rebase, and restore"
                " them, or commit(...) (omit files to stage everything) then"
                " sync_branch() again"
                if dirty
                else "the git rebase could not complete; escalate via"
                " i_am_blocked(reason='...') with the error"
            )
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"sync_branch failed: {exc}",
                    remediate=remediate,
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=agent_id,
                task_id=task_id,
                verb="sync_branch",
            )
        # Heartbeat — the agent is actively working the task.
        await self._touch(task_id)
        evidence = {
            "rebase": result,
            "base_branch": base_branch,
            "head_branch": str(t.branch_name),
        }
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=self._sync_branch_next_hint(t, result),
            evidence=evidence,
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)

    @staticmethod
    def _sync_branch_next_hint(t: Any, result: dict[str, Any]) -> str:
        """Compute the ``next`` hint for a completed ``sync_branch`` run.

        Four shapes: a genuine divergence (neither side touched, a human
        must reconcile), a rebase conflict (files listed, stash noted if one
        was taken), a clean rebase whose stash pop then conflicted (stash
        preserved, never dropped), or the plain spec default.
        """
        status = str(result.get("status", "unknown"))
        if status == "diverged":
            hint = (
                "sync_branch refused: your branch and its origin copy have "
                f"DIVERGED ({result.get('local_only', '?')} commit(s) only "
                f"in your workspace, {result.get('origin_only', '?')} only "
                "on origin) — neither side was touched. escalate via "
                "i_am_blocked(reason='...') so a human can reconcile the two "
                "histories by hand"
            )
            if result.get("stash_pop_conflict"):
                hint += (
                    " — your stashed changes were also popped back into a "
                    "conflict; the stash is preserved (not dropped), resolve "
                    "it by hand once the divergence is reconciled"
                )
            return hint
        if status == "conflicts":
            hint = (
                f"sync_branch hit conflicts on {result.get('files', [])};"
                " resolve by hand, commit(message='...'), then sync_branch again"
            )
            if result.get("stash_preserved"):
                hint += (
                    " — your stashed changes were left untouched (git stash"
                    " list); pop them by hand once the conflict is resolved"
                )
            return hint
        if result.get("stash_pop_conflict"):
            return (
                "sync_branch rebased cleanly but restoring your stashed "
                "changes conflicted; the stash is preserved (not dropped) — "
                "resolve the conflict by hand, then commit(...) and continue"
            )
        return spec_module._INTENT_VERBS["sync_branch"].next_hint(t)

    async def _sync_branch_preflight_rejection(
        self,
        *,
        agent_id: UUID,
        task_id: UUID,
        t: Any,
        agent: Any,
        role_str: str,
        briefing: dict[str, Any],
    ) -> tuple[Envelope | None, str]:
        """Role + spec-gate + branch/base guards for ``sync_branch``.

        Returns ``(rejection_envelope, base_branch)``. When all guards pass the
        rejection is ``None`` and ``base_branch`` is the resolved merge target
        the handler hands to ``GitService.sync_task_branch``; on any guard
        failure the envelope is set and ``base_branch`` is empty. Extracted so
        ``sync_branch`` stays under the PLR0911 return budget (mirrors
        ``_open_pr_preflight_rejection``).
        """
        if t is None:
            return (
                Envelope.not_found(message=f"task {task_id} not found"),
                "",
            )
        try:
            role = spec_module.Role(role_str)
        except ValueError:
            return (
                Envelope.not_authorized(
                    message=f"unknown role '{role_str}'",
                    remediate="role is not declared in the lifecycle spec",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                "",
            )
        spec_ctx = spec_module.Context(
            actor_id=agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            agent_team=str(agent.team) if agent is not None and agent.team else None,
        )
        decision = spec_module.can_invoke_intent(role, "sync_branch", t, spec_ctx)
        if not decision.allowed:
            return (
                Envelope.from_decision(decision, briefing=briefing).with_introspection(
                    task=t, role=role_str
                ),
                "",
            )
        # The task must carry a branch (claimed/in_progress) — there is nothing
        # to sync before the branch is cut, and branchless coordination roots
        # carry none.
        if not t.branch_name:
            return (
                Envelope.invalid_state(
                    message=f"task {task_id} has no branch_name to sync",
                    remediate="call i_will_work_on(task_id) first to cut a branch",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                "",
            )
        base_branch = await resolve_parent_branch(t, self.task)
        if await self._sync_base_refused(base_branch, t):
            return (
                Envelope.invalid_state(
                    message=f"resolved base branch '{base_branch}' is protected",
                    remediate=(
                        "the task's base resolved to master/main even though it"
                        " has a parent that should own the base branch — the"
                        " hierarchy resolution looks wrong; escalate via"
                        " i_am_blocked(reason='...') instead of rebasing"
                    ),
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                "",
            )
        return None, base_branch

    async def _sync_base_refused(self, base_branch: str, t: Any) -> bool:
        """Whether ``sync_branch`` must refuse rebasing onto ``base_branch``.

        ``-``-prefixed refs are refused unconditionally (argument-injection
        guard). master/main is refused only when it cannot be the task's real
        merge target — mirroring ``resolve_parent_branch``: a parentless task
        and a child of a branchless coordination parent both legitimately
        merge into the project default branch, and the rebase only ever
        force-pushes the task branch (with lease), never the base. A
        branch-bearing parent (the base should have been that branch) or an
        unresolvable parent row means the resolution went wrong — refuse.
        """
        if base_branch.startswith("-"):
            return True
        if base_branch not in ("master", "main"):
            return False
        parent_id = getattr(t, "parent_task_id", None)
        if parent_id is None:
            return False
        try:
            pid = UUID(str(parent_id))
        except ValueError:
            return True
        parent = await self.task.get(pid)
        return parent is None or bool(parent.branch_name)


class EscalationMixin(_Base):
    """``i_am_blocked`` with rate-limit parking, ``escalate_up`` and
    ``escalate_to_ceo``."""

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
        *,
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
        # ``block`` is the LAST composed action, so a ``None`` return (escalate
        # resolved no target) re-binds ``t`` to ``None``; guard the deref with
        # an invalid_state so the agent gets a retryable rejection, not a 500.
        if updated is None:
            return t, await self._emit_rejection(
                Envelope.invalid_state(
                    message=(
                        "i_am_blocked did not transition the task — no escalation "
                        "target could be resolved for your role (the PM above you "
                        "is missing or unassignable)."
                    ),
                    remediate=(
                        "re-fetch with evidence(task_id); escalate to the CEO "
                        "directly via your PM, or retry once the escalation target "
                        "is staffed."
                    ),
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
        *,
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
        #
        # An activate() failure is logged loudly, NOT bare-suppressed: the
        # probe-resume loop is tracker-driven, so a silent failure strands
        # every parked agent waiting on a provider the tracker never learned.
        if provider != "unknown":
            try:
                from roboco.services.gateway.rate_limit_tracker import (
                    RateLimitStateTracker,
                )

                await RateLimitStateTracker(provider).activate(
                    retry_after=retry_after_seconds,
                    affected_agents=affected_agents,
                )
            except Exception as exc:
                logger.error(
                    "rate_limit_tracker.activate failed; parked agents rely on"
                    " the in-memory probe fallback to resume",
                    provider=provider,
                    affected_agents=affected_agents,
                    error=str(exc),
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
            agent_team=str(agent.team) if agent is not None and agent.team else None,
            original_developer_slug=_extract_original_developer(t),
            notes=reason,
        )
        soup = self._free_text_soup(checks=(("reason", reason, 8),))
        decision = spec_module.can_invoke_intent(role, "i_am_blocked", t, spec_ctx)
        if env := self._soup_or_decision_env(soup, decision, briefing):
            return await self._emit_rejection(
                env.with_introspection(task=t, role=role_str),
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
            agent_id=agent_id,
            task_id=task_id,
            t=t,
            agent=agent,
            spec_ctx=spec_ctx,
            role_str=role_str,
            briefing=briefing,
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
        actor_slug, agent_team = actor_context_fields(me)
        spec_ctx = spec_module.Context(
            actor_id=pm_agent_id,
            actor_slug=actor_slug,
            agent_team=agent_team,
            original_developer_slug=_extract_original_developer(t),
            notes=reason,
        )
        soup = self._free_text_soup(checks=(("reason", reason, 10),))
        decision = spec_module.can_invoke_intent(role, "escalate_up", t, spec_ctx)
        if env := self._soup_or_decision_env(soup, decision, briefing):
            return await self._emit_rejection(
                env.with_introspection(task=t, role=role_str),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="escalate_up",
            )

        # Write-then-gate: the escalation reason becomes the journal:decision
        # the preflight gate requires.
        pm_decision_outcome = await self._ensure_pm_decision(
            pm_agent_id, task_id, reason
        )
        preflight = await self._escalate_up_preflight(
            pm_agent_id,
            t,
            me,
            briefing,
            role_str,
            pm_decision_outcome=pm_decision_outcome,
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
        *,
        pm_decision_outcome: PmDecisionOutcome,
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
            "escalate_up",
            pm_agent_id,
            t.id,
            t,
            pm_decision_outcome=pm_decision_outcome,
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
        actor_slug, agent_team = actor_context_fields(me)
        spec_ctx = spec_module.Context(
            actor_id=agent_id,
            actor_slug=actor_slug,
            agent_team=agent_team,
            original_developer_slug=_extract_original_developer(t),
            notes=reason,
        )
        soup = self._free_text_soup(checks=(("reason", reason, 10),))
        decision = spec_module.can_invoke_intent(role, "escalate_to_ceo", t, spec_ctx)
        if env := self._soup_or_decision_env(soup, decision, briefing):
            return await self._emit_rejection(
                env.with_introspection(task=t, role=role_str),
                agent_id=agent_id,
                task_id=task_id,
                verb="escalate_to_ceo",
            )

        # Verb-specific preflight: journal:decision presence (out of spec scope).
        # Delegates to _check_pm_decision_required which consumes
        # VERB_REQUIREMENTS["escalate_to_ceo"].
        # Verb-body preflight: journal:decision presence, then the parent-AC
        # backstop (a root may not escalate to the CEO with parent ACs that no
        # completed subtask covered — inert until coverage is declared).
        # Write-then-gate: the escalation reason becomes the journal:decision
        # the gate below requires.
        pm_decision_outcome = await self._ensure_pm_decision(agent_id, task_id, reason)
        env = await self._check_pm_decision_required(
            "escalate_to_ceo",
            agent_id,
            task_id,
            t,
            pm_decision_outcome=pm_decision_outcome,
        ) or await self._parent_acs_covered_envelope(
            agent_id, task_id, context_phrase="escalating to CEO"
        )
        if env:
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


class OwnershipMixin(_Base):
    """``unclaim``, ``resume`` and ``reassign``: handing a task back, picking
    it up again, and moving it to another agent."""

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
        actor_slug, agent_team = actor_context_fields(agent)
        spec_ctx = spec_module.Context(
            actor_id=agent_id,
            actor_slug=actor_slug,
            agent_team=agent_team,
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
                        "docs already complete? call i_documented(files=[...], "
                        "notes='verified existing docs') — that is the exit "
                        "from awaiting_documentation"
                        if str(t.status) == "awaiting_documentation"
                        else "only a task assigned to you in pending / claimed"
                        " / in_progress / verifying / needs_revision can be"
                        " unclaimed"
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
        await self._teardown_sandbox_best_effort(agent_id)
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

    async def _reassign_guard(
        self, t: Any, agent_id: UUID, task_id: UUID, new_assignee: str
    ) -> Envelope | None:
        """``_validate_reassign`` plus the target's own single-active-task rule.

        Handing an active task to a developer that still holds claimed,
        in_progress, blocked or paused work stacks a task that developer's own
        ``i_will_work_on`` refuses (live 2026-10-28: be-pm reassigned 6d4baecf
        to be-dev-2 while it held a blocked and a paused task, and neither dev
        could start it afterwards).
        """
        from roboco.seeds.initial_data import AGENT_UUIDS

        if guard := self._validate_reassign(t, agent_id, new_assignee):
            return guard
        held = await self.task.held_task_blocking_claim(
            UUID(AGENT_UUIDS[new_assignee]), task_id
        )
        if held is None:
            return None
        return Envelope.invalid_state(
            message=f"{new_assignee} still holds {held.status} task {held.id}",
            remediate=(
                "reassign to a developer with no claimed, in_progress, blocked"
                " or paused task; a busy developer's own i_will_work_on refuses"
                " this task until that work is finished"
            ),
            context_briefing={},
        )

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
            agent_team=str(agent.team) if agent is not None and agent.team else None,
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
        guard = await self._reassign_guard(t, agent_id, task_id, new_assignee)
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
        briefing = await self._briefing_for(agent_id, task_id, task=t, full=True)
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
        actor_slug, agent_team = actor_context_fields(agent)
        spec_ctx = spec_module.Context(
            actor_id=agent_id,
            actor_slug=actor_slug,
            agent_team=agent_team,
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


class LeafClosureMixin(_Base):
    """Closing a leaf without new code: ``declare_coverage`` when another task
    already covers it, and ``cancel_leaf``."""

    @staticmethod
    def _self_declare_result(
        child: Any, briefing: dict[str, Any]
    ) -> tuple[Envelope | None, Any]:
        """Resolve a root-owned self-declare (caller is ``child``'s assignee).

        Returns ``(None, child)`` when ``child`` is a root: a true root (no
        parent) or a MegaTask root-subtask (parented by the umbrella yet a
        genuine Main-PM coordination root with its own project/branch/PR,
        see ``is_batch_root_subtask``) so its own ``parent_ac_refs`` count as
        self-coverage at the read-side gate (``_parent_ac_ref_sets`` applies
        the identical root predicate). A plain parented task (a cell task or
        an ordinary leaf) is rejected: self-declaring there would stamp refs
        the read-side guard treats as inert upward refs, not self-coverage,
        so they'd silently do nothing. Reject up front rather than return a
        silent no-op that looks like success.
        """
        if child.parent_task_id is None or is_batch_root_subtask(
            batch_id=child.batch_id, parent_task_id=child.parent_task_id
        ):
            return None, child
        return (
            Envelope.invalid_state(
                message=(
                    "root-owned self-declare is reserved for a true"
                    " coordination root (a task with no parent, or a"
                    " MegaTask root-subtask)"
                ),
                remediate=(
                    "this task has a parent, so self-declared refs would"
                    " be inert; declare coverage on the parent's criteria"
                    " via the child-declare flow instead"
                ),
                context_briefing=briefing,
            ),
            None,
        )

    async def _declare_coverage_guard(
        self, pm_agent_id: UUID, child: Any, agent: Any, briefing: dict[str, Any]
    ) -> tuple[Envelope | None, Any]:
        """Role + parent-presence + ownership guard for ``declare_coverage``.

        Returns ``(rejection, parent)`` — ``parent`` is non-``None`` only
        when ``rejection`` is ``None``. Ownership mirrors submit_up (the PM
        assigned to the parent coordination task), with a fallback for any
        PM on the child's own team — the minimum bar the spec asks for.

        Root-owned self-declare: when the caller is the assignee of
        ``child`` itself, ``task_id`` names the PM's OWN root/coordination
        task, not a child — criteria only the root's own machinery can
        satisfy (PR-supersede, closing a contributor PR, a root-level
        merge) that must never be pushed into a cell's acceptance criteria.
        ``parent`` is then ``child`` itself (caller-identity is the
        signal — callers never target their own claimed task in the
        ordinary child-declare flow, so this cannot misfire on it).
        """
        if agent is None or agent.role not in ("cell_pm", "main_pm"):
            return (
                Envelope.not_authorized(
                    message="declare_coverage is reserved for PM roles",
                    remediate="only a cell PM or main PM may declare coverage",
                    context_briefing=briefing,
                ),
                None,
            )
        if child.assigned_to == pm_agent_id:
            return self._self_declare_result(child, briefing)
        if not child.parent_task_id:
            return (
                Envelope.invalid_state(
                    message="task has no parent; nothing to cover",
                    remediate=(
                        "declare_coverage only applies to a decomposition"
                        " child (a subtask with a parent coordination task)"
                    ),
                    context_briefing=briefing,
                ),
                None,
            )
        parent = await self.task.get(child.parent_task_id)
        if parent is None:
            return (
                Envelope.invalid_state(
                    message="parent task not found",
                    remediate="the parent task may have been deleted; escalate",
                    context_briefing=briefing,
                ),
                None,
            )
        child_team = getattr(child.team, "value", child.team)
        agent_team = getattr(agent, "team", None)
        agent_team_val = getattr(agent_team, "value", agent_team)
        owns_parent = parent.assigned_to == pm_agent_id
        on_child_team = child_team is not None and child_team == agent_team_val
        if not owns_parent and not on_child_team:
            return (
                Envelope.not_authorized(
                    message="not the parent's PM and not on the child's team",
                    remediate=(
                        "declare_coverage requires owning the parent"
                        " coordination task or being a PM on the child's team"
                    ),
                    context_briefing=briefing,
                ),
                None,
            )
        return None, parent

    async def declare_coverage(
        self, pm_agent_id: UUID, task_id: UUID, criteria: list[str]
    ) -> Envelope:
        """PM stamps parent acceptance criteria onto an existing child.

        Fixes the roll-up-gate deadlock where a replacement child —
        delegated without ``covers_parent_criteria`` — finishes a parent
        AC's real work but ``_parent_acs_covered_envelope`` still shows it
        uncovered, since only ``delegate`` writes ``parent_ac_refs`` today.
        ``criteria`` takes the same id-or-text representation
        ``covers_parent_criteria`` does (``TaskService._normalize_ac_refs``
        resolves it at read time), so a PM can copy straight out of the
        gate's own uncovered-criteria listing. ``task_id`` is the CHILD
        (any non-cancelled status — the live case is a completed child).

        Root-owned mode: ``task_id`` may instead be the PM's OWN root, for
        criteria only the root itself satisfies (never delegable to a
        cell — see ``_declare_coverage_guard``). The refs land in that
        task's own ``parent_ac_refs`` and are read back against its own
        criteria, satisfied unconditionally (no child/status to wait on).
        """
        child = await self.task.get(task_id)
        briefing = await self._briefing_for(pm_agent_id, task_id, task=child)
        if child is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="declare_coverage",
            )
        agent = await self.task.agent_for(pm_agent_id)
        role_str = str(agent.role) if agent is not None else "cell_pm"
        rejection, parent = await self._declare_coverage_guard(
            pm_agent_id, child, agent, briefing
        )
        if rejection is not None:
            return await self._emit_rejection(
                rejection.with_introspection(task=child, role=role_str),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="declare_coverage",
            )
        unknown = self.task.unknown_ac_refs(parent, criteria)
        if unknown:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"unknown criteria: {'; '.join(unknown)}",
                    remediate=(
                        "criteria must match a parent acceptance criterion"
                        " (id or exact text): "
                        f"{'; '.join(parent.acceptance_criteria or [])}"
                    ),
                    context_briefing=briefing,
                ).with_introspection(task=child, role=role_str),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="declare_coverage",
            )
        updated = await self.task.add_parent_ac_refs(
            task_id, criteria, declared_by=pm_agent_id
        )
        # Self-declare targets its own uncovered-set (parent is child by
        # identity, see the guard); child-declare targets the real parent.
        uncovered_target = task_id if parent is child else child.parent_task_id
        uncovered = await self.task.uncovered_parent_acceptance_criteria(
            uncovered_target
        )
        next_hint = (
            "the roll-up gate will pass now"
            if not uncovered
            else f"{len(uncovered)} parent ACs still uncovered: {'; '.join(uncovered)}"
        )
        return Envelope.ok(
            status=str((updated or child).status),
            task_id=str(task_id),
            next=next_hint,
            evidence={"remaining_uncovered_parent_acs": uncovered},
            context_briefing=briefing,
        ).with_introspection(task=updated or child, role=role_str)

    async def _cancel_leaf_guard(
        self, agent_id: UUID, t: Any, agent: Any, briefing: dict[str, Any]
    ) -> tuple[Envelope | None, Any]:
        """Leaf and ownership guard for ``cancel_leaf`` (verb body owns it).

        Returns ``(rejection, parent)`` - ``parent`` is non-``None`` only
        when ``rejection`` is ``None``. ``t`` must actually be a leaf: it
        has no children of its own, checked first because ``TaskService.cancel``
        force-cascades onto every descendant (2026-09-06 finding - a PM could
        close out a whole live subtree, PR and all, through the one verb meant
        for a childless zero-diff task). ``t`` must also be a delegated child
        (a root/coordination task with no parent is cancelled via the plain
        ``cancel`` action, not this). A cell PM must be the parent's
        assigned PM; a main PM may act on any root's descendant - it
        coordinates across every cell, so ownership of one immediate
        parent is not the bar for it.
        """
        children = await self.task.children_count(t.id)
        if children:
            return (
                Envelope.invalid_state(
                    message=(
                        f"cancel_leaf only closes a leaf: task {t.id} has"
                        f" {children} children"
                    ),
                    remediate=(
                        "cancel or complete the children first, then retry;"
                        " to discard the whole subtree at once use cancel"
                        " via the CEO route"
                    ),
                    context_briefing=briefing,
                ),
                None,
            )
        if not t.parent_task_id:
            return (
                Envelope.invalid_state(
                    message=(
                        "task has no parent; cancel_leaf only closes a delegated child"
                    ),
                    remediate=(
                        "a root/coordination task without a parent is"
                        " cancelled via the ordinary cancel path, not"
                        " cancel_leaf"
                    ),
                    context_briefing=briefing,
                ),
                None,
            )
        parent = await self.task.get(t.parent_task_id)
        if parent is None:
            return (
                Envelope.invalid_state(
                    message="parent task not found",
                    remediate="the parent task may have been deleted; escalate",
                    context_briefing=briefing,
                ),
                None,
            )
        role_str = str(agent.role) if agent is not None else ""
        if role_str != "main_pm" and parent.assigned_to != agent_id:
            return (
                Envelope.not_authorized(
                    message="not the assigned PM of this task's parent",
                    remediate=(
                        "cancel_leaf only closes children of your own"
                        " coordination task; main_pm may act on any root's"
                        " descendant"
                    ),
                    context_briefing=briefing,
                ),
                None,
            )
        return None, parent

    async def _cancel_leaf_zero_diff_error(
        self, t: Any, agent_id: UUID, briefing: dict[str, Any]
    ) -> Envelope | None:
        """Fail-CLOSED zero-diff gate for ``cancel_leaf``.

        Mirrors the ahead-count check ``VerbRunner._maybe_waive_pr_creation``
        uses, but the opposite way on error: that check only ever skips a
        PR-creation side effect and fails OPEN so a flaky fetch can't strand
        a real submit. This one authorizes discarding a task outright, so an
        unanswerable git check must never read as "safe to cancel" - refuse
        instead and let the PM retry once git is healthy.
        """
        if getattr(t, "pr_number", None) is not None:
            return Envelope.invalid_state(
                message=f"task {t.id} has an open PR (#{t.pr_number})",
                remediate=(
                    "an open PR is reviewed/merged through the normal PR"
                    " flow, not cancel_leaf"
                ),
                context_briefing=briefing,
            )
        if not getattr(t, "branch_name", None):
            # Never claimed/started - trivially zero-diff
            return None
        try:
            base_branch = await resolve_parent_branch(t, self.task)
            _behind, ahead = await self.git.is_behind_base(
                t, base_branch=base_branch, actor_agent_id=agent_id
            )
        except Exception as exc:
            logger.warning(
                "cancel_leaf_zero_diff_check_failed",
                task_id=str(t.id),
                error=str(exc),
            )
            return Envelope.invalid_state(
                message=(
                    f"could not verify task {t.id} has zero commits ahead"
                    f" of its base (git check failed: {exc})"
                ),
                remediate=(
                    "retry once git/workspace access is healthy - cancel_leaf"
                    " refuses rather than guess when it can't confirm there"
                    " is no diff to lose"
                ),
                context_briefing=briefing,
            )
        if ahead > 0:
            return Envelope.invalid_state(
                message=(
                    f"task {t.id}'s branch '{t.branch_name}' is {ahead}"
                    " commit(s) ahead of its base - not a zero-diff leaf"
                ),
                remediate=(
                    "cancel_leaf only closes a leaf with no real diff; a"
                    " leaf with commits must go through i_am_done/QA or a"
                    " plain cancel by a PM/CEO with authority to discard work"
                ),
                context_briefing=briefing,
            )
        return None

    async def _cancel_leaf_preflight(
        self, agent_id: UUID, t: Any, briefing: dict[str, Any]
    ) -> tuple[Envelope | None, str]:
        """Role, spec-gate, ownership, and zero-diff checks for ``cancel_leaf``.

        Collapsed into one early-return path so ``cancel_leaf`` itself stays
        under the return-statement budget. Returns ``(rejection, role_str)``;
        ``rejection`` is ``None`` only when every check passed.
        """
        agent = await self.task.agent_for(agent_id)
        role_str = str(agent.role) if agent is not None else "cell_pm"
        try:
            role = spec_module.Role(role_str)
        except ValueError:
            return (
                Envelope.not_authorized(
                    message=f"unknown role '{role_str}'",
                    remediate="role is not declared in the lifecycle spec",
                    context_briefing=briefing,
                ),
                role_str,
            )
        decision = spec_module.can_invoke_intent(
            role, "cancel_leaf", t, spec_module.Context(actor_id=agent_id)
        )
        if not decision.allowed:
            return (
                Envelope.from_decision(decision, briefing=briefing),
                role_str,
            )
        guard, _ = await self._cancel_leaf_guard(agent_id, t, agent, briefing)
        if guard is not None:
            return guard, role_str
        diff_guard = await self._cancel_leaf_zero_diff_error(t, agent_id, briefing)
        if diff_guard is not None:
            return diff_guard, role_str
        return None, role_str

    async def cancel_leaf(self, agent_id: UUID, task_id: UUID, reason: str) -> Envelope:
        """PM closes a zero-diff leaf a merged sibling already fixed.

        The mechanical path `i_am_done` (NO_COMMITS gate) and `complete`
        (needs an open PR) can never take: zero commits, no PR, nothing
        left to verify - the 2026-09-05 de9379cc incident, where 8
        from-scratch verifications agreed no legitimate diff remained but
        the PM had no way to close the leaf short of a CEO cancelling it by
        hand. Reuses `TaskService.cancel`; `_cancel_leaf_guard` enforces
        that `t` has no children before that cascade ever runs, so it never
        has a real descendant to touch. Records `reason` as both the
        journal:decision the tracing gate requires and the task's
        cancellation note.
        """
        t = await self.task.get(task_id)
        briefing = await self._briefing_for(agent_id, task_id, task=t)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=agent_id,
                task_id=task_id,
                verb="cancel_leaf",
            )
        rejection, role_str = await self._cancel_leaf_preflight(agent_id, t, briefing)
        if rejection is not None:
            return await self._emit_rejection(
                rejection.with_introspection(task=t, role=role_str),
                agent_id=agent_id,
                task_id=task_id,
                verb="cancel_leaf",
            )
        pm_decision_outcome = await self._ensure_pm_decision(agent_id, task_id, reason)
        if env := await self._check_pm_decision_required(
            "cancel_leaf",
            agent_id,
            task_id,
            t,
            pm_decision_outcome=pm_decision_outcome,
        ):
            return await self._emit_rejection(
                env.with_introspection(task=t, role=role_str),
                agent_id=agent_id,
                task_id=task_id,
                verb="cancel_leaf",
            )
        cancelled = await self.task.cancel(
            task_id,
            agent_role=role_str,
            cancellation_note=f"[CANCELLED by {role_str}] {reason}",
        )
        if cancelled is None:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"cancel failed for task {task_id}",
                    remediate="re-fetch with evidence(task_id) and retry",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=agent_id,
                task_id=task_id,
                verb="cancel_leaf",
            )
        return Envelope.ok(
            status=str(cancelled.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS["cancel_leaf"].next_hint(cancelled),
            context_briefing=briefing,
        ).with_introspection(task=cancelled, role=role_str)


class IdleMixin(_Base):
    """``i_am_idle``: the next-message hint, pending acks, auto-pausing
    in-progress work, and the legitimacy check with its stranded-agent
    notice."""

    async def i_am_idle(self, agent_id: UUID) -> Envelope:
        """Report no more work. Soft-block if there are unread A2As,
        @mentions, or pending ack-required notifications this role can clear.

        Before marking the agent idle:

        1. Bail with ``idle_with_unread`` when context_briefing has unread A2A,
           @mentions, or a pending ack-required notification the caller's role
           carries ``notify_ack`` for (must address those first). A role that
           does not carry ``notify_ack`` (auditor, pr_reviewer, prompter,
           secretary) is never blocked on the notification leg: it could
           never satisfy it, so blocking would be a permanent dead-end
           instead of a fixable gate.
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
        4. Release every open specialized claim (QA / doc / PR review) the
           caller has not closed with a decision, back to its review queue.
           Deliberately a release, NOT a gate: idling must never be
           conditional on submitting a verdict, because a reviewer that
           cannot honestly decide would either be trapped (the decision
           gates can refuse, and the stale-claim reaper never sweeps
           awaiting_* rows to free it) or cornered into rubber-stamping. The
           lane un-wedges because competing claimants are only rejected
           while the lock is held; a claimant that repeatedly idles out
           instead of reviewing still trips the orchestrator's respawn
           breaker, which is the designed escalation.
        """
        briefing = await self._briefing_for(agent_id, None)
        pending_ack = await self._pending_ack_notifications(agent_id, briefing)
        if briefing.get("unread_a2a") or briefing.get("unread_mentions") or pending_ack:
            return Envelope.ok(
                status="idle_with_unread",
                task_id=None,
                next=self._idle_with_unread_next(briefing, pending_ack),
                context_briefing=briefing,
            )
        # A caller holding an OPEN review claim (qa_claim / doc_claim /
        # pr review) without a submitted decision releases it here. fe-qa
        # idled out of 99f61c8c's review exactly once and the held
        # single-claimant lock made the task unreviewable by anyone else -
        # the FE lane wedged behind it (2026-09-28). Releasing beats both
        # alternatives: stranding the lock, or a soft-block that can never
        # be satisfied when the decision verbs themselves refuse.
        released_ids = list(await self.task.release_review_claims(agent_id) or [])
        # Pre-idle guards, evaluated in order — the first that returns an
        # Envelope short-circuits to a rejection (kept as a loop so adding a
        # guard doesn't push this verb over the return-count bound).
        idle_guards = (
            self._pending_assignment_guard,
            self._pm_unfinished_review_guard,
            self._pm_uncovered_decomposition_guard,
            self._pm_uncovered_required_cells_guard,
            self._auditor_note_guard,
        )
        for guard_fn in idle_guards:
            if guard := await guard_fn(agent_id, briefing):
                return await self._emit_rejection(
                    guard, agent_id=agent_id, task_id=None, verb="i_am_idle"
                )
        idle_hint = await self._idle_legitimacy_hint(agent_id)
        paused_ids = await self._auto_pause_in_progress_tasks(agent_id)
        await self.task.mark_agent_idle(agent_id)
        next_msg = self._idle_next_message(paused_ids, released_ids, idle_hint)
        # The agent truly disengages here (container shuts down) — unlike
        # the idle_with_unread early return above, which sends it right
        # back to work, so no teardown fires there.
        await self._teardown_sandbox_best_effort(agent_id)
        return Envelope.ok(
            status="idle",
            task_id=None,
            next=next_msg,
            context_briefing=briefing,
        )

    @staticmethod
    def _idle_next_message(
        paused_ids: list[Any], released_ids: list[Any], idle_hint: str | None
    ) -> str:
        """Compose i_am_idle's ``next``: how to resume paused work, which
        review claims went back to their queues, and any legitimacy hint."""
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
        if released_ids:
            # Name the release so the agent's journal reflects reality: the
            # tasks went back to their review queues unclaimed, they did not
            # quietly stay "its" reviews.
            joined = ", ".join(str(tid) for tid in released_ids)
            next_msg = (
                f"{next_msg}; released your unsubmitted review claim(s) on"
                f" {joined} back to the review queue - the next reviewer"
                " claims them fresh; submit pass_review/fail_review (or the"
                " lane's decision verb) before idling next time if you want"
                " the verdict to be yours"
            )
        if idle_hint:
            next_msg = f"{next_msg}; {idle_hint}"
        return next_msg

    async def _pending_ack_notifications(
        self, agent_id: UUID, briefing: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Ack-required notifications the caller's role can actually clear.

        ``briefing["pending_notifications"]`` (EvidenceRepo.list_pending_
        notifications) is already scoped to requires_ack rows; the remaining
        gate here is the role check, so a role without ``notify_ack`` is
        never handed a condition it cannot resolve. The role lookup only
        runs when there is something pending, so the common empty-inbox
        path pays no extra query.
        """
        pending = list(briefing.get("pending_notifications") or [])
        if not pending:
            return []
        agent = await self.task.agent_for(agent_id)
        if agent is None or not role_carries_notify_ack(agent.role):
            return []
        return pending

    @staticmethod
    def _idle_with_unread_next(
        briefing: dict[str, Any], pending_ack: list[dict[str, Any]]
    ) -> str:
        """Build the ``idle_with_unread`` remediation, naming the exact tool
        for whichever reason(s) triggered it, since a mention-only message
        left a pending-ack caller with no actual instruction to follow."""
        reasons = ["clear your inbox, then retry i_am_idle():"]
        if briefing.get("unread_a2a"):
            reasons.append(" read_messages() for unread A2A;")
        if briefing.get("unread_mentions"):
            reasons.append(" notify_ack() per @mention notification;")
        if pending_ack:
            first_id = pending_ack[0].get("notification_id")
            reasons.append(
                f" notify_ack(notification_id='{first_id}') for"
                f" {len(pending_ack)} pending ack-required notification(s);"
            )
        return "".join(reasons).rstrip(";")

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
        # The lookup also returns blocked tasks (so the claim guard sees them);
        # i_am_idle only auto-pauses genuinely in_progress ones — a blocked task
        # waits on an external dep, not the agent, so it stays blocked.
        from roboco.models.base import TaskStatus

        paused_ids: list[str] = []
        for t in in_progress:
            if t.status != TaskStatus.IN_PROGRESS:
                continue
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

    async def _idle_legitimacy_hint(self, agent_id: UUID) -> str | None:
        """B34 idle_legitimacy advisory (decisions pilot, default-off).

        Structural idle guards cannot see "finished but never opened a
        PR": that dev idles cleanly and the task rots until the reaper.
        When the pilot is ON, classify the idle from owned-task state
        (commits? PR? recency). MAY ONLY ESCALATE or hint: a stranded
        verdict sends a best-effort PM notification, a likely-done
        verdict becomes a ``next`` hint naming the unsubmitted task, and
        a legit idle is NEVER blocked. Off/shadow/below-floor/unreachable
        returns None (exactly today's idle).
        """
        owned_state = await self._idle_owned_state(agent_id)
        if owned_state is None:
            return None
        from roboco.services.decisions.pilots_gateway import (
            IdleLegitimacy,
            idle_legitimacy,
        )

        try:
            verdict = await idle_legitimacy(
                self.task.session,
                agent_slug=str(agent_id),
                owned_tasks=owned_state,
            )
        except Exception as exc:
            logger.warning(
                "idle_legitimacy_skip", agent_id=str(agent_id), error=str(exc)
            )
            return None
        if verdict is IdleLegitimacy.LIKELY_STRANDED_ESCALATE:
            await self._notify_idle_stranded(agent_id, owned_state)
            return None
        if verdict is IdleLegitimacy.LIKELY_DONE_SUBMIT_NOW:
            first = owned_state[0]
            return (
                f"decisions hint: task {first['task_id'][:8]} "
                f"('{first['title'][:60]}') looks finished but was never "
                f"submitted - call i_am_done(task_id='{first['task_id']}') "
                "before idling"
            )
        return None

    async def _idle_owned_state(self, agent_id: UUID) -> list[dict[str, Any]] | None:
        """Owned-task state for the B34 verdict (commits, PR, recency),
        composed best-effort from what ``i_am_idle`` already holds in
        scope. ``None`` means "stay out" (pilot off / no owned tasks /
        any fetch error)."""
        try:
            from roboco.services.decisions.pilots import PilotMode, pilot_mode

            mode = await pilot_mode(self.task.session, "idle_legitimacy")
        except Exception:
            return None
        if mode is PilotMode.OFF:
            return None
        try:
            owned = await self.task.list_in_progress_for_agent(agent_id)
        except Exception as exc:
            logger.warning(
                "idle_legitimacy_skip", agent_id=str(agent_id), error=str(exc)
            )
            return None
        if not owned:
            return None
        now = datetime.now(UTC)
        return [self._idle_task_state(t, now) for t in owned[:5]]

    @staticmethod
    def _idle_task_state(t: Any, now: datetime) -> dict[str, Any]:
        """One owned task's B34 state row (commit count, PR presence,
        recency) composed best-effort from what i_am_idle holds in scope."""
        minutes: float | None = None
        updated = getattr(t, "updated_at", None)
        if updated is not None:
            try:
                minutes = max(0.0, (now - updated).total_seconds() / 60)
            except (TypeError, ValueError):
                minutes = None
        return {
            "task_id": str(t.id),
            "title": str(getattr(t, "title", "") or ""),
            "status": str(getattr(t, "status", "") or ""),
            "commit_count": len(list(getattr(t, "commits", None) or [])),
            "has_pr": bool(getattr(t, "pr_number", None)),
            "minutes_since_last_activity": (
                round(minutes, 1) if minutes is not None else None
            ),
        }

    async def _notify_idle_stranded(
        self, agent_id: UUID, owned_state: list[dict[str, Any]]
    ) -> None:
        """The escalate lane of B34: a PM-facing ack-required note that a
        dev idled over work that looks stranded. Best-effort; never
        blocks the idle (pattern mirrors spawn_exit's parking escalation)."""
        try:
            from roboco.services.notification import NotificationService

            first = owned_state[0] if owned_state else None
            body = (
                f"[decisions] Idle-legitimacy: agent {agent_id} idled while its "
                "in-progress task(s) look stranded (commits without a PR or "
                "submission path); the task may rot until the reaper."
                + (
                    f" First: {first['task_id'][:8]} '{first['title'][:60]}'."
                    if first
                    else ""
                )
            )
            await NotificationService().send_ack_notification(
                from_agent="system",
                to_agent="main-pm",
                body=body,
                task_id=first["task_id"] if first else None,
            )
        except Exception as exc:
            logger.warning(
                "failed to send idle-stranded notification",
                agent_id=str(agent_id),
                error=str(exc),
            )


class IdleGuardsMixin(_Base):
    """The guards ``i_am_idle`` runs first: a pending assignment to claim
    instead, a PM's unfinished review or uncovered decomposition, and the
    auditor's note obligation."""

    # The decision verb pair (or single verb) each claimed awaiting_* status
    # waits on. Keyed by str(status), the same awaiting_* values
    # ``list_open_review_claims`` filters on.
    async def _pending_assignment_guard(
        self, agent_id: UUID, briefing: dict[str, Any]
    ) -> Envelope | None:
        """Refuse i_am_idle when caller owns any pending (unclaimed) task.

        Pre-gateway: the orchestrator would respawn the agent after
        i_am_idle if they still owned pending work, leading to a tight
        respawn loop. Now an explicit refusal lets the agent fix it via
        i_will_work_on or i_will_plan before exiting.

        Two shapes turn that refusal into a permanent dead-end instead of a
        fixable gate, and neither may block the exit:

        - The agent already holds claimed/in_progress work: the claim gate
          refuses a second claim ("finish or pause it before claiming new
          work"), so no pending assignment can be acted on in THIS run. The
          spawn-per-pending dispatcher gives each root its own run after
          this container exits — a cell PM holding one root in_progress with
          two more assigned wedged at this guard on EVERY run until the CEO
          paused them by hand (be-pm, 2026-09-27).
        - The pending task is held by the claim gate itself (dependency/
          sequence reachability — the exact predicate the claim endpoint
          enforces). Demanding a claim the gate will refuse is the same
          "gate A refuses action, gate B refuses exit" trap as the dev's
          lane-held leaf below (be-dev-2's idle refusal, 2026-09-27).
        """
        assigned = await self.task.list_assigned_for_agent(agent_id)
        pending = [t for t in assigned if str(t.status) == "pending"]
        if not pending:
            return None
        if any(str(t.status) in ("claimed", "in_progress") for t in assigned):
            return None
        agent = await self.task.agent_for(agent_id)
        pending = await self._pending_blocking_idle(agent, pending)
        if not pending:
            return None
        claimable = await self._claimable_pending(pending)
        if not claimable:
            return None
        first = claimable[0]
        verb = self._pending_claim_verb(agent)
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

    async def _claimable_pending(self, pending: list[Any]) -> list[Any]:
        """Pending tasks the claim gate would actually admit right now: a leaf
        held by the dependency/sequence reachability gate must not block the
        exit (demanding a claim the gate will refuse is the same "gate A
        refuses action, gate B refuses exit" trap as the lane-held leaf)."""
        claimable: list[Any] = []
        for t in pending:
            if await self.task.is_pending_claim_blocked(t.id) is True:
                continue
            claimable.append(t)
        return claimable

    @staticmethod
    def _pending_claim_verb(agent: Any) -> str:
        """The claim verb for the caller's kind: PMs plan, everyone else works."""
        if agent and agent.role in ("cell_pm", "main_pm"):
            return "i_will_plan"
        return "i_will_work_on"

    async def _pending_blocking_idle(self, agent: Any, pending: list[Any]) -> list[Any]:
        """Pending tasks that should block i_am_idle, after role exemptions.

        Board/advisory roles (product_owner, head_marketing, auditor) review and
        advise without ever claiming — they have no i_will_work_on / i_will_plan
        verb, and their one-shot dispatch is meant to leave the coordination task
        pending for the CEO to reassign. They must idle freely, so nothing blocks
        them. Developers own a whole per-dev code queue up front, so a leaf still
        waiting behind an earlier non-terminal sibling in its own lane must not
        pin them — the orchestrator spawns it when the lane clears (see
        ``_pending_not_lane_held``). Other roles: every pending task blocks.
        """
        if not agent:
            return pending
        if agent.role in ("product_owner", "head_marketing", "auditor"):
            return []
        if agent.role == "developer":
            return await self._pending_not_lane_held(pending)
        return pending

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

    # The auditor must have recorded an observation within this window before
    # it may go idle (session-scoped note obligation; see _auditor_note_guard).
    _AUDITOR_IDLE_NOTE_WINDOW_SECONDS: int = 3600

    async def _auditor_note_guard(
        self, agent_id: UUID, briefing: dict[str, Any]
    ) -> Envelope | None:
        """Refuse i_am_idle when the auditor has not recorded an observation.

        Every role with a dedicated note section is obligated to populate it,
        the same way journals are obligated. The auditor's section is
        ``auditor_notes``, but it owns no delivery task and has no delivery
        verb to hang the obligation on — so the obligation is session-scoped:
        before going idle the auditor must have recorded an observation within
        the last ``_AUDITOR_IDLE_NOTE_WINDOW_SECONDS`` (a reflect journal entry,
        or a note(scope='handoff') section write — either leaves a journal
        entry). Inert for every other role.
        """
        agent = await self.task.agent_for(agent_id)
        if agent is None or str(agent.role) != "auditor":
            return None
        if await self.journal.has_recent_entry(
            agent_id, self._AUDITOR_IDLE_NOTE_WINDOW_SECONDS
        ):
            return None
        return Envelope.invalid_state(
            message=(
                "as the auditor you must record an observation before going "
                "idle — your auditor_notes section is the artifact the company "
                "reviews, so it cannot be left empty."
            ),
            remediate=(
                "call note(scope='reflect', text='<what you observed and any "
                "concern>') for a journal observation, or "
                "note(scope='handoff', task_id='<task>', section={'summary': "
                "'<observation>', 'severity': 'info'|'watch'|'risk'}) to fill a "
                "task's auditor_notes — then retry i_am_idle()."
            ),
            context_briefing=briefing,
        )

    async def _pm_uncovered_decomposition_guard(
        self, agent_id: UUID, briefing: dict[str, Any]
    ) -> Envelope | None:
        """Refuse i_am_idle when a PM left a parent criterion unclaimed (Spec 2).

        The decomposition floor: a PM that has mapped *some* children to parent
        acceptance criteria (``covers_parent_criteria``) cannot idle while
        another parent criterion still has no subtask responsible for it — the
        "two leaves, half the ACs silently dropped" pattern from PR #175.
        Distinct from the roll-up gate (Spec 4), which fires at submit_up /
        complete / escalate and demands a *completed* child; this fires earlier,
        at PM exit, and asks only that every criterion be *claimed*.
        Safe-by-construction: ``unclaimed_parent_acceptance_criteria`` returns
        ``[]`` until the PM declares coverage on at least one child, so this is
        inert for legacy / not-yet-adopted decompositions and never blocks a PM
        who never touched coverage at all.
        """
        agent = await self.task.agent_for(agent_id)
        if not agent or agent.role not in ("cell_pm", "main_pm"):
            return None
        assigned = await self.task.list_assigned_for_agent(agent_id)
        for parent in assigned:
            if str(parent.status) in self._TERMINAL_STATUSES:
                continue
            unclaimed = await self.task.unclaimed_parent_acceptance_criteria(parent.id)
            # isinstance keeps the gate inert under partial test mocks (an
            # AsyncMock TaskService returns a truthy stub, not a concrete list).
            if not isinstance(unclaimed, list) or not unclaimed:
                continue
            listing = "; ".join(unclaimed)
            return Envelope.invalid_state(
                message=(
                    f"task {parent.id} has {len(unclaimed)} acceptance criteria with"
                    " no subtask responsible for them; cannot idle mid-decomposition."
                ),
                remediate=(
                    "delegate (or reassign) subtasks whose covers_parent_criteria"
                    f" include these criteria, then retry i_am_idle: {listing}"
                ),
                context_briefing=briefing,
            )
        return None

    async def _pm_uncovered_required_cells_guard(
        self, agent_id: UUID, briefing: dict[str, Any]
    ) -> Envelope | None:
        """Refuse i_am_idle when a PM left an explicitly-named cell undelegated.

        The brief / acceptance criteria / Board handoff can name specific cells
        (recorded as a ``required_cells:`` marker on the parent). The Main PM
        must create a subtask for each — its discretion covers only un-named
        scope; a genuinely-unnecessary named cell must be confirmed via
        escalate_up/dm, not silently dropped. This is the structured companion
        to the prompt rule (commit 60de3499), firing at PM exit. Inert when no
        parent carries the marker, so legacy decompositions are never blocked.
        """
        agent = await self.task.agent_for(agent_id)
        if not agent or agent.role not in ("cell_pm", "main_pm"):
            return None
        assigned = await self.task.list_assigned_for_agent(agent_id)
        for parent in assigned:
            if str(parent.status) in self._TERMINAL_STATUSES:
                continue
            uncovered = await self.task.uncovered_required_cells(parent.id)
            # isinstance keeps the gate inert under partial test mocks (an
            # AsyncMock TaskService returns a truthy stub, not a concrete list).
            if not isinstance(uncovered, list) or not uncovered:
                continue
            listing = ", ".join(uncovered)
            return Envelope.invalid_state(
                message=(
                    f"task {parent.id} names cells with no subtask: {listing}; "
                    "cannot idle while an explicitly-named cell is undelegated."
                ),
                remediate=(
                    f"delegate a subtask for each named cell ({listing}); if one is "
                    "genuinely unnecessary, confirm via escalate_up/dm rather than "
                    "dropping it, then retry i_am_idle"
                ),
                context_briefing=briefing,
            )
        return None


class TracingGatesMixin(_Base):
    """The tracing gates: the journal entries a claim, ``i_am_done``,
    ``submit_up`` or ``complete`` needs, the PM decision record, and the
    tracing-gap envelope with its hints."""

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
        from roboco.config import settings as _settings
        from roboco.foundation.policy import tracing as _tr

        has_reflect = await self.journal.has_reflect_for_task(agent_id, task_id)
        has_decision = await self.journal.has_decision_for_task(agent_id, task_id)
        has_learning = await self.journal.has_learning_for_task(agent_id, task_id)
        has_struggle = await self.journal.has_struggle_for_task(agent_id, task_id)
        during_work_count = sum([has_decision, has_learning, has_struggle])
        open_finding_ids = await self._open_finding_ids(task_id)

        ctx = _tr.GateContext(
            journal_reflect_present=has_reflect,
            journal_decision_present=has_decision,
            journal_learning_present=has_learning,
            journal_struggle_present=has_struggle,
            journal_during_work_count=during_work_count,
            # DEV_NOTES_MIN_CHARS reads the persisted task.dev_notes — the dev
            # pre-writes it via note(scope='handoff') before i_am_done.
            dev_notes_min_chars=_settings.dev_notes_min_chars,
            open_finding_ids=open_finding_ids,
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

    async def _ensure_pm_decision(
        self, agent_id: UUID, task_id: UUID, rationale: str | None
    ) -> PmDecisionOutcome:
        """Auto-record a journal:decision from a PM verb's own rationale.

        The write-then-gate pattern (mirrors i_am_blocked → write_struggle):
        a PM verb that already carries a substantive rationale — complete /
        submit_up / submit_root ``notes``, escalate ``reason``, or a
        synthesized unblock context line — records it as the decision
        *before* the journal:decision tracing gate runs. A loaded or weak
        model that forgot the separate note(scope='decision') call then no
        longer stalls in a tracing_gap → respawn loop; the gate still runs
        as defense-in-depth and now passes off the just-written entry.

        Idempotent within the decision window: when a fresh decision already
        satisfies the gate, no duplicate is written. Best-effort — a journal
        write failure is logged and swallowed so the verb falls through to
        the normal gate — which the caller can now short-circuit to PASS
        (see ``PmDecisionOutcome`` / the gate helpers below) instead of
        laundering a transient DB hiccup into a rejection the PM's own
        rationale already answers.

        Savepoint-guarded: the journal INSERT can lock-timeout on a
        concurrent claim holding the task row's FK share lock (live
        incident: an escalate_up request's write hit
        ``LockNotAvailableError`` mid-flush). Swallowing that without a
        rollback left the session's transaction poisoned — the very next
        attribute touch anywhere in the request (``_escalate_up_preflight``
        reading ``t.id``) raised an unhandled ``PendingRollbackError``
        instead of the clean gate rejection this docstring promises.
        ``begin_nested()`` scopes the failure to a SAVEPOINT the except
        below rolls back to, leaving the outer transaction — and every
        object this call didn't itself touch, e.g. the caller's ``t`` —
        exactly as usable as if the write had never been attempted.

        Returns the outcome so the caller can thread it into the gate that
        runs right after: "fresh" / "wrote" both mean a decision genuinely
        exists; "transient_failure" means the write raised but a rationale
        WAS in hand (the caller may treat the gate as satisfied for this
        call); "absent" means there was no rationale to record at all (the
        gate should reject exactly as before).
        """
        from roboco.config import settings as _settings

        text = (rationale or "").strip()
        if not text:
            return "absent"
        try:
            async with self.task.session.begin_nested():
                latest = await self.journal.latest_decision_at(agent_id, task_id)
                window = _settings.pm_decision_window_seconds
                if (
                    latest is not None
                    and (datetime.now(UTC) - latest).total_seconds() <= window
                ):
                    return "fresh"
                await self.journal.write_decision(
                    agent_id=agent_id, task_id=task_id, content=text
                )
                return "wrote"
        except Exception as exc:
            # Best-effort; caller's gate handles the fallout
            logger.warning(
                "auto-record pm decision failed",
                error=str(exc),
                task_id=str(task_id),
            )
            return "transient_failure"

    async def _check_pm_decision_required(
        self,
        verb: str,
        agent_id: UUID,
        task_id: UUID,
        t: Any,
        *,
        pm_decision_outcome: PmDecisionOutcome | None = None,
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

        ``pm_decision_outcome`` (from the caller's own
        ``_ensure_pm_decision`` call, defaulting to ``None`` for legacy
        parity) short-circuits the freshness check to satisfied when it is
        ``"transient_failure"`` — the write lock-timed out under DB
        contention, but the verb's own rationale WAS in hand and is the
        substance the gate actually cares about; the write was only ever a
        convenience. Any other value (or ``None``) leaves this check
        byte-for-byte unchanged.
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
        gate_satisfied_by_rationale = pm_decision_outcome == "transient_failure"
        if gate_satisfied_by_rationale:
            logger.warning(
                "pm decision auto-record failed transiently; gate satisfied "
                "by verb rationale",
                task_id=str(task_id),
                verb=verb,
            )

        # QUICK_CONTEXT_MIN_CHARS applies only to ``delegate`` (its
        # required-set is the only one carrying it); the PM pre-writes the
        # parent's quick_context via note(scope='handoff') before delegating.
        # Setting the threshold here is inert for unblock / escalate, which do
        # not require it.
        ctx = _tr.GateContext(
            journal_decision_present=fresh or gate_satisfied_by_rationale,
            quick_context_min_chars=_settings.quick_context_min_chars,
        )
        result = _tr.check_requirements(
            task=t,
            requirements=list(_tr.requirements_for(verb)),
            ctx=ctx,
        )
        if result.passed:
            return None
        return await self._build_tracing_gap(agent_id, task_id, result.missing, task=t)

    async def _check_complete_gates(
        self,
        agent_id: UUID,
        task_id: UUID,
        notes: str,
        *,
        pm_decision_outcome: PmDecisionOutcome | None = None,
    ) -> Envelope | None:
        """Tracing gate for cell-PM and main-PM ``complete`` verbs.

        VERB_REQUIREMENTS["complete"] = JOURNAL_DECISION + JOURNAL_REFLECT
        + NOTES_MIN_CHARS. ``SUBTASKS_TERMINAL`` is enforced separately by
        ``_subtasks_not_terminal_envelope`` in the cell/main complete
        guards because that gate emits a richer remediation message
        listing the non-terminal subtasks; keeping it inline preserves
        that UX.

        ``pm_decision_outcome`` mirrors ``_check_pm_decision_required``'s
        param: ``"transient_failure"`` (the caller's ``_ensure_pm_decision``
        write lock-timed out but a rationale WAS provided) satisfies both
        the decision and reflect legs for this call — same substitution
        already applied to a genuinely-written decision below — instead of
        rejecting a DB hiccup as a missing decision.
        """
        from types import SimpleNamespace

        from roboco.config import settings as _settings
        from roboco.foundation.policy import tracing as _tr

        has_decision = await self.journal.has_decision_for_task(agent_id, task_id)
        has_reflect = await self.journal.has_reflect_for_task(agent_id, task_id)
        gate_satisfied_by_rationale = pm_decision_outcome == "transient_failure"
        if gate_satisfied_by_rationale:
            logger.warning(
                "pm decision auto-record failed transiently; gate satisfied "
                "by verb rationale",
                task_id=str(task_id),
                verb="complete",
            )
        task_view = SimpleNamespace(notes=notes)
        ctx = _tr.GateContext(
            journal_decision_present=has_decision or gate_satisfied_by_rationale,
            # A PM closing/submitting a task documents it in its *decision* note;
            # a separate *reflect* adds little for a coordination/review close and
            # is exactly the artifact weak-model PMs forget — looping on the
            # reflect gate until reaped (re-confirmed live 2026-06-10). Accept a
            # decision as satisfying reflect for the PM complete/submit_up close;
            # the gate still requires a decision + substantive notes, so the close
            # stays documented — only the redundant second-artifact demand drops.
            journal_reflect_present=(
                has_reflect or has_decision or gate_satisfied_by_rationale
            ),
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
        self,
        agent_id: UUID,
        task_id: UUID,
        notes: str,
        *,
        pm_decision_outcome: PmDecisionOutcome | None = None,
    ) -> Envelope | None:
        """Tracing gate for ``submit_up`` (cell PM bubble-up).

        VERB_REQUIREMENTS["submit_up"] = SUBTASKS_TERMINAL + JOURNAL_DECISION
        + JOURNAL_REFLECT + NOTES_MIN_CHARS + FINDINGS_ADDRESSED. The notes
        value is threaded through a SimpleNamespace shim because the verb
        hasn't persisted it to the task yet. ``SUBTASKS_TERMINAL`` is filtered
        out here because the inline ``_subtasks_not_terminal_envelope`` that
        follows enumerates the non-terminal subtask ids — strictly richer
        remediation than the generic foundation hint. Shared verbatim by
        ``submit_root`` (both route through ``_submit_up_guard``), so
        ``open_finding_ids`` covers pr_gate/pm/ceo-origin findings on either
        a cell root or a Main-PM root.

        ``pm_decision_outcome`` mirrors ``_check_complete_gates``'s param —
        ``"transient_failure"`` satisfies the decision + reflect legs for
        this call instead of rejecting a DB-contention write hiccup as a
        missing decision.
        """
        from types import SimpleNamespace

        from roboco.config import settings as _settings
        from roboco.foundation.policy import tracing as _tr

        has_decision = await self.journal.has_decision_for_task(agent_id, task_id)
        has_reflect = await self.journal.has_reflect_for_task(agent_id, task_id)
        open_finding_ids = await self._open_finding_ids(task_id)
        gate_satisfied_by_rationale = pm_decision_outcome == "transient_failure"
        if gate_satisfied_by_rationale:
            logger.warning(
                "pm decision auto-record failed transiently; gate satisfied "
                "by verb rationale",
                task_id=str(task_id),
                verb="submit_up",
            )
        task_view = SimpleNamespace(notes=notes)
        ctx = _tr.GateContext(
            journal_decision_present=has_decision or gate_satisfied_by_rationale,
            # A PM closing/submitting a task documents it in its *decision* note;
            # a separate *reflect* adds little for a coordination/review close and
            # is exactly the artifact weak-model PMs forget — looping on the
            # reflect gate until reaped (re-confirmed live 2026-06-10). Accept a
            # decision as satisfying reflect for the PM complete/submit_up close;
            # the gate still requires a decision + substantive notes, so the close
            # stays documented — only the redundant second-artifact demand drops.
            journal_reflect_present=(
                has_reflect or has_decision or gate_satisfied_by_rationale
            ),
            notes_min_chars=getattr(_settings, "notes_min_chars", 20),
            open_finding_ids=open_finding_ids,
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
            "dev_notes>=min": hint_for_short_dev_notes(
                min_chars=getattr(_roboco_settings, "dev_notes_min_chars", 40),
                task_id=tid,
            ),
            "pr_reviewer_notes>=min": hint_for_short_pr_reviewer_notes(
                min_chars=getattr(_roboco_settings, "pr_reviewer_notes_min_chars", 40),
            ),
            "quick_context>=min": hint_for_short_quick_context(
                min_chars=getattr(_roboco_settings, "quick_context_min_chars", 30),
                task_id=tid,
            ),
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
            "render_preview": hint_for_render_preview(),
        }
        return simple_hints.get(missing_key)

    def _tracing_gap_hints(self, missing: list[str], task_id: UUID) -> list[str]:
        """Categorize missing requirement keys into agent-facing hints.

        ``acceptance_criterion:<name>`` and ``finding:<id8>`` are prefix-based
        tokens batched into ONE hint each (not one per item); every other key
        maps through ``_hint_for_missing_key``, falling back to a generic
        hint (defense-in-depth: the agent gets at least the key name even
        when no hint is registered) when unrecognized.
        """
        hints: list[str] = []
        unaddressed: list[str] = []
        open_findings: list[str] = []
        unhinted: list[str] = []
        for m in missing:
            if m.startswith("acceptance_criterion:"):
                unaddressed.append(m.split(":", 1)[1])
                continue
            if m.startswith("finding:"):
                open_findings.append(m.split(":", 1)[1])
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
        if open_findings:
            hints.append(
                hint_for_open_findings(finding_ids=open_findings, task_id=str(task_id))
            )
        for token in unhinted:
            hints.append(
                f"requirement {token!r} not satisfied — see lifecycle docs "
                f"or escalate via i_am_blocked if you do not know how to "
                f"satisfy this."
            )
        return hints

    async def _build_tracing_gap(
        self,
        agent_id: UUID,
        task_id: UUID,
        missing: list[str],
        *,
        task: Any | None = None,
    ) -> Envelope:
        """Translate missing requirement keys into agent-facing hints.

        Multi-missing remediate uses a numbered list so the agent sees each
        requirement as a distinct step instead of a single semicolon-joined
        sentence the model parses as one instruction.
        """
        hints = self._tracing_gap_hints(missing, task_id)
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


class QualityGuardsMixin(_Base):
    """The delivery-gate guards ``i_am_done`` and ``pr_pass`` share: a
    workspace whose toolchain can't run the suite, and the architectural
    conventions check."""

    async def _toolchain_broken_guard(
        self, agent_id: UUID, task: Any, *, reviewer: bool = False
    ) -> Envelope | None:
        """Refuse a delivery gate when the acting agent's workspace cannot run
        the project's suite (interpreter mismatch).

        A role that cannot execute the suite degrades to "verifying by reading
        source", which the QA + PR-review gates exist to prevent. Inert when the
        flag is off; only a recorded ``broken`` status blocks — a missing or
        ``unknown`` status never strands a task (fail-open).

        ``reviewer=True`` for the pr_pass gate: a PR reviewer has no
        ``i_am_blocked`` verb, so the remediation points at ``pr_fail`` (their
        reject lever, sending the PR back to needs_revision for the dev to fix
        the environment) instead of a verb they cannot call.
        """
        from roboco.config import settings as _settings

        if not _settings.toolchain_match_enabled:
            return None
        status = await self.git.toolchain_status_for_task(agent_id, task)
        if status == "unknown":
            # Fail-open (precision over recall) but never silent: a recorded
            # 'unknown' means provisioning ran yet the smoke could not confirm
            # the suite is collectable under the interpreter — a possible hollow
            # pass. Surface it so an operator can see the gate proceeded blind.
            logger.warning(
                "toolchain.unverified_gate_pass",
                agent_id=str(agent_id),
                task_id=str(getattr(task, "id", "")),
                detail=(
                    "gate proceeding past an unverified toolchain — the "
                    "workspace smoke could not confirm the project's suite is "
                    "collectable under the provisioned interpreter"
                ),
            )
        if status != "broken":
            return None
        if reviewer:
            remediate = (
                "the workspace Python does not match the project's requirement, "
                "so the suite cannot be executed to verify this PR. call "
                "pr_fail(issues=['toolchain: interpreter mismatch — suite "
                "cannot run']) so the PR returns to needs_revision and the dev "
                "rebuilds the environment — do NOT pr_pass on a source read"
            )
        else:
            remediate = (
                "the workspace Python does not match the project's requirement; "
                "call i_am_blocked(reason='toolchain') so the environment is "
                "rebuilt against the right interpreter — do NOT pass on a "
                "source read"
            )
        return Envelope.invalid_state(
            message=(
                "the project's test suite cannot be executed in this workspace "
                "(interpreter mismatch) — verifying by reading source is hollow"
            ),
            remediate=remediate,
            context_briefing={},
        )

    async def _conventions_gate(self, ctx: _IAmDoneContext) -> Envelope | None:
        """Block i_am_done on unresolved block-level architectural violations.

        Also persists the findings for the panel's violations feed — even the
        ones that block this submit.
        """
        from roboco.config import settings as _settings

        if not _settings.conventions_enabled:
            return None
        result = await self.git.conventions_check_for_task(ctx.agent_id, ctx.task)
        await self._record_convention_findings(ctx.task, result)
        return self._conventions_rejection(result, ctx.briefing)

    @staticmethod
    async def _record_convention_findings(task: Any, result: dict[str, Any]) -> None:
        """Persist the task's findings for the feed, best-effort.

        Runs in its OWN committed session so a finding that *blocks* the submit
        is captured regardless of the verb's transaction outcome.
        """
        project_id = getattr(task, "project_id", None)
        task_id = getattr(task, "id", None)
        if project_id is None or task_id is None:
            return
        try:
            from roboco.db.base import get_session_factory
            from roboco.services.conventions import get_conventions_service

            factory = get_session_factory()
            async with factory() as db:
                await get_conventions_service(db).record_findings(
                    UUID(str(project_id)),
                    UUID(str(task_id)),
                    result.get("findings", []),
                )
                await db.commit()
        except Exception as exc:
            logger.warning(
                "Recording convention findings failed (non-fatal)", error=str(exc)
            )

    async def _conventions_guard(
        self,
        agent_id: UUID,
        task: Any,
        briefing: dict[str, Any],
        preferred_parent: str | None = None,
    ) -> Envelope | None:
        """Run the conventions validator on the actor's changed files (gated).

        A ``block`` finding (a misplaced definition, a lint suppression) or a
        validator that could not run returns a rejection with the offending
        ``file:line`` + fix hint. ``warn`` findings never block. Inert when the
        flag is off. This is the pr_pass (reviewer) path — the remediation is
        reviewer-aware (``pr_fail``, not ``i_am_blocked`` which a reviewer
        lacks) via ``_conventions_rejection(..., reviewer=True)``.

        ``preferred_parent`` is the assembled task's real parent branch (see
        ``PRGateEvidenceMixin._gate_diff_parent``) — the same cross-team-correct base
        the gate's own diff evidence uses, so a misplaced-definition finding
        can't be raised against inherited base-branch content.
        """
        from roboco.config import settings as _settings

        if not _settings.conventions_enabled:
            return None
        result = await self.git.conventions_check_for_task(
            agent_id, task, preferred_parent=preferred_parent
        )
        return self._conventions_rejection(result, briefing, reviewer=True)

    @staticmethod
    def _conventions_rejection(
        result: dict[str, Any], briefing: dict[str, Any], *, reviewer: bool = False
    ) -> Envelope | None:
        """Turn a validator result into a rejection Envelope, or None to pass.

        ``reviewer=True`` for the pr_pass gate: a reviewer has no
        ``i_am_blocked`` verb, so the could_not_run remediation points at
        ``pr_fail`` instead.
        """
        if result.get("could_not_run"):
            if reviewer:
                remediate = (
                    "the validator failed to analyze the diff (a parse or grammar "
                    "error). call pr_fail(issues=['conventions: validator could "
                    "not run on the changed files']) so the PR returns to "
                    "needs_revision and the dev resolves it — do NOT pr_pass"
                )
            else:
                remediate = (
                    "the validator failed to analyze the diff (a parse or grammar "
                    "error). resolve it and call the verb again; if it persists, "
                    "call i_am_blocked"
                )
            return Envelope.invalid_state(
                message=(
                    "the architectural-conventions validator could not run on "
                    "your changed files — this blocks rather than passing silently"
                ),
                remediate=remediate,
                context_briefing=briefing,
            )
        blocks = [f for f in result.get("findings", []) if f.get("level") == "block"]
        if not blocks:
            return None
        listing = "\n".join(
            f"- {f.get('file')}:{f.get('line')} — {f.get('fix_hint')}" for f in blocks
        )
        if reviewer:
            # The pr_pass gate runs on the REVIEWER, who has no commit verb on
            # the assembled branch; the only lever is pr_fail — bounce the PR
            # back to needs_revision with the findings as issues so the dev
            # fixes the violation or commits a waiver.
            remediate = (
                "the assembled PR carries block-level architectural-convention"
                " violations. call pr_fail(issues=[<file:line — fix_hint>, ...])"
                " with the findings below so the PR returns to needs_revision and"
                " the dev places each definition in the module the architecture"
                " map assigns it, or — if a finding is a false positive — commits"
                " a waiver to .roboco/conventions.yml in the PR branch for review"
                " and re-submits:\n\n" + listing
            )
        else:
            remediate = (
                "place each definition in the module the architecture map assigns "
                "it, then commit and call the verb again. if a finding is a false "
                "positive, add a waiver to .roboco/conventions.yml in your branch "
                "for the PR to review:\n\n" + listing
            )
        return Envelope.invalid_state(
            message=(
                f"{len(blocks)} architectural-convention violation(s) must be "
                "fixed before this can proceed"
            ),
            remediate=remediate,
            context_briefing=briefing,
        )


class ResolvedFindingsMixin(_Base):
    """Resolving review findings on a resubmit (``resolved_findings``), with
    the advisory that maps each finding to the diff."""

    async def _apply_resolved_findings(self, ctx: _IAmDoneContext) -> None:
        """Best-effort: mark ledger findings 'addressed' from resolved_findings.

        Write-then-gate (mirrors dev_notes / the a2a warning pattern): this
        runs BEFORE ``_check_tracing_gates``, so a resolution applied here is
        immediately reflected in that gate's open-findings count. A malformed
        entry (bad ref, wrong task, already non-open) is silently skipped —
        the FINDINGS_ADDRESSED gate below re-surfaces anything that didn't
        actually get marked, naming the id, so nothing is lost quietly.
        """
        await self._apply_resolved_findings_for(ctx.task_id, ctx.resolved_findings)

    async def _apply_resolved_findings_for(
        self, task_id: UUID, resolved_findings: list[dict[str, Any]] | None
    ) -> None:
        """Task-id-keyed sibling of ``_apply_resolved_findings`` — shared by
        ``submit_up`` / ``submit_root``, which have no ``_IAmDoneContext`` of
        their own. Same write-then-gate posture: mark ledger rows 'addressed'
        BEFORE the FINDINGS_ADDRESSED gate re-reads open_finding_ids.
        """
        if not resolved_findings:
            return
        repo = ReviewFindingsRepository(self.task.session)
        addressed_rows: list[Any] = []
        for item in resolved_findings:
            ref = str(item.get("finding_id") or "").strip()
            if not ref:
                continue
            row = await repo.mark_addressed(
                task_id,
                ref,
                commit=item.get("commit"),
                note=item.get("note"),
            )
            if row is not None:
                addressed_rows.append(row)
        await self._findings_mapping_advisory(task_id, addressed_rows)

    async def _findings_mapping_advisory(self, task_id: UUID, rows: list[Any]) -> None:
        """B31 findings_mapping advisory (decisions pilot, default-off).

        After findings are marked addressed by id (historically with ZERO
        diff verification), batch one decisions call per finding ("this
        diff plausibly addresses finding F") and log a warning for every
        resolved finding with no plausible diff overlap. ADVISORY ONLY:
        the ledger rows stay addressed, nothing is closed, waived, or
        re-opened here - the warning is the audit trail QA/calibration
        reads. Fail-open: pilot off/unreachable/error changes nothing.
        """
        if not rows:
            return
        if not await self._findings_mapping_pilot_active(task_id):
            return
        t = await self.task.get(task_id)
        if t is None or not getattr(t, "branch_name", None):
            return
        diff, files_changed = await self._findings_mapping_diff_files(t, task_id)
        mapping = await self._findings_mapping_verdict(
            task_id, rows, diff, files_changed
        )
        if mapping is None:
            return
        self._warn_unmapped_findings(task_id, rows, mapping)

    async def _findings_mapping_pilot_active(self, task_id: UUID) -> bool:
        """The B31 pilot's on/off gate; fail-open (False) on error."""
        try:
            from roboco.services.decisions.pilots import PilotMode, pilot_mode

            mode = await pilot_mode(self.task.session, "findings_mapping")
        except Exception as exc:
            logger.warning(
                "findings_mapping_skip", task_id=str(task_id), error=str(exc)
            )
            return False
        return mode is not PilotMode.OFF

    async def _findings_mapping_diff_files(
        self, t: Any, task_id: UUID
    ) -> tuple[str, list[str]]:
        """Bounded diff + changed-files leg for the overlap screen; empty
        defaults when Decisions is disabled."""
        from roboco.config import settings as _settings

        if not _settings.decisions_enabled:
            return "", []
        evidence_gaps: list[str] = []
        budget = LegBudget(_settings.evidence_assembly_timeout_seconds)
        return await run_bounded_leg(
            self.git.diff_and_files(branch_name=t.branch_name),
            default=("", []),
            budget=budget,
            leg="findings-mapping diff + files",
            hint="overlap checks degraded; run the diff manually",
            task_id=task_id,
            gaps=evidence_gaps,
        )

    async def _findings_mapping_verdict(
        self,
        task_id: UUID,
        rows: list[Any],
        diff: str,
        files_changed: list[str],
    ) -> Any:
        """One batched findings_mapping call; None = no verdict (fail-open:
        the ledger rows stay addressed and nothing is logged)."""
        from roboco.services.decisions.pilots_gateway import findings_mapping

        findings = [
            {
                "id": str(row.id),
                "file": getattr(row, "file", None),
                "line": getattr(row, "line", None),
                "expected": getattr(row, "expected", None),
                "actual": getattr(row, "actual", None),
            }
            for row in rows
        ]
        try:
            return await findings_mapping(
                self.task.session,
                task_id=str(task_id),
                findings=findings,
                diff=diff,
                files_changed=list(files_changed),
            )
        except Exception as exc:
            logger.warning(
                "findings_mapping_skip", task_id=str(task_id), error=str(exc)
            )
            return None

    def _warn_unmapped_findings(
        self, task_id: UUID, rows: list[Any], mapping: Any
    ) -> None:
        """The advisory audit trail: one warning per resolved finding the
        diff plausibly does not touch. Nothing is closed or re-opened."""
        mapped_ids = {entry.get("finding_id") for entry in mapping}
        for row in rows:
            if str(row.id) in mapped_ids:
                continue
            logger.warning(
                "resolved_finding_zero_diff_overlap",
                task_id=str(task_id),
                finding_id=str(row.id)[:8],
                file=getattr(row, "file", None),
                note="advisory only; the ledger row stays addressed",
            )
