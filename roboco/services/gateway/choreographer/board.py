"""Board + Auditor verbs (first per-role split).

Mixin extracted from ``_impl.py`` to prove the per-role pattern. Relies
on ``self.task`` and ``self._briefing_for`` from the base class via
Python's MRO. ``board_triage`` and ``auditor_triage`` are read-only
verbs that don't share helper code with any other role, making this
the safest first extraction.

The mixin inherits from ``ChoreographerHelpers`` only when type-checking
so mypy resolves ``self.task`` etc. to the typed surface. At runtime
the actual class is composed in ``__init__.py`` and inherits from
``_LegacyChoreographer`` (where the real implementations live).
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from roboco.foundation.policy.content import Severity
from roboco.services.gateway.envelope import Envelope
from roboco.services.repositories.review_findings import (
    STATUS_OPEN,
    ReviewFindingsRepository,
)

if TYPE_CHECKING:
    from uuid import UUID

    from roboco.services.gateway.choreographer._protocol import ChoreographerHelpers

    _Base = ChoreographerHelpers
else:
    _Base = object

# Severity stored as the enum's string value (``f.severity.value`` at insert).
_BLOCKING = frozenset({Severity.BLOCKER.value, Severity.MAJOR.value})


class BoardMixin(_Base):
    """Board (Product Owner + Head Marketing) + Auditor verbs."""

    async def board_triage(self, board_agent_id: UUID) -> Envelope:
        """Phase 4: Board triage — next strategic root task awaiting PM review.

        The idle branch (no strategic root to review) is also what the
        Product Owner's roadmap-exploration and Head of Marketing's
        feature-spotlight-exploration one-shot spawns hit first (their
        directly-assigned exploration task is never itself a "strategic root
        awaiting PM review"): pass ``include_company_goals`` so the CEO's
        charter (brand_voice/north_star) still reaches them there, without
        paying for the rest of ``full``'s heavy sections on this low-
        cardinality, board-only path.
        """
        strategic = await self.task.list_strategic_for_board()
        if strategic:
            t = strategic[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=(
                    f"review and call escalate_to_ceo(task_id='{t.id}', reason=...)"
                    " or i_am_idle"
                ),
                context_briefing=await self._briefing_for(
                    board_agent_id, t.id, full=True
                ),
            )
        return Envelope.ok(
            status="idle",
            task_id=None,
            next="no strategic-review work — i_am_idle",
            context_briefing=await self._briefing_for(
                board_agent_id, None, include_company_goals=True
            ),
        )

    async def auditor_triage(self, auditor_agent_id: UUID) -> Envelope:
        """Phase 4: Auditor triage — surfaces anomalies (long-running blocked, etc.)."""
        anomalies = await self.task.list_long_running_blocked()
        if anomalies:
            t = anomalies[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=(
                    "log a reflect-note observing the anomaly via "
                    f"note(scope='reflect', task_id='{t.id}', text='...')"
                ),
                context_briefing=await self._briefing_for(
                    auditor_agent_id, t.id, full=True
                ),
            )
        return Envelope.ok(
            status="idle",
            task_id=None,
            next="no anomalies — i_am_idle",
            context_briefing=await self._briefing_for(auditor_agent_id, None),
        )

    async def waive_finding(
        self, auditor_agent_id: UUID, finding_id: UUID, note: str
    ) -> Envelope:
        """Waive one minor/nit finding by id with a required note.

        The auditor is the only role that can close a finding without a dev
        fix — but only for non-blocking severity (minor/nit). Blocker/major
        must be fixed, never waived. No task status change: the ledger row
        moves ``open -> waived`` and an audit event records the decision.
        ``mark_waived`` is the long-unwired repo method this finally calls.
        """
        repo = ReviewFindingsRepository(self.task.session)
        row = await repo.get(finding_id)
        if row is None:
            return Envelope.not_found(
                message=f"finding {str(finding_id)[:8]} not found.",
                remediate=(
                    "find open findings via the task's GET /findings or triage()."
                ),
            )
        if row.status != STATUS_OPEN:
            return Envelope.invalid_state(
                message=(
                    f"finding {str(finding_id)[:8]} is already {row.status}; "
                    "only open findings can be waived."
                ),
                remediate=(
                    "pick an open finding — waived/addressed/verified "
                    "rows are immutable."
                ),
            )
        if row.severity in _BLOCKING:
            return Envelope.invalid_state(
                message=(
                    f"finding {str(finding_id)[:8]} is {row.severity} — "
                    "blocker/major findings must be fixed, never waived."
                ),
                remediate=(
                    "leave it for the dev to address; waive only minor/nit findings."
                ),
            )
        clean_note = note.strip()
        if not clean_note:
            return Envelope.invalid_state(
                message=(
                    "a waive requires a note explaining why this finding "
                    "is not worth a fix."
                ),
                remediate="pass note=<why this minor/nit is waived>.",
            )
        await repo.mark_waived(finding_id, clean_note)
        with contextlib.suppress(Exception):
            await self.audit.log_task_event(
                event_type="task.finding_waived",
                task_id=row.task_id,
                agent_id=auditor_agent_id,
                severity="info",
                details={
                    "finding_id": str(finding_id),
                    "severity": row.severity,
                    "origin": row.origin,
                    "note": clean_note[:300],
                },
            )
        return Envelope.ok(
            status="waived",
            task_id=str(row.task_id),
            next=(
                f"finding {str(finding_id)[:8]} waived; triage() for next item "
                "or i_am_idle"
            ),
        )
