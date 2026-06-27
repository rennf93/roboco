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
        gate = await self._gate_tracing(
            reviewer_agent_id, task_id, t, role_str, verb, notes=notes
        )
        if gate is not None:
            return gate
        if verb == "pr_pass":
            blocked = await self._pr_pass_blocked(
                reviewer_agent_id, task_id, t, role_str, briefing
            )
            if blocked is not None:
                return blocked
        # Author the canonical pr_review verdict note BEFORE the transition so
        # it is persisted by the same commit (mirrors post_pr_review). This is
        # what keeps notes_structured.pr_review in lock-step with the decision —
        # a later pr_fail overwrites an earlier pr_pass verdict instead of
        # leaving a stale "passed" on a task that was just sent back.
        self._record_gate_verdict(t, verb, notes, issues=issues)
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
        # Deliver the change-requests to the owner that now has to act on them
        # — the cell PM the runner just re-assigned via _revision_pm_for_task.
        # The reviewer posts the verdict on the PR itself but that never reaches
        # any PM-readable channel (no a2a, and _briefing_for / build_task_handoff
        # read neither pr_reviewer_notes nor notes_structured.pr_review). Without
        # this the owning PM respawned into needs_revision saw a generic "needs
        # revision" with zero concrete issues, concluded nothing to rework, and
        # re-submitted the same PR — an infinite pr_fail loop (live on
        # 9980d0a0 / PR #138). Mirrors QA's fail_review a2a to the dev (qa.py:671).
        # Best-effort: the transition already committed, so a delivery failure
        # must not roll the verdict back or 500 the reviewer.
        if verb == "pr_fail" and t.assigned_to is not None:
            # A Main-PM branch-bearing root is an assembled cell→root / root→master
            # PR — coordination, not the Main PM's own code. The rejection is
            # about the cells' merged code, which the Main PM cannot fix directly
            # (no code verb). Steer the a2a body to re-delegate + wait for
            # re-assembly so the PM doesn't re-submit the unchanged root (the
            # 2026-06-27 infinite pr_fail loop). The Envelope ``next`` hint makes
            # the same steer via _next_hint_pr_fail.
            team = getattr(t, "team", None)
            team_value = str(getattr(team, "value", team))
            is_main_pm_root = team_value == spec_module.Team.MAIN_PM.value and bool(
                getattr(t, "branch_name", None)
            )
            steer = (
                " Assembled cell work failed — re-delegate the fixes to the"
                " owning cell PM(s) and wait for re-assembly; do NOT re-submit"
                " the root."
                if is_main_pm_root
                else ""
            )
            try:
                await self.a2a.send(
                    from_agent=reviewer_agent_id,
                    to_agent=t.assigned_to,
                    skill="code_review",
                    task_id=task_id,
                    body=f"PR review needs changes. {notes}{steer}",
                )
            except Exception:
                logger.exception(
                    "pr_fail a2a to owning PM failed", task_id=str(task_id)
                )
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS[verb].next_hint(t),
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)

    async def _pr_pass_blocked(
        self,
        reviewer_agent_id: UUID,
        task_id: UUID,
        t: Any,
        role_str: str,
        briefing: dict[str, Any],
    ) -> Envelope | None:
        """Refuse pr_pass on a broken toolchain or a block-level violation.

        A reviewer must not PASS an assembled PR whose suite can't run in the
        workspace, or that carries unresolved architectural-convention
        violations; pr_fail stays available. Returns the emitted rejection or
        None to proceed. Both guards are inert when their flag is off.
        """
        guards = (
            lambda: self._toolchain_broken_guard(reviewer_agent_id, t),
            lambda: self._conventions_guard(reviewer_agent_id, t, briefing),
        )
        for guard in guards:
            rejection = await guard()
            if rejection is not None:
                return await self._emit_rejection(
                    rejection.with_introspection(task=t, role=role_str),
                    agent_id=reviewer_agent_id,
                    task_id=task_id,
                    verb="pr_pass",
                )
        return None

    def _record_gate_verdict(
        self, t: Any, verb: str, notes: str, issues: tuple[str, ...] = ()
    ) -> None:
        """Persist the gate verdict as the canonical ``pr_review`` note.

        The tracing gate only threads ``notes`` through a throwaway shim, so
        nothing wrote the task's structured PR-reviewer slot — a task passed
        once and later failed kept showing the stale ``verdict: passed``. This
        authors the slot on every decision (``pr_pass`` → passed, ``pr_fail`` →
        failed) so it can never contradict the transition. For ``pr_fail`` the
        free-text ``issues`` land in the structured ``issues`` slot (not the
        format-enforced ``findings`` list, which needs file/severity/expected/
        actual) so a reader of ``notes_structured.pr_review`` — or the owning
        PM's briefing that mirrors it — gets the concrete change-requests.
        Best-effort: content validation (e.g. a too-short summary) must never
        roll back the gate, so a malformed payload is logged and skipped.
        """
        from roboco.foundation.policy.content import ContentValidationError
        from roboco.services.content_notes import apply_structured_note

        verdict = "passed" if verb == "pr_pass" else "failed"
        if verb == "pr_fail" and issues:
            # The free-text issues render under their own ``## Issues`` section
            # (render_markdown). Baking them into ``summary`` too duplicated each
            # issue on the Task Details "PR Reviewer Notes" card (once under
            # ## Summary, once under ## Issues). The summary is a substantive
            # non-issues sentence; ``notes`` (with the issues) still drives the
            # GitHub PR post and the a2a to the owning PM — those are raw text,
            # not rendered through render_markdown, so no duplication there.
            summary = (
                f"In-path PR-review gate requested changes - "
                f"{len(issues)} issue(s) listed below."
            )
        else:
            summary = notes
        payload: dict[str, Any] = {
            "summary": summary,
            "findings": [],
            "verdict": verdict,
        }
        if issues:
            payload["issues"] = list(issues)
        try:
            apply_structured_note(t, "pr_review", payload)
        except ContentValidationError:
            logger.warning(
                "gate verdict note skipped (invalid content)",
                verb=verb,
                task_id=str(getattr(t, "id", "")),
            )

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
