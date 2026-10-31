"""Typed stub mixins inherit from for static analysis.

The role mixins (`board.py`, `doc.py`, `qa.py`, …) call methods like
``self.task.get(...)`` and ``self._emit_rejection(...)`` that live on
``BaseChoreographer`` in ``_impl.py`` or on another mixin. Without a typed
reference, mypy reports each of those calls as ``attr-defined``, and silencing
that leaves an ``Any`` which then bubbles up as ``no-any-return`` errors at
every Envelope-returning verb.

This module gives mypy a typed view of those helpers. ``ChoreographerHelpers``
is **not** a Protocol — Protocol with abstract members causes the
composed ``Choreographer`` class to be flagged as instantiating an
abstract class. Instead it's a plain class with stub signatures used
ONLY under ``TYPE_CHECKING``; at runtime the mixins and ``BaseChoreographer``
inherit from ``object`` so there's no abstract-method baggage. The real
implementations are picked up by Python's MRO when the composed
``Choreographer`` runs.

The stubs are grouped by the class that defines each method: ``FooSurface``
declares what ``FooMixin`` (or, for ``BaseChoreographerSurface``,
``BaseChoreographer``) offers the other classes, each stub with the real
signature so every call across classes stays fully checked.
``ChoreographerHelpers`` composes the surfaces and declares the service
attributes. A method that moves to another class takes its stub to that
class's surface: ``tests/unit/services/test_choreographer_protocol.py`` fails
on a stub its class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from typing import Any
    from uuid import UUID

    from roboco.foundation.policy import lifecycle as spec_module
    from roboco.services.gateway.choreographer._impl import (
        ChoreographerDeps,
        DelegateInputs,
        PmDecisionOutcome,
        _ClaimPlanStartContext,
        _IAmDoneContext,
        _ReassignedCtx,
    )
    from roboco.services.gateway.choreographer._verb_runner import VerbRunner
    from roboco.services.gateway.envelope import Envelope


class BaseChoreographerSurface:
    """``BaseChoreographer``'s methods that the mixins call."""

    async def _touch(self, task_id: UUID | None) -> None:
        raise NotImplementedError

    def _verb_runner(self) -> VerbRunner:
        raise NotImplementedError

    async def _record_milestone_progress(
        self,
        task_id: UUID,
        agent_id: UUID,
        message: str,
        percentage: int | None = None,
    ) -> None:
        raise NotImplementedError


class BranchSyncSurface:
    """``BranchSyncMixin``'s methods that the other classes call."""

    async def _behind_base_gate(self, ctx: _IAmDoneContext) -> Envelope | None:
        raise NotImplementedError

    async def _branch_staleness_advisory(self, agent_id: UUID, t: Any) -> None:
        raise NotImplementedError

    async def _assembled_submit_guards(
        self, t: Any, task_id: UUID, verb: str
    ) -> tuple[Envelope | None, int | None]:
        raise NotImplementedError


class BriefingSurface:
    """``BriefingMixin``'s methods that the other classes call."""

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
        raise NotImplementedError


class ClaimGuardsSurface:
    """``ClaimGuardsMixin``'s methods that the other classes call."""

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
        raise NotImplementedError

    async def _drop_access_denied(self, tasks: list[Any], agent_id: UUID) -> list[Any]:
        raise NotImplementedError

    async def _budget_unblock_guard(self, t: Any) -> Envelope | None:
        raise NotImplementedError


class ClaimPlanSurface:
    """``ClaimPlanMixin``'s methods that the other classes call."""

    async def _claim_plan_start_gate(
        self,
        ctx: _ClaimPlanStartContext,
        role: spec_module.Role,
        spec_ctx: spec_module.Context,
    ) -> Envelope | None:
        raise NotImplementedError

    async def _claim_plan_start_run(
        self, ctx: _ClaimPlanStartContext, agent: Any, spec_ctx: spec_module.Context
    ) -> Envelope:
        raise NotImplementedError

    async def _post_claim_journal_gate(
        self,
        verb: str,
        agent_id: UUID,
        task_id: UUID,
        envelope: Envelope,
    ) -> Envelope:
        raise NotImplementedError

    @staticmethod
    def _resolve_effective_plan(
        plan: str, rich_plan: dict[str, Any] | None
    ) -> str | dict[str, Any]:
        raise NotImplementedError

    async def _resume_from_claimed(
        self,
        ctx: _ClaimPlanStartContext,
    ) -> Envelope:
        raise NotImplementedError


class DelegateSurface:
    """``DelegateMixin``'s methods that the other classes call."""

    @staticmethod
    def _resolve_delegate_enums(inputs: DelegateInputs) -> tuple[Any, Any, Any]:
        raise NotImplementedError


class DoneChecksSurface:
    """``DoneChecksMixin``'s methods that the other classes call."""

    async def _check_quality_gate(self, ctx: _IAmDoneContext) -> Envelope | None:
        raise NotImplementedError

    async def _maybe_i_am_done_fast_path(
        self, ctx: _IAmDoneContext, t: Any, agent_id: UUID
    ) -> Envelope | None:
        raise NotImplementedError

    async def _open_finding_ids(self, task_id: UUID) -> tuple[str, ...]:
        raise NotImplementedError

    async def _write_criteria_status(
        self, agent_id: UUID, task_id: UUID, t: Any
    ) -> None:
        raise NotImplementedError


class GiveMeWorkSurface:
    """``GiveMeWorkMixin``'s methods that the other classes call."""

    async def _pending_not_lane_held(self, pending: list[Any]) -> list[Any]:
        raise NotImplementedError


class IAmDoneSurface:
    """``IAmDoneMixin``'s methods that the other classes call."""

    async def _build_i_am_done_ok(
        self, agent_id: UUID, task_id: UUID, t: Any
    ) -> Envelope:
        raise NotImplementedError

    async def _check_submit_qa_field_gates(
        self, agent_id: UUID, task_id: UUID, t: Any
    ) -> Envelope | None:
        raise NotImplementedError

    async def _notify_qa(self, agent_id: UUID, task_id: UUID, t: Any) -> str | None:
        raise NotImplementedError

    async def _reject_i_am_done(self, ctx: _IAmDoneContext, env: Envelope) -> Envelope:
        raise NotImplementedError


class IdleGuardsSurface:
    """``IdleGuardsMixin``'s methods that the other classes call."""

    async def _auditor_note_guard(
        self, agent_id: UUID, briefing: dict[str, Any]
    ) -> Envelope | None:
        raise NotImplementedError

    async def _pending_assignment_guard(
        self, agent_id: UUID, briefing: dict[str, Any]
    ) -> Envelope | None:
        raise NotImplementedError

    async def _pm_uncovered_decomposition_guard(
        self, agent_id: UUID, briefing: dict[str, Any]
    ) -> Envelope | None:
        raise NotImplementedError

    async def _pm_uncovered_required_cells_guard(
        self, agent_id: UUID, briefing: dict[str, Any]
    ) -> Envelope | None:
        raise NotImplementedError

    async def _pm_unfinished_review_guard(
        self, agent_id: UUID, briefing: dict[str, Any]
    ) -> Envelope | None:
        raise NotImplementedError


class OpenPRSurface:
    """``OpenPRMixin``'s methods that the other classes call."""

    async def _ensure_branch_pushed(self, ctx: _IAmDoneContext) -> Envelope | None:
        raise NotImplementedError


class PMWorkSurface:
    """``PMWorkMixin``'s methods that the other classes call."""

    async def _own_review_hint(self, pm_agent_id: UUID, exclude_task_id: UUID) -> str:
        raise NotImplementedError


class PRGatePassSurface:
    """``PRGatePassMixin``'s methods that the other classes call."""

    async def _resolve_ci_status(self, task_id: UUID, t: Any) -> dict[str, Any] | None:
        raise NotImplementedError


class PRReviewerSurface:
    """``PRReviewerMixin``'s methods that the other classes call."""

    async def _project_slug_for(self, t: Any) -> str | None:
        raise NotImplementedError


class QualityGuardsSurface:
    """``QualityGuardsMixin``'s methods that the other classes call."""

    async def _toolchain_broken_guard(
        self, agent_id: UUID, task: Any, *, reviewer: bool = False
    ) -> Envelope | None:
        raise NotImplementedError

    async def _conventions_guard(
        self,
        agent_id: UUID,
        task: Any,
        briefing: dict[str, Any],
        preferred_parent: str | None = None,
    ) -> Envelope | None:
        raise NotImplementedError

    async def _conventions_gate(self, ctx: _IAmDoneContext) -> Envelope | None:
        raise NotImplementedError


class RejectionSurface:
    """``RejectionMixin``'s methods that the other classes call."""

    async def _emit_rejection(
        self,
        env: Envelope,
        *,
        agent_id: UUID,
        task_id: UUID | None,
        verb: str,
    ) -> Envelope:
        raise NotImplementedError

    async def _teardown_sandbox_best_effort(self, agent_id: UUID) -> None:
        raise NotImplementedError

    @classmethod
    def _free_text_soup(
        cls, checks: tuple[tuple[str, Any, int], ...]
    ) -> Envelope | None:
        raise NotImplementedError

    @staticmethod
    def _soup_or_decision_env(
        soup: Envelope | None, decision: Any, briefing: dict[str, Any]
    ) -> Envelope | None:
        raise NotImplementedError

    async def _guard_free_text(
        self,
        *,
        checks: tuple[tuple[str, Any, int], ...],
        task: Any,
        agent_id: UUID,
        role_str: str,
        verb: str,
    ) -> Envelope | None:
        raise NotImplementedError

    @staticmethod
    def _with_briefing(
        env: Envelope,
        briefing: dict[str, Any],
    ) -> Envelope:
        raise NotImplementedError

    @staticmethod
    def _reassigned_rejection(
        ctx: _ReassignedCtx,
    ) -> Envelope | None:
        raise NotImplementedError

    @staticmethod
    def _soup_reason(value: str, field: str, min_chars: int) -> Envelope | None:
        raise NotImplementedError

    @staticmethod
    def _runner_failed_envelope(
        exc: Exception, t: Any, briefing: Any, role_str: str
    ) -> Envelope:
        raise NotImplementedError

    @staticmethod
    def _step_failed_envelope(
        step: str, ctx: _ClaimPlanStartContext, briefing: Any, role_str: str
    ) -> Envelope:
        raise NotImplementedError


class ResolvedFindingsSurface:
    """``ResolvedFindingsMixin``'s methods that the other classes call."""

    async def _apply_resolved_findings(self, ctx: _IAmDoneContext) -> None:
        raise NotImplementedError

    async def _apply_resolved_findings_for(
        self, task_id: UUID, resolved_findings: list[dict[str, Any]] | None
    ) -> None:
        raise NotImplementedError


class SiblingDedupSurface:
    """``SiblingDedupMixin``'s methods that the other classes call."""

    async def _delegate_sibling_dedup_guard(
        self,
        parent_task_id: UUID,
        inputs: DelegateInputs,
    ) -> Envelope | None:
        raise NotImplementedError

    @classmethod
    def _delegate_sizing_guard(cls, inputs: DelegateInputs) -> Envelope | None:
        raise NotImplementedError

    @classmethod
    def _sizing_hint(cls, inputs: DelegateInputs) -> str | None:
        raise NotImplementedError


class SubmitRootSurface:
    """``SubmitRootMixin``'s methods that the other classes call."""

    async def _unchanged_pr_guard(
        self,
        t: Any,
        briefing: dict[str, Any],
        *,
        verb: str,
        pr_scope: str,
        delegate_target: str,
        subtask_noun: str,
        work_noun: str,
    ) -> Envelope | None:
        raise NotImplementedError


class SubmitUpSurface:
    """``SubmitUpMixin``'s methods that the other classes call."""

    async def _notify_pr_reviewer(self, task_id: UUID, t: Any) -> None:
        raise NotImplementedError

    async def _parent_acs_covered_envelope(
        self,
        agent_id: UUID,
        task_id: UUID,
        *,
        context_phrase: str,
    ) -> Envelope | None:
        raise NotImplementedError

    async def _submit_up_guard(
        self, pm_agent_id: UUID, task_id: UUID, t: Any, notes: str
    ) -> Envelope | None:
        raise NotImplementedError

    async def _subtasks_not_terminal_envelope(
        self,
        agent_id: UUID,
        task_id: UUID,
        *,
        context_phrase: str,
    ) -> Envelope | None:
        raise NotImplementedError


class SubtaskCreateSurface:
    """``SubtaskCreateMixin``'s methods that the other classes call."""

    async def _create_subtask_from_inputs(
        self,
        pm_agent_id: UUID,
        parent_task_id: UUID,
        parent: Any,
        inputs: DelegateInputs,
    ) -> Any:
        raise NotImplementedError

    async def _wire_ux_frontend_dependency(self, new_task: Any, parent: Any) -> None:
        raise NotImplementedError


class TracingGatesSurface:
    """``TracingGatesMixin``'s methods that the other classes call."""

    async def _build_tracing_gap(
        self,
        agent_id: UUID,
        task_id: UUID,
        missing: list[str],
        *,
        task: Any | None = None,
    ) -> Envelope:
        raise NotImplementedError

    async def _check_tracing_gates(
        self, agent_id: UUID, task_id: UUID, t: Any
    ) -> Envelope | None:
        raise NotImplementedError

    async def _check_complete_gates(
        self,
        agent_id: UUID,
        task_id: UUID,
        notes: str,
        *,
        pm_decision_outcome: PmDecisionOutcome | None = None,
    ) -> Envelope | None:
        raise NotImplementedError

    async def _check_pm_decision_required(
        self,
        verb: str,
        agent_id: UUID,
        task_id: UUID,
        t: Any,
        *,
        pm_decision_outcome: PmDecisionOutcome | None = None,
    ) -> Envelope | None:
        raise NotImplementedError

    async def _check_submit_up_gates(
        self,
        agent_id: UUID,
        task_id: UUID,
        notes: str,
        *,
        pm_decision_outcome: PmDecisionOutcome | None = None,
    ) -> Envelope | None:
        raise NotImplementedError

    async def _ensure_pm_decision(
        self, agent_id: UUID, task_id: UUID, rationale: str | None
    ) -> PmDecisionOutcome:
        raise NotImplementedError


class ChoreographerHelpers(
    BaseChoreographerSurface,
    BranchSyncSurface,
    BriefingSurface,
    ClaimGuardsSurface,
    ClaimPlanSurface,
    DelegateSurface,
    DoneChecksSurface,
    GiveMeWorkSurface,
    IAmDoneSurface,
    IdleGuardsSurface,
    OpenPRSurface,
    PMWorkSurface,
    PRGatePassSurface,
    PRReviewerSurface,
    QualityGuardsSurface,
    RejectionSurface,
    ResolvedFindingsSurface,
    SiblingDedupSurface,
    SubmitRootSurface,
    SubmitUpSurface,
    SubtaskCreateSurface,
    TracingGatesSurface,
):
    """Typed stub of attributes + helpers role mixins call on ``self``.

    Stub bodies are never executed; the real methods live on the class each
    surface names and resolve via MRO at runtime.
    """

    _COORDINATOR_ROLES: ClassVar[frozenset[str]]
    _COVERS_PARENT_CRITERIA_HINT: ClassVar[str]
    _TERMINAL_STATUSES: ClassVar[frozenset[str]]
    _deps: ChoreographerDeps
    task: Any
    work_session: Any
    git: Any
    a2a: Any
    journal: Any
    audit: Any
    evidence_repo: Any
    product: Any
    project: Any
    orchestrator: Any
    stream_bus: Any


def actor_context_fields(agent: Any) -> tuple[str | None, str | None]:
    """``(actor_slug, agent_team)`` for a spec ``Context``, None-agent safe.

    Every verb builds its policy Context with the same two conditional
    kwargs; inlining them pushed nine verbs over the xenon B threshold when
    the team-match sweep added ``agent_team`` to all 27 sites.
    """
    if agent is None:
        return None, None
    team = getattr(agent, "team", None)
    return getattr(agent, "slug", None), str(team) if team else None
