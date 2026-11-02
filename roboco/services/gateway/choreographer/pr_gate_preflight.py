"""Preflight for the in-path PR-review gate's verbs.

Split out of ``pr_gate.py`` for the same reason ``devops_gate.py`` /
``second_review_gate.py`` are: a focused, independently testable unit instead
of one more concern piled onto an already-large mixin. These are the checks a
gate verb runs before it does anything: the task fetch, the reviewer's role
and ownership (with the DevOps co-reviewer's carve-out), the spec gate with
its self-review wiring, and the tracing gate (a journal learning entry plus
substantive reviewer notes). ``claim_gate_review``, ``pr_pass``, ``pr_fail``
and ``record_devops_review`` all start here.

Inherits ``ChoreographerHelpers`` under ``TYPE_CHECKING`` only so mypy resolves
``self.task`` etc.; at runtime the composed ``Choreographer`` supplies the real
attributes via MRO (same pattern as ``QAMixin`` / ``PRReviewerMixin``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from roboco.config import settings
from roboco.foundation.policy import lifecycle as spec_module
from roboco.foundation.policy import tracing as _tr
from roboco.foundation.policy.content import markers
from roboco.services.gateway.choreographer import devops_gate as devops_gate_lib
from roboco.services.gateway.envelope import Envelope

if TYPE_CHECKING:
    from uuid import UUID

    from roboco.services.gateway.choreographer._protocol import ChoreographerHelpers

    _Base = ChoreographerHelpers
else:
    _Base = object


class PRGatePreflightMixin(_Base):
    """Role, ownership, spec and tracing checks every gate verb runs first."""

    async def _claim_gate_preflight(
        self, reviewer_agent_id: UUID, task_id: UUID
    ) -> Any:
        """Task fetch + role + spec gate for ``claim_gate_review``.

        Returns a rejection ``Envelope`` or the ``(t, role_str, briefing)``
        tuple on pass.
        """
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb="claim_gate_review",
            )
        agent = await self.task.agent_for(reviewer_agent_id)
        role_str = self._role_str_for_agent(agent)
        briefing = await self._briefing_for(reviewer_agent_id, task_id, full=True)
        role = await self._gate_role_or_rejection(
            t=t,
            role_str=role_str,
            briefing=briefing,
            agent_id=reviewer_agent_id,
            task_id=task_id,
            verb="claim_gate_review",
        )
        if isinstance(role, Envelope):
            return role
        spec_ctx = spec_module.Context(
            actor_id=reviewer_agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            agent_team=str(agent.team) if agent is not None and agent.team else None,
        )
        decision = spec_module.can_invoke_intent(role, "claim_gate_review", t, spec_ctx)
        if not decision.allowed:
            return await self._emit_rejection(
                Envelope.from_decision(decision, briefing=briefing).with_introspection(
                    task=t, role=role_str
                ),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb="claim_gate_review",
            )
        return (t, role_str, briefing)

    @staticmethod
    def _role_str_for_agent(agent: Any) -> str:
        """Reviewer role string off the agent view, defaulting to pr_reviewer."""
        return str(agent.role) if agent is not None else "pr_reviewer"

    async def _gate_preflight(
        self,
        reviewer_agent_id: UUID,
        task_id: UUID,
        verb: str,
        *,
        notes: str,
        issues: tuple[str, ...],
    ) -> Any:
        """Ownership + role + spec gate for pr_pass / pr_fail.

        Returns a rejection ``Envelope`` or the
        ``(t, agent, role_str, briefing, spec_ctx)`` tuple on pass.
        """
        t = await self._gate_ownership_or_rejection(reviewer_agent_id, task_id, verb)
        if isinstance(t, Envelope):
            return t
        agent = await self.task.agent_for(reviewer_agent_id)
        role_str = self._role_str_for_agent(agent)
        briefing = await self._briefing_for(reviewer_agent_id, task_id)
        role = await self._gate_role_or_rejection(
            t=t,
            role_str=role_str,
            briefing=briefing,
            agent_id=reviewer_agent_id,
            task_id=task_id,
            verb=verb,
        )
        if isinstance(role, Envelope):
            return role
        spec_ctx = self._gate_preflight_spec_ctx(
            reviewer_agent_id, agent, t, notes, issues
        )
        if soup := await self._guard_free_text(
            checks=(("notes", notes, 8), ("issues", list(issues), 8)),
            task=t,
            agent_id=reviewer_agent_id,
            role_str=role_str,
            verb=verb,
        ):
            return soup
        decision = spec_module.can_invoke_intent(role, verb, t, spec_ctx)
        if not decision.allowed:
            return await self._emit_rejection(
                Envelope.from_decision(decision, briefing=briefing).with_introspection(
                    task=t, role=role_str
                ),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb=verb,
            )
        return (t, agent, role_str, briefing, spec_ctx)

    async def _gate_ownership_or_rejection(
        self, reviewer_agent_id: UUID, task_id: UUID, verb: str
    ) -> Any:
        """Fetch the task and verify it is assigned to the reviewer.

        Returns the task on success, or a ``not_found`` / ``not_authorized``
        rejection ``Envelope`` on failure.

        Additive devops carve-out (the infra review gate, flag-gated): the
        DevOps co-reviewer is accepted too - it holds the ``devops_gate_claimant``
        marker instead of ownership, and must never steal the primary
        reviewer's ``assigned_to``. Flag off => the check below is byte-for-byte
        today (the helper returns False before any DB work).
        """
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb=verb,
            )
        if t.assigned_to != reviewer_agent_id and not await self._devops_gate_actor(
            t, reviewer_agent_id
        ):
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message="not assigned to you",
                    remediate="claim it via claim_gate_review(task_id) first",
                    context_briefing=await self._briefing_for(
                        reviewer_agent_id, task_id
                    ),
                ).with_introspection(task=t, role="pr_reviewer"),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb=verb,
            )
        return t

    async def _devops_gate_actor(self, t: Any, reviewer_agent_id: UUID) -> bool:
        """Whether ``reviewer_agent_id`` is a flag-armed DevOps co-reviewer
        holding the co-claim on ``t``. Kept as its own helper so the
        ownership guard's rejection path stays byte-for-byte when the flag
        is off (short-circuit before the agent lookup)."""

        if not settings.devops_enabled:
            return False
        agent = await self.task.agent_for(reviewer_agent_id)
        if agent is None or str(agent.role) != spec_module.Role.DEVOPS.value:
            return False
        return devops_gate_lib.is_devops_co_claimant(t, reviewer_agent_id)

    @staticmethod
    def _gate_preflight_spec_ctx(
        reviewer_agent_id: UUID, agent: Any, t: Any, notes: str, issues: tuple[str, ...]
    ) -> spec_module.Context:
        """Build the pr_pass / pr_fail spec ``Context``, including the
        self-review wiring.

        The spec gate's ``self_review_block`` is the only self-review defense
        for pr_pass / pr_fail: the service-layer ``_validate_not_self_review``
        backstop covers qa/documenter but skips pr_reviewer. For the comparison
        to fire, both sides must be populated. ``GatewayAgentView`` carries no
        ``slug`` field (so ``getattr(agent, "slug", None)`` is always None in
        production), and the ``original_developer`` marker stores the dev's
        UUID — so resolve both as UUID strings and let the spec's string
        equality do the rest. The marker is never set on assembled coordination
        tasks (only on dev-leaf tasks at QA/doc claim), so the block is dormant
        by design in production — but the gate is now correctly wired to fire
        if the marker were ever set to the reviewer.
        """
        return spec_module.Context(
            actor_id=reviewer_agent_id,
            actor_slug=str(reviewer_agent_id),
            agent_team=str(agent.team) if agent is not None and agent.team else None,
            original_developer_slug=markers.get_original_developer(t),
            notes=notes,
            issues=issues,
        )

    async def _gate_role_or_rejection(
        self,
        *,
        t: Any,
        role_str: str,
        briefing: dict[str, Any],
        agent_id: UUID,
        task_id: UUID,
        verb: str,
    ) -> Any:
        """Parse the role enum, or return a not_authorized rejection Envelope."""
        try:
            return spec_module.Role(role_str)
        except ValueError:
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message=f"unknown role '{role_str}'",
                    remediate="role is not declared in the lifecycle spec",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=agent_id,
                task_id=task_id,
                verb=verb,
            )

    async def _gate_tracing(
        self,
        reviewer_agent_id: UUID,
        task_id: UUID,
        t: Any,
        role_str: str,
        verb: str,
        *,
        notes: str,
    ) -> Envelope | None:
        """pr_pass / pr_fail require a journal:learning entry (parity with QA)
        plus a substantive pr_reviewer_notes section.

        The section note is the verb's own ``notes`` argument (the review
        verdict / issues), not yet persisted to the task, so it is threaded
        through a SimpleNamespace shim (the foundation checker reads
        ``task.pr_reviewer_notes`` — same write-then-gate pattern as qa_notes).
        """
        from roboco.config import settings as _settings

        has_learning = await self.journal.has_learning_for_task(
            reviewer_agent_id, task_id
        )
        ctx = _tr.GateContext(
            journal_learning_present=has_learning,
            pr_reviewer_notes_min_chars=_settings.pr_reviewer_notes_min_chars,
        )
        result = _tr.check_requirements(
            task=SimpleNamespace(pr_reviewer_notes=notes),
            requirements=list(_tr.requirements_for(verb)),
            ctx=ctx,
        )
        if result.passed:
            return None
        return await self._emit_rejection(
            (
                await self._build_tracing_gap(
                    reviewer_agent_id, task_id, result.missing
                )
            ).with_introspection(task=t, role=role_str),
            agent_id=reviewer_agent_id,
            task_id=task_id,
            verb=verb,
        )
