"""PR-reviewer verbs (inbound external/fork PR review).

Mixin for ``claim_pr_review`` and ``post_pr_review`` — distinct from QA's
surface (per the locked design). The reviewer reviews PRs the org did NOT
author. The review is **read-only**: the diff is fetched from the GitHub API
(``git.get_pr_diff``), never checked out, and the contributor's code is never
run here. ``post_pr_review`` posts exactly one change-request to the PR and
finishes the review task.

Inherits ``ChoreographerHelpers`` under ``TYPE_CHECKING`` only so mypy resolves
``self.task`` etc.; at runtime the composed ``Choreographer`` supplies the real
attributes via MRO (same pattern as ``QAMixin``).
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


class PRReviewerMixin(_Base):
    """PR-reviewer-role verbs (inbound external/fork PRs)."""

    async def claim_pr_review(self, reviewer_agent_id: UUID, task_id: UUID) -> Envelope:
        """Reviewer claims an external-PR review task and starts work.

        Spec gate enforces role (pr_reviewer) + the composed ``claim`` action's
        source-status (PENDING). The composed ``claim``+``start`` runs through
        the verb runner (pending -> claimed -> in_progress; the review task is
        branch-gate exempt). The response carries the contributor's unified diff
        INLINE — fetched read-only via the GitHub API; the fork code is never
        checked out or run — so the reviewer inspects it before posting.
        """
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb="claim_pr_review",
            )
        agent = await self.task.agent_for(reviewer_agent_id)
        role_str = str(agent.role) if agent is not None else "pr_reviewer"
        briefing = await self._briefing_for(reviewer_agent_id, task_id)
        role_or_rejection = await self._resolve_role(
            t, role_str, briefing, reviewer_agent_id, task_id, "claim_pr_review"
        )
        if isinstance(role_or_rejection, Envelope):
            return role_or_rejection
        role = role_or_rejection
        spec_ctx = spec_module.Context(
            actor_id=reviewer_agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
        )
        decision = spec_module.can_invoke_intent(role, "claim_pr_review", t, spec_ctx)
        if not decision.allowed:
            return await self._emit_rejection(
                Envelope.from_decision(decision, briefing=briefing).with_introspection(
                    task=t, role=role_str
                ),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb="claim_pr_review",
            )
        guard = await self._run_claim_guards(agent_id=reviewer_agent_id, task=t)
        if guard:
            guard.with_introspection(task=t, role=role_str)
            return await self._emit_rejection(
                self._with_briefing(guard, briefing),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb="claim_pr_review",
            )
        # Verb body owns the claim (mirrors QA's claim_review): a specialized
        # pending->in_progress claim with NO plan and NO branch. The spec's
        # composes=("claim","start") is for the gate above only — routing it
        # through the verb runner would hit start()'s plan gate and auto-create
        # a branch, neither of which a read-only review task wants.
        claimed = await self.task.pr_review_claim(reviewer_agent_id, task_id)
        if claimed is None:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message="this external-PR review task is no longer claimable",
                    remediate="it may already be claimed; give_me_work for the next",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb="claim_pr_review",
            )
        t = claimed
        evidence = await self._build_pr_review_evidence(t)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS["claim_pr_review"].next_hint(t),
            evidence=evidence,
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)

    async def post_pr_review(
        self,
        reviewer_agent_id: UUID,
        task_id: UUID,
        body: str,
        event: str = "REQUEST_CHANGES",
    ) -> Envelope:
        """Post ONE change-request to the PR and finish the review task.

        Spec gate enforces role + the ``pr_review_done`` source-status
        (IN_PROGRESS). The tracing gate requires a journal:learning entry. The
        composed ``pr_review_done`` runs through the verb runner
        (in_progress -> completed); then — mirroring QA's ``a2a.send`` pattern —
        the review is posted to GitHub from the verb body, after the transition.
        """
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb="post_pr_review",
            )
        pre = await self._post_pr_review_preflight(t, reviewer_agent_id, task_id, body)
        if isinstance(pre, Envelope):
            return pre
        agent, role_str, briefing, spec_ctx = pre
        slug = await self._project_slug_for(t)
        pr_number = t.pr_number
        runner = self._verb_runner()
        try:
            t = await runner.run_intent("post_pr_review", t, agent, spec_ctx)
        except Exception as exc:
            return await self._runner_failure(
                exc, t, role_str, briefing, reviewer_agent_id, task_id, "post_pr_review"
            )
        # GitHub side-effect AFTER the DB transition (a2a.send pattern). Best-
        # effort: a posting failure is logged, not rolled back — the review task
        # is complete; a missed post can be re-driven manually.
        if slug and pr_number:
            try:
                await self.git.post_pr_review(slug, pr_number, body, event=event)
            except Exception:
                logger.exception(
                    "post_pr_review GitHub post failed", task_id=str(task_id)
                )
        # Surface the review to the CEO as an actionable decision (supersede /
        # dismiss). The reviewer is read-only with no notify verb, so the server
        # emits it. Best-effort — a notify failure must not fail the review.
        if pr_number:
            try:
                from roboco.services.notification import NotificationService

                await NotificationService().send_external_pr_reviewed_notification(
                    task_id=str(task_id),
                    pr_number=pr_number,
                    pr_url=str(getattr(t, "pr_url", "") or ""),
                )
            except Exception:
                logger.exception(
                    "post_pr_review CEO notify failed", task_id=str(task_id)
                )
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS["post_pr_review"].next_hint(t),
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)

    # -- helpers ----------------------------------------------------------

    async def _post_pr_review_preflight(
        self, t: Any, reviewer_agent_id: UUID, task_id: UUID, body: str
    ) -> Any:
        """Pre-runner guards for post_pr_review.

        Returns a rejection ``Envelope`` or the context tuple
        ``(agent, role_str, briefing, spec_ctx)`` on pass.
        """
        agent = await self.task.agent_for(reviewer_agent_id)
        role_str = str(agent.role) if agent is not None else "pr_reviewer"
        briefing = await self._briefing_for(reviewer_agent_id, task_id)
        if not body or not body.strip():
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message="post_pr_review requires a non-empty review body",
                    remediate=(
                        "pass body='<complete change-request with per-criterion "
                        "findings>'"
                    ),
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb="post_pr_review",
            )
        role_or_rejection = await self._resolve_role(
            t, role_str, briefing, reviewer_agent_id, task_id, "post_pr_review"
        )
        if isinstance(role_or_rejection, Envelope):
            return role_or_rejection
        role = role_or_rejection
        spec_ctx = spec_module.Context(
            actor_id=reviewer_agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            notes=body,
        )
        decision = spec_module.can_invoke_intent(role, "post_pr_review", t, spec_ctx)
        if not decision.allowed:
            return await self._emit_rejection(
                Envelope.from_decision(decision, briefing=briefing).with_introspection(
                    task=t, role=role_str
                ),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb="post_pr_review",
            )
        gate = await self._pr_review_tracing_gate(
            reviewer_agent_id, task_id, t, role_str
        )
        if gate is not None:
            return gate
        return (agent, role_str, briefing, spec_ctx)

    async def _resolve_role(
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

    async def _runner_failure(
        self,
        exc: Exception,
        t: Any,
        role_str: str,
        briefing: dict[str, Any],
        agent_id: UUID,
        task_id: UUID,
        verb: str,
    ) -> Envelope:
        """Shared rejection for a verb-runner failure."""
        return await self._emit_rejection(
            Envelope.invalid_state(
                message=f"verb runner failed: {exc}",
                remediate="retry; if persistent, unclaim and notify the CEO",
                context_briefing=briefing,
            ).with_introspection(task=t, role=role_str),
            agent_id=agent_id,
            task_id=task_id,
            verb=verb,
        )

    async def _build_pr_review_evidence(self, t: Any) -> dict[str, Any]:
        """Inline evidence for claim_pr_review: the PR's unified diff (read-only)."""
        slug = await self._project_slug_for(t)
        diff = ""
        if slug and t.pr_number:
            diff = await self.git.get_pr_diff(slug, t.pr_number)
        return {
            "pr_number": t.pr_number,
            "pr_url": t.pr_url,
            "pr_diff": diff,
            "is_external_pr": True,
        }

    async def _pr_review_tracing_gate(
        self, reviewer_agent_id: UUID, task_id: UUID, t: Any, role_str: str
    ) -> Envelope | None:
        """post_pr_review requires a journal:learning entry (parity with QA)."""
        has_learning = await self.journal.has_learning_for_task(
            reviewer_agent_id, task_id
        )
        ctx = _tr.GateContext(journal_learning_present=has_learning)
        result = _tr.check_requirements(
            task=SimpleNamespace(),
            requirements=list(_tr.requirements_for("post_pr_review")),
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
            verb="post_pr_review",
        )

    async def _project_slug_for(self, t: Any) -> str | None:
        """Resolve the project slug for a task (for read-only PR API calls)."""
        from roboco.services.project import get_project_service

        if t.project_id is None:
            return None
        project = await get_project_service(self.task.session).get(t.project_id)
        return project.slug if project is not None else None
