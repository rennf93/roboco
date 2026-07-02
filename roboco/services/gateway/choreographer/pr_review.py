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
from roboco.foundation.policy.content import (
    ContentValidationError,
    pr_review_conflict,
    validate_content,
)
from roboco.services.content_notes import apply_structured_note
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
        briefing = await self._briefing_for(reviewer_agent_id, task_id, full=True)
        role_or_rejection = await self._resolve_role(
            t, role_str, briefing, reviewer_agent_id, task_id, "claim_pr_review"
        )
        if isinstance(role_or_rejection, Envelope):
            return role_or_rejection
        role = role_or_rejection
        spec_ctx = spec_module.Context(
            actor_id=reviewer_agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            agent_team=str(agent.team) if agent is not None and agent.team else None,
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

    @staticmethod
    def _build_pr_review_content(
        body: str, findings: list[dict[str, Any]], event: str
    ) -> Any:
        """Validate structured findings into a PrReviewContent, or an Envelope.

        The reviewer supplies a summary (``body``) + structured ``findings``; the
        canonical GitHub comment is generated from them. ``event`` maps to the
        verdict (APPROVE → approved, else changes_requested).
        """
        verdict = "approved" if event == "APPROVE" else "changes_requested"
        try:
            return validate_content(
                "pr_review",
                {"summary": body, "findings": findings, "verdict": verdict},
            )
        except ContentValidationError as exc:
            return Envelope.invalid_state(
                message=f"malformed PR-review findings: {exc.field} — {exc.reason}",
                remediate=(
                    "each finding needs file + expected + actual (line + severity "
                    "optional); re-call post_pr_review with structured findings"
                ),
            )

    def _resolve_post_body(
        self, t: Any, body: str, findings: list[dict[str, Any]] | None, event: str
    ) -> Any:
        """The GitHub comment body: the canonical render when findings are given
        (and stored structured), else the free-text body. Envelope on malformed
        findings."""
        if not findings:
            return body
        structured = self._build_pr_review_content(body, findings, event)
        if isinstance(structured, Envelope):
            return structured
        apply_structured_note(t, "pr_review", structured)
        return structured.render_markdown()

    @staticmethod
    def _is_hand_formatted_verdict(body: str) -> bool:
        """True when a free-text ``body`` carries verdict/section markdown headers
        the system would otherwise generate — i.e. the reviewer hand-formatted a
        verdict into ``body`` instead of passing structured ``findings``.

        Matches the section headers the canonical renderer emits (``## Findings``)
        plus the ones a hand-formatter reaches for (``## Summary`` / ``## Issues``
        / ``## Verdict``). A real one-paragraph summary does not contain ``## ``
        headers, so the prose word "summary" never trips this.

        The header must sit at the START of a line (a real markdown header the
        reviewer authored) — a header quoted from the PR itself (``> ## Summary``)
        or named mid-prose (``the ## Summary section``) is a citation, not a
        hand-formatted verdict, and must not trip the guard.
        """
        import re

        lowered = (body or "").lower()
        return any(
            re.search(rf"^[ \t]*{re.escape(header)}", lowered, re.MULTILINE)
            for header in ("## summary", "## issues", "## verdict", "## findings")
        )

    async def _post_review_side_effects(
        self,
        t: Any,
        slug: str | None,
        pr_number: int | None,
        post_body: str,
        event: str,
        task_id: UUID,
    ) -> None:
        """Post the review to GitHub + surface it to the CEO (both best-effort)."""
        if slug and pr_number:
            try:
                await self.git.post_pr_review(slug, pr_number, post_body, event=event)
            except Exception:
                logger.exception(
                    "post_pr_review GitHub post failed", task_id=str(task_id)
                )
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

    async def post_pr_review(
        self,
        reviewer_agent_id: UUID,
        task_id: UUID,
        body: str,
        event: str = "REQUEST_CHANGES",
        findings: list[dict[str, Any]] | None = None,
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
        # Content gates BEFORE anything is recorded or posted to the
        # contributor's PR: (1) refuse a verdict that contradicts the findings
        # (a forgotten event='APPROVE' defaulting to a blocking REQUEST_CHANGES
        # with no findings); (2) refuse a hand-formatted verdict body with no
        # findings (the tool contract is "body = a one-paragraph summary; the
        # system GENERATES the comment from structured findings — do not
        # hand-format"). Folded into one helper so neither slips through and the
        # verb body stays under the return-count lint ceiling.
        rejection = await self._post_pr_review_content_gates(
            t,
            reviewer_agent_id,
            task_id,
            role_str,
            briefing,
            event=event,
            findings=findings,
            body=body,
        )
        if rejection is not None:
            return rejection
        slug = await self._project_slug_for(t)
        pr_number = t.pr_number
        post_body = self._resolve_post_body(t, body, findings, event)
        if isinstance(post_body, Envelope):
            return await self._emit_rejection(
                post_body.with_introspection(task=t, role=role_str),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb="post_pr_review",
            )
        runner = self._verb_runner()
        try:
            t = await runner.run_intent("post_pr_review", t, agent, spec_ctx)
        except Exception as exc:
            return await self._runner_failure(
                exc, t, role_str, briefing, reviewer_agent_id, task_id, "post_pr_review"
            )
        # Side-effects AFTER the DB transition (a2a.send pattern), both best-
        # effort: post the canonical review to GitHub + surface it to the CEO.
        await self._post_review_side_effects(
            t, slug, pr_number, post_body, event, task_id
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
            agent_team=str(agent.team) if agent is not None and agent.team else None,
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
            reviewer_agent_id, task_id, t, role_str, body=body
        )
        if gate is not None:
            return gate
        return (agent, role_str, briefing, spec_ctx)

    async def _verdict_consistency_gate(
        self,
        t: Any,
        reviewer_agent_id: UUID,
        task_id: UUID,
        role_str: str,
        briefing: dict[str, Any],
        *,
        event: str,
        findings: list[dict[str, Any]] | None,
    ) -> Envelope | None:
        """Reject a self-contradicting (event, findings) pair, else None.

        The recorded ``pr_review`` verdict and the posted GitHub review event
        both derive from ``event``; ``pr_review_conflict`` is the pure invariant
        that keeps them honest. Runs before any side effect so a contradictory
        review never reaches the task record or the PR.
        """
        conflict = pr_review_conflict(event, findings)
        if conflict is None:
            return None
        message, remediate = conflict
        return await self._emit_rejection(
            Envelope.invalid_state(
                message=message,
                remediate=remediate,
                context_briefing=briefing,
            ).with_introspection(task=t, role=role_str),
            agent_id=reviewer_agent_id,
            task_id=task_id,
            verb="post_pr_review",
        )

    async def _post_pr_review_content_gates(
        self,
        t: Any,
        reviewer_agent_id: UUID,
        task_id: UUID,
        role_str: str,
        briefing: dict[str, Any],
        *,
        event: str,
        findings: list[dict[str, Any]] | None,
        body: str,
    ) -> Envelope | None:
        """Pre-side-effect content gates for ``post_pr_review``: verdict
        consistency, then the no-hand-formatted-body guard. Returns the first
        rejection ``Envelope`` or ``None`` to proceed.

        The hand-format guard: the tool contract is "``body`` = a one-paragraph
        summary; the system GENERATES the GitHub comment from structured
        findings — do not hand-format it in ``body``". Nothing enforced that, so
        a reviewer could pass ``findings=[]`` and dump a self-formatted
        ``## Summary`` / ``## Issues`` / ``## Verdict`` blob into ``body``,
        which ``_resolve_post_body`` posts verbatim (the renderer emits
        ``## Findings``, never ``## Issues`` — so a ``## Issues`` section on the
        PR is proof the body was hand-formatted). Observed live: a duplicated,
        self-redundant hand-formatted verdict posted to a contributor's PR.
        Refuse it and point the reviewer at the structured path. Scoped to empty
        findings: with structured findings the system generates the comment, so
        a header-shaped word in the summary is harmless; a genuine plain-note
        ``COMMENT`` (no verdict headers) is still allowed.
        """
        conflict = await self._verdict_consistency_gate(
            t,
            reviewer_agent_id,
            task_id,
            role_str,
            briefing,
            event=event,
            findings=findings,
        )
        if conflict is not None:
            return conflict
        if not findings and self._is_hand_formatted_verdict(body):
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=(
                        "post_pr_review body is hand-formatted as a verdict — "
                        "pass structured findings instead"
                    ),
                    remediate=(
                        "do not hand-format the review. Pass a one-paragraph "
                        "summary in `body` plus structured "
                        "`findings=[{file, line?, severity "
                        "(blocker|major|minor|nit), expected, actual}, ...]`; the "
                        "system generates the GitHub comment (summary + findings "
                        "table + verdict). event='REQUEST_CHANGES' requires >=1 "
                        "finding; a bare event='COMMENT' with no findings is for a "
                        "plain note, not a verdict"
                    ),
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb="post_pr_review",
            )
        return None

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
        self,
        reviewer_agent_id: UUID,
        task_id: UUID,
        t: Any,
        role_str: str,
        *,
        body: str,
    ) -> Envelope | None:
        """post_pr_review requires a journal:learning entry (parity with QA)
        plus a substantive pr_reviewer_notes section.

        The section note is the verb's own ``body`` argument (the change
        request), not yet persisted to the task, so it is threaded through a
        SimpleNamespace shim (the foundation checker reads
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
            task=SimpleNamespace(pr_reviewer_notes=body),
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
        """Resolve the project slug for a task (read-only PR API calls + the
        in-path gate's PR comment). Delegates to the module-level resolver so
        the unchanged-PR gate in ``_impl.py`` reuses the EXACT same path (it
        can't see this mixin method — ``_LegacyChoreographer`` does not inherit
        ``ChoreographerHelpers``). See ``resolve_task_project_slug``.
        """
        return await resolve_task_project_slug(self.task.session, t)


async def resolve_task_project_slug(session: Any, t: Any) -> str | None:
    """Resolve the project slug for a task (read-only PR API calls + the
    in-path gate's PR comment). Module-level so it is shared by
    ``PRReviewerMixin._project_slug_for`` and the unchanged-PR gate's
    ``_current_root_pr_head_sha`` (in ``_impl.py``) — DRY, and the only way the
    legacy choreographer class can reach the resolver without inheriting
    ``ChoreographerHelpers``.

    A normal task carries ``project_id``. A Main-PM coordination root — the
    only task a root→master PR ever sits on — often carries just a
    ``product_id`` (the cell→repo map) and no project of its own; its
    ``feature/main_pm/{root}`` branch + PR live in the product's repo. Fall
    through to the product's first distinct project (a monorepo product maps
    every cell to one repo) so the gate verdict reaches the PR instead of
    silently no-op'ing. Purely additive: a task WITH ``project_id`` resolves
    exactly as before.
    """
    from uuid import UUID

    from roboco.services.project import get_project_service

    project_service = get_project_service(session)
    if t.project_id is not None:
        project = await project_service.get(t.project_id)
        return project.slug if project is not None else None
    product_id = getattr(t, "product_id", None)
    if product_id is not None:
        from roboco.services.product import get_product_service

        product_service = get_product_service(session)
        project_ids = await product_service.distinct_project_ids(UUID(str(product_id)))
        if not project_ids:
            return None
        project = await project_service.get(project_ids[0])
        return project.slug if project is not None else None
    # Ad-hoc per-cell map root-subtask: mirror the product root's first-project
    # resolution so the gate verdict reaches the PR in the mapped repo.
    cell_map = getattr(t, "cell_projects", None) or []
    seen: set[UUID] = set()
    for mapping in sorted(cell_map, key=lambda m: m.team.value):
        pid = UUID(str(mapping.project_id))
        if pid not in seen:
            seen.add(pid)
            project = await project_service.get(pid)
            return project.slug if project is not None else None
    return None
