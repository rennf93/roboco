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
from roboco.foundation.policy.batch import is_batch_root_subtask
from roboco.foundation.policy.content import markers
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
        pre = await self._claim_gate_preflight(reviewer_agent_id, task_id)
        if isinstance(pre, Envelope):
            return pre
        t, role_str, briefing = pre
        guard = await self._run_claim_guards(
            agent_id=reviewer_agent_id,
            task=t,
            skip_dev_guards=True,
        )
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
            t, role_str, briefing, reviewer_agent_id, task_id, "claim_gate_review"
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
        t = await self._gate_ownership_or_rejection(reviewer_agent_id, task_id, verb)
        if isinstance(t, Envelope):
            return t
        agent = await self.task.agent_for(reviewer_agent_id)
        role_str = self._role_str_for_agent(agent)
        briefing = await self._briefing_for(reviewer_agent_id, task_id)
        role = await self._gate_role_or_rejection(
            t, role_str, briefing, reviewer_agent_id, task_id, verb
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
        return t

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

    async def _record_gate_verdict_for(
        self,
        verb: str,
        t: Any,
        notes: str,
        *,
        issues: tuple[str, ...],
        ci_note: str | None = None,
    ) -> str | None:
        """Author the canonical pr_review verdict note before the transition.

        On pr_fail also stamp the assembled PR's head SHA so the next submit_root
        can structurally refuse to re-submit the unchanged root (the 2026-06-27
        infinite pr_fail re-submit loop). Best-effort: a capture failure leaves
        head_sha absent and submit_root fails open rather than wedging the PM.
        On pr_pass, ``ci_note`` (set by ``_pr_pass_blocked`` when the CI-status
        guard passed through a project with no CI configured) is stamped into
        the verdict's ``ci_status`` field as evidence the guard actually ran.

        Returns the captured head_sha for pr_fail (None for pr_pass) so the caller
        can re-capture after the transition commits and re-stamp if the PR head
        advanced in between (#189 staleness).
        """
        if verb == "pr_fail":
            head_sha = await self._capture_pr_head_sha(t)
            self._record_gate_verdict(t, verb, notes, issues=issues, head_sha=head_sha)
            return head_sha
        self._record_gate_verdict(t, verb, notes, issues=issues, ci_note=ci_note)
        return None

    async def _re_stamp_pr_fail_head_sha_if_advanced(
        self,
        t: Any,
        notes: str,
        *,
        issues: tuple[str, ...],
        pre_sha: str | None,
    ) -> None:
        """Re-capture the PR head SHA after the transition commits and re-stamp
        the verdict note when it advanced past the pre-transition capture (#189).

        The pre-transition capture can go stale if cell work lands on the root
        branch between that capture and the commit; a stale recorded SHA makes
        ``submit_root`` false-allow an unchanged re-submit and re-opens the
        pr_fail loop. Re-stamping only on a real advance keeps the no-advance
        case to a single note write. Best-effort: a re-capture failure leaves
        the pre-transition SHA in place (the fail-open direction).
        """
        try:
            post_sha = await self._capture_pr_head_sha(t)
        except Exception:
            logger.exception(
                "pr_fail head-sha re-capture failed (keeping pre-transition sha)",
                task_id=str(getattr(t, "id", "")),
            )
            return
        if post_sha is not None and post_sha != pre_sha:
            self._record_gate_verdict(
                t, "pr_fail", notes, issues=issues, head_sha=post_sha
            )

    async def _post_gate_review(
        self, t: Any, agent: Any, role_str: str, verb: str, notes: str
    ) -> None:
        """Post the gate verdict to the PR itself (best-effort, after the DB
        transition — a GitHub failure must not roll back the gate decision)."""
        reviewer_slug = getattr(agent, "slug", None) or role_str
        await self._post_gate_review_to_pr(t, verb, reviewer_slug, notes)

    async def _deliver_pr_fail_to_owner(
        self, t: Any, reviewer_agent_id: UUID, task_id: UUID, notes: str
    ) -> None:
        """a2a the pr_fail change-requests to the owning PM (best-effort).

        The verdict is posted on the PR but never reaches a PM-readable channel
        (no a2a, and _briefing_for / build_task_handoff read neither
        pr_reviewer_notes nor notes_structured.pr_review), so without this the
        owning PM respawned into needs_revision re-submits the same PR blind —
        an infinite pr_fail loop (live on 9980d0a0 / PR #138). Mirrors QA's
        fail_review a2a. A Main-PM branch-bearing root is assembled cell work the
        Main PM can't fix directly, so steer it to re-delegate + wait for
        re-assembly rather than re-submit the unchanged root.
        """
        if t.assigned_to is None:
            return
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
            logger.exception("pr_fail a2a to owning PM failed", task_id=str(task_id))

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
        ci_note: str | None = None
        if verb == "pr_pass":
            blocked, ci_note = await self._pr_pass_blocked(
                reviewer_agent_id, task_id, t, role_str, briefing
            )
            if blocked is not None:
                return blocked
        # Author the canonical pr_review verdict note BEFORE the transition so it
        # is persisted by the same commit (mirrors post_pr_review) and stays in
        # lock-step with the decision (pr_fail overwrites an earlier pr_pass).
        pre_sha = await self._record_gate_verdict_for(
            verb, t, notes, issues=issues, ci_note=ci_note
        )
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
        # A concurrent transition (cancel, racing reviewer) between the
        # precondition gate and the runner's final action makes run_intent
        # return None; guard the dereferences below with a clean rejection so
        # the reviewer re-fetches and re-issues.
        if t is None:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=(
                        f"{verb}: the task moved out of awaiting_pr_review before"
                        " the decision committed — a concurrent transition"
                        " (cancel or a racing reviewer) beat you to it."
                    ),
                    remediate=(
                        "re-fetch with evidence(task_id) and re-issue your gate"
                        " verb once the task is back in awaiting_pr_review"
                    ),
                    context_briefing=briefing,
                ),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb=verb,
            )
        # pr_fail: re-capture the PR head SHA AFTER the transition commits. The
        # pre-transition capture (in _record_gate_verdict_for) can go stale if
        # cell work lands on the root branch between that capture and the commit
        # — a stale recorded SHA would make submit_root false-allow an unchanged
        # re-submit (current head vs an older recorded head ⇒ "different") and
        # re-open the pr_fail loop. Re-stamp the note only when the head actually
        # advanced, so the no-advance case stays a single note write. Best-effort:
        # a re-capture failure leaves the pre-transition SHA in place (fail-open).
        if verb == "pr_fail":
            await self._re_stamp_pr_fail_head_sha_if_advanced(
                t, notes, issues=issues, pre_sha=pre_sha
            )
        # Post the gate verdict on the PR itself (best-effort, after the DB
        # transition — a GitHub failure must not roll back the gate decision).
        await self._post_gate_review(t, agent, role_str, verb, notes)
        # a2a the pr_fail change-requests to the owning PM (best-effort) — see
        # _deliver_pr_fail_to_owner for the rationale and the Main-PM-root steer.
        if verb == "pr_fail":
            await self._deliver_pr_fail_to_owner(t, reviewer_agent_id, task_id, notes)
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
    ) -> tuple[Envelope | None, str | None]:
        """Refuse pr_pass on a broken toolchain, a block-level violation, or
        non-green CI on the assembled PR's head commit.

        A reviewer must not PASS an assembled PR whose suite can't run in the
        workspace, that carries unresolved architectural-convention
        violations, or whose CI is red/pending/unscheduled/unresolvable;
        pr_fail stays available for all three. Returns ``(rejection, None)``
        to block, or ``(None, ci_note)`` to proceed — ``ci_note`` is a
        non-None evidence stamp only when the CI guard passed through a
        project with no CI configured at all. The toolchain/conventions
        guards are inert when their flag is off; the CI guard fails open on
        an unresolvable gate-level slug/PR number (``None`` from
        ``_resolve_ci_status``) and also passes through — with an evidence
        stamp — when ``get_pr_ci_status`` itself classifies a missing
        project/git_url/token or an unreachable/nonexistent repo as
        ``no_ci_configured``.
        """
        guards = (
            lambda: self._toolchain_broken_guard(reviewer_agent_id, t, reviewer=True),
            lambda: self._conventions_guard(reviewer_agent_id, t, briefing),
        )
        for guard in guards:
            rejection = await guard()
            if rejection is not None:
                return (
                    await self._emit_rejection(
                        rejection.with_introspection(task=t, role=role_str),
                        agent_id=reviewer_agent_id,
                        task_id=task_id,
                        verb="pr_pass",
                    ),
                    None,
                )
        return await self._ci_status_guard(
            reviewer_agent_id, task_id, t, role_str, briefing
        )

    async def _resolve_ci_status(self, task_id: UUID, t: Any) -> dict[str, Any] | None:
        """Best-effort CI-status lookup for the assembled PR's head commit.

        Returns ``None`` on ANY configuration gap or lookup failure (no
        resolvable slug/PR number, a raised exception, or a caller returning
        something other than the documented ``dict[str, Any]`` shape) so
        ``_ci_status_guard`` fails open on every one of them uniformly.
        """
        pr_number = getattr(t, "pr_number", None)
        try:
            slug = await self._project_slug_for(t)
        except Exception:
            logger.exception(
                "ci status guard: slug resolve failed", task_id=str(task_id)
            )
            return None
        if not slug or not pr_number:
            return None
        try:
            status = await self.git.get_pr_ci_status(slug, int(pr_number))
        except Exception:
            logger.exception(
                "ci status guard: get_pr_ci_status raised", task_id=str(task_id)
            )
            return None
        return status if isinstance(status, dict) else None

    async def _ci_status_guard(
        self,
        reviewer_agent_id: UUID,
        task_id: UUID,
        t: Any,
        role_str: str,
        briefing: dict[str, Any],
    ) -> tuple[Envelope | None, str | None]:
        """Refuse pr_pass unless CI on the assembled PR's head commit is green.

        Failing, pending, unscheduled, or unresolvable-via-API CI states all
        block with a reviewer-aware remediation pointing at ``pr_fail`` (a
        reviewer has no ``i_am_blocked``) — pending/unscheduled/error are
        framed as retryable (wait and call pr_pass again), never as a defect
        to route back to the dev. A project with no CI configured at all
        passes through cleanly, returning an evidence note so the caller can
        stamp the verdict with why the guard did not block.
        """
        status = await self._resolve_ci_status(task_id, t)
        if status is None:
            # A configuration gap or lookup failure — never mistaken for a CI
            # signal, so the guard fails open rather than blocking.
            return None, None
        state = status.get("state")
        if state == "success":
            return None, None
        if state == "no_ci_configured":
            return None, "no CI configured on this project"
        message, remediate = self._ci_status_block_message(state, status)
        return (
            await self._emit_rejection(
                Envelope.invalid_state(
                    message=message,
                    remediate=remediate,
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb="pr_pass",
            ),
            None,
        )

    @staticmethod
    def _ci_status_block_message(
        state: str | None, status: dict[str, Any]
    ) -> tuple[str, str]:
        """(message, remediate) for a blocking CI state — failure / pending /
        pending_not_scheduled / error (the non-terminal, non-green states).

        pending/unscheduled/error are framed as retryable (wait and call
        pr_pass again), never as a defect to route back to the dev.
        """
        if state == "failure":
            names = (
                ", ".join(status.get("failing_checks") or []) or "one or more checks"
            )
            return (
                f"CI is failing on the assembled PR's head commit — {names}",
                f"call pr_fail(issues=['CI failing: {names}']) so the PR returns "
                "to needs_revision and the dev fixes the failing check(s) — do "
                "NOT pr_pass on red CI",
            )
        if state == "pending":
            return (
                "CI is still running on the assembled PR's head commit",
                "wait for CI to finish and call pr_pass again once it's green "
                "— do NOT pr_pass while checks are still running",
            )
        if state == "pending_not_scheduled":
            return (
                "CI has not started running on the assembled PR's head commit yet",
                "wait for CI to be scheduled and call pr_pass again once it's "
                "green — do NOT pr_pass before any check has run",
            )
        # state == "error" (or an unrecognized value) — a genuine GitHub API
        # failure resolving the signal; never treat this as green.
        return (
            "could not determine CI status for the assembled PR (GitHub API error)",
            "retry pr_pass shortly once the CI status can be resolved; "
            "if it persists, pr_fail(issues=[...]) to unwedge the PR",
        )

    def _record_gate_verdict(
        self,
        t: Any,
        verb: str,
        notes: str,
        issues: tuple[str, ...] = (),
        *,
        head_sha: str | None = None,
        ci_note: str | None = None,
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

        On ``pr_fail`` the assembled PR's head SHA is stamped into the slot
        (``head_sha``) so the next ``submit_root`` can structurally refuse to
        re-submit the unchanged root — the 2026-06-27 infinite ``pr_fail``
        re-submit loop. ``None`` (the default) leaves it absent, which the
        ``submit_root`` gate treats as fail-open.

        On ``pr_pass``, ``ci_note`` (set only when the CI-status guard passed
        through a project with no CI configured) is stamped into the slot's
        ``ci_status`` field — the evidence that the guard ran and deliberately
        did not block, rather than silently never having checked at all.
        """
        from roboco.foundation.policy.content import ContentValidationError
        from roboco.services.content_notes import apply_structured_note

        verdict = "passed" if verb == "pr_pass" else "failed"
        summary = self._gate_verdict_summary(verb, notes, issues)
        payload = self._gate_verdict_payload(
            verdict, summary, issues, verb, head_sha=head_sha, ci_note=ci_note
        )
        try:
            apply_structured_note(t, "pr_review", payload)
        except ContentValidationError:
            logger.warning(
                "gate verdict note skipped (invalid content)",
                verb=verb,
                task_id=str(getattr(t, "id", "")),
            )

    @staticmethod
    def _gate_verdict_summary(verb: str, notes: str, issues: tuple[str, ...]) -> str:
        """The verdict note's ``summary`` field.

        The free-text ``issues`` render under their own ``## Issues`` section
        (render_markdown). Baking them into ``summary`` too duplicated each
        issue on the Task Details "PR Reviewer Notes" card (once under
        ## Summary, once under ## Issues). The summary is a substantive
        non-issues sentence; ``notes`` (with the issues) still drives the
        GitHub PR post and the a2a to the owning PM — those are raw text,
        not rendered through render_markdown, so no duplication there.
        """
        if verb == "pr_fail" and issues:
            return (
                f"In-path PR-review gate requested changes - "
                f"{len(issues)} issue(s) listed below."
            )
        return notes

    @staticmethod
    def _gate_verdict_payload(
        verdict: str,
        summary: str,
        issues: tuple[str, ...],
        verb: str,
        *,
        head_sha: str | None,
        ci_note: str | None,
    ) -> dict[str, Any]:
        """Assemble the structured ``pr_review`` note payload."""
        payload: dict[str, Any] = {
            "summary": summary,
            "findings": [],
            "verdict": verdict,
        }
        if issues:
            payload["issues"] = list(issues)
        if verb == "pr_fail" and head_sha:
            payload["head_sha"] = head_sha
        if verb == "pr_pass" and ci_note:
            payload["ci_status"] = ci_note
        return payload

    async def _capture_pr_head_sha(self, t: Any) -> str | None:
        """Best-effort capture of the assembled PR's head SHA at ``pr_fail`` time.

        Resolves the project slug via ``_project_slug_for`` (handles a
        Main-PM root that carries only a ``product_id``) and asks
        ``git.get_pr_head_sha``. Returns ``None`` on ANY failure (no PR number,
        no resolvable project, git error) so the ``submit_root`` unchanged-PR
        gate fails open rather than wedging the PM — only the exact-unchanged
        case is hard-blocked.
        """
        pr_number = getattr(t, "pr_number", None)
        if not pr_number:
            return None
        try:
            slug = await self._project_slug_for(t)
        except Exception:
            logger.exception(
                "pr_fail head-sha capture: slug resolve failed",
                task_id=str(getattr(t, "id", "")),
            )
            return None
        if not slug:
            return None
        try:
            sha = await self.git.get_pr_head_sha(slug, int(pr_number))
            return sha if isinstance(sha, str) else None
        except Exception:
            logger.exception(
                "pr_fail head-sha capture: git lookup failed",
                task_id=str(getattr(t, "id", "")),
                pr=pr_number,
            )
            return None

    @staticmethod
    def _gate_review_event_verdict(verb: str, is_root: bool) -> tuple[str, str]:
        """Map the gate verb to a (review event, verdict label) pair.

        ``pr_pass`` → APPROVE, ``pr_fail`` → REQUEST_CHANGES — except on the
        root→master PR (``is_root``), which always gets a plain COMMENT so the
        gate never leaves an approval that could satisfy branch protection (only
        the CEO merges master) nor a blocking review that could impede that merge.
        """
        if verb == "pr_pass":
            return "COMMENT" if is_root else "APPROVE", "PASSED ✅"
        return "COMMENT" if is_root else "REQUEST_CHANGES", "CHANGES REQUESTED 🔴"

    @staticmethod
    def _gate_review_body(
        verdict: str, reviewer_slug: str, notes: str, is_root: bool
    ) -> str:
        """Render the gate-review comment body posted to the assembled PR."""
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
        return "\n".join(body_lines)

    async def _post_gate_review_to_pr(
        self, t: Any, verb: str, reviewer_slug: str, notes: str
    ) -> None:
        """Post the gate verdict as a review on the assembled PR (best-effort).

        ``pr_pass`` posts an APPROVE, ``pr_fail`` a REQUEST_CHANGES — EXCEPT on
        the root→master PR, which always gets a plain COMMENT: only the CEO acts
        on master, so the gate never leaves an approval that could satisfy
        branch protection and let anyone else merge, nor a blocking review that
        could impede the CEO's merge. A root→master PR is identified by
        ``is_root``: a task with no ``parent_task_id`` (a plain Main-PM
        coordination root) OR a MegaTask root-subtask (which has a parent — the
        umbrella — but opens its own root→master PR per repo; detected via
        ``is_batch_root_subtask``). A non-batch cell-PM coordination root keeps
        ``batch_id=None`` → not a root-subtask → still a cell→root PR. For the
        org's own PRs ``git.post_pr_review`` already downgrades a forbidden
        self-review to a COMMENT, so the verdict lands regardless.
        """
        try:
            slug = await self._project_slug_for(t)
        except Exception:
            logger.exception(
                "gate review PR post: slug resolve failed",
                task_id=str(getattr(t, "id", "")),
            )
            return
        pr_number = getattr(t, "pr_number", None)
        if not slug or not pr_number:
            return
        parent_task_id = getattr(t, "parent_task_id", None)
        is_root = parent_task_id is None or is_batch_root_subtask(
            batch_id=getattr(t, "batch_id", None),
            parent_task_id=parent_task_id,
        )
        event, verdict = self._gate_review_event_verdict(verb, is_root)
        body = self._gate_review_body(verdict, reviewer_slug, notes, is_root)
        try:
            await self.git.post_pr_review(slug, int(pr_number), body, event=event)
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
