"""Board + Auditor verbs (P2-2 first per-role split).

Mixin extracted from ``_impl.py`` to prove the per-role pattern. Relies
on ``self.task`` and ``self._briefing_for`` from the base class via
Python's MRO. ``board_triage`` and ``auditor_triage`` are read-only
verbs that don't share helper code with any other role, making this
the safest first extraction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from roboco.services.gateway.envelope import Envelope

if TYPE_CHECKING:
    from uuid import UUID


class BoardMixin:
    """Board (Product Owner + Head Marketing) + Auditor verbs."""

    async def board_triage(self, board_agent_id: UUID) -> Envelope:
        """Phase 4: Board triage — next strategic root task awaiting PM review."""
        strategic = await self.task.list_strategic_for_board()  # type: ignore[attr-defined]
        if strategic:
            t = strategic[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=(
                    f"review and call escalate_to_ceo(task_id='{t.id}', reason=...)"
                    " or i_am_idle"
                ),
                context_briefing=await self._briefing_for(board_agent_id, t.id),  # type: ignore[attr-defined]
            )
        return Envelope.ok(
            status="idle",
            task_id=None,
            next="no strategic-review work — i_am_idle",
            context_briefing=await self._briefing_for(board_agent_id, None),  # type: ignore[attr-defined]
        )

    async def auditor_triage(self, auditor_agent_id: UUID) -> Envelope:
        """Phase 4: Auditor triage — surfaces anomalies (long-running blocked, etc.)."""
        anomalies = await self.task.list_long_running_blocked()  # type: ignore[attr-defined]
        if anomalies:
            t = anomalies[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=(
                    "log a reflect-note observing the anomaly via "
                    f"note(scope='reflect', task_id='{t.id}', text='...')"
                ),
                context_briefing=await self._briefing_for(auditor_agent_id, t.id),  # type: ignore[attr-defined]
            )
        return Envelope.ok(
            status="idle",
            task_id=None,
            next="no anomalies — i_am_idle",
            context_briefing=await self._briefing_for(auditor_agent_id, None),  # type: ignore[attr-defined]
        )
