"""Autonomous strategy engine ("engine 2") — dormant by default.

Engine 1 is the delivery lifecycle (agents shipping tasks). Engine 2 watches
the company against its standing goals and, when something needs attention,
surfaces it to the CEO. It is deliberately conservative:

* **Default OFF.** ``strategy_engine_enabled`` is False, so the orchestrator
  loop never starts and the existing system is completely unaffected.
* **Human-in-the-loop.** Even when enabled it only *notifies* the CEO — it
  never spends, builds, or auto-approves. Originating actual work stays a CEO
  decision (e.g. approving a pitch). This keeps a clear boundary around the
  autonomous surface.
* **Bounded + deduped.** One pass per interval, at most one notification per
  observation kind; the notification layer's purpose-dedup suppresses repeats
  until the CEO acknowledges.

Observations today: the company is idle while goals stand (drift toward doing
nothing), and tasks stranded in ``blocked`` past a threshold (work that needs a
human decision). Auto-origination (e.g. drafting pitches) is intentionally a
further opt-in, not part of this dormant baseline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from roboco.config import settings
from roboco.services.base import BaseService
from roboco.services.company_goals import get_company_goals_service
from roboco.services.notification import NotificationService
from roboco.services.task import get_task_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable


@dataclass(frozen=True)
class StrategyObservation:
    """One thing the engine noticed about company state."""

    kind: str  # "idle" | "stranded_blocked"
    summary: str
    detail: str


# Matches the blocker note `TaskService.soft_block` appends to `dev_notes`
# (task.py: `[BLOCKED - <TYPE>]\nReason: ...\nWhat's needed: ...`), anchored
# on the literal `_append_capped` blank-line separator (or end of string) so
# it never bleeds into the next note of any kind (dev_notes accumulates
# base-inheritance / substitute / assignment-redirect notes too, and is
# never cleared on unblock — several blocked notes can stack over a task's
# lifetime).
_BLOCKED_NOTE_RE = re.compile(
    r"\[BLOCKED - (?P<type>[^\]]+)\]\n"
    r"Reason: (?P<reason>.*?)\n"
    r"What's needed: (?P<what_needed>.*?)"
    r"(?=\n\n\[BLOCKED - |\Z)",
    re.DOTALL,
)


def _derive_block_reason(task: TaskTable) -> str:
    """The actual WHY a task is stuck, parsed from its last blocked note.

    ``blocker_resolver_type`` only records WHO resolves the block (human vs
    agent) — it is used here strictly as a fallback for a task blocked
    through a path that never appended a dev_notes note (e.g. a dependency
    ``block()``, which carries no reason/what-needed text at all).
    """
    matches = list(_BLOCKED_NOTE_RE.finditer(task.dev_notes or ""))
    if matches:
        m = matches[-1]
        reason = m.group("reason").strip()
        what_needed = m.group("what_needed").strip()
        return f"Reason: {reason} / What's needed: {what_needed}"
    return task.blocker_resolver_type.value if task.blocker_resolver_type else "unknown"


class StrategyEngine(BaseService):
    """Assess company state against goals; surface what needs the CEO."""

    service_name = "strategy_engine"

    async def assess(self) -> list[StrategyObservation]:
        """Read company state and return observations (no side effects)."""
        observations: list[StrategyObservation] = []
        task_svc = get_task_service(self.session)

        in_flight = await task_svc.list_in_progress_or_claimed()
        goals = await get_company_goals_service(self.session).get()
        objectives = goals.get("objectives") or []
        north_star = (goals.get("north_star") or "").strip()
        has_direction = bool(objectives or north_star)

        if not in_flight and has_direction:
            observations.append(
                StrategyObservation(
                    kind="idle",
                    summary="The company is idle but has standing goals.",
                    detail=(
                        "No delivery work is in progress or claimed, yet the "
                        "charter defines goals to pursue. Consider authoring a "
                        "pitch or starting work toward an objective."
                    ),
                )
            )

        stranded = await task_svc.list_long_running_blocked(
            threshold_minutes=settings.strategy_stranded_blocked_minutes
        )
        if stranded:
            observations.append(
                StrategyObservation(
                    kind="stranded_blocked",
                    summary=f"{len(stranded)} task(s) have been blocked a long time.",
                    detail=(
                        "These tasks have sat in 'blocked' beyond the threshold "
                        "and likely need a human decision to move forward."
                    ),
                )
            )
        return observations

    async def run_cycle(self) -> list[StrategyObservation]:
        """Assess and notify the CEO. No-op unless the engine is enabled.

        An ``idle`` observation additionally triggers a roadmap Board Program
        cycle (``BoardProgramEngine.open_program_cycle`` — enabled+dedup
        checked there, so a still-open cycle makes this a no-op); the nudge
        text reflects the outcome instead of only describing the drift.
        A ``stranded_blocked`` observation additionally triggers a Coroner
        autopsy (``CoronerEngine.open_for_incident`` — armed+dedup checked
        there) for the most-stale stranded task, and the nudge text names
        both the task and the autopsy outcome.
        """
        if not settings.strategy_engine_enabled:
            return []
        observations = await self.assess()
        if not observations:
            return []
        notifier = NotificationService()
        for obs in observations:
            body = f"[strategy engine] {obs.summary}\n\n{obs.detail}"
            if obs.kind == "idle":
                body = f"{body}\n\n{await self._trigger_roadmap_cycle()}"
            elif obs.kind == "stranded_blocked":
                body = f"{body}\n\n{await self._trigger_coroner_incident()}"
            await notifier.send_ack_notification(
                from_agent="system",
                to_agent="ceo",
                body=body,
            )
        return observations

    async def _trigger_roadmap_cycle(self) -> str:
        """Best-effort: open a roadmap cycle via the Board Program engine.

        A DB/engine failure here must never break the idle notification —
        degrades to a plain "attempted" line rather than raising.
        """
        try:
            from roboco.services.board_programs import get_board_program_engine

            task = await get_board_program_engine(self.session).open_program_cycle(
                "roadmap"
            )
        except Exception:
            self.log.warning(
                "strategy-engine: roadmap-cycle trigger failed (best-effort)"
            )
            return "Attempted to open a roadmap exploration cycle (failed; see logs)."
        if task is not None:
            return "A roadmap exploration cycle was opened for the Product Owner."
        return (
            "A roadmap exploration cycle is already open (or the roadmap "
            "program is disabled)."
        )

    async def _stranded_context(self, incident: TaskTable) -> dict[str, str]:
        """Derive ``block_reason``/``time_blocked``/``escalation_history`` for
        ``incident`` — the Coroner marker + prompt payload.

        The real blockage start is the latest ``task.blocked`` audit
        transition, not ``updated_at`` — that column moves on ANY update
        (markers, assignment, comments) while the task sits blocked, so an
        updated_at-only delta can wildly underestimate how long it's
        actually been stuck. Mirrors metrics.py's ``_blocked_since_map``,
        falling back to ``updated_at`` for a task with no audit row. The
        same query also counts real escalation events (``task.escalated`` /
        ``task.escalated_to_main_pm``) so ``escalation_history`` reports
        actual escalation activity, not just how many times the task was
        blocked.
        """
        from datetime import UTC, datetime

        from sqlalchemy import case, func, select

        from roboco.db.tables import AuditLogTable

        escalation_events = ("task.escalated", "task.escalated_to_main_pm")
        blocked_stats = await self.session.execute(
            select(
                func.count(case((AuditLogTable.event_type == "task.blocked", 1))),
                func.count(case((AuditLogTable.event_type.in_(escalation_events), 1))),
                func.max(
                    case(
                        (
                            AuditLogTable.event_type == "task.blocked",
                            AuditLogTable.timestamp,
                        )
                    )
                ),
            ).where(
                AuditLogTable.target_id == incident.id,
                AuditLogTable.target_type == "task",
                AuditLogTable.event_type.in_(("task.blocked", *escalation_events)),
            )
        )
        blocked_count, escalation_count, blocked_since = blocked_stats.one()
        blocked_since = blocked_since or incident.updated_at
        elapsed = (
            (datetime.now(UTC) - blocked_since).total_seconds() / 60
            if blocked_since
            else 0
        )
        return {
            "block_reason": _derive_block_reason(incident),
            "time_blocked": f"{elapsed:.0f} minutes" if blocked_since else "unknown",
            "escalation_history": (
                f"escalated {escalation_count or 0} time(s) "
                f"(blocked {blocked_count or 0} time(s), "
                f"revision_count={incident.revision_count or 0})"
            ),
        }

    async def _trigger_coroner_incident(self) -> str:
        """Best-effort: open a Coroner autopsy for the most-stale stranded task.

        Mirrors ``_trigger_roadmap_cycle``: re-queries the stranded list,
        calls ``CoronerEngine.open_for_incident`` for the head (most-stale
        first), and returns a human-readable outcome string. A DB/engine
        failure here must never break the stranded notification — degrades
        to a plain "attempted" line rather than raising.
        """
        incident: TaskTable | None = None
        try:
            from roboco.services.coroner_engine import get_coroner_engine

            task_svc = get_task_service(self.session)
            stranded = await task_svc.list_long_running_blocked(
                threshold_minutes=settings.strategy_stranded_blocked_minutes
            )
            if not stranded:
                return "No stranded tasks found for a Coroner autopsy."
            incident = stranded[0]

            task = await get_coroner_engine(self.session).open_for_incident(
                cast("UUID", incident.id),
                kind="stranded",
                extra_context=await self._stranded_context(incident),
            )
        except Exception:
            self.log.warning(
                "strategy-engine: coroner-incident trigger failed (best-effort)"
            )
            incident_ref = (
                f" for stranded task '{incident.title}' ({str(incident.id)[:8]})"
                if incident is not None
                else ""
            )
            return (
                f"Attempted to open a Coroner autopsy{incident_ref} (failed; see logs)."
            )
        task_ref = f"'{incident.title}' ({str(incident.id)[:8]})"
        if task is not None:
            return f"A Coroner autopsy was opened for stranded task {task_ref}."
        return (
            f"A Coroner autopsy was not opened for stranded task {task_ref} "
            "(already open or the coroner program is disabled)."
        )


def get_strategy_engine(session: AsyncSession) -> StrategyEngine:
    """Construct a StrategyEngine bound to ``session``."""
    return StrategyEngine(session)
