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

import structlog

from roboco.config import settings
from roboco.foundation.policy import lifecycle as spec_module
from roboco.foundation.policy import tracing as _tr
from roboco.foundation.policy.content import (
    ContentValidationError,
    markers,
    validate_findings,
)
from roboco.services.content_notes import apply_structured_note
from roboco.services.gateway.choreographer import findings as findings_lib
from roboco.services.gateway.choreographer._protocol import actor_context_fields
from roboco.services.gateway.choreographer.collision import build_collision_context
from roboco.services.gateway.envelope import Envelope
from roboco.services.gateway.evidence_builder import build_evidence_for_task

logger = structlog.get_logger()

# Cap on one criteria_verified entry's `evidence` — mirrors Finding.fix's cap
# (roboco.foundation.policy.content.models._FINDING_FIX_CAP): a pointer
# (file:line, screenshot ref, test name), not a transcript.
_CRITERION_EVIDENCE_CAP = 500

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
    return markers.get_original_developer(task)


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
        briefing = await self._briefing_for(qa_agent_id, task_id, full=True)
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
            agent_team=str(agent.team) if agent is not None and agent.team else None,
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

        ev = await self._build_qa_claim_evidence(qa_agent_id, t, task_id)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS["claim_review"].next_hint(t),
            evidence=ev.as_dict(),
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)

    async def _qa_convention_findings(
        self, qa_agent_id: UUID, t: Any
    ) -> list[dict[str, Any]]:
        """Convention-validator findings on the task's changed files (flag-gated).

        Empty when the subsystem is off; a validator that could not run surfaces
        a single explicit ``could_not_run`` entry rather than being dropped, so
        QA never mistakes a silent failure for a clean diff.
        """
        if not settings.conventions_enabled:
            return []
        result = await self.git.conventions_check_for_task(qa_agent_id, t)
        if result.get("could_not_run"):
            reason = result.get("reason") or "validator could not run"
            return [{"could_not_run": True, "reason": reason}]
        return list(result.get("findings", []))

    @staticmethod
    def _qa_video_context(t: Any) -> dict[str, Any] | None:
        """QA-facing artifact context for a video-authoring task.

        None for every non-video task. Carries the composition id (from the
        ``video_draft`` marker) + the latest ``request_render`` preview so QA
        is pointed at the rendered artifact instead of the source alone.
        """
        if getattr(t, "source", None) != markers.VIDEO_TASK_SOURCE:
            return None
        draft = markers.get_video_draft(t) or {}
        return {
            "composition_id": draft.get("composition_id"),
            "render_preview": markers.get_render_preview(t),
            "note": (
                "This task ships a rendered video. Call request_render to "
                "render the PR branch state, then Read every returned frame "
                "image — verify each acceptance criterion's scene appears "
                "fully and legibly. Do not pass on source reading alone."
            ),
        }

    async def _build_qa_claim_evidence(
        self, qa_agent_id: UUID, t: Any, task_id: UUID
    ) -> Any:
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
        # The ask-chain (parent → root descriptions) so QA judges INTENT
        # against the intake's original analysis, not only the leaf's ACs.
        # Leaf-only journals stay (include_ancestors defaults False above);
        # ancestor *descriptions* are the ask, not work-so-far.
        parent_context = await self.evidence_repo.ancestor_context_for_task(task_id)
        convention_findings = await self._qa_convention_findings(qa_agent_id, t)
        open_findings = await findings_lib.open_findings_for_task(
            self.task.session, t.id
        )
        # The full ledger (every status) so QA verifies prior rounds
        # item-by-item, not just what is still open.
        prior_findings = await findings_lib.full_ledger_for_task(
            self.task.session, t.id
        )
        # The collision map: surfaced siblings (same parent) that would
        # collide with this task, with the declared-vs-actual drift (QA has
        # the real touched files in hand). Best-effort — a fetch failure omits
        # the block rather than breaking claim_review.
        collision_context: list[dict[str, Any]] | None = None
        try:
            if t.parent_task_id:
                siblings = await self.task.get_subtasks(t.parent_task_id)
                collision_context = build_collision_context(
                    task=t, siblings=siblings, actual_files=files_changed
                )
        except Exception as exc:  # best-effort enrichment, never breaks the verb
            logger.warning(
                "qa_collision_context_skip", task_id=str(t.id), error=str(exc)
            )
        return build_evidence_for_task(
            t,
            journal_highlights=journal_highlights,
            files_changed=files_changed,
            pr_diff_summary=diff_summary,
            convention_findings=convention_findings,
            revision_findings=open_findings,
            prior_findings=prior_findings,
            parent_context=parent_context,
            collision_context=collision_context,
            video_context=self._qa_video_context(t),
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
            agent_team=str(agent.team) if agent is not None and agent.team else None,
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

    @staticmethod
    def _nonblank_verdicts(ac_verdicts: list[str] | None) -> list[str]:
        """Non-empty verdict strings (defensive against blanks / non-strings)."""
        return [
            v.strip() for v in (ac_verdicts or []) if isinstance(v, str) and v.strip()
        ]

    @classmethod
    def _qa_ac_coverage_check(
        cls, task: Any, ac_verdicts: list[str] | None
    ) -> Envelope | None:
        """Legacy count-only per-AC gate — superseded by ``criteria_verified``.

        No longer wired into ``pass_review`` (a count of arbitrary strings
        never verified they actually named the right criterion — the live gap
        ``_validate_criteria_verified`` closes). Kept for ``ac_verdicts``'
        existing callers/tests; ``ac_verdicts`` itself still folds into the
        persisted notes when supplied.
        """
        criteria = list(getattr(task, "acceptance_criteria", None) or [])
        if not criteria:
            return None
        verdicts = cls._nonblank_verdicts(ac_verdicts)
        if len(verdicts) >= len(criteria):
            return None
        return Envelope.invalid_state(
            message=(
                f"pass_review needs a verification for each of the "
                f"{len(criteria)} acceptance criteria; got {len(verdicts)}."
            ),
            remediate=(
                "Re-call pass_review with ac_verdicts=[...] — one entry per "
                "acceptance criterion (in the task's criterion order) stating "
                "how you verified it. If any criterion does NOT hold, call "
                "fail_review with the specific gaps instead of passing a partial."
            ),
            context_briefing={},
        )

    @classmethod
    def _merge_ac_verdicts_into_notes(
        cls, notes: str, ac_verdicts: list[str] | None
    ) -> str:
        """Fold the per-criterion verdicts into the persisted QA notes.

        Keeps the per-criterion verification in the audit trail (qa_notes), so
        PM/CEO can see exactly which criteria QA checked and how.
        """
        lines = cls._nonblank_verdicts(ac_verdicts)
        if not lines:
            return notes
        body = "\n".join(f"- {line}" for line in lines)
        return f"{notes}\n\nPer-criterion verification:\n{body}"

    @staticmethod
    def _store_qa_note(
        task: Any,
        notes: str,
        ac_verdicts: list[str] | None,
        *,
        passed: bool,
        findings: list[Any] | None = None,
    ) -> None:
        """Best-effort: persist the QA review as a structured QaNote (chokepoint).

        Each ac_verdict string is coerced into a structured entry (a pass means
        every criterion verified). ``findings`` (fail_review's validated,
        ledger-inserted revision findings) embed alongside — parity with
        ``PrReviewContent.findings``. Falls back silently to the legacy
        qa_notes string on any validation issue, so a QA transition is never
        blocked by note formatting. Mirrors the PR-reviewer pattern.
        """
        verdicts = (
            [
                {
                    "criterion": v.strip(),
                    "status": "verified",
                    "how": "verified by QA during review",
                }
                for v in (ac_verdicts or [])
                if isinstance(v, str) and v.strip()
            ]
            if passed
            else []
        )
        try:
            apply_structured_note(
                task,
                "qa",
                {
                    "summary": notes,
                    "ac_verdicts": verdicts,
                    "findings": [f.model_dump(mode="json") for f in (findings or [])],
                    "verdict": "passed" if passed else "failed",
                },
            )
        except ContentValidationError:
            return

    async def _qa_review_text_gate(
        self,
        *,
        qa_agent_id: UUID,
        task_id: UUID,
        notes: str,
        task: Any,
        role_str: str,
        verb: str,
        soup_checks: tuple[tuple[str, Any, int], ...],
    ) -> Envelope | None:
        """Notes/journal/evidence gate + free-text anti-soup, in one call.

        Shared by pass_review (checks ``notes``) and fail_review (checks each
        ``issue``). Keeps the soup branch out of the verb bodies so they stay
        under the cyclomatic bound. Returns the first rejection, else ``None``.
        """
        gate = await self._qa_pass_gate_check(qa_agent_id, task_id, notes, task, verb)
        if gate is not None:
            return gate
        soup = self._free_text_soup(checks=soup_checks)
        if soup is None:
            return None
        return await self._emit_rejection(
            soup.with_introspection(task=task, role=role_str),
            agent_id=qa_agent_id,
            task_id=task_id,
            verb=verb,
        )

    @staticmethod
    def _parse_criterion_entry(entry: Any, idx: int) -> tuple[str, str] | Envelope:
        """Validate one ``criteria_verified`` entry into a (criterion, evidence)
        pair, or an ``Envelope`` rejection on any structural problem."""
        criterion = entry.get("criterion") if isinstance(entry, dict) else None
        evidence = entry.get("evidence") if isinstance(entry, dict) else None
        if not isinstance(criterion, str) or not criterion.strip():
            return Envelope.invalid_state(
                message=f"criteria_verified[{idx}] is missing a `criterion` string",
                remediate=(
                    "each entry needs {criterion, evidence} naming one "
                    "acceptance criterion"
                ),
            )
        if not isinstance(evidence, str) or not evidence.strip():
            return Envelope.invalid_state(
                message=(
                    f"criteria_verified[{idx}] ({criterion!r}) is missing `evidence`"
                ),
                remediate=(
                    "state concrete evidence: file:line, screenshot ref, "
                    "rendered-frame path, test name"
                ),
            )
        if len(evidence) > _CRITERION_EVIDENCE_CAP:
            return Envelope.invalid_state(
                message=(
                    f"criteria_verified[{idx}] evidence exceeds "
                    f"{_CRITERION_EVIDENCE_CAP} chars"
                ),
                remediate="keep evidence concise — a pointer, not a transcript",
            )
        return criterion.strip(), evidence.strip()

    @classmethod
    def _parse_criteria_verified_entries(
        cls, criteria_verified: list[dict[str, Any]]
    ) -> tuple[list[tuple[str, str]], Envelope | None]:
        """Shape + soup validation for every ``criteria_verified`` entry.

        Pure parsing — AC matching/coverage is the caller's job. Split out
        of ``_validate_criteria_verified`` to keep its return count under
        the complexity bound.
        """
        pairs: list[tuple[str, str]] = []
        for idx, entry in enumerate(criteria_verified):
            parsed = cls._parse_criterion_entry(entry, idx)
            if isinstance(parsed, Envelope):
                return [], parsed
            pairs.append(parsed)
        soup = cls._free_text_soup(
            checks=(("criteria_verified.evidence", [e for _, e in pairs], 8),)
        )
        if soup is not None:
            return [], soup
        return pairs, None

    @classmethod
    def _validate_criteria_verified(
        cls, t: Any, criteria_verified: list[dict[str, Any]] | None
    ) -> tuple[list[tuple[str, str]], Envelope | None]:
        """Mandatory per-AC verification gate for pass_review.

        Returns ``(pairs, rejection)`` — ``pairs`` is ``[]`` and ``rejection``
        non-None on any failure: none supplied (lists every AC verbatim),
        a malformed or soupy entry, an entry naming a criterion absent from
        the task (names the valid criteria), or a task AC left uncovered
        (names the gap). No task ACs imposes no requirement (mirrors the
        legacy ``_qa_ac_coverage_check``). A gestalt "looks good" is no
        longer enough — every criterion needs its own matched, evidenced
        entry, or QA must call ``fail_review`` instead of passing a partial.
        """
        criteria = list(getattr(t, "acceptance_criteria", None) or [])
        if not criteria:
            return [], None
        if not criteria_verified:
            return [], Envelope.invalid_state(
                message=(
                    "pass_review needs criteria_verified naming every "
                    f"acceptance criterion; none supplied. Unverified: {criteria!r}"
                ),
                remediate=(
                    "re-run the review and call pass_review with "
                    "criteria_verified=[{criterion, evidence}, ...] — stamp "
                    "EACH criterion with concrete evidence (file:line, "
                    "screenshot ref, rendered-frame path, test name)"
                ),
            )
        pairs, bad = cls._parse_criteria_verified_entries(criteria_verified)
        if bad is not None:
            return [], bad
        provided = [c for c, _ in pairs]
        if unknown := findings_lib.unmatched_criteria(t, provided):
            return [], Envelope.invalid_state(
                message=(
                    f"criteria_verified names criteria not on this task: {unknown!r}"
                ),
                remediate=(
                    "each entry's criterion must match one of the task's "
                    f"acceptance criteria (by id or exact text): {criteria!r}"
                ),
            )
        if uncovered := findings_lib.uncovered_acceptance_criteria(t, provided):
            return [], Envelope.invalid_state(
                message=(
                    "criteria_verified is missing these acceptance criteria: "
                    f"{uncovered!r}"
                ),
                remediate=(
                    "stamp every criterion with concrete evidence, or call "
                    "fail_review with the specific gap if one does not hold"
                ),
            )
        return pairs, None

    @staticmethod
    def _render_criteria_verified(pairs: list[tuple[str, str]]) -> list[str]:
        """One '[AC] <criterion> — verified: <evidence>' line per entry.

        Style-matched to the findings ledger's '[F-<id8>] ...' bracket-tag
        rendering (``findings_lib.render_finding_line``).
        """
        return [
            f"[AC] {criterion} — verified: {evidence}" for criterion, evidence in pairs
        ]

    @classmethod
    def _merge_criteria_verified_into_notes(
        cls, notes: str, pairs: list[tuple[str, str]]
    ) -> str:
        """Fold the per-AC verification lines into the persisted QA notes.

        Mirrors ``_merge_ac_verdicts_into_notes`` — keeps the per-criterion
        verification in the audit trail (qa_notes) so PM/CEO see exactly how
        QA verified each acceptance criterion.
        """
        lines = cls._render_criteria_verified(pairs)
        if not lines:
            return notes
        return f"{notes}\n\n" + "\n".join(lines)

    async def _qa_pass_final_gates(
        self,
        qa_agent_id: UUID,
        task_id: UUID,
        t: Any,
        role_str: str,
        criteria_verified: list[dict[str, Any]] | None,
    ) -> tuple[Envelope | None, list[tuple[str, str]]]:
        """Per-AC verification + toolchain-runnability gates for pass_review.

        Returns ``(rejection, pairs)`` — the first emitted rejection (else
        None) and the validated ``criteria_verified`` (criterion, evidence)
        pairs for the caller to render into notes. QA must not PASS on a
        workspace that cannot run the suite — that is a source-read
        "verification"; fail_review is unaffected.
        """
        pairs, bad = self._validate_criteria_verified(t, criteria_verified)
        if bad is not None:
            rejection = await self._emit_rejection(
                bad.with_introspection(task=t, role=role_str),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="pass_review",
            )
            return rejection, []
        if toolchain := await self._toolchain_broken_guard(qa_agent_id, t):
            rejection = await self._emit_rejection(
                toolchain.with_introspection(task=t, role=role_str),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="pass_review",
            )
            return rejection, []
        return None, pairs

    async def pass_review(
        self,
        qa_agent_id: UUID,
        task_id: UUID,
        notes: str,
        ac_verdicts: list[str] | None = None,
        criteria_verified: list[dict[str, Any]] | None = None,
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

        ``criteria_verified`` ({criterion, evidence} entries) is the
        mandatory per-AC verification gate (``_validate_criteria_verified``):
        every one of the task's acceptance criteria must be named by exactly
        one entry — matched by AC id or exact text, the same match
        ``fail_review``'s findings ledger uses for its own ``criterion``
        field — carrying substantive evidence, or the pass is refused. A
        gestalt "looks good" is no longer enough; QA must walk each
        criterion. ``ac_verdicts`` (legacy, count-only) still folds into the
        persisted notes when supplied but no longer gates the pass.
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
        if gate_rejection := await self._qa_review_text_gate(
            qa_agent_id=qa_agent_id,
            task_id=task_id,
            notes=notes,
            task=t,
            role_str=role_str,
            verb="pass_review",
            soup_checks=(("notes", notes, 8),),
        ):
            return gate_rejection
        final_rejection, criteria_pairs = await self._qa_pass_final_gates(
            qa_agent_id, task_id, t, role_str, criteria_verified
        )
        if final_rejection is not None:
            return final_rejection

        briefing = await self._briefing_for(qa_agent_id, task_id)
        merged_notes = self._merge_criteria_verified_into_notes(
            self._merge_ac_verdicts_into_notes(notes, ac_verdicts), criteria_pairs
        )
        spec_ctx = spec_module.Context(
            actor_id=qa_agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            agent_team=str(agent.team) if agent is not None and agent.team else None,
            original_developer_slug=_extract_original_developer(t),
            notes=merged_notes,
        )
        self._store_qa_note(t, notes, ac_verdicts, passed=True)
        runner = self._verb_runner()
        try:
            # QA passing IS the confirmation: bulk-verify every already-
            # addressed qa-origin finding, in the same session/transaction as
            # the pass itself (not best-effort — the ledger's integrity is
            # the point). A failure here raises before run_intent, so the
            # pass never lands against a stale ledger.
            await findings_lib.stamp_addressed_verified(
                self.task.session, t.id, origin="qa"
            )
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

        warning = await self._pass_review_documenter_handoff(qa_agent_id, task_id, t)
        await self._teardown_sandbox_best_effort(qa_agent_id)
        env = Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS["pass_review"].next_hint(t),
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)
        if warning:
            env.warning = warning
        return env

    async def _pass_review_documenter_handoff(
        self, qa_agent_id: UUID, task_id: UUID, t: Any
    ) -> str | None:
        """Best-effort reassign + a2a-notify the team's documenter.

        Returns a warning string when the side-effect failed (the QA-pass
        transition is already committed at this point), else None. Pulled
        out of ``pass_review`` to keep it under the cyclomatic bound.
        """
        doc_agent = await self.task.documenter_for_team(t.team)
        if doc_agent is None:
            return None
        try:
            await self.task.reassign(task_id, doc_agent.id)
            if self.a2a:
                await self.a2a.send(
                    from_agent=qa_agent_id,
                    to_agent=doc_agent.id,
                    skill="documentation",
                    task_id=task_id,
                    body=f"QA passed task {t.id}. PR: {t.pr_url}. Please document.",
                )
        except Exception as exc:
            logger.warning(
                "pass_review side-effect failed - transition committed, "
                "handoff did not fire",
                task_id=str(task_id),
                error=str(exc),
            )
            return (
                f"QA-pass transition committed but the documenter handoff "
                f"failed ({exc}). Re-issue the notification via dm."
            )
        return None

    @staticmethod
    def _validate_fail_review_findings(
        t: Any, issues: list[str] | None, findings: list[dict[str, Any]] | None
    ) -> tuple[list[Any], Envelope | None]:
        """Normalize + validate fail_review's findings.

        Returns ``(validated, rejection)`` — ``validated`` is ``[]`` and
        ``rejection`` non-None on any failure: no findings at all (after the
        ``issues`` shim), over the hard cap, malformed structure, or a
        ``criterion`` that matches none of the task's acceptance criteria.
        The caller attaches ``context_briefing``/introspection (this helper
        has no ``self``).
        """
        raw = findings_lib.merge_findings_and_issues(findings, issues)
        if not raw:
            return [], Envelope.invalid_state(
                message="fail_review requires at least one finding",
                remediate=(
                    "pass findings=[{file, severity, expected, actual}, ...] "
                    "(issues=['...'] is still accepted this release, deprecated)"
                ),
            )
        if cap := findings_lib.findings_count_guard(raw):
            return [], cap
        try:
            validated = validate_findings(raw)
        except ContentValidationError as exc:
            return [], Envelope.invalid_state(
                message=f"malformed finding: {exc.field} — {exc.reason}",
                remediate=(
                    "each finding needs expected + actual (file/line/severity/"
                    "criterion/fix/evidence optional)"
                ),
            )
        if unknown := findings_lib.unknown_finding_criteria(t, validated):
            return [], findings_lib.criterion_mismatch_rejection(t, unknown)
        return validated, None

    async def _attach_fail_review_findings(
        self,
        t: Any,
        actor_slug: str | None,
        role_str: str,
        validated: list[Any],
    ) -> str:
        """Insert fail_review's findings into the ledger, write the QaNote
        (with findings embedded), and return the id-prefixed rendering used
        for the a2a body to the original developer."""
        # GatewayAgentView carries no slug field — falls back to the role
        # string (mirrors _post_gate_review's reviewer_slug fallback).
        author_slug = actor_slug or role_str
        _, summary = await findings_lib.insert_and_render(
            self.task.session,
            task_id=t.id,
            origin="qa",
            round=findings_lib.next_round(t),
            author_slug=author_slug,
            findings=validated,
        )
        self._store_qa_note(t, summary, None, passed=False, findings=validated)
        return summary

    async def fail_review(
        self,
        qa_agent_id: UUID,
        task_id: UUID,
        issues: list[str] | None = None,
        findings: list[dict[str, Any]] | None = None,
    ) -> Envelope:
        """QA fails the task with concrete findings; transitions to needs_revision.

        ``findings`` is the structured revision-findings ledger entry (wire
        Pattern A — a loose ``list[dict]``, deep-validated here via
        ``validate_findings``). ``issues`` (free text) is still accepted for
        one release and shimmed into file-less findings (deprecated; logs a
        warning). Spec gate runs first and enforces role membership (qa) plus
        the composed ``qa_fail`` action's source-status (AWAITING_QA),
        task_type, and self-review block (same shape as ``pass_review``).
        After the spec gate accepts, the verb-specific gates stay (ownership
        via ``_verify_qa_owner`` and notes-length / journal:learning /
        qa_evidence_inspected via ``_qa_pass_gate_check``). Findings are
        inserted into the ledger BEFORE the composed atomic ``qa_fail`` runs
        (mirrors ``_store_qa_note`` writing before ``run_intent``, so the
        ledger rows and the transition commit together), then the composed
        atomic is dispatched through ``VerbRunner.run_intent`` and the verb
        body notifies the original developer via a2a with the same rendering.
        """
        rejection, t = await self._verify_qa_owner(qa_agent_id, task_id, "fail_review")
        if rejection is not None:
            return rejection
        validated, bad = self._validate_fail_review_findings(t, issues, findings)
        if bad is not None:
            bad.context_briefing = await self._briefing_for(qa_agent_id, task_id)
            return await self._emit_rejection(
                bad.with_introspection(task=t, role="qa"),
                agent_id=qa_agent_id,
                task_id=task_id,
                verb="fail_review",
            )

        # Placeholder (id-less) rendering drives the existing gates — the
        # ledger ids don't exist until after insert, further down.
        notes = findings_lib.render_findings_summary([(None, f) for f in validated])
        issues_tuple = tuple(issues or [])
        spec_rejection, agent, role_str = await self._qa_review_spec_gate(
            _QASpecGateInputs(
                qa_agent_id=qa_agent_id,
                task_id=task_id,
                task=t,
                verb="fail_review",
                notes=notes,
                issues=issues_tuple,
            )
        )
        if spec_rejection is not None:
            return spec_rejection
        if gate_rejection := await self._qa_review_text_gate(
            qa_agent_id=qa_agent_id,
            task_id=task_id,
            notes=notes,
            task=t,
            role_str=role_str,
            verb="fail_review",
            soup_checks=(("notes", notes, 8),),
        ):
            return gate_rejection

        briefing = await self._briefing_for(qa_agent_id, task_id)
        actor_slug, agent_team = actor_context_fields(agent)
        spec_ctx = spec_module.Context(
            actor_id=qa_agent_id,
            actor_slug=actor_slug,
            agent_team=agent_team,
            original_developer_slug=_extract_original_developer(t),
            notes=notes,
            issues=issues_tuple,
        )
        summary = await self._attach_fail_review_findings(
            t, actor_slug, role_str, validated
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

        if t.assigned_to is not None and self.a2a:
            await self.a2a.send(
                from_agent=qa_agent_id,
                to_agent=t.assigned_to,
                skill="code_review",
                task_id=task_id,
                body=f"QA needs changes.\n{summary}",
            )
        await self._teardown_sandbox_best_effort(qa_agent_id)
        env = Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS["fail_review"].next_hint(t),
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)
        if hint := findings_lib.findings_count_hint(validated):
            env.warning = hint
        return env
