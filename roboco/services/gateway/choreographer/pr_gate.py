"""In-path PR-review gate verbs (assembled cell→root + root→master PRs).

Mixin for ``claim_gate_review``, ``pr_pass``, ``pr_fail`` and the DevOps
co-reviewer's ``record_devops_review`` — the reviewer side of the in-path
gate. Distinct from ``PRReviewerMixin`` (inbound external/fork PRs): these
GATE an internal delivery task between the PM's submit (submit_up /
submit_root) and the PM's merge. The reviewer reads the assembled diff and
either passes it on to awaiting_pm_review or fails it back to needs_revision —
exactly like QA's pass_review / fail_review, but at the assembled-PR level.

The helpers live in sibling mixins, stacked so each calls only what sits below
it: ``pr_gate_preflight`` (role, ownership, spec and tracing checks),
``pr_gate_evidence`` (the diff the reviewer sees), ``pr_gate_verdicts`` (the
verdict note and the PR review) and ``pr_gate_pass`` (what only ``pr_pass``
runs). The base ones inherit ``ChoreographerHelpers`` under ``TYPE_CHECKING``
only so mypy resolves ``self.task`` etc.; at runtime the composed
``Choreographer`` supplies the real attributes via MRO (same pattern as
``QAMixin`` / ``PRReviewerMixin``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from roboco.foundation.policy import lifecycle as spec_module
from roboco.foundation.policy.content import ContentValidationError
from roboco.services.gateway.choreographer import devops_gate as devops_gate_lib
from roboco.services.gateway.choreographer import findings as findings_lib
from roboco.services.gateway.choreographer.pr_gate_pass import PRGatePassMixin
from roboco.services.gateway.choreographer.pr_gate_preflight import PRGatePreflightMixin
from roboco.services.gateway.envelope import Envelope
from roboco.utils.converters import to_python_uuid

if TYPE_CHECKING:
    from uuid import UUID

logger = structlog.get_logger()


class PRGateMixin(PRGatePreflightMixin, PRGatePassMixin):
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
        # Durability boundary: commit the claim BEFORE the advisory evidence
        # assembly begins — see claim_review (qa.py) for the full rationale.
        # Same-agent retry: if the task is already claimed by THIS reviewer,
        # skip the re-claim and go straight to evidence rebuild.
        #
        # DevOps co-review lane (the infra review gate): the devops agent
        # co-claims WITHOUT touching the primary reviewer's claim/ownership
        # (a marker-based co-claim, see ``devops_gate_co_claim``); the
        # flag-off rejection inside the service method is defense in depth,
        # since nothing spawns devops-1 with the flag off anyway.
        if role_str == spec_module.Role.DEVOPS.value:
            claimed = await self.task.devops_gate_co_claim(reviewer_agent_id, task_id)
            if claimed is None:
                return await self._emit_rejection(
                    Envelope.invalid_state(
                        message=(
                            "this assembled-PR review task is no longer co-claimable"
                        ),
                        remediate=(
                            "the gate may have moved on, or another DevOps"
                            " review already holds the co-claim; re-fetch with"
                            " evidence(task_id) and retry"
                        ),
                        context_briefing=briefing,
                    ).with_introspection(task=t, role=role_str),
                    agent_id=reviewer_agent_id,
                    task_id=task_id,
                    verb="claim_gate_review",
                )
            t = claimed
            await self.task.session.commit()
        elif to_python_uuid(t.active_claimant_id) != reviewer_agent_id:
            claimed = await self.task.pr_gate_claim(reviewer_agent_id, task_id)
            if claimed is None:
                return await self._emit_rejection(
                    Envelope.invalid_state(
                        message="this assembled-PR review task is no longer claimable",
                        remediate=(
                            "it may already be claimed; give_me_work for the next"
                        ),
                        context_briefing=briefing,
                    ).with_introspection(task=t, role=role_str),
                    agent_id=reviewer_agent_id,
                    task_id=task_id,
                    verb="claim_gate_review",
                )
            t = claimed
            await self.task.session.commit()

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

    @staticmethod
    def _validate_pr_fail_findings(
        t: Any, issues: list[str] | None, findings: list[dict[str, Any]] | None
    ) -> tuple[list[Any], Envelope | None]:
        """Normalize + validate pr_fail's findings — mirrors QA's fail_review
        helper (``QAMixin._validate_fail_review_findings``).

        Returns ``(validated, rejection)``; the caller attaches
        ``context_briefing``/introspection (this helper has no ``self``).
        """
        raw = findings_lib.merge_findings_and_issues(findings, issues)
        if not raw:
            return [], Envelope.invalid_state(
                message="pr_fail requires at least one finding",
                remediate=(
                    "pass findings=[{file, severity, expected, actual}, ...] "
                    "(issues=['...'] is still accepted this release, deprecated)"
                ),
            )
        if cap := findings_lib.findings_count_guard(raw):
            return [], cap
        validated, bad = findings_lib.validate_or_reject(raw)
        if bad is not None:
            return [], bad
        if t is not None and (
            unknown := findings_lib.unknown_finding_criteria(t, validated)
        ):
            return [], findings_lib.criterion_mismatch_rejection(t, unknown)
        return validated, None

    async def pr_fail(
        self,
        reviewer_agent_id: UUID,
        task_id: UUID,
        issues: list[str] | None = None,
        findings: list[dict[str, Any]] | None = None,
    ) -> Envelope:
        """Fail the assembled PR with concrete findings; → needs_revision.

        ``findings`` is the structured revision-findings ledger entry (wire
        Pattern A); ``issues`` (free text) is still accepted for one release
        and shimmed into file-less findings (deprecated).
        """
        t = await self.task.get(task_id)
        validated, bad = self._validate_pr_fail_findings(t, issues, findings)
        if bad is not None:
            briefing = await self._briefing_for(reviewer_agent_id, task_id)
            return await self._emit_rejection(
                self._with_briefing(bad, briefing).with_introspection(
                    task=t, role="pr_reviewer"
                ),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb="pr_fail",
            )
        # Placeholder (id-less) rendering drives the existing gates — the
        # ledger ids don't exist until _gate_decision inserts them.
        notes = findings_lib.render_findings_summary([(None, f) for f in validated])
        return await self._gate_decision(
            reviewer_agent_id,
            task_id,
            "pr_fail",
            notes=notes,
            issues=(),
            findings=tuple(validated),
        )

    async def _attach_pr_fail_findings(
        self, t: Any, agent: Any, role_str: str, findings: list[Any]
    ) -> str:
        """Insert pr_fail's findings into the ledger; return the id-prefixed
        rendering used for the structured note, the PR comment, and the a2a
        body to the owning PM (all three read ``notes`` after this call)."""
        # GatewayAgentView carries no slug field — falls back to the role
        # string (mirrors _post_gate_review's reviewer_slug fallback).
        author_slug = getattr(agent, "slug", None) or role_str
        # The DevOps second reviewer's rejections ledger under their own
        # origin (devops_review) so the primary pr_gate ledger stays the
        # primary reviewer's - same free-form-origin ledger, dedicated row
        # attribution (the ``second_review`` precedent).
        origin = (
            devops_gate_lib.DEVOPS_REVIEW_ORIGIN
            if role_str == spec_module.Role.DEVOPS.value
            else "pr_gate"
        )
        _, summary = await findings_lib.insert_and_render(
            self.task.session,
            task_id=t.id,
            origin=origin,
            round=findings_lib.next_round(t),
            author_slug=author_slug,
            findings=findings,
        )
        return summary

    async def _run_gate_verb(
        self,
        verb: str,
        t: Any,
        agent: Any,
        spec_ctx: spec_module.Context,
        *,
        reviewer_agent_id: UUID,
        task_id: UUID,
        role_str: str,
        briefing: dict[str, Any],
    ) -> Envelope | Any:
        """Dispatch the composed atomic action via the verb runner.

        Returns the rejection ``Envelope`` on a runner exception or a
        concurrent-transition race (the last composed action returning
        ``None``), else the post-transition task.
        """
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
        return t

    async def _post_gate_transition_effects(
        self,
        t: Any,
        agent: Any,
        role_str: str,
        verb: str,
        notes: str,
        *,
        reviewer_agent_id: UUID,
        task_id: UUID,
        issues: tuple[str, ...],
        pre_sha: str | None,
        findings: list[Any] | None = None,
    ) -> None:
        """Best-effort side effects after a gate decision commits.

        pr_fail: re-capture the PR head SHA AFTER the transition commits. The
        pre-transition capture (in ``_record_gate_verdict_for``) can go stale
        if cell work lands on the root branch between that capture and the
        commit — a stale recorded SHA would make submit_root false-allow an
        unchanged re-submit and re-open the pr_fail loop. Re-stamp only when
        the head actually advanced, so the no-advance case stays a single
        note write — carrying ``findings`` forward too, since the re-stamp
        fully replaces the ``pr_review`` slot. Then post the gate verdict on
        the PR itself (a GitHub failure must not roll back the gate
        decision), and — pr_fail only — a2a the change-requests to the
        owning PM (see ``_deliver_pr_fail_to_owner`` for the rationale and
        the Main-PM-root steer).
        """
        if verb == "pr_fail":
            await self._re_stamp_pr_fail_head_sha_if_advanced(
                t,
                notes,
                issues=issues,
                pre_sha=pre_sha,
                findings=findings,
                reviewer_role=role_str,
            )
        # Stage 3: a DEVOPS verdict does NOT post to GitHub - the DB note is
        # the authoritative record, and a second bot review (an APPROVE on a
        # cell→root PR from the shared project bot) adds approval noise
        # without any consumer. The primary reviewer's post path is unchanged.
        if role_str != spec_module.Role.DEVOPS.value:
            await self._post_gate_review(t, agent, role_str, verb, notes)
        if verb == "pr_fail":
            await self._deliver_pr_fail_to_owner(t, reviewer_agent_id, task_id, notes)

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
        findings: tuple[Any, ...] = (),
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
        second_review_evidence: dict[str, Any] | None = None
        if verb == "pr_pass":
            rejection, ci_note = await self._gate_pr_pass_preflight(
                reviewer_agent_id, task_id, t, role_str, briefing
            )
            if rejection is not None:
                return rejection
            second_review_evidence = await self._run_second_review_pass(
                t, agent, role_str
            )
        # Insert the ledger rows now that the task + role gates are settled,
        # THEN rebuild `notes` with the real ids — every downstream reader
        # (the structured note, the PR comment, the a2a to the owning PM)
        # sees the id-prefixed rendering from this point on.
        if verb == "pr_fail" and findings:
            notes = await self._attach_pr_fail_findings(
                t, agent, role_str, list(findings)
            )
        # Author the canonical pr_review verdict note BEFORE the transition so it
        # is persisted by the same commit (mirrors post_pr_review) and stays in
        # lock-step with the decision (pr_fail overwrites an earlier pr_pass).
        pre_sha = await self._record_gate_verdict_for(
            verb,
            t,
            notes,
            issues=issues,
            ci_note=ci_note,
            findings=list(findings),
            reviewer_role=role_str,
        )
        result = await self._run_gate_verb(
            verb,
            t,
            agent,
            spec_ctx,
            reviewer_agent_id=reviewer_agent_id,
            task_id=task_id,
            role_str=role_str,
            briefing=briefing,
        )
        if isinstance(result, Envelope):
            return result
        t = result
        await self._post_gate_transition_effects(
            t,
            agent,
            role_str,
            verb,
            notes,
            reviewer_agent_id=reviewer_agent_id,
            task_id=task_id,
            issues=issues,
            pre_sha=pre_sha,
            findings=list(findings),
        )
        env = Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS[verb].next_hint(t),
            evidence=second_review_evidence,
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)
        if verb == "pr_fail" and (hint := findings_lib.findings_count_hint(findings)):
            env.warning = hint
        return env

    async def record_devops_review(
        self, devops_agent_id: UUID, task_id: UUID, notes: str
    ) -> Envelope:
        """Record the DevOps infra-review pass verdict WITHOUT transitioning.

        The pass half of the second-reviewer surface (``pr_fail`` is the fail
        half): devops-1 co-claims via ``claim_gate_review``, reviews the diff,
        then records a ``passed`` verdict note (stamped with the PR's current
        head SHA) so the primary reviewer's ``pr_pass`` composes. Devops may
        instead call ``pr_pass`` itself - that IS its verdict and transitions
        the gate. Requires the co-claim marker (claim first, then decide) and
        rejects cleanly on an invalid payload rather than silently recording
        nothing: the verb's whole product is the note.
        """
        pre = await self._claim_gate_preflight(devops_agent_id, task_id)
        if isinstance(pre, Envelope):
            return pre
        t, role_str, briefing = pre
        if role_str != spec_module.Role.DEVOPS.value or not (
            devops_gate_lib.is_devops_co_claimant(t, devops_agent_id)
        ):
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message=(
                        "record_devops_review requires a DevOps co-claim "
                        "on this gate task"
                    ),
                    remediate=(
                        "claim the review via claim_gate_review(task_id) first, "
                        "then record your verdict"
                    ),
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=devops_agent_id,
                task_id=task_id,
                verb="record_devops_review",
            )
        gate = await self._gate_tracing(
            devops_agent_id, task_id, t, role_str, "record_devops_review", notes=notes
        )
        if gate is not None:
            return gate
        head_sha = await self._capture_pr_head_sha(t)
        payload: dict[str, Any] = {
            "summary": notes,
            "verdict": "passed",
        }
        if head_sha:
            payload["head_sha"] = head_sha
        from roboco.services.content_notes import apply_structured_note

        try:
            apply_structured_note(t, devops_gate_lib.DEVOPS_REVIEW_ORIGIN, payload)
        except ContentValidationError:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=(
                        "the devops verdict note was rejected by content validation"
                    ),
                    remediate=(
                        "re-issue record_devops_review with a substantive "
                        "summary (>= 10 chars) describing what you verified "
                        "about the infra surface"
                    ),
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=devops_agent_id,
                task_id=task_id,
                verb="record_devops_review",
            )
        # Durability boundary: the note IS the verb's deliverable - commit it
        # before returning (mirrors claim_gate_review's claim commit).
        await self.task.session.commit()
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS["record_devops_review"].next_hint(t),
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)
