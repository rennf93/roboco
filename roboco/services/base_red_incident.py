"""Base-red incident service — one owned P0 incident per red integration base.

Fed by the CI readings ``CiWatchEngine`` already fetches through the existing
``GitService`` CI plumbing (no new client). A project+base with no reading this
cycle (pending, no signal, lookup error — the telemetry source drops all of
those) is *unknown*: it neither counts as red nor closes an incident, so a CI
lookup failure fails open. Reds accumulate on a ``watching`` row; the Nth
consecutive red promotes it to ``open`` and records one owner (the fix task).
The first green reading closes a live incident.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select

from roboco.config import settings
from roboco.db.tables import BaseRedIncidentTable
from roboco.foundation import identity as _foundation
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
from roboco.models.base_red_incident import BaseRedIncident
from roboco.models.env_branches import head_branch
from roboco.services.audit import get_audit_service
from roboco.services.base import BaseService
from roboco.services.notification import NotificationService
from roboco.services.task import CI_WATCH_SOURCE, TaskCreateRequest, get_task_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable

_LIVE = ("watching", "open")
_OWNER_AGENT = "main-pm"


def _slug_for(agent_uuid: Any) -> str:
    """Slug of the registry agent with ``agent_uuid`` (the owner), else Main PM."""
    for slug, agent in _foundation.AGENTS.items():
        if agent.uuid == agent_uuid:
            return slug
    return _OWNER_AGENT


class BaseRedIncidentService(BaseService):
    """Detect, own, close and clear base-red incidents. Flushes; caller commits."""

    service_name = "base_red_incident"

    # ------------------------------------------------------------------ reads

    async def get_open_incident(self, project_id: UUID) -> BaseRedIncident | None:
        row = await self._one(project_id, statuses=("open",))
        return BaseRedIncident.model_validate(row) if row else None

    async def is_owner_task(self, task_id: UUID) -> bool:
        stmt = select(BaseRedIncidentTable.id).where(
            BaseRedIncidentTable.owner_task_id == task_id,
            BaseRedIncidentTable.status == "open",
        )
        return (await self.session.execute(stmt)).first() is not None

    # ----------------------------------------------------------------- clear

    async def clear(
        self, project_id: UUID, cleared_by: str, reason: str
    ) -> BaseRedIncident | None:
        """Manually close the project's open incident (audited). None if none."""
        row = await self._one(project_id, statuses=("open",))
        if row is None:
            return None
        self._close(row, reason=reason, cleared_by=cleared_by)
        await self.session.flush()
        await self._audit("base_red.cleared", row, cleared_by=cleared_by)
        return BaseRedIncident.model_validate(row)

    # --------------------------------------------------------------- observe

    async def observe(self, samples: list[Any], projects: list[Any]) -> None:
        """Apply one cycle of CI readings (``TelemetrySample``s) to incidents.

        Never raises: one project's failure is logged and skipped so the CI-watch
        cycle (and any gate) fails open.
        """
        by_slug = {str(getattr(p, "slug", "")): p for p in projects}
        for sample in samples:
            project = by_slug.get(sample.repo_hint)
            if project is None or getattr(project, "id", None) is None:
                continue
            try:
                async with self.session.begin_nested():
                    await self._apply(project, sample)
            except Exception as exc:
                self.log.warning(
                    "base-red observe failed (fail-open)",
                    project=sample.repo_hint,
                    error=str(exc),
                )

    async def _apply(self, project: Any, sample: Any) -> None:
        project_id = cast("UUID", project.id)
        base = head_branch(project)
        row = await self._one(project_id, base=base, statuses=_LIVE)
        if not sample.is_breach:
            if row is not None:
                await self._on_green(row)
            return
        if row is None:
            row = BaseRedIncidentTable(
                project_id=project_id,
                base_branch=base,
                status="watching",
                consecutive_red=0,
            )
            self.session.add(row)
        row.consecutive_red += 1
        if (
            row.status == "watching"
            and row.consecutive_red >= settings.base_red_consecutive_threshold
        ):
            await self._open(row, project, sample)
        await self.session.flush()

    async def _on_green(self, row: BaseRedIncidentTable) -> None:
        if row.status == "open":
            self._close(row, reason="base CI green", cleared_by="system")
            await self.session.flush()
            await self._audit("base_red.closed", row, cleared_by="system")
        else:
            # Never reached the threshold: a transient red, not an incident.
            await self.session.delete(row)
            await self.session.flush()

    async def _open(self, row: BaseRedIncidentTable, project: Any, sample: Any) -> None:
        task = await self._owner_task(project, sample)
        row.status = "open"
        row.opened_at = datetime.now(UTC)
        row.owner_task_id = task.id
        row.owner_agent = _slug_for(task.assigned_to)
        await self.session.flush()
        await self._audit("base_red.opened", row)
        await self._notify_owner(row, sample)

    async def _owner_task(self, project: Any, sample: Any) -> TaskTable:
        """Adopt the repo's open ci_watch fix task, else open one."""
        task_svc = get_task_service(self.session)
        existing = await task_svc.list_open_ci_watch_tasks(git_url=project.git_url)
        if existing:
            return existing[0]
        slug = str(getattr(project, "slug", "") or sample.repo_hint)
        return await task_svc.create(
            TaskCreateRequest(
                title=f"P0 base-red: fix the red base branch on {slug}",
                description=(
                    f"The integration base of {slug} is red.\n\n{sample.detail}\n\n"
                    f"Evidence: {sample.raw_ref}\n\n"
                    "You own this base-red incident. Delegate the fix to a cell "
                    "dev; other cells' submissions are gated until base CI is "
                    "green."
                ),
                acceptance_criteria=[
                    "The base-branch CI failure is decomposed into a code-fix "
                    "subtask delegated to a cell developer",
                    f"CI on {slug}'s base branch is green again",
                ],
                team=_foundation.AGENTS[_OWNER_AGENT].team,
                assigned_to=_foundation.AGENTS[_OWNER_AGENT].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.PLANNING,
                nature=TaskNature.TECHNICAL,
                estimated_complexity=Complexity.MEDIUM,
                project_id=cast("UUID", project.id),
                status=TaskStatus.PENDING,
                source=CI_WATCH_SOURCE,
                confirmed_by_human=True,
            )
        )

    async def _notify_owner(self, row: BaseRedIncidentTable, sample: Any) -> None:
        try:
            async with self.session.begin_nested():
                await NotificationService().send_ack_notification(
                    from_agent="system",
                    to_agent=row.owner_agent or _OWNER_AGENT,
                    task_id=cast("UUID | None", row.owner_task_id),
                    body=(
                        f"[base-red P0] {row.base_branch} CI is red "
                        f"({row.consecutive_red} consecutive readings). You own "
                        f"this incident via task {row.owner_task_id}; other "
                        f"cells should not re-investigate.\n\n{sample.detail}"
                    ),
                )
        except Exception as exc:
            self.log.warning("base-red owner notify failed", error=str(exc))

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _close(row: BaseRedIncidentTable, *, reason: str, cleared_by: str) -> None:
        row.status = "closed"
        row.closed_at = datetime.now(UTC)
        row.closed_reason = reason
        row.cleared_by = cleared_by

    async def _one(
        self,
        project_id: UUID,
        *,
        base: str | None = None,
        statuses: tuple[str, ...] = _LIVE,
    ) -> BaseRedIncidentTable | None:
        stmt = select(BaseRedIncidentTable).where(
            BaseRedIncidentTable.project_id == project_id,
            BaseRedIncidentTable.status.in_(statuses),
        )
        if base is not None:
            stmt = stmt.where(BaseRedIncidentTable.base_branch == base)
        return (await self.session.execute(stmt.limit(1))).scalar_one_or_none()

    async def _audit(
        self, event: str, row: BaseRedIncidentTable, cleared_by: str | None = None
    ) -> None:
        try:
            await get_audit_service().log_event(
                event_type=event,
                task_id=cast("UUID | None", row.owner_task_id),
                details={
                    "project_id": str(row.project_id),
                    "base_branch": row.base_branch,
                    "reason": row.closed_reason,
                    "cleared_by": cleared_by,
                    "owner_agent": row.owner_agent,
                },
            )
        except Exception as exc:
            self.log.warning("base-red audit failed", event=event, error=str(exc))


def get_base_red_incident_service(session: AsyncSession) -> BaseRedIncidentService:
    """Construct a BaseRedIncidentService bound to ``session``."""
    return BaseRedIncidentService(session)
