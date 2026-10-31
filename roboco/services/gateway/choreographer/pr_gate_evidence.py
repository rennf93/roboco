"""What the in-path PR-review gate's reviewer is shown.

Split out of ``pr_gate.py``: the assembled task's real diff base, the files it
touched, the collision map, the B43 coherence scaffold, and the inline evidence
``claim_gate_review`` returns. The ``pr_pass`` guards, the DevOps predicate
and the second review read the same diff base from here, so each judges the
diff the reviewer saw.

Inherits ``ChoreographerHelpers`` under ``TYPE_CHECKING`` only so mypy resolves
``self.task`` etc.; at runtime the composed ``Choreographer`` supplies the real
attributes via MRO (same pattern as ``QAMixin`` / ``PRReviewerMixin``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from roboco.config import settings
from roboco.services.gateway.choreographer import findings as findings_lib
from roboco.services.gateway.choreographer.collision import build_collision_context
from roboco.services.gateway.choreographer.evidence_legs import (
    LegBudget,
    run_bounded_leg,
)
from roboco.services.gateway.evidence_builder import render_findings
from roboco.services.gateway.merge_chain import resolve_parent_branch

if TYPE_CHECKING:
    from roboco.services.gateway.choreographer._protocol import ChoreographerHelpers

    _Base = ChoreographerHelpers
else:
    _Base = object

logger = structlog.get_logger()


class PRGateEvidenceMixin(_Base):
    """The assembled diff, its touched files and the evidence built from them."""

    async def _gate_diff_parent(self, t: Any) -> str | None:
        """The assembled task's real parent branch, or None (branchless task).

        ``resolve_parent_branch`` reads the parent TASK's own ``branch_name``
        (correct across a team boundary — every cell→root hop, where the
        child's own team segment can't derive the root's ``main_pm``
        branch), unlike the string-derived ``parent_branch_for`` that
        ``git.diff``'s default base falls back on. Fail-open on a lookup
        error (None → the derived-base fallback), like every other
        ``resolve_parent_branch`` call site — a transient DB miss degrades
        the diff base, never 500s the gate verb.
        """
        if not t.branch_name:
            return None
        try:
            return await resolve_parent_branch(t, self.task)
        except Exception as exc:
            logger.warning("gate_diff_parent_skip", task_id=str(t.id), error=str(exc))
            return None

    async def _gate_changed_files(self, t: Any, gate_parent: str | None) -> list[str]:
        """The assembled PR's real touched files, same base as the diff — so
        the collision map's declared-vs-actual drift is accurate. Best-effort:
        a fetch failure yields ``[]`` and drift is simply omitted."""
        try:
            return list(
                await self.git.list_changed_files(
                    branch_name=t.branch_name, preferred_parent=gate_parent
                )
            )
        except Exception as exc:
            logger.warning(
                "gate_review_files_changed_skip",
                task_id=str(t.id),
                error=str(exc),
            )
            return []

    async def _gate_collision_evidence(
        self, t: Any, files_changed: list[str]
    ) -> list[dict[str, Any]] | None:
        """The collision map for an assembled task — surfaced siblings that
        would collide with it, with declared-vs-actual drift (the gate has
        the real touched files in hand). Best-effort — mirrors the
        ``parent_context`` block; a failure omits the block, never breaks the
        gate."""
        if not t.parent_task_id:
            return None
        try:
            siblings = await self.task.get_subtasks(t.parent_task_id)
            return build_collision_context(
                task=t, siblings=siblings, actual_files=files_changed or None
            )
        except Exception as exc:
            logger.warning(
                "gate_review_collision_context_skip",
                task_id=str(t.id),
                error=str(exc),
            )
            return None

    async def _build_gate_review_evidence(self, t: Any) -> dict[str, Any]:
        """Inline evidence for claim_gate_review: the assembled diff +
        criteria + the task's OPEN findings (so they aren't crowded out by
        the full ledger's cap) + the full findings ledger (every status,
        newest round first) so the reviewer verifies prior rounds
        item-by-item — parity with QA's ``_build_qa_claim_evidence``.

        The diff + changed-files legs run bounded via ``run_bounded_leg``
        against ONE shared ``LegBudget`` for this build — a timeout skips
        that piece and records a note in ``evidence_gaps`` instead of
        hanging this advisory (non-gating) verb.
        """
        diff = ""
        files_changed: list[str] = []
        evidence_gaps: list[str] = []
        if t.branch_name:
            gate_parent = await self._gate_diff_parent(t)
            budget = LegBudget(settings.evidence_assembly_timeout_seconds)
            diff = await run_bounded_leg(
                self.git.diff(
                    branch_name=t.branch_name,
                    preferred_parent=gate_parent,
                ),
                default="",
                budget=budget,
                leg="pr diff",
                hint="review the PR diff on GitHub directly",
                task_id=t.id,
                gaps=evidence_gaps,
            )
            files_changed = await run_bounded_leg(
                self._gate_changed_files(t, gate_parent),
                default=[],
                budget=budget,
                leg="files_changed",
                hint="review the PR diff on GitHub directly",
                task_id=t.id,
                gaps=evidence_gaps,
            )
        open_findings = await findings_lib.open_findings_for_task(
            self.task.session, t.id
        )
        prior_findings = await findings_lib.full_ledger_for_task(
            self.task.session, t.id
        )
        # The ask: this assembled task's own description + the upstream
        # chain (parent → root) so the gate reviewer checks INTENT against
        # the intake's original analysis, not only the AC list. Empty chain
        # for a parentless root is fine (omitted downstream when empty).
        parent_context: list[dict[str, Any]] = []
        try:
            parent_context = await self.evidence_repo.ancestor_context_for_task(t.id)
        except Exception as exc:
            logger.warning(
                "gate_review_parent_context_skip", task_id=str(t.id), error=str(exc)
            )
        evidence: dict[str, Any] = {
            "pr_number": t.pr_number,
            "pr_url": t.pr_url,
            "pr_diff": diff,
            "acceptance_criteria": list(getattr(t, "acceptance_criteria", None) or []),
            "is_assembled_pr": True,
            "revision_findings": render_findings(open_findings),
            "prior_findings": render_findings(prior_findings),
        }
        description = getattr(t, "description", None)
        if description:
            evidence["description"] = description
        if parent_context:
            evidence["parent_context"] = parent_context
        collision = await self._gate_collision_evidence(t, files_changed)
        if collision:
            evidence["collision_context"] = collision
        if evidence_gaps:
            evidence["evidence_gaps"] = evidence_gaps
        coherence = await self._coherence_scaffold(t, diff, files_changed)
        if coherence:
            evidence["coherence_scaffold"] = coherence
        return evidence

    async def _coherence_scaffold(
        self, t: Any, diff: str, files_changed: list[str]
    ) -> dict[str, Any] | None:
        """B43 assembled_coherence scaffold (decisions pilot, default-off).

        The gate reviewer judges assembled cell->root / root->master diffs
        raw; this pre-digests an AC-coverage x mixed-concerns x
        missing-pieces score from the diff + AC list already assembled
        here and surfaces it as ``coherence_scaffold`` in the claim
        envelope evidence. Purely advisory scaffolding for the existing
        reviewer: it never decides, blocks, or passes the gate. Below
        floor / shadow / off / no verdict / no diff omits the field.
        """
        if not diff:
            return None
        try:
            from roboco.services.decisions.pilots import PilotMode, pilot_mode

            mode = await pilot_mode(self.task.session, "assembled_coherence")
        except Exception:
            return None
        if mode is PilotMode.OFF:
            return None
        from roboco.services.decisions.pilots_gateway import assembled_coherence

        try:
            return await assembled_coherence(
                self.task.session,
                task_id=str(t.id),
                diff=diff,
                acceptance_criteria=list(getattr(t, "acceptance_criteria", None) or []),
                files_changed=files_changed,
            )
        except Exception as exc:
            logger.warning(
                "coherence_scaffold_skip",
                task_id=str(getattr(t, "id", "")),
                error=str(exc),
            )
            return None
