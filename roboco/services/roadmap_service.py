"""RoadmapService — the CEO's per-item approve/reject glue over a held
roadmap cycle.

The roadmap engine opens a HELD exploration task (``board_roadmap`` source);
the Product Owner authors the cycle onto it via ``propose_roadmap`` (a goal +
3-7 item drafts, persisted as a marker payload — see
``roboco.foundation.policy.content.markers.get_roadmap_cycle``). This service
is what the CEO-gated routes call: ``approve_item`` materializes one item as a
BACKLOG task (``source=roadmap``, via ``PrompterService.create_task_from_draft``
— CEO approval IS the confirmation); ``reject_item`` records the reason. Once
every item on the cycle is terminal (approved/rejected) the exploration task
itself completes. Both actions are idempotent per item.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from roboco.foundation.policy.content import markers
from roboco.models.base import TaskStatus
from roboco.services.base import BaseService
from roboco.services.task import ROADMAP_ITEM_SOURCE, ROADMAP_SOURCE, get_task_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable

_TERMINAL_ITEM_STATUSES = ("approved", "rejected")


@dataclass(frozen=True)
class RoadmapItemResult:
    """Outcome of an approve/reject call on one roadmap item.

    `status` is one of: approved, already_approved, rejected,
    already_rejected, invalid_state.
    """

    status: str
    item_id: str
    materialized_task_id: str | None
    detail: str


class RoadmapService(BaseService):
    """List / approve / reject items within the open roadmap cycle(s)."""

    service_name = "roadmap_service"

    async def list_open_cycles(self) -> list[TaskTable]:
        """Every open (non-terminal) roadmap exploration task, authored or not."""
        return await get_task_service(self.session).list_open_roadmap_cycles()

    async def approve_item(
        self, task_id: UUID, item_id: str, *, created_by: UUID
    ) -> RoadmapItemResult | None:
        """Materialize one proposed item as a BACKLOG task.

        Returns None when ``task_id`` is not an open roadmap cycle or
        ``item_id`` does not exist on it. Idempotent: an already-approved item
        returns its stored materialized task id without creating a duplicate.
        An already-rejected item cannot be approved (surfaced as
        ``invalid_state`` — the CEO's reject already recorded a decision).
        """
        task, payload, item = await self._find_item(task_id, item_id)
        if task is None or payload is None or item is None:
            return None
        if item["status"] == "approved":
            return RoadmapItemResult(
                status="already_approved",
                item_id=item_id,
                materialized_task_id=item.get("materialized_task_id"),
                detail="this item was already approved",
            )
        if item["status"] != "proposed":
            return RoadmapItemResult(
                status="invalid_state",
                item_id=item_id,
                materialized_task_id=None,
                detail=f"item is {item['status']!r}, not proposed — cannot approve",
            )
        try:
            new_task = await self._materialize(item, created_by=created_by)
        except ValueError as exc:
            return RoadmapItemResult(
                status="invalid_state",
                item_id=item_id,
                materialized_task_id=None,
                detail=str(exc),
            )
        item["status"] = "approved"
        item["materialized_task_id"] = str(new_task.id)
        markers.set_roadmap_cycle(task, payload)
        self._maybe_complete_cycle(task, payload)
        await self.session.flush()
        return RoadmapItemResult(
            status="approved",
            item_id=item_id,
            materialized_task_id=str(new_task.id),
            detail="materialized into the backlog",
        )

    async def reject_item(
        self, task_id: UUID, item_id: str, reason: str
    ) -> RoadmapItemResult | None:
        """Record the CEO's reason for declining one item.

        Idempotent: an already-rejected item returns its stored reason
        without re-recording. An already-approved item cannot be rejected
        (irreversible — a BACKLOG task already exists for it).
        """
        task, payload, item = await self._find_item(task_id, item_id)
        if task is None or payload is None or item is None:
            return None
        if item["status"] == "rejected":
            return RoadmapItemResult(
                status="already_rejected",
                item_id=item_id,
                materialized_task_id=None,
                detail="this item was already rejected",
            )
        if item["status"] != "proposed":
            return RoadmapItemResult(
                status="invalid_state",
                item_id=item_id,
                materialized_task_id=item.get("materialized_task_id"),
                detail=f"item is {item['status']!r}, not proposed — cannot reject",
            )
        item["status"] = "rejected"
        item["reject_reason"] = reason
        markers.set_roadmap_cycle(task, payload)
        self._maybe_complete_cycle(task, payload)
        await self.session.flush()
        return RoadmapItemResult(
            status="rejected",
            item_id=item_id,
            materialized_task_id=None,
            detail="recorded; feeds the next cycle's prompt",
        )

    async def _find_item(
        self, task_id: UUID, item_id: str
    ) -> tuple[TaskTable | None, dict[str, Any] | None, dict[str, Any] | None]:
        """Resolve (exploration task, cycle payload, one item) or (None, None, None).

        The payload is a DEEP COPY of the stored marker — mutating it (and the
        returned ``item``) never touches ``task.orchestration_markers`` until
        ``markers.set_roadmap_cycle`` reassigns it. Mutating the live nested
        dict in place first would poison SQLAlchemy's dirty-check: the
        "unchanged" baseline it compares against is a reference to that same
        mutable structure, not a snapshot, so an in-place edit followed by
        reassignment can compare equal to itself and the UPDATE gets skipped.
        """
        task = await get_task_service(self.session).get(task_id)
        if task is None or task.source != ROADMAP_SOURCE:
            return None, None, None
        stored = markers.get_roadmap_cycle(task)
        if stored is None:
            return None, None, None
        payload = copy.deepcopy(stored)
        item = next(
            (it for it in payload.get("items", []) if it.get("id") == item_id), None
        )
        if item is None:
            return None, None, None
        return task, payload, item

    async def _materialize(
        self, item: dict[str, Any], *, created_by: UUID
    ) -> TaskTable:
        """Turn one approved item draft into a real BACKLOG task."""
        from roboco.services.project import get_project_service
        from roboco.services.prompter import get_prompter_service

        project = await get_project_service(self.session).get_by_slug(
            item["project_slug"]
        )
        if project is None or project.id is None:
            raise ValueError(f"unknown project slug: {item['project_slug']!r}")
        draft = {
            "title": item["title"],
            "objective": item["description"],
            "notes": [f"Rationale: {item['rationale']}"],
            "acceptance_criteria": item["acceptance_criteria"],
            "project_id": str(project.id),
            "team": item["team"],
            "priority": item.get("priority", 2),
            "source": ROADMAP_ITEM_SOURCE,
        }
        return await get_prompter_service(self.session).create_task_from_draft(
            draft,
            created_by,
            status=TaskStatus.BACKLOG,
        )

    def _maybe_complete_cycle(self, task: TaskTable, payload: dict[str, Any]) -> None:
        """Complete the exploration task once every item is terminal."""
        items = payload.get("items") or []
        if items and all(it.get("status") in _TERMINAL_ITEM_STATUSES for it in items):
            from_status = (
                task.status.value
                if isinstance(task.status, TaskStatus)
                else str(task.status)
            )
            task.status = TaskStatus.COMPLETED
            get_task_service(self.session)._emit_status_transition_audit(
                task,
                from_status=from_status,
                to_status=TaskStatus.COMPLETED.value,
                agent_role=None,
                audit_agent_id=None,
            )


def get_roadmap_service(session: AsyncSession) -> RoadmapService:
    """Construct a RoadmapService bound to ``session``."""
    return RoadmapService(session)
