"""WarRoomEngine — War Room (Board Program), the Head of Marketing's campaign
planner (docs/internal/specs/2026-07-24-board-programs-design.md §4).

Like Coroner, War Room's trigger is ``TriggerKind.EVENT`` — never opened by
``BoardProgramEngine.run_due_programs`` (which skips every non-CRON program
before ever consulting an originator). Unlike Coroner it is NOT a
never-originating ``_ORIGINATORS`` stub: ``run_cycle`` below is a REAL
originator (registered as war_room's ``_ORIGINATORS`` entry in
``roboco.services.board_programs``) that the CEO's "run now" route
(``BoardProgramEngine.open_program_cycle``) genuinely drives — the loop still
never calls it (guarded by the trigger-kind check inside ``run_due_programs``
itself), so the EVENT contract holds without needing a stub. The
release-publish hook (``ReleaseProposalService._draft_war_room``) bypasses
``_ORIGINATORS`` entirely and calls ``open_for_release`` directly, mirroring
``CoronerEngine.open_for_incident`` — building its own LEARN ledger row since
there is no loop tick to route it through.

V1 is manual-cadence (spec, 2026-07-24, pinned by the orchestrating session):
a campaign is an ordered set of held X drafts, each carrying a recommended
``publish_after`` timestamp rendered as GUIDANCE in the panel queue — the CEO
approves each draft at its own moment, exactly like every other X-queue
draft. Nothing here schedules or auto-posts ("nothing auto-posts" stays
absolute). The documented ceiling is an auto-schedule upgrade (a cron sweep
that posts a draft once its ``publish_after`` has passed AND the CEO already
approved it) — NOT built in this release.

* **No master enable flag.** Armed via ``roboco.services.board_programs.
  program_armed`` — the settings-store ``board_program.war_room.enabled`` key
  is the ONLY arming path; off by default like every other program.
* **X-credentials gate.** Mirrors XEngine's release/spotlight guard: drafting
  a campaign nobody can ever post is pointless, so both entry points no-op
  without a configured X client.
* **One open campaign at a time.** Dedup by ``source=board_war_room``
  non-terminal tasks (``TaskService.list_open_war_room_cycles``) — consulted
  by BOTH entry points directly (not only via ``BoardProgramEngine``'s ledger
  dedup, which the release-hook path bypasses entirely).
* **The engine never authors content.** It opens ONE held, PENDING
  exploration task assigned to the Head of Marketing (``Team.BOARD``,
  ``confirmed_by_human=False``); the board dispatcher spawns HoM, who designs
  the campaign arc and calls ``propose_campaign`` exactly once, which
  materializes every post (via ``XEngine.materialize_campaign_post``) and
  completes the exploration task in the same call (mirrors
  ``XEngine.materialize_feature_spotlight``'s complete-at-propose asymmetry).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from roboco.config import settings
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
from roboco.services.base import BaseService
from roboco.services.board_programs import program_armed
from roboco.services.project import get_project_service
from roboco.services.task import WAR_ROOM_SOURCE, TaskCreateRequest, get_task_service
from roboco.services.x_client import XClient, build_x_client
from roboco.services.x_credentials import get_x_credentials_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import ProjectTable, TaskTable
    from roboco.services.task import TaskService

_EXPLORATION_TITLE = "War Room campaign planning"


def _description_for(release_ref: dict[str, Any]) -> str:
    if release_ref:
        version = release_ref.get("version", "")
        return (
            f"RoboCo v{version} just shipped. Design ONE campaign for it — an "
            "ordered set of 2-6 posts (teaser, launch, follow-up, spotlight) "
            "with recommended publish_after timestamps — then author it via "
            "propose_campaign(). The release highlights are in your briefing."
        )
    return (
        "Design ONE marketing campaign for the CEO to review — an ordered "
        "set of 2-6 posts (teaser, launch, follow-up, spotlight) with "
        "recommended publish_after timestamps — then author it via "
        "propose_campaign(). No release triggered this cycle; ground the "
        "campaign in what's actually shipped and worth talking about."
    )


class WarRoomEngine(BaseService):
    """Originate ONE held War Room campaign-planning cycle for the Head of
    Marketing."""

    service_name = "war_room_engine"

    def __init__(self, session: AsyncSession, client: XClient | None = None) -> None:
        super().__init__(session)
        self._injected_client = client

    async def _client(self) -> XClient:
        if self._injected_client is not None:
            return self._injected_client
        creds = await get_x_credentials_service(self.session).get_decrypted()
        return build_x_client(
            creds,
            account_user_id=settings.x_account_user_id,
            timeout=settings.x_request_timeout_seconds,
        )

    async def run_cycle(self) -> TaskTable | None:
        """On-demand origination (the "run now" seam): a blank brief — no
        release to anchor to. Registered as ``_ORIGINATORS["war_room"]``, so
        ``BoardProgramEngine._originate_and_record`` records the LEARN ledger
        row for this path; never call this directly from an event hook (see
        ``open_for_release`` below).
        """
        return await self._open(release_ref=None)

    async def open_for_release(
        self,
        *,
        version: str,
        highlights: list[str],
        project_id: UUID | None = None,
    ) -> TaskTable | None:
        """Release-publish hook: a campaign anchored to the just-shipped
        release. Bypasses ``_ORIGINATORS`` (called directly from
        ``ReleaseProposalService._draft_war_room``, mirroring
        ``CoronerEngine.open_for_incident``) so it records its own LEARN
        ledger row here — there is no loop tick to route it through.
        """
        task = await self._open(
            release_ref={"version": version, "highlights": highlights[:5]},
            project_id=project_id,
        )
        if task is not None:
            await self._record_cycle(task)
        return task

    async def _open(
        self,
        *,
        release_ref: dict[str, Any] | None,
        project_id: UUID | None = None,
    ) -> TaskTable | None:
        """Shared arm/creds/dedup/project gate for both entry points.

        Checked here (not only at ``_originate_and_record``, the shared
        chokepoint ``run_cycle`` rides) because ``open_for_release`` bypasses
        that chokepoint entirely: it records its own LEARN ledger row.
        """
        from roboco.services.maintenance_pause import PauseScope, is_paused

        if not await program_armed(self.session, "war_room") or await is_paused(
            self.session, PauseScope.BOARD_PROGRAMS
        ):
            return None
        client = await self._client()
        if not client.configured:
            return None
        task_svc = get_task_service(self.session)
        if await task_svc.list_open_war_room_cycles():
            return None  # one open campaign at a time
        project = await self._project_or_default(project_id)
        if project is None or project.id is None:
            self.log.warning("war-room: target project not resolvable; skipping cycle")
            return None
        return await self._originate(
            task_svc, cast("UUID", project.id), release_ref or {}
        )

    async def _roboco_project(self) -> ProjectTable | None:
        slug = (settings.self_heal_project_slug or "roboco-api").strip()
        return await get_project_service(self.session).get_by_slug(slug)

    async def _project_or_default(self, project_id: UUID | None) -> ProjectTable | None:
        """The explicitly-targeted (released) project, or the deployment-anchor
        fallback — mirrors ``XEngine._project_or_default``."""
        if project_id is not None:
            return await get_project_service(self.session).get(project_id)
        return await self._roboco_project()

    async def _originate(
        self, task_svc: TaskService, project_id: UUID, release_ref: dict[str, Any]
    ) -> TaskTable:
        """Open ONE PENDING, HELD exploration task assigned to the Head of
        Marketing, carrying the release ref (or {} when opened on-demand)."""
        task = await task_svc.create(
            TaskCreateRequest(
                title=_EXPLORATION_TITLE,
                description=_description_for(release_ref),
                acceptance_criteria=[
                    "propose_campaign() is called exactly once with a "
                    "campaign_name and 2-6 ordered posts, ascending "
                    "publish_after timestamps"
                ],
                team=Team.BOARD,
                assigned_to=_foundation.AGENTS["head-marketing"].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.ADMINISTRATIVE,
                nature=TaskNature.NON_TECHNICAL,
                estimated_complexity=Complexity.LOW,
                project_id=project_id,
                status=TaskStatus.PENDING,
                source=WAR_ROOM_SOURCE,
                confirmed_by_human=False,  # HELD; board-dispatched, not delivery
            )
        )
        markers.set_war_room_brief(task, release_ref)
        await self.session.flush()
        self.log.info(
            "war-room campaign-planning cycle opened (Head of Marketing)",
            task_id=str(task.id),
            release=bool(release_ref),
        )
        return task

    async def _record_cycle(self, task: TaskTable) -> None:
        """The LEARN ledger row for the release-hook path — inline, since it
        bypasses ``BoardProgramEngine._originate_and_record`` entirely
        (mirrors ``CoronerEngine._record_cycle``)."""
        from roboco.db.tables import BoardProgramCycleTable

        self.session.add(
            BoardProgramCycleTable(
                program_key="war_room",
                exploration_task_id=task.id,
                opened_at=datetime.now(UTC),
            )
        )
        await self.session.flush()


def get_war_room_engine(
    session: AsyncSession, client: XClient | None = None
) -> WarRoomEngine:
    """Build a WarRoomEngine for ``session`` (optional injected client for
    tests, mirrors ``get_x_engine``)."""
    return WarRoomEngine(session, client=client)
