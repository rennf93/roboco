"""Recording and publishing the in-path PR-review gate's verdict.

Split out of ``pr_gate.py``: the canonical ``pr_review`` structured note (its
summary and payload, and the assembled PR's head SHA stamped on ``pr_fail`` and
re-stamped when the head advances), and the review posted on the PR itself.

Inherits ``ChoreographerHelpers`` under ``TYPE_CHECKING`` only so mypy resolves
``self.task`` etc.; at runtime the composed ``Choreographer`` supplies the real
attributes via MRO (same pattern as ``QAMixin`` / ``PRReviewerMixin``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from roboco.foundation.policy import lifecycle as spec_module
from roboco.foundation.policy.batch import is_batch_root_subtask
from roboco.foundation.policy.content import ContentValidationError
from roboco.services.gateway.choreographer import devops_gate as devops_gate_lib

if TYPE_CHECKING:
    from roboco.services.gateway.choreographer._protocol import ChoreographerHelpers

    _Base = ChoreographerHelpers
else:
    _Base = object

logger = structlog.get_logger()


class PRGateVerdictMixin(_Base):
    """The ``pr_review`` verdict note and the review posted on the PR."""

    async def _record_gate_verdict_for(
        self,
        verb: str,
        t: Any,
        notes: str,
        *,
        issues: tuple[str, ...],
        ci_note: str | None = None,
        findings: list[Any] | None = None,
        reviewer_role: str = "pr_reviewer",
    ) -> str | None:
        """Author the canonical pr_review verdict note before the transition.

        On pr_fail also stamp the assembled PR's head SHA so the next submit_root
        can structurally refuse to re-submit the unchanged root (the 2026-06-27
        infinite pr_fail re-submit loop). Best-effort: a capture failure leaves
        head_sha absent and submit_root fails open rather than wedging the PM.
        On pr_pass, ``ci_note`` (set by ``_pr_pass_blocked`` when the CI-status
        guard passed through a project with no CI configured) is stamped into
        the verdict's ``ci_status`` field as evidence the guard actually ran.
        ``findings`` (pr_fail's already-ledgered revision findings) embed
        alongside — the first time this slot's ``findings`` list is ever
        non-empty on the in-path gate.

        Returns the captured head_sha for pr_fail (None for pr_pass) so the caller
        can re-capture after the transition commits and re-stamp if the PR head
        advanced in between (#189 staleness).
        """
        if verb == "pr_fail":
            head_sha = await self._capture_pr_head_sha(t)
            self._record_gate_verdict(
                t,
                verb,
                notes,
                issues=issues,
                head_sha=head_sha,
                findings=findings,
                reviewer_role=reviewer_role,
            )
            return head_sha
        self._record_gate_verdict(
            t,
            verb,
            notes,
            issues=issues,
            ci_note=ci_note,
            findings=findings,
            reviewer_role=reviewer_role,
        )
        return None

    async def _re_stamp_pr_fail_head_sha_if_advanced(
        self,
        t: Any,
        notes: str,
        *,
        issues: tuple[str, ...],
        pre_sha: str | None,
        findings: list[Any] | None = None,
        reviewer_role: str = "pr_reviewer",
    ) -> None:
        """Re-capture the PR head SHA after the transition commits and re-stamp
        the verdict note when it advanced past the pre-transition capture (#189).

        The pre-transition capture can go stale if cell work lands on the root
        branch between that capture and the commit; a stale recorded SHA makes
        ``submit_root`` false-allow an unchanged re-submit and re-opens the
        pr_fail loop. Re-stamping only on a real advance keeps the no-advance
        case to a single note write. Best-effort: a re-capture failure leaves
        the pre-transition SHA in place (the fail-open direction). ``findings``
        must be re-passed here too — this re-stamp fully replaces the
        ``pr_review`` slot (``apply_structured_note`` overwrites, not merges),
        so omitting it would silently wipe the findings the pre-transition
        write just recorded.
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
                t,
                "pr_fail",
                notes,
                issues=issues,
                head_sha=post_sha,
                findings=findings,
                reviewer_role=reviewer_role,
            )

    async def _post_gate_review(
        self, t: Any, agent: Any, role_str: str, verb: str, notes: str
    ) -> None:
        """Post the gate verdict to the PR itself (best-effort, after the DB
        transition — a GitHub failure must not roll back the gate decision)."""
        reviewer_slug = getattr(agent, "slug", None) or role_str
        await self._post_gate_review_to_pr(t, verb, reviewer_slug, notes)

    def _record_gate_verdict(
        self,
        t: Any,
        verb: str,
        notes: str,
        issues: tuple[str, ...] = (),
        *,
        head_sha: str | None = None,
        ci_note: str | None = None,
        findings: list[Any] | None = None,
        reviewer_role: str = "pr_reviewer",
    ) -> None:
        """Persist the gate verdict as the canonical ``pr_review`` note.

        The tracing gate only threads ``notes`` through a throwaway shim, so
        nothing wrote the task's structured PR-reviewer slot — a task passed
        once and later failed kept showing the stale ``verdict: passed``. This
        authors the slot on every decision (``pr_pass`` → passed, ``pr_fail`` →
        failed) so it can never contradict the transition. For ``pr_fail``,
        ``findings`` (the ledgered, id-prefixed revision findings) populate
        the format-enforced ``findings`` list — its own render_markdown table
        already displays them, so ``summary`` stays a plain sentence (see
        ``_gate_verdict_summary``) rather than duplicating every line.
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

        The DevOps second reviewer (``reviewer_role="devops"``) writes its OWN
        ``devops_review`` slot instead - a devops verdict must never overwrite
        the primary reviewer's ``pr_review`` note - and stamps ``head_sha`` on
        BOTH verbs: the pr_pass precondition matches the verdict against the
        PR's current head, so the stamp is what makes the verdict re-arm.
        """
        from roboco.services.content_notes import apply_structured_note

        is_devops = reviewer_role == spec_module.Role.DEVOPS.value
        verdict = "passed" if verb == "pr_pass" else "failed"
        summary = self._gate_verdict_summary(verb, notes, issues, findings)
        payload = self._gate_verdict_payload(
            verdict,
            summary,
            issues,
            verb,
            head_sha=head_sha,
            ci_note=ci_note,
            findings=findings,
        )
        if is_devops and head_sha:
            payload["head_sha"] = head_sha
        try:
            apply_structured_note(
                t,
                devops_gate_lib.DEVOPS_REVIEW_ORIGIN if is_devops else "pr_review",
                payload,
            )
        except ContentValidationError:
            logger.warning(
                "gate verdict note skipped (invalid content)",
                verb=verb,
                task_id=str(getattr(t, "id", "")),
            )

    @staticmethod
    def _gate_verdict_summary(
        verb: str,
        notes: str,
        issues: tuple[str, ...],
        findings: list[Any] | None = None,
    ) -> str:
        """The verdict note's ``summary`` field.

        Findings render under their own ``## Findings`` table
        (render_markdown). Baking the per-finding text into ``summary`` too
        would duplicate every line on the Task Details "PR Reviewer Notes"
        card (once under ## Summary, once under ## Findings). The summary is
        a substantive non-issues sentence; ``notes`` (the id-prefixed
        rendering) still drives the GitHub PR post and the a2a to the owning
        PM — those are raw text, not rendered through render_markdown, so no
        duplication there.
        """
        count = len(findings) if findings else len(issues)
        if verb == "pr_fail" and count:
            return (
                f"In-path PR-review gate requested changes - "
                f"{count} issue(s) listed below."
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
        findings: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Assemble the structured ``pr_review`` note payload."""
        payload: dict[str, Any] = {
            "summary": summary,
            "findings": [f.model_dump(mode="json") for f in (findings or [])],
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
