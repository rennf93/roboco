"""SpackleService — the CEO's per-item approve/reject glue over a held
spackle (Board Program) cycle.

The spackle engine opens a HELD exploration task (``board_spackle`` source);
the Product Owner authors the gap-fill audit onto it via ``propose_gap_fill``
(1-5 evidence-backed item drafts, persisted as a marker payload — see
``roboco.foundation.policy.content.markers.get_gap_fill``). This service is
what the CEO-gated routes call: ``approve_item`` materializes one item as a
PENDING, Main-PM-owned root task (``source=spackle``, ``assigned_to=main-pm``,
via ``PrompterService.create_task_from_draft`` — CEO approval IS the
confirmation); ``reject_item`` records the reason. Once every item on the
cycle is terminal (approved/rejected) the exploration task itself completes.
Both actions are idempotent per item. Mirrors ``PestControlService`` exactly.

A materialized item is NEVER an unowned BACKLOG task — see
``RoadmapService._materialize``'s docstring for why.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from roboco.foundation.policy.board_programs import PROGRAMS, project_participates
from roboco.foundation.policy.content import markers
from roboco.models.base import TaskStatus, Team
from roboco.services.base import BaseService
from roboco.services.board_programs import learn_ref
from roboco.services.task import SPACKLE_ITEM_SOURCE, SPACKLE_SOURCE

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable

_TERMINAL_ITEM_STATUSES = ("approved", "rejected")


@dataclass(frozen=True)
class GapFillItemResult:
    """Outcome of an approve/reject call on one gap-fill item.

    `status` is one of: approved, already_approved, rejected,
    already_rejected, invalid_state.
    """

    status: str
    item_id: str
    materialized_task_id: str | None
    detail: str


class SpackleService(BaseService):
    """List / approve / reject items within the open spackle cycle(s)."""

    service_name = "spackle_service"

    async def list_open_cycles(self) -> list[TaskTable]:
        """Every open (non-terminal) spackle exploration task, authored or
        not."""
        from roboco.services.task import get_task_service

        return await get_task_service(self.session).list_open_spackle_cycles()

    async def approve_item(
        self, task_id: UUID, item_id: str, *, created_by: UUID
    ) -> GapFillItemResult | None:
        """Materialize one proposed item as a BACKLOG task.

        Returns None when ``task_id`` is not an open spackle cycle or
        ``item_id`` does not exist on it. Idempotent: an already-approved item
        returns its stored materialized task id without creating a duplicate.
        An already-rejected item cannot be approved.
        """
        task, payload, item = await self._find_item(task_id, item_id)
        if task is None or payload is None or item is None:
            return None
        if item["status"] == "approved":
            return GapFillItemResult(
                status="already_approved",
                item_id=item_id,
                materialized_task_id=item.get("materialized_task_id"),
                detail="this item was already approved",
            )
        if item["status"] != "proposed":
            return GapFillItemResult(
                status="invalid_state",
                item_id=item_id,
                materialized_task_id=None,
                detail=f"item is {item['status']!r}, not proposed — cannot approve",
            )
        try:
            new_task = await self._materialize(item, created_by=created_by)
        except ValueError as exc:
            return GapFillItemResult(
                status="invalid_state",
                item_id=item_id,
                materialized_task_id=None,
                detail=str(exc),
            )
        item["status"] = "approved"
        item["materialized_task_id"] = str(new_task.id)
        markers.set_gap_fill(task, payload)
        self._maybe_complete_cycle(task, payload)
        await self._record_learn(task, learn_ref(item), "approved")
        await self.session.flush()
        return GapFillItemResult(
            status="approved",
            item_id=item_id,
            materialized_task_id=str(new_task.id),
            detail="materialized into the backlog",
        )

    async def reject_item(
        self, task_id: UUID, item_id: str, reason: str
    ) -> GapFillItemResult | None:
        """Record the CEO's reason for declining one item.

        Idempotent: an already-rejected item returns its stored reason
        without re-recording. An already-approved item cannot be rejected
        (irreversible — a BACKLOG task already exists for it).
        """
        task, payload, item = await self._find_item(task_id, item_id)
        if task is None or payload is None or item is None:
            return None
        if item["status"] == "rejected":
            return GapFillItemResult(
                status="already_rejected",
                item_id=item_id,
                materialized_task_id=None,
                detail="this item was already rejected",
            )
        if item["status"] != "proposed":
            return GapFillItemResult(
                status="invalid_state",
                item_id=item_id,
                materialized_task_id=item.get("materialized_task_id"),
                detail=f"item is {item['status']!r}, not proposed — cannot reject",
            )
        item["status"] = "rejected"
        item["reject_reason"] = reason
        markers.set_gap_fill(task, payload)
        self._maybe_complete_cycle(task, payload)
        await self._record_learn(task, learn_ref(item), "rejected", reason)
        await self.session.flush()
        return GapFillItemResult(
            status="rejected",
            item_id=item_id,
            materialized_task_id=None,
            detail="recorded; feeds the next cycle's prompt",
        )

    async def _find_item(
        self, task_id: UUID, item_id: str
    ) -> tuple[TaskTable | None, dict[str, Any] | None, dict[str, Any] | None]:
        """Resolve (exploration task, gap-fill payload, one item) or (None,
        None, None). Deep-copies the stored marker before mutating it — see
        ``PestControlService._find_item``'s identical dirty-check rationale."""
        from roboco.services.task import get_task_service

        task = await get_task_service(self.session).get(task_id)
        if task is None or task.source != SPACKLE_SOURCE:
            return None, None, None
        stored = markers.get_gap_fill(task)
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
        """Turn one approved item draft into a Main-PM-owned root task.
        Mirrors ``RoadmapService._materialize`` — PENDING + main-pm and
        ``team=Team.MAIN_PM`` (via ``BatchPlacement.team_override``), not a
        parentless BACKLOG task and not left on the item's own cell team; the
        item's own cell survives as a Notes delegation hint instead."""
        from roboco.seeds.initial_data import AGENT_UUIDS
        from roboco.services.project import get_project_service
        from roboco.services.prompter import BatchPlacement, get_prompter_service

        project = await get_project_service(self.session).get_by_slug(
            item["project_slug"]
        )
        if project is None or project.id is None:
            raise ValueError(f"unknown project slug: {item['project_slug']!r}")
        if not project_participates(PROGRAMS["spackle"], project.board_programs):
            self.log.warning(
                "spackle: materialize skipped — project not opted in",
                project_slug=item["project_slug"],
            )
            raise ValueError(
                f"project {item['project_slug']!r} is not opted into the "
                "spackle program"
            )
        draft = {
            "title": item["title"],
            "objective": item["description"],
            "notes": [
                f"Evidence: {item['evidence']}",
                f"Delegation hint: originated as a {item['team']} item — "
                f"delegate into the {item['team']} cell.",
            ],
            "acceptance_criteria": item["acceptance_criteria"],
            "project_id": str(project.id),
            "team": item["team"],
            "priority": item.get("priority", 2),
            "source": SPACKLE_ITEM_SOURCE,
        }
        return await get_prompter_service(self.session).create_task_from_draft(
            draft,
            created_by,
            status=TaskStatus.PENDING,
            assigned_to=UUID(AGENT_UUIDS["main-pm"]),
            placement=BatchPlacement(team_override=Team.MAIN_PM),
        )

    def _maybe_complete_cycle(self, task: TaskTable, payload: dict[str, Any]) -> None:
        """Complete the exploration task once every item is terminal."""
        from roboco.services.task import get_task_service

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

    async def _record_learn(
        self, task: TaskTable, item_ref: str, verdict: str, reason: str | None = None
    ) -> None:
        """Best-effort LEARN: a record_decision failure must never break the
        CEO's approve/reject — mirrors ``PestControlService._record_learn``."""
        try:
            from roboco.services.board_programs import get_board_program_engine

            await get_board_program_engine(self.session).record_decision(
                "spackle",
                item_ref,
                verdict,
                reason,
                exploration_task_id=cast("UUID", task.id),
            )
        except Exception:
            self.log.warning("spackle: LEARN record_decision failed (best-effort)")


def get_spackle_service(session: AsyncSession) -> SpackleService:
    """Construct a SpackleService bound to ``session``."""
    return SpackleService(session)
