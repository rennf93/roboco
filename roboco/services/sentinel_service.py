"""SentinelService — the CEO's per-item approve/dismiss glue over a
completed Sentinel quality report.

The Sentinel engine opens a HELD exploration task (``board_sentinel``
source); the Auditor files ONE "state of quality" report onto it via
``propose_quality_report`` (a headline + 1-7 drift items, persisted as a
marker payload — see
``roboco.foundation.policy.content.markers.get_quality_report``) and the
exploration task completes in that same call — a report, not a per-item
queue.

Unlike the exploration task, each ITEM still carries its own
proposed/approved/rejected status the CEO decides on afterward — that is
what this service is for. ``approve_item`` materializes one item as a
PENDING, Main-PM-owned root task (``source=sentinel``, ``assigned_to=
main-pm`` — see ``RoadmapService._materialize``'s docstring for why never a
parentless BACKLOG task); ``reject_item`` records the reason ("dismiss" —
no task). Both are idempotent per item. Mirrors ``PeriscopeService`` closely
— a Sentinel drift item already carries a machine-readable
``suggested_action``, used directly as the materialized task's acceptance
criterion.

A drift item carries no ``project_slug`` the way a roadmap item does
(Sentinel watches the org's own process — waivers, findings, conventions,
budget, docs drift — not one repo). The target project resolves to RoboCo's
own project (``settings.self_heal_project_slug``, the same fallback
``SentinelEngine._roboco_project`` already uses to anchor the exploration
task itself) — drift in RoboCo's own delivery process is, definitionally,
about RoboCo's own project.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from roboco.config import settings
from roboco.foundation.policy.content import markers
from roboco.models.base import TaskStatus, TaskType, Team
from roboco.services.base import BaseService
from roboco.services.board_programs import learn_ref
from roboco.services.task import SENTINEL_ITEM_SOURCE, SENTINEL_SOURCE

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable

# Sentinel items tagged "docs" materialize as documentation tasks — mirrors
# MirrorService's identical area->task_type override.
_DOCS_AREA = "docs"


@dataclass(frozen=True)
class QualityReportItemResult:
    """Outcome of an approve/reject call on one quality-report item.

    `status` is one of: approved, already_approved, rejected,
    already_rejected, invalid_state.
    """

    status: str
    item_id: str
    materialized_task_id: str | None
    detail: str


class SentinelService(BaseService):
    """Approve / reject items within a completed Sentinel quality report."""

    service_name = "sentinel_service"

    async def approve_item(
        self, task_id: UUID, item_id: str, *, created_by: UUID
    ) -> QualityReportItemResult | None:
        """Materialize one proposed drift item as a Main-PM-owned root task.

        Returns None when ``task_id`` carries no Sentinel report or
        ``item_id`` does not exist on it. Idempotent: an already-approved
        item returns its stored materialized task id without creating a
        duplicate. An already-rejected item cannot be approved.
        """
        task, payload, item = await self._find_item(task_id, item_id)
        if task is None or payload is None or item is None:
            return None
        if item["status"] == "approved":
            return QualityReportItemResult(
                status="already_approved",
                item_id=item_id,
                materialized_task_id=item.get("materialized_task_id"),
                detail="this item was already approved",
            )
        if item["status"] != "proposed":
            return QualityReportItemResult(
                status="invalid_state",
                item_id=item_id,
                materialized_task_id=None,
                detail=f"item is {item['status']!r}, not proposed — cannot approve",
            )
        try:
            new_task = await self._materialize(item, created_by=created_by)
        except ValueError as exc:
            return QualityReportItemResult(
                status="invalid_state",
                item_id=item_id,
                materialized_task_id=None,
                detail=str(exc),
            )
        item["status"] = "approved"
        item["materialized_task_id"] = str(new_task.id)
        markers.set_quality_report(task, payload)
        await self._record_learn(task, item, "approved")
        await self.session.flush()
        return QualityReportItemResult(
            status="approved",
            item_id=item_id,
            materialized_task_id=str(new_task.id),
            detail="materialized as a Main-PM-owned task",
        )

    async def reject_item(
        self, task_id: UUID, item_id: str, reason: str
    ) -> QualityReportItemResult | None:
        """Dismiss one proposed drift item, recording the CEO's reason.

        Idempotent: an already-rejected item returns its stored reason
        without re-recording. An already-approved item cannot be rejected
        (irreversible — a task already exists for it).
        """
        task, payload, item = await self._find_item(task_id, item_id)
        if task is None or payload is None or item is None:
            return None
        if item["status"] == "rejected":
            return QualityReportItemResult(
                status="already_rejected",
                item_id=item_id,
                materialized_task_id=None,
                detail="this item was already dismissed",
            )
        if item["status"] != "proposed":
            return QualityReportItemResult(
                status="invalid_state",
                item_id=item_id,
                materialized_task_id=item.get("materialized_task_id"),
                detail=f"item is {item['status']!r}, not proposed — cannot dismiss",
            )
        item["status"] = "rejected"
        item["reject_reason"] = reason
        markers.set_quality_report(task, payload)
        await self._record_learn(task, item, "rejected", reason)
        await self.session.flush()
        return QualityReportItemResult(
            status="rejected",
            item_id=item_id,
            materialized_task_id=None,
            detail="dismissed; feeds the next cycle's prompt",
        )

    async def _find_item(
        self, task_id: UUID, item_id: str
    ) -> tuple[TaskTable | None, dict[str, Any] | None, dict[str, Any] | None]:
        """Resolve (exploration task, report payload, one item) or (None,
        None, None). Deep-copies the stored marker before mutating it — see
        ``RoadmapService._find_item``'s identical dirty-check rationale.

        An item authored before this feature shipped carries no ``status``
        key at all — ``setdefault`` treats it as ``proposed`` rather than
        crashing on a missing key.
        """
        from roboco.services.task import get_task_service

        task = await get_task_service(self.session).get(task_id)
        if task is None or task.source != SENTINEL_SOURCE:
            return None, None, None
        stored = markers.get_quality_report(task)
        if stored is None:
            return None, None, None
        payload = copy.deepcopy(stored)
        item = next(
            (it for it in payload.get("items", []) if it.get("id") == item_id), None
        )
        if item is None:
            return None, None, None
        item.setdefault("status", "proposed")
        return task, payload, item

    async def _materialize(
        self, item: dict[str, Any], *, created_by: UUID
    ) -> TaskTable:
        """Turn one approved drift item into a real Main-PM-owned root task,
        anchored on the RoboCo project (see module docstring for why).
        ``team=Team.MAIN_PM`` (via ``BatchPlacement.team_override``), matching
        ``TaskService.approve_and_start`` — a process/quality drift item has no
        natural owning cell, so unlike ``RoadmapService._materialize`` there is
        no per-item cell to preserve as a delegation hint."""
        from roboco.seeds.initial_data import AGENT_UUIDS
        from roboco.services.prompter import BatchPlacement, get_prompter_service

        project = await self._roboco_project()
        if project is None or project.id is None:
            raise ValueError(
                "the RoboCo project (settings.self_heal_project_slug) is not "
                "resolvable — cannot anchor a materialized task"
            )
        draft = {
            "title": f"Sentinel [{item['area']}]: {item['suggested_action']}"[:200],
            "objective": item["suggested_action"],
            "notes": [
                f"Observation: {item['observation']}",
                f"Evidence: {item['evidence']}",
            ],
            "acceptance_criteria": [
                item["suggested_action"],
                f"Addresses the drift observed: {item['observation']}",
            ],
            "project_id": str(project.id),
            "team": Team.BACKEND.value,
            "priority": 2,
            "source": SENTINEL_ITEM_SOURCE,
        }
        if item.get("area") == _DOCS_AREA:
            draft["task_type"] = TaskType.DOCUMENTATION.value
        return await get_prompter_service(self.session).create_task_from_draft(
            draft,
            created_by,
            status=TaskStatus.PENDING,
            assigned_to=UUID(AGENT_UUIDS["main-pm"]),
            placement=BatchPlacement(team_override=Team.MAIN_PM),
        )

    async def _roboco_project(self) -> Any:
        """Mirrors ``SentinelEngine._roboco_project`` exactly — the same
        fallback anchor a Sentinel exploration task itself resolves against."""
        from roboco.services.project import get_project_service

        slug = (settings.self_heal_project_slug or "roboco-api").strip()
        return await get_project_service(self.session).get_by_slug(slug)

    async def _record_learn(
        self,
        task: TaskTable,
        item: dict[str, Any],
        verdict: str,
        reason: str | None = None,
    ) -> None:
        """Best-effort LEARN: a record_decision failure must never break the
        CEO's approve/reject — mirrors ``RoadmapService._record_learn``.

        ``learn_ref`` expects a ``title``/``target_task_title`` field; a
        drift item carries neither, so it's wrapped with its
        ``suggested_action`` under ``title`` rather than reinventing the
        truncation/fallback logic.
        """
        try:
            from roboco.services.board_programs import get_board_program_engine

            await get_board_program_engine(self.session).record_decision(
                "sentinel",
                learn_ref({"title": item.get("suggested_action")}),
                verdict,
                reason,
                exploration_task_id=cast("UUID", task.id),
            )
        except Exception:
            self.log.warning("sentinel: LEARN record_decision failed (best-effort)")


def get_sentinel_service(session: AsyncSession) -> SentinelService:
    """Construct a SentinelService bound to ``session``."""
    return SentinelService(session)
