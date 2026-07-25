"""PeriscopeEngine — Periscope (Board Program), org-scoped.

Mirrors ``roboco.services.roadmap_engine.RoadmapEngine``'s "detect ->
originate a CEO-gated artifact -> hold" shape: org-scoped (spec §4 — it reads
the market, not a repo, so it needs no per-project opt-in to RUN, unlike
Pest Control), but the exploration task itself still needs a resolvable
``project_id`` — ``TaskService._require_target_or_umbrella`` is a hard
service-layer invariant every non-coordination task must satisfy, the same
constraint roadmap/x_feature already carry despite being org-scoped too. It
resolves against the RoboCo project (``settings.self_heal_project_slug``,
the same resolution roadmap/x_feature use) purely as an FK anchor — HoM's
research itself is web/KB-driven, not a repo read.

* **No master enable flag.** Armed via ``roboco.services.board_programs.
  program_armed`` — the settings-store ``board_program.periscope.enabled``
  key is the ONLY arming path (no legacy flag exists for it); off by default
  like every other program.
* **One open cycle at a time.** Dedup by ``source=board_periscope``
  non-terminal tasks.
* **The engine never authors content.** It opens ONE held, PENDING
  exploration task assigned to the Head of Marketing (``Team.BOARD``,
  ``confirmed_by_human=False``); the board dispatcher spawns HoM, who
  researches the market and calls ``propose_market_brief`` exactly once,
  which completes the exploration task in the same call (a report has no
  per-item CEO decision to wait on — mirrors ``XEngine.
  materialize_feature_spotlight``'s complete-at-propose asymmetry, not
  roadmap/pest-control's per-item queue).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from roboco.config import settings
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
from roboco.services.base import BaseService
from roboco.services.board_programs import program_armed
from roboco.services.project import get_project_service
from roboco.services.task import PERISCOPE_SOURCE, TaskCreateRequest, get_task_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import ProjectTable, TaskTable
    from roboco.services.task import TaskService

_EXPLORATION_TITLE = "Periscope market-research cycle"
_EXPLORATION_DESCRIPTION = (
    "Research the market — competitors, adjacent-tool releases, positioning "
    "shifts — and file ONE weekly brief for the CEO via propose_market_brief(). "
    "This is a report, not a task queue: nothing here materializes work, and "
    "there is no per-item approve/reject. Cite a source URL for every claim — "
    "the verb rejects an uncited finding."
)

# Render cap for the "latest brief" injected into the roadmap exploration
# prompt (spec §4: Periscope's brief is Printer's cross-role input) — bounds
# the section regardless of how many findings a brief carries.
_PROMPT_RENDER_CHAR_CAP = 2000


class PeriscopeEngine(BaseService):
    """Originate ONE held Periscope-exploration cycle for the Head of Marketing."""

    service_name = "periscope_engine"

    async def run_cycle(self) -> TaskTable | None:
        """Originate one held exploration task, or None (no-op).

        No-ops when the program isn't armed, a cycle is already open, or the
        RoboCo project (the task's required FK anchor) isn't resolvable.
        Never authors content itself — the Head of Marketing does, via
        ``propose_market_brief`` once spawned by the board dispatcher.
        """
        if not await program_armed(self.session, "periscope"):
            return None
        task_svc = get_task_service(self.session)
        if await task_svc.list_open_periscope_cycles():
            return None  # one open cycle at a time
        project = await self._roboco_project()
        if project is None or project.id is None:
            self.log.warning(
                "periscope-engine: RoboCo project not resolvable; skipping"
            )
            return None
        return await self._originate(task_svc, cast("UUID", project.id))

    async def _roboco_project(self) -> ProjectTable | None:
        slug = (settings.self_heal_project_slug or "roboco-api").strip()
        return await get_project_service(self.session).get_by_slug(slug)

    async def _originate(self, task_svc: TaskService, project_id: UUID) -> TaskTable:
        """Open ONE PENDING, HELD exploration task assigned to the Head of
        Marketing."""
        task = await task_svc.create(
            TaskCreateRequest(
                title=_EXPLORATION_TITLE,
                description=_EXPLORATION_DESCRIPTION,
                acceptance_criteria=[
                    "propose_market_brief() is called once with a headline "
                    "and 1-7 cited findings"
                ],
                team=Team.BOARD,
                assigned_to=_foundation.AGENTS["head-marketing"].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.ADMINISTRATIVE,
                nature=TaskNature.NON_TECHNICAL,
                estimated_complexity=Complexity.LOW,
                project_id=project_id,
                status=TaskStatus.PENDING,
                source=PERISCOPE_SOURCE,
                confirmed_by_human=False,  # HELD; board-dispatched, not delivery
            )
        )
        await self.session.flush()
        self.log.info(
            "periscope exploration cycle opened (Head of Marketing)",
            task_id=str(task.id),
        )
        return task

    async def latest_brief_context(self) -> str:
        """Compact rendering of the most recent completed brief, for the
        roadmap exploration prompt's cross-role injection (spec §4).
        Best-effort shape: empty string when no brief exists yet or the
        latest cycle carries no marker (never blocks the caller)."""
        briefs = await get_task_service(self.session).list_periscope_briefs(limit=1)
        if not briefs:
            return ""
        payload = markers.get_market_brief(briefs[0])
        if not payload:
            return ""
        return _render_brief_compact(payload)


def _render_brief_compact(payload: dict[str, Any]) -> str:
    """Headline + each finding's claim (with its source) — capped so the
    roadmap prompt's injected section stays bounded regardless of how large
    a brief's finding list is."""
    lines = [f"Headline: {payload.get('headline', '')}"]
    for finding in payload.get("findings") or []:
        claim = finding.get("claim", "")
        source = finding.get("source_url", "")
        lines.append(f"- {claim} (source: {source})")
    rendered = "\n".join(lines)
    if len(rendered) > _PROMPT_RENDER_CHAR_CAP:
        rendered = rendered[:_PROMPT_RENDER_CHAR_CAP].rstrip() + "…"
    return rendered


def get_periscope_engine(session: AsyncSession) -> PeriscopeEngine:
    """Build a PeriscopeEngine for ``session``."""
    return PeriscopeEngine(session)
