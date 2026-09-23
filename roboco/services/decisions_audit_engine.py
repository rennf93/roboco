"""Decisions Audit board-program engine (program #15, role: Auditor).

The CEO's standing review loop over the Decisions service (2026-09-23):
daily, the Auditor receives the persisted decision_log aggregates -
per-pilot volume, verdict distribution, mean confidence, action counts,
and fallback-tier spend, yesterday vs. the trailing-week baseline - and
files an adjustment recommendation when the evidence shows a pilot
miscalibrated. The engine itself never changes a threshold or a mode:
recommendations land as held work the CEO decides on, exactly like every
other Board Program (one held task, nothing auto-applies).

The same cycle prunes decision_log past
``settings.decisions_log_retention_days`` - the retention sweep rides the
program that consumes the history, so the baseline window and the prune
window are configured in one place and reviewed by the same pass.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import structlog
from sqlalchemy import delete, select

from roboco.config import settings
from roboco.db.tables import DecisionLogTable, TaskTable
from roboco.services.base import BaseService
from roboco.services.board_programs import program_armed
from roboco.services.task import DECISIONS_AUDIT_SOURCE, get_task_service

if TYPE_CHECKING:
    from uuid import UUID

    from roboco.db.tables import ProjectTable
    from roboco.services.task import TaskService

logger = structlog.get_logger(__name__)

_AUDIT_TITLE = "Decisions audit: daily pilot-health review"
_AUDIT_DESCRIPTION = (
    "Review the Decisions service's persisted verdict log (evidence "
    "gathered for you below: yesterday's per-pilot aggregates against the "
    "trailing-week baseline) and record your assessment via note(). Where "
    "the evidence shows a pilot miscalibrated - verdict volume collapsing "
    "or spiking, mean confidence drifting away from the threshold it gates "
    "on, actions that stopped correlating with outcomes, spend anomalies - "
    "name the pilot, the numbers, and your recommended knob change "
    "(threshold value or shadow/on flip) in your notes. You never adjust "
    "anything yourself: recommendations are requirements the CEO decides."
)

_ROBOCO_PROJECT_SLUG_FALLBACK = "roboco-api"
_MEAN_CONF_DIGITS = 3
_PILOT_LINE_CAP = 25
_BASELINE_DAYS = 7


class DecisionsAuditEngine(BaseService):
    """Originate ONE held decisions-audit review for the Auditor."""

    service_name = "decisions_audit_engine"

    async def run_cycle(self) -> TaskTable | None:
        """Daily pass: prune retention, originate the held review task, and
        send the CEO the daily brief. No-ops when the program isn't armed,
        a cycle is already open, or the RoboCo project isn't resolvable."""
        if not await program_armed(self.session, "decisions_audit"):
            return None
        await self.prune_retention()
        if await self._open_cycle_exists():
            return None  # one open cycle at a time
        project = await self._roboco_project()
        if project is None or project.id is None:
            self.log.warning("decisions-audit: RoboCo project not resolvable; skipping")
            return None
        task = await self._originate(
            get_task_service(self.session), cast("UUID", project.id)
        )
        brief = await self.daily_brief()
        if brief:
            await self._notify_ceo(brief)
        return task

    # ------------------------------------------------------------------
    # Aggregates
    # ------------------------------------------------------------------

    async def _rows_since(self, since: datetime) -> list[DecisionLogTable]:
        result = await self.session.execute(
            select(DecisionLogTable).where(DecisionLogTable.created_at >= since)
        )
        return list(result.scalars().all())

    async def daily_brief(self) -> str:
        """Server-assembled daily brief: yesterday's per-pilot aggregates
        against the trailing-week baseline. Empty when nothing fired."""
        now = datetime.now(UTC)
        rows = await self._rows_since(now - timedelta(days=_BASELINE_DAYS))
        if not rows:
            return ""
        cutoff = now - timedelta(days=1)
        yesterday = [r for r in rows if r.created_at >= cutoff]
        if not yesterday:
            return ""
        baseline_rows = [r for r in rows if r.created_at < cutoff]
        baseline_days = max(_BASELINE_DAYS - 1, 1)
        baseline: dict[str, dict[str, float]] = defaultdict(
            lambda: {"count": 0, "cost": 0.0}
        )
        for r in baseline_rows:
            baseline[r.pilot]["count"] += 1
            baseline[r.pilot]["cost"] += r.cost or 0.0

        by_pilot: dict[str, list[DecisionLogTable]] = defaultdict(list)
        for r in yesterday:
            by_pilot[r.pilot].append(r)

        lines = [
            f"Decisions audit brief (last 24h; baseline = prior {baseline_days}d):"
        ]
        for pilot in sorted(by_pilot, key=lambda p: -len(by_pilot[p]))[
            :_PILOT_LINE_CAP
        ]:
            rows_p = by_pilot[pilot]
            on_count = sum(1 for r in rows_p if r.mode == "on")
            confs = [c for r in rows_p for c in _gate_confidences(r) if c is not None]
            mean_conf = (
                round(sum(confs) / len(confs), _MEAN_CONF_DIGITS) if confs else None
            )
            spend = sum(r.cost or 0.0 for r in rows_p)
            line = (
                f"- {pilot}: {len(rows_p)} verdicts "
                f"(on={on_count}/shadow={len(rows_p) - on_count}), "
                f"mean confidence {mean_conf}, spend ${spend:.4f}"
            )
            per_day = baseline[pilot]["count"] / baseline_days
            if per_day:
                line += f", baseline {round(per_day, 1)}/day"
            lines.append(line)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Cycle plumbing (mirrors LibrarianEngine)
    # ------------------------------------------------------------------

    async def prune_retention(self) -> int:
        """Delete decision_log rows older than the retention window.
        Returns the number of rows removed (0 is the normal small case)."""
        cutoff = datetime.now(UTC) - timedelta(
            days=settings.decisions_log_retention_days
        )
        result = await self.session.execute(
            delete(DecisionLogTable).where(DecisionLogTable.created_at < cutoff)
        )
        await self.session.flush()
        removed = int(result.rowcount or 0)
        if removed:
            self.log.info(
                "decision_log retention prune",
                removed=removed,
                retention_days=settings.decisions_log_retention_days,
            )
        return removed

    async def _open_cycle_exists(self) -> bool:
        result = await self.session.execute(
            select(TaskTable.id)
            .where(
                TaskTable.source == DECISIONS_AUDIT_SOURCE,
                TaskTable.status.notin_(["completed", "cancelled"]),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def _roboco_project(self) -> ProjectTable | None:
        from roboco.services.project import get_project_service

        slug = (
            settings.self_heal_project_slug or _ROBOCO_PROJECT_SLUG_FALLBACK
        ).strip()
        return await get_project_service(self.session).get_by_slug(slug)

    async def _originate(self, task_svc: TaskService, project_id: UUID) -> TaskTable:
        """Open ONE PENDING, HELD audit task assigned to the Auditor."""
        from roboco.foundation import identity as _foundation
        from roboco.models.base import (
            Complexity,
            TaskNature,
            TaskStatus,
            TaskType,
            Team,
        )
        from roboco.services.task import TaskCreateRequest

        task = await task_svc.create(
            TaskCreateRequest(
                title=_AUDIT_TITLE,
                description=_AUDIT_DESCRIPTION,
                acceptance_criteria=[
                    "note() records your per-pilot assessment of the "
                    "injected decision_log aggregates (or an explicit "
                    "'nothing anomalous' statement)",
                    "where the evidence supports a knob change, the note "
                    "names the pilot, the numbers, and the recommended "
                    "threshold value or shadow/on flip",
                ],
                team=Team.BOARD,
                assigned_to=_foundation.AGENTS["auditor"].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.ADMINISTRATIVE,
                nature=TaskNature.NON_TECHNICAL,
                estimated_complexity=Complexity.LOW,
                project_id=project_id,
                status=TaskStatus.PENDING,
                source=DECISIONS_AUDIT_SOURCE,
                confirmed_by_human=False,  # HELD; board-dispatched, not delivery
            )
        )
        await self.session.flush()
        self.log.info("decisions-audit cycle opened (Auditor)", task_id=str(task.id))
        return task

    async def _notify_ceo(self, brief: str) -> None:
        """The daily report-back: one ack-required CEO notification carrying
        the aggregates brief. Best-effort; never fails the cycle."""
        try:
            from roboco.services.notification import NotificationService

            await NotificationService().send_ack_notification(
                from_agent="auditor",
                to_agent="ceo",
                body=f"[decisions-audit] Daily Decisions review:\n\n{brief}",
            )
        except Exception as exc:
            logger.warning(
                "decisions-audit: CEO brief notification failed (best-effort)",
                error=str(exc),
            )

    async def evidence_context(self) -> str:
        """The aggregates block injected into the Auditor's spawn prompt -
        the same evidence the CEO brief carries, so both see one truth."""
        return await self.daily_brief()


def _gate_confidences(row: DecisionLogTable) -> list[float | None]:
    conf = row.confidence or {}
    return [v for v in conf.values() if isinstance(v, (int, float))]


def get_decisions_audit_engine(session) -> DecisionsAuditEngine:
    return DecisionsAuditEngine(session)
