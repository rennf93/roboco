"""SentinelEngine — Sentinel (Board Program), org-scoped.

Mirrors ``roboco.services.periscope_engine.PeriscopeEngine``'s "detect ->
originate a CEO-gated artifact -> hold, complete-at-propose" shape: org-scoped
(spec §4 — it reads org-wide drift signals, not a repo, so it needs no
per-project opt-in), but the exploration task itself still needs a resolvable
``project_id`` — ``TaskService._require_target_or_umbrella`` is a hard
service-layer invariant every non-coordination task must satisfy, the same
constraint roadmap/x_feature/periscope already carry despite being org-scoped
too. It resolves against the RoboCo project (``settings.self_heal_project_slug``,
the same resolution roadmap/x_feature/periscope use) purely as an FK anchor —
the Auditor's assessment itself is DB-read-driven, not a repo read.

Also mirrors ``roboco.services.pest_control_engine.PestControlEngine``'s
server-assembled-evidence shape: the Auditor cannot run aggregate SQL itself
(no SQL tool), so this engine gathers waiver/findings/conventions/budget
evidence ahead of the spawn (``evidence_context``) — a pure DB read, no
content authored here either way.

* **No master enable flag.** Armed via ``roboco.services.board_programs.
  program_armed`` — the settings-store ``board_program.sentinel.enabled`` key
  is the ONLY arming path (no legacy flag exists for it); off by default like
  every other program.
* **One open cycle at a time.** Dedup by ``source=board_sentinel``
  non-terminal tasks.
* **The engine never authors content.** It opens ONE held, PENDING
  exploration task assigned to the Auditor (``Team.BOARD``,
  ``confirmed_by_human=False``); the board dispatcher spawns the Auditor, who
  assesses drift and calls ``propose_quality_report`` exactly once, which
  completes the exploration task in the same call (a report has no per-item
  CEO decision to wait on — mirrors ``PeriscopeEngine``'s complete-at-propose
  asymmetry, not roadmap/pest-control's per-item queue). The report goes to
  the CEO only — the Auditor stays silent to agents (spec §4's "Auditor
  boundary").
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

from roboco.config import settings
from roboco.foundation import identity as _foundation
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
from roboco.services.base import BaseService
from roboco.services.board_programs import program_armed
from roboco.services.project import get_project_service
from roboco.services.task import SENTINEL_SOURCE, TaskCreateRequest, get_task_service

if TYPE_CHECKING:
    from typing import Any
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import ProjectTable, TaskTable
    from roboco.services.task import TaskService

_EXPLORATION_TITLE = "Sentinel drift-watch cycle"
_EXPLORATION_DESCRIPTION = (
    "Assess org-wide quality drift — waiver-accumulation trends, "
    "conventions-violation hotspots, budget anomalies — and file ONE "
    '"state of quality" report for the CEO via propose_quality_report(). '
    "This is a report, not a task queue: nothing here materializes work, and "
    "there is no per-item approve/reject."
)

# Evidence-context caps — the prompt injection stays bounded regardless of how
# large the ledger/backlog has grown. Mirrors PestControlEngine's caps.
_TREND_LIMIT = 10
_MIN_CONVENTIONS_COUNT = 1


class SentinelEngine(BaseService):
    """Originate ONE held Sentinel-exploration cycle for the Auditor."""

    service_name = "sentinel_engine"

    async def run_cycle(self) -> TaskTable | None:
        """Originate one held exploration task, or None (no-op).

        No-ops when the program isn't armed, a cycle is already open, or the
        RoboCo project (the task's required FK anchor) isn't resolvable.
        Never authors content itself — the Auditor does, via
        ``propose_quality_report`` once spawned by the board dispatcher.
        """
        if not await program_armed(self.session, "sentinel"):
            return None
        task_svc = get_task_service(self.session)
        if await task_svc.list_open_sentinel_cycles():
            return None  # one open cycle at a time
        project = await self._roboco_project()
        if project is None or project.id is None:
            self.log.warning("sentinel-engine: RoboCo project not resolvable; skipping")
            return None
        return await self._originate(task_svc, cast("UUID", project.id))

    async def _roboco_project(self) -> ProjectTable | None:
        slug = (settings.self_heal_project_slug or "roboco-api").strip()
        return await get_project_service(self.session).get_by_slug(slug)

    async def _originate(self, task_svc: TaskService, project_id: UUID) -> TaskTable:
        """Open ONE PENDING, HELD exploration task assigned to the Auditor."""
        task = await task_svc.create(
            TaskCreateRequest(
                title=_EXPLORATION_TITLE,
                description=_EXPLORATION_DESCRIPTION,
                acceptance_criteria=[
                    "propose_quality_report() is called once with a headline, "
                    "1-7 evidence-backed drift items, and an overall_assessment"
                ],
                team=Team.BOARD,
                assigned_to=_foundation.AGENTS["auditor"].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.ADMINISTRATIVE,
                nature=TaskNature.NON_TECHNICAL,
                estimated_complexity=Complexity.LOW,
                project_id=project_id,
                status=TaskStatus.PENDING,
                source=SENTINEL_SOURCE,
                confirmed_by_human=False,  # HELD; board-dispatched, not delivery
            )
        )
        await self.session.flush()
        self.log.info(
            "sentinel exploration cycle opened (Auditor)",
            task_id=str(task.id),
        )
        return task

    async def evidence_context(self) -> str:
        """Server-assembled drift evidence for the Auditor's prompt —
        waived-findings trend, open-findings-by-severity, conventions-
        violation hotspots, and a budget snapshot, all capped so the prompt
        stays bounded. The Auditor cannot run these aggregate queries itself
        (no SQL tool), so the engine gathers them ahead of the spawn (mirrors
        ``PestControlEngine.evidence_context``'s shape)."""
        sections = [
            ("Waived findings this week vs prior", await self._waived_trend()),
            ("Open findings by severity", await self._open_findings_by_severity()),
            (
                "Conventions-violation hotspots",
                await self._conventions_hotspots(),
            ),
            ("Top spend by task (all-time)", await self._top_task_spend()),
            ("Top spend by project (all-time)", await self._top_project_spend()),
        ]
        return "\n\n".join(
            f"{title}:\n" + "\n".join(lines) for title, lines in sections if lines
        )

    async def _waived_trend(self) -> list[str]:
        from sqlalchemy import func, select

        from roboco.db.tables import TaskReviewFindingTable
        from roboco.services.repositories.review_findings import STATUS_WAIVED

        now = datetime.now(UTC)
        week_ago = now - timedelta(days=7)
        two_weeks_ago = now - timedelta(days=14)

        this_week = await self.session.scalar(
            select(func.count())
            .select_from(TaskReviewFindingTable)
            .where(
                TaskReviewFindingTable.status == STATUS_WAIVED,
                TaskReviewFindingTable.created_at >= week_ago,
            )
        )
        prior_week = await self.session.scalar(
            select(func.count())
            .select_from(TaskReviewFindingTable)
            .where(
                TaskReviewFindingTable.status == STATUS_WAIVED,
                TaskReviewFindingTable.created_at >= two_weeks_ago,
                TaskReviewFindingTable.created_at < week_ago,
            )
        )
        this_week = this_week or 0
        prior_week = prior_week or 0
        if this_week == 0 and prior_week == 0:
            return []
        return [f"- {this_week} waived this week (prior week: {prior_week})"]

    async def _open_findings_by_severity(self) -> list[str]:
        from sqlalchemy import func, select

        from roboco.db.tables import TaskReviewFindingTable
        from roboco.services.repositories.review_findings import STATUS_OPEN

        result = await self.session.execute(
            select(TaskReviewFindingTable.severity, func.count())
            .where(TaskReviewFindingTable.status == STATUS_OPEN)
            .group_by(TaskReviewFindingTable.severity)
            .order_by(func.count().desc())
            .limit(_TREND_LIMIT)
        )
        return [f"- {severity}: {count} open" for severity, count in result.all()]

    async def _conventions_hotspots(self) -> list[str]:
        """Conventions-violation mentions ARE cheaply reachable —
        ``project_convention_findings`` (migration-backed, indexed on
        ``detected_at``) already carries the latest validator findings per
        task; a plain GROUP BY rule is a single cheap aggregate query."""
        from sqlalchemy import func, select

        from roboco.db.tables import ProjectConventionFindingTable

        result = await self.session.execute(
            select(ProjectConventionFindingTable.rule, func.count())
            .group_by(ProjectConventionFindingTable.rule)
            .having(func.count() >= _MIN_CONVENTIONS_COUNT)
            .order_by(func.count().desc())
            .limit(_TREND_LIMIT)
        )
        return [f"- {rule}: {count} violations" for rule, count in result.all()]

    async def _top_task_spend(self) -> list[str]:
        from sqlalchemy import String, func, select

        from roboco.db.tables import AgentSpawnSessionTable, TaskTable

        task_id_str = cast("Any", TaskTable.id).cast(String)
        result = await self.session.execute(
            select(TaskTable.title, func.sum(AgentSpawnSessionTable.estimated_cost_usd))
            .select_from(AgentSpawnSessionTable)
            .join(TaskTable, task_id_str == AgentSpawnSessionTable.task_id)
            .where(AgentSpawnSessionTable.estimated_cost_usd.isnot(None))
            .group_by(TaskTable.id, TaskTable.title)
            .having(func.sum(AgentSpawnSessionTable.estimated_cost_usd) > 0)
            .order_by(func.sum(AgentSpawnSessionTable.estimated_cost_usd).desc())
            .limit(_TREND_LIMIT)
        )
        return [f"- {title}: ${total:.2f}" for title, total in result.all()]

    async def _top_project_spend(self) -> list[str]:
        from sqlalchemy import String, func, select

        from roboco.db.tables import AgentSpawnSessionTable, ProjectTable, TaskTable

        task_id_str = cast("Any", TaskTable.id).cast(String)
        result = await self.session.execute(
            select(
                ProjectTable.slug,
                func.sum(AgentSpawnSessionTable.estimated_cost_usd),
            )
            .select_from(AgentSpawnSessionTable)
            .join(TaskTable, task_id_str == AgentSpawnSessionTable.task_id)
            .join(ProjectTable, TaskTable.project_id == ProjectTable.id)
            .where(AgentSpawnSessionTable.estimated_cost_usd.isnot(None))
            .group_by(ProjectTable.id, ProjectTable.slug)
            .having(func.sum(AgentSpawnSessionTable.estimated_cost_usd) > 0)
            .order_by(func.sum(AgentSpawnSessionTable.estimated_cost_usd).desc())
            .limit(_TREND_LIMIT)
        )
        return [f"- {slug}: ${total:.2f}" for slug, total in result.all()]


def get_sentinel_engine(session: AsyncSession) -> SentinelEngine:
    """Build a SentinelEngine for ``session``."""
    return SentinelEngine(session)
