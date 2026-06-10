"""QA verbs (third per-role split).

Mixin for ``claim_review``, ``pass_review``, ``fail_review`` and the
verb-specific helper ``_qa_pass_gate_check``. The helper stays with
the verbs that use it — it's not used by any other role.

Inherits from ``ChoreographerHelpers`` under ``TYPE_CHECKING`` only so
mypy resolves ``self.task`` etc. as typed; at runtime the composed
``Choreographer`` supplies the real attributes via MRO.

All three verbs route their role/state gate through
``spec.can_invoke_intent``. The verb-specific
helpers (``_verify_qa_owner``, ``_qa_pass_gate_check``) STAY — they
encode notes-length / journal:learning / qa_evidence_inspected gates
the spec doesn't model. The self-review block lives at the atomic-
action layer (``_ATOMIC_ACTIONS["qa_pass" | "qa_fail"]
.self_review_block=True``) and naturally fires when the verb body
builds a Context with ``actor_slug == original_developer_slug``; no
verb-body retrofits needed.

``_qa_pass_gate_check`` delegates the actual requirement
checking to ``foundation.policy.tracing.check_requirements`` — the
verb→required-set mapping lives in ``VERB_REQUIREMENTS`` (single
source of truth). The hint translation lives in the shared
``_build_tracing_gap`` on Choreographer.

``claim_review`` composes ``("claim", "start")`` in the spec, but the
runtime semantic is "QA inspects, status stays at awaiting_qa". The
verb body therefore owns dispatch via ``task.qa_claim`` (mirroring the
``escalate_up`` empty-composes pattern) so subsequent ``pass_review``
/ ``fail_review`` calls still find the task in awaiting_qa as the
spec's ``qa_pass`` / ``qa_fail`` source-status requires. The spec gate
still validates role + claim source-status + task_type before dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from roboco.config import settings
from roboco.foundation.policy import lifecycle as spec_module
from roboco.foundation.policy import tracing as _tr
from roboco.services.gateway.envelope import Envelope
from roboco.services.gateway.evidence_builder import build_evidence_for_task

if TYPE_CHECKING:
    from uuid import UUID

    from roboco.services.gateway.choreographer._protocol import ChoreographerHelpers

    _Base = ChoreographerHelpers
else:
    _Base = object


@dataclass(frozen=True)
class _QASpecGateInputs:
    """Inputs for ``_qa_review_spec_gate`` bundled to keep PLR0913 at bay.

    Frozen so the helper sites can't mutate caller state.
    """

    qa_agent_id: Any
    task_id: Any
    task: Any
    verb: str
    notes: str
    issues: tuple[str, ...] = field(default_factory=tuple)


def _extract_original_developer(task: Any) -> str | None:
    """Pull the original_developer slug out of a task's quick_context, if any.

    Mirrors ``_impl._extract_original_developer``. Lives here too so
    QA / Doc mixins don't depend on ``_impl``'s module-level helper —
    the spec's self-review block reads ``ctx.original_developer_slug``
    and the mixins build the Context.
    """
    qc = getattr(task, "quick_context", None) or ""
    marker = "original_developer:"
    if marker not in qc:
        return None
    tail = qc.split(marker, 1)[1].strip()
    if not tail:
        return None
    return tail.split()[0] or None


class QAMixin(_Base):
    """QA-role verbs."""

    async def claim_review(self, qa_agent_id: UUID, task_id: UUID) -> Envelope:
        """QA agent claims task in awaiting_qa for review.

        Spec gate runs first and enforces role membership (qa only) plus
        the composed ``claim`` action's source-status constraint
        (AWAITING_QA is one of the allowed sources). After the gate
        accepts, the verb body owns dispatch via ``task.qa_claim``
        (specialized claim that keeps status at AWAITING_QA so the
        downstream ``qa_pass`` / ``qa_fail`` source-status requirement
        still matches). The behavioral claim guards (already_active /
        paused) still run after the spec gate — they encode concurrency
        invariants the spec doesn't model.

        The response includes evidence (pr_url, pr_number, commits,
        files_changed, journal_highlights, acceptance_criteria_status)
        INLINE so the QA agent cannot miss the PR data. Marks
        ``qa_evidence_inspected=true`` automatically.
        """
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="claim_review",
            )
        agent = await self.task.agent_for(qa_agent_id)
        role_str = str(agent.role) if agent is not None else "qa"
        briefing = await self._briefing_for(qa_agent_id, task_id)
        try:
            role = spec_module.Role(role_str)
        except ValueError:
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message=f"unknown role '{role_str}'",
                    remediate="role is not declared in the lifecycle spec",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="claim_review",
            )
        spec_ctx = spec_module.Context(
            actor_id=qa_agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            original_developer_slug=_extract_original_developer(t),
        )
        decision = spec_module.can_invoke_intent(role, "claim_review", t, spec_ctx)
        if not decision.allowed:
            return await self._emit_rejection(
                Envelope.from_decision(decision, briefing=briefing).with_introspection(
                    task=t, role=role_str
                ),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="claim_review",
            )

        guard = await self._run_claim_guards(
            agent_id=qa_agent_id,
            task=t,
        )
        if guard:
            guard.with_introspection(task=t, role=role_str)
            return await self._emit_rejection(
                self._with_briefing(guard, briefing),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="claim_review",
            )

        # Verb body owns dispatch — claim_review's spec composes=("claim",
        # "start") but qa_claim is the runtime-correct specialized form
        # that keeps status at AWAITING_QA so qa_pass / qa_fail's source-
        # status requirement matches downstream. See module docstring.
        t = await self.task.qa_claim(qa_agent_id, task_id)
        await self.task.mark_evidence_inspected(task_id)

        ev = await self._build_qa_claim_evidence(t, task_id)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS["claim_review"].next_hint(t),
            evidence=ev.as_dict(),
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)

    async def _build_qa_claim_evidence(self, t: Any, task_id: UUID) -> Any:
        """Assemble the inline evidence payload returned by claim_review.

        Bundles files_changed + pr_diff_summary (both from git, the
        authoritative source) + journal_highlights so the QA agent has
        the full PR context up-front and can't miss a piece.

        files_changed comes from ``git.list_changed_files``
        instead of ``work_session.files_modified``. The legacy
        ``add_files_modified`` HTTP path that populated files_modified
        is not called by the gateway ``commit()``, so the work_session
        list was always empty — QA saw no files even on real PRs.
        """
        files_changed: list[str] = []
        diff_summary = ""
        if t.branch_name:
            diff_summary = await self.git.diff(branch_name=t.branch_name)
            files_changed = await self.git.list_changed_files(branch_name=t.branch_name)
        journal_highlights = await self.evidence_repo.journal_highlights_for_task(
            task_id
        )
        return build_evidence_for_task(
            t,
            journal_highlights=journal_highlights,
            files_changed=files_changed,
            pr_diff_summary=diff_summary,
        )

    async def _verify_qa_owner(
        self, qa_agent_id: UUID, task_id: UUID, verb: str
    ) -> tuple[Envelope | None, Any]:
        """Lookup task + verify QA is the assignee. Returns (rejection, task)."""
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb=verb,
            ), None
        if t.assigned_to != qa_agent_id:
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message="not assigned to you",
                    remediate="claim it via claim_review(task_id) first",
                    context_briefing=await self._briefing_for(qa_agent_id, task_id),
                ).with_introspection(task=t, role="qa"),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb=verb,
            ), None
        return None, t

    async def _qa_pass_gate_check(
        self, qa_agent_id: UUID, task_id: UUID, notes: str, t: Any, verb: str
    ) -> Envelope | None:
        """QA pass/fail-gate evaluation via foundation.policy.tracing.

        Returns rejection envelope or None on pass. The required-set
        for ``pass_review`` and ``fail_review`` is identical (both need
        QA_NOTES_MIN_CHARS + QA_EVIDENCE_INSPECTED + JOURNAL_LEARNING),
        so a single helper handles both — the caller just passes the
        verb name through for VERB_REQUIREMENTS lookup.

        The verb's ``notes`` argument hasn't been persisted to the task
        yet (the spec runner writes it via the atomic action), so we
        thread it through a SimpleNamespace shim with the minimal
        attributes the foundation checkers read off the task object
        (qa_notes + qa_evidence_inspected — see
        foundation.policy.tracing._check_qa_notes_min_chars and
        _check_qa_evidence_inspected).
        """
        has_learning = await self.journal.has_learning_for_task(qa_agent_id, task_id)
        task_view = SimpleNamespace(
            qa_notes=notes,
            qa_evidence_inspected=getattr(t, "qa_evidence_inspected", False),
        )
        ctx = _tr.GateContext(
            journal_learning_present=has_learning,
            qa_notes_min_chars=settings.qa_notes_min_chars,
        )
        result = _tr.check_requirements(
            task=task_view,
            requirements=list(_tr.requirements_for(verb)),
            ctx=ctx,
        )
        if result.passed:
            return None
        return await self._emit_rejection(
            (
                await self._build_tracing_gap(qa_agent_id, task_id, result.missing)
            ).with_introspection(task=t, role="qa"),
            agent_id=qa_agent_id,
            task_id=task_id,
            verb=verb,
        )

    async def _qa_review_spec_gate(
        self, inputs: _QASpecGateInputs
    ) -> tuple[Envelope | None, Any, str]:
        """Run the spec gate for pass_review / fail_review.

        Returns (rejection, agent, role_str). Builds the Context with
        ``actor_slug`` + ``original_developer_slug`` so the underlying
        atomic action's ``self_review_block=True`` fires when the QA
        agent is the original developer of the task.
        """
        qa_agent_id = inputs.qa_agent_id
        task_id = inputs.task_id
        t = inputs.task
        verb = inputs.verb
        agent = await self.task.agent_for(qa_agent_id)
        role_str = str(agent.role) if agent is not None else "qa"
        briefing = await self._briefing_for(qa_agent_id, task_id)
        try:
            role = spec_module.Role(role_str)
        except ValueError:
            return (
                await self._emit_rejection(
                    Envelope.not_authorized(
                        message=f"unknown role '{role_str}'",
                        remediate="role is not declared in the lifecycle spec",
                        context_briefing=briefing,
                    ).with_introspection(task=t, role=role_str),
                    agent_id=qa_agent_id,
                    task_id=task_id,
                    verb=verb,
                ),
                agent,
                role_str,
            )
        spec_ctx = spec_module.Context(
            actor_id=qa_agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            original_developer_slug=_extract_original_developer(t),
            notes=inputs.notes,
            issues=inputs.issues,
        )
        decision = spec_module.can_invoke_intent(role, verb, t, spec_ctx)
        if not decision.allowed:
            return (
                await self._emit_rejection(
                    Envelope.from_decision(
                        decision, briefing=briefing
                    ).with_introspection(task=t, role=role_str),
                    agent_id=qa_agent_id,
                    task_id=task_id,
                    verb=verb,
                ),
                agent,
                role_str,
            )
        return None, agent, role_str

    async def pass_review(
        self, qa_agent_id: UUID, task_id: UUID, notes: str
    ) -> Envelope:
        """QA passes the task; transitions awaiting_qa → awaiting_documentation.

        Spec gate runs first and enforces role membership (qa) plus the
        composed ``qa_pass`` action's source-status (AWAITING_QA),
        task_type, and self-review block (the atomic action's
        ``self_review_block=True`` rejects when the QA actor's slug
        matches the original developer's). After the spec gate accepts,
        the verb-specific gates stay (ownership via ``_verify_qa_owner``
        and notes-length / journal:learning / qa_evidence_inspected via
        ``_qa_pass_gate_check``); none of those are modelled by the spec.
        The composed atomic ``qa_pass`` is then dispatched through
        ``VerbRunner.run_intent``, after which the verb body reassigns
        the documenter for handoff.
        """
        rejection, t = await self._verify_qa_owner(qa_agent_id, task_id, "pass_review")
        if rejection is not None:
            return rejection
        spec_rejection, agent, role_str = await self._qa_review_spec_gate(
            _QASpecGateInputs(
                qa_agent_id=qa_agent_id,
                task_id=task_id,
                task=t,
                verb="pass_review",
                notes=notes,
            )
        )
        if spec_rejection is not None:
            return spec_rejection
        gate_rejection = await self._qa_pass_gate_check(
            qa_agent_id, task_id, notes, t, "pass_review"
        )
        if gate_rejection is not None:
            return gate_rejection

        briefing = await self._briefing_for(qa_agent_id, task_id)
        spec_ctx = spec_module.Context(
            actor_id=qa_agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            original_developer_slug=_extract_original_developer(t),
            notes=notes,
        )
        runner = self._verb_runner()
        try:
            t = await runner.run_intent("pass_review", t, agent, spec_ctx)
        except Exception as exc:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"verb runner failed: {exc}",
                    remediate="check workspace + retry; if persistent, escalate",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="pass_review",
            )

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
            next=spec_module._INTENT_VERBS["pass_review"].next_hint(t),
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)

    async def fail_review(
        self, qa_agent_id: UUID, task_id: UUID, issues: list[str]
    ) -> Envelope:
        """QA fails the task with concrete issues; transitions to needs_revision.

        Spec gate runs first and enforces role membership (qa) plus the
        composed ``qa_fail`` action's source-status (AWAITING_QA),
        task_type, and self-review block (same shape as ``pass_review``).
        After the spec gate accepts, the verb-specific gates stay
        (ownership via ``_verify_qa_owner``, ``issues`` non-empty, and
        notes-length / journal:learning / qa_evidence_inspected via
        ``_qa_pass_gate_check``). The composed atomic ``qa_fail`` is
        dispatched through ``VerbRunner.run_intent``, then the verb body
        notifies the original developer via a2a.
        """
        rejection, t = await self._verify_qa_owner(qa_agent_id, task_id, "fail_review")
        if rejection is not None:
            return rejection
        if not issues:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message="fail_review requires at least one issue",
                    remediate="pass issues=['<concrete actionable issue>', ...]",
                    context_briefing=await self._briefing_for(qa_agent_id, task_id),
                ).with_introspection(task=t, role="qa"),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="fail_review",
            )

        notes = "Issues:\n" + "\n".join(f"- {issue}" for issue in issues)
        spec_rejection, agent, role_str = await self._qa_review_spec_gate(
            _QASpecGateInputs(
                qa_agent_id=qa_agent_id,
                task_id=task_id,
                task=t,
                verb="fail_review",
                notes=notes,
                issues=tuple(issues),
            )
        )
        if spec_rejection is not None:
            return spec_rejection
        gate_rejection = await self._qa_pass_gate_check(
            qa_agent_id, task_id, notes, t, "fail_review"
        )
        if gate_rejection is not None:
            return gate_rejection

        briefing = await self._briefing_for(qa_agent_id, task_id)
        spec_ctx = spec_module.Context(
            actor_id=qa_agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            original_developer_slug=_extract_original_developer(t),
            notes=notes,
            issues=tuple(issues),
        )
        runner = self._verb_runner()
        try:
            t = await runner.run_intent("fail_review", t, agent, spec_ctx)
        except Exception as exc:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"verb runner failed: {exc}",
                    remediate="check workspace + retry; if persistent, escalate",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="fail_review",
            )

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
            next=spec_module._INTENT_VERBS["fail_review"].next_hint(t),
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)
