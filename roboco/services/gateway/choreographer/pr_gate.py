"""In-path PR-review gate verbs (assembled cell→root + root→master PRs).

Mixin for ``claim_gate_review``, ``pr_pass`` and ``pr_fail`` — the reviewer
side of the in-path gate. Distinct from ``PRReviewerMixin`` (inbound
external/fork PRs): these GATE an internal delivery task between the PM's
submit (submit_up / submit_root) and the PM's merge. The reviewer reads the
assembled diff and either passes it on to awaiting_pm_review or fails it back to
needs_revision — exactly like QA's pass_review / fail_review, but at the
assembled-PR level.

Inherits ``ChoreographerHelpers`` under ``TYPE_CHECKING`` only so mypy resolves
``self.task`` etc.; at runtime the composed ``Choreographer`` supplies the real
attributes via MRO (same pattern as ``QAMixin`` / ``PRReviewerMixin``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import structlog

from roboco.foundation.policy import lifecycle as spec_module
from roboco.foundation.policy import tracing as _tr
from roboco.services.gateway.envelope import Envelope

if TYPE_CHECKING:
    from uuid import UUID

    from roboco.services.gateway.choreographer._protocol import ChoreographerHelpers

    _Base = ChoreographerHelpers
else:
    _Base = object

logger = structlog.get_logger()


class PRGateMixin(_Base):
    """In-path PR-review-gate verbs (assembled cell→root + root→master PRs)."""

    async def claim_gate_review(
        self, reviewer_agent_id: UUID, task_id: UUID
    ) -> Envelope:
        """Reviewer claims an awaiting_pr_review task without transitioning it.

        Mirrors QA's claim_review: the spec gate enforces role (pr_reviewer) +
        the claim source-status (AWAITING_PR_REVIEW); the verb body then claims
        without transition (status stays awaiting_pr_review) so the downstream
        pr_pass / pr_fail source-status still matches. The assembled PR's diff is
        returned inline (read-only) so the reviewer inspects it before deciding.
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
        role_str = str(agent.role) if agent is not None else "pr_reviewer"
        briefing = await self._briefing_for(reviewer_agent_id, task_id)
        role = await self._gate_role_or_rejection(
            t, role_str, briefing, reviewer_agent_id, task_id, "claim_gate_review"
        )
        if isinstance(role, Envelope):
            return role
        spec_ctx = spec_module.Context(
            actor_id=reviewer_agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
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
        guard = await self._run_claim_guards(agent_id=reviewer_agent_id, task=t)
        if guard:
            guard.with_introspection(task=t, role=role_str)
            return await self._emit_rejection(
                self._with_briefing(guard, briefing),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb="claim_gate_review",
            )
        claimed = await self.task.pr_gate_claim(reviewer_agent_id, task_id)
        if claimed is None:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message="this assembled-PR review task is no longer claimable",
                    remediate="it may already be claimed; give_me_work for the next",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb="claim_gate_review",
            )
        t = claimed
        evidence = await self._build_gate_review_evidence(t)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS["claim_gate_review"].next_hint(t),
            evidence=evidence,
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)

    async def pr_pass(
        self, reviewer_agent_id: UUID, task_id: UUID, notes: str
    ) -> Envelope:
        """Pass the assembled PR; awaiting_pr_review → awaiting_pm_review."""
        return await self._gate_decision(
            reviewer_agent_id, task_id, "pr_pass", notes=notes, issues=()
        )

    async def pr_fail(
        self, reviewer_agent_id: UUID, task_id: UUID, issues: list[str]
    ) -> Envelope:
        """Fail the assembled PR with concrete issues; → needs_revision."""
        if not issues:
            t = await self.task.get(task_id)
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message="pr_fail requires at least one issue",
                    remediate="pass issues=['<concrete actionable issue>', ...]",
                    context_briefing=await self._briefing_for(
                        reviewer_agent_id, task_id
                    ),
                ).with_introspection(task=t, role="pr_reviewer"),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb="pr_fail",
            )
        notes = "Issues:\n" + "\n".join(f"- {issue}" for issue in issues)
        return await self._gate_decision(
            reviewer_agent_id, task_id, "pr_fail", notes=notes, issues=tuple(issues)
        )

    # -- helpers ----------------------------------------------------------

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
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb=verb,
            )
        if t.assigned_to != reviewer_agent_id:
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
        agent = await self.task.agent_for(reviewer_agent_id)
        role_str = str(agent.role) if agent is not None else "pr_reviewer"
        briefing = await self._briefing_for(reviewer_agent_id, task_id)
        role = await self._gate_role_or_rejection(
            t, role_str, briefing, reviewer_agent_id, task_id, verb
        )
        if isinstance(role, Envelope):
            return role
        spec_ctx = spec_module.Context(
            actor_id=reviewer_agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            notes=notes,
            issues=issues,
        )
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

    async def _gate_decision(
        self,
        reviewer_agent_id: UUID,
        task_id: UUID,
        verb: str,
        *,
        notes: str,
        issues: tuple[str, ...],
    ) -> Envelope:
        """Shared body for pr_pass / pr_fail: preflight + tracing + run."""
        pre = await self._gate_preflight(
            reviewer_agent_id, task_id, verb, notes=notes, issues=issues
        )
        if isinstance(pre, Envelope):
            return pre
        t, agent, role_str, briefing, spec_ctx = pre
        gate = await self._gate_tracing(reviewer_agent_id, task_id, t, role_str, verb)
        if gate is not None:
            return gate
        runner = self._verb_runner()
        try:
            t = await runner.run_intent(verb, t, agent, spec_ctx)
        except Exception as exc:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"verb runner failed: {exc}",
                    remediate="retry; if persistent, unclaim and notify the CEO",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb=verb,
            )
        # Leave the gate verdict on the PR itself so there's a visible trail on
        # the very PR the PM (or CEO) merges. Best-effort and AFTER the DB
        # transition — a GitHub failure must not roll back the gate decision.
        reviewer_slug = getattr(agent, "slug", None) or role_str
        await self._post_gate_review_to_pr(t, verb, reviewer_slug, notes)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS[verb].next_hint(t),
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)

    async def _post_gate_review_to_pr(
        self, t: Any, verb: str, reviewer_slug: str, notes: str
    ) -> None:
        """Post the gate verdict as a review on the assembled PR (best-effort).

        ``pr_pass`` posts an APPROVE, ``pr_fail`` a REQUEST_CHANGES — EXCEPT on
        the root→master PR (a root task has no ``parent_task_id``), which always
        gets a plain COMMENT: only the CEO acts on master, so the gate never
        leaves an approval that could satisfy branch protection and let anyone
        else merge, nor a blocking review that could impede the CEO's merge.
        For the org's own PRs ``git.post_pr_review`` already downgrades a
        forbidden self-review to a COMMENT, so the verdict lands regardless.
        """
        slug = await self._project_slug_for(t)
        pr_number = getattr(t, "pr_number", None)
        if not slug or not pr_number:
            return
        is_root = getattr(t, "parent_task_id", None) is None
        if verb == "pr_pass":
            event = "COMMENT" if is_root else "APPROVE"
            verdict = "PASSED ✅"
        else:
            event = "COMMENT" if is_root else "REQUEST_CHANGES"
            verdict = "CHANGES REQUESTED 🔴"
        body_lines = [
            f"## In-path PR-review gate — {verdict}",
            "",
            f"Reviewed by **{reviewer_slug}** (RoboCo PR reviewer). Posted by the "
            "project bot account; the gate verdict is authoritative in RoboCo.",
            "",
            (notes or "").strip() or "_(no additional notes)_",
        ]
        if is_root:
            body_lines += ["", "_Only the CEO merges this PR into `master`._"]
        try:
            await self.git.post_pr_review(
                slug, int(pr_number), "\n".join(body_lines), event=event
            )
        except Exception:
            logger.exception(
                "gate review PR post failed", task_id=str(getattr(t, "id", ""))
            )

    async def _gate_role_or_rejection(
        self,
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
    ) -> Envelope | None:
        """pr_pass / pr_fail require a journal:learning entry (parity with QA)."""
        has_learning = await self.journal.has_learning_for_task(
            reviewer_agent_id, task_id
        )
        ctx = _tr.GateContext(journal_learning_present=has_learning)
        result = _tr.check_requirements(
            task=SimpleNamespace(),
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

    async def _build_gate_review_evidence(self, t: Any) -> dict[str, Any]:
        """Inline evidence for claim_gate_review: the assembled diff + criteria."""
        diff = ""
        if t.branch_name:
            diff = await self.git.diff(branch_name=t.branch_name)
        return {
            "pr_number": t.pr_number,
            "pr_url": t.pr_url,
            "pr_diff": diff,
            "acceptance_criteria": list(getattr(t, "acceptance_criteria", None) or []),
            "is_assembled_pr": True,
        }
