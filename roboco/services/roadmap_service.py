"""RoadmapService — the CEO's per-item approve/reject glue over a held
roadmap cycle.

The roadmap engine opens a HELD exploration task (``board_roadmap`` source);
the Product Owner authors the cycle onto it via ``propose_roadmap`` (a goal +
3-7 item drafts, persisted as a marker payload — see
``roboco.foundation.policy.content.markers.get_roadmap_cycle``). This service
is what the CEO-gated routes call: ``approve_item`` materializes one item as a
PENDING, Main-PM-owned root task (``source=roadmap``, ``assigned_to=main-pm``,
via ``PrompterService.create_task_from_draft`` — CEO approval IS the
confirmation); ``reject_item`` records the reason. Once every item on the
cycle is terminal (approved/rejected) the exploration task itself completes.
Both actions are idempotent per item.

A materialized item is NEVER an unowned BACKLOG task: nothing dispatches
BACKLOG, and a parentless root a cell PM claims directly would resolve its
own merge target straight to the project's head rung on ``complete()``,
bypassing the Main-PM root, the root->master PR, and the CEO's approval gate
— see ``_materialize``'s docstring.
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
from roboco.services.task import ROADMAP_ITEM_SOURCE, ROADMAP_SOURCE, get_task_service

if TYPE_CHECKING:
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
        await self._record_learn(task, learn_ref(item), "approved")
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
        await self._record_learn(task, learn_ref(item), "rejected", reason)
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
        """Turn one approved item draft into a Main-PM-owned root task.

        PENDING + ``assigned_to=main-pm`` — NOT a parentless BACKLOG task.
        Nothing dispatches BACKLOG, and a parentless task a cell PM claims
        and completes resolves its merge target (``resolve_parent_branch``)
        straight to the project's head rung, bypassing the Main-PM root, the
        root->master PR, and the CEO's approval gate (live proof: PRs
        #703/#704 merged feature/{team}/... -> slave directly). Pre-assigning
        the Main PM makes this a real coordination root that dispatches
        immediately — ``team=Team.MAIN_PM`` (via ``BatchPlacement``'s
        ``team_override`` seam, the same knob a MegaTask batch uses), matching
        ``TaskService.approve_and_start`` exactly, since every "is this a
        coordination root" check (``pr_fail``'s next-hint, the PR-gate steer,
        delegate's wave-chain branch, the PR labeler) keys on ``team``, not
        ``assigned_to``. The item's own cell survives as a delegation hint in
        the task's Notes (part of the composed description, which the Main
        PM's spawn briefing renders verbatim) rather than in the ``team``
        column — the same shape intake's "Approve & Start" produces.
        """
        from roboco.seeds.initial_data import AGENT_UUIDS
        from roboco.services.project import get_project_service
        from roboco.services.prompter import BatchPlacement, get_prompter_service

        project = await get_project_service(self.session).get_by_slug(
            item["project_slug"]
        )
        if project is None or project.id is None:
            raise ValueError(f"unknown project slug: {item['project_slug']!r}")
        if not project_participates(PROGRAMS["roadmap"], project.board_programs):
            self.log.warning(
                "roadmap: materialize skipped — project excluded (!roadmap)",
                project_slug=item["project_slug"],
            )
            raise ValueError(
                f"project {item['project_slug']!r} is excluded from the roadmap program"
            )
        draft = {
            "title": item["title"],
            "objective": item["description"],
            "notes": [
                f"Rationale: {item['rationale']}",
                f"Delegation hint: originated as a {item['team']} item — "
                f"delegate into the {item['team']} cell.",
            ],
            "acceptance_criteria": item["acceptance_criteria"],
            "project_id": str(project.id),
            "team": item["team"],
            "priority": item.get("priority", 2),
            "source": ROADMAP_ITEM_SOURCE,
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
        CEO's approve/reject — mirrors the vault-writer best-effort seams.

        Targets ``task`` (this exploration task) by id — exact attribution
        even when a newer cycle for "roadmap" has since opened, unlike
        ``record_decision``'s most-recent fallback.
        """
        try:
            from roboco.services.board_programs import get_board_program_engine

            await get_board_program_engine(self.session).record_decision(
                "roadmap",
                item_ref,
                verdict,
                reason,
                exploration_task_id=cast("UUID", task.id),
            )
        except Exception:
            self.log.warning("roadmap: LEARN record_decision failed (best-effort)")


def get_roadmap_service(session: AsyncSession) -> RoadmapService:
    """Construct a RoadmapService bound to ``session``."""
    return RoadmapService(session)
