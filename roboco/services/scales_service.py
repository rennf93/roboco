"""ScalesService — the CEO's per-item approve/reject glue over a held Scales
portfolio-rebalance cycle.

The scales engine opens a HELD exploration task (``board_scales`` source);
the Product Owner authors the plan onto it via ``propose_rebalance`` (1-7
re-priority/cancellation item drafts, each referencing a LIVE task, persisted
as a marker payload — see ``roboco.foundation.policy.content.markers.
get_rebalance_plan``). This service is what the CEO-gated routes call:
``approve_item`` EXECUTES the item's action against the live target task —
``reprioritize`` updates its ``priority`` (through ``TaskService.update``,
audited), ``cancel`` routes through the normal PM/CEO cancel path
(``TaskService.cancel``, ``agent_role="ceo"`` — the CEO acting via this
CEO-gated route is the legitimate actor). ``reject_item`` records the reason.
Once every item on the cycle is terminal (approved/rejected) the exploration
task itself completes. Both actions are idempotent per item. Mirrors
``PestControlService`` closely — the one structural difference is that
approval MUTATES an existing task instead of materializing a new one, so
there is no "materialized_task_id"; instead each item carries the target
task's id (resolved at propose time) and an ``executed_detail`` string
recording what actually happened.
"""

from __future__ import annotations

import contextlib
import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from roboco.foundation.policy.content import markers
from roboco.models.base import TaskStatus
from roboco.services.base import BaseService
from roboco.services.board_programs import learn_ref
from roboco.services.task import SCALES_SOURCE

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable

_TERMINAL_ITEM_STATUSES = ("approved", "rejected")


@dataclass(frozen=True)
class RebalanceItemResult:
    """Outcome of an approve/reject call on one rebalance item.

    `status` is one of: approved, already_approved, rejected,
    already_rejected, invalid_state.
    """

    status: str
    item_id: str
    executed_detail: str | None
    detail: str


class ScalesService(BaseService):
    """List / approve / reject items within the open Scales cycle(s)."""

    service_name = "scales_service"

    async def list_open_cycles(self) -> list[TaskTable]:
        """Every open (non-terminal) Scales exploration task, authored or
        not."""
        from roboco.services.task import get_task_service

        return await get_task_service(self.session).list_open_scales_cycles()

    async def approve_item(
        self, task_id: UUID, item_id: str, *, created_by: UUID
    ) -> RebalanceItemResult | None:
        """Execute one proposed item's action against its live target task.

        Returns None when ``task_id`` is not an open Scales cycle or
        ``item_id`` does not exist on it. Idempotent: an already-approved
        item returns its stored executed-detail without re-executing. An
        already-rejected item cannot be approved.
        """
        task, payload, item = await self._find_item(task_id, item_id)
        if task is None or payload is None or item is None:
            return None
        if item["status"] == "approved":
            return RebalanceItemResult(
                status="already_approved",
                item_id=item_id,
                executed_detail=item.get("executed_detail"),
                detail="this item was already approved",
            )
        if item["status"] != "proposed":
            return RebalanceItemResult(
                status="invalid_state",
                item_id=item_id,
                executed_detail=None,
                detail=f"item is {item['status']!r}, not proposed — cannot approve",
            )
        try:
            executed_detail = await self._execute(item, created_by=created_by)
        except ValueError as exc:
            return RebalanceItemResult(
                status="invalid_state",
                item_id=item_id,
                executed_detail=None,
                detail=str(exc),
            )
        item["status"] = "approved"
        item["executed_detail"] = executed_detail
        markers.set_rebalance_plan(task, payload)
        self._maybe_complete_cycle(task, payload)
        await self._record_learn(task, learn_ref(item), "approved")
        await self.session.flush()
        return RebalanceItemResult(
            status="approved",
            item_id=item_id,
            executed_detail=executed_detail,
            detail=executed_detail,
        )

    async def reject_item(
        self, task_id: UUID, item_id: str, reason: str
    ) -> RebalanceItemResult | None:
        """Record the CEO's reason for declining one item.

        Idempotent: an already-rejected item returns its stored reason
        without re-recording. An already-approved item cannot be rejected
        (irreversible — the target task's priority/status already changed).
        """
        task, payload, item = await self._find_item(task_id, item_id)
        if task is None or payload is None or item is None:
            return None
        if item["status"] == "rejected":
            return RebalanceItemResult(
                status="already_rejected",
                item_id=item_id,
                executed_detail=None,
                detail="this item was already rejected",
            )
        if item["status"] != "proposed":
            return RebalanceItemResult(
                status="invalid_state",
                item_id=item_id,
                executed_detail=item.get("executed_detail"),
                detail=f"item is {item['status']!r}, not proposed — cannot reject",
            )
        item["status"] = "rejected"
        item["reject_reason"] = reason
        markers.set_rebalance_plan(task, payload)
        self._maybe_complete_cycle(task, payload)
        await self._record_learn(task, learn_ref(item), "rejected", reason)
        await self.session.flush()
        return RebalanceItemResult(
            status="rejected",
            item_id=item_id,
            executed_detail=None,
            detail="recorded; feeds the next cycle's prompt",
        )

    async def _find_item(
        self, task_id: UUID, item_id: str
    ) -> tuple[TaskTable | None, dict[str, Any] | None, dict[str, Any] | None]:
        """Resolve (exploration task, rebalance payload, one item) or (None,
        None, None). Deep-copies the stored marker before mutating it — see
        ``RoadmapService._find_item``'s identical dirty-check rationale."""
        from roboco.services.task import get_task_service

        task = await get_task_service(self.session).get(task_id)
        if task is None or task.source != SCALES_SOURCE:
            return None, None, None
        stored = markers.get_rebalance_plan(task)
        if stored is None:
            return None, None, None
        payload = copy.deepcopy(stored)
        item = next(
            (it for it in payload.get("items", []) if it.get("id") == item_id), None
        )
        if item is None:
            return None, None, None
        return task, payload, item

    async def _execute(self, item: dict[str, Any], *, created_by: UUID) -> str:
        """Run the item's action against its live target task; returns a
        human-readable detail of what happened. Raises ``ValueError`` (caught
        by the caller and returned as ``invalid_state``) when the target no
        longer exists or has left BACKLOG/PENDING since propose time — e.g. a
        PM claimed it, or an earlier item in the same cycle already touched
        it."""
        from roboco.services.task import get_task_service

        task_svc = get_task_service(self.session)
        target = await task_svc.get(UUID(item["target_task_id"]))
        if target is None:
            raise ValueError(f"target task {item['target_task_id']} no longer exists")
        if target.status not in (TaskStatus.BACKLOG, TaskStatus.PENDING):
            raise ValueError(
                f"target task is now {target.status.value!r}, no longer "
                "BACKLOG/PENDING — this rebalance no longer applies"
            )
        target_id = cast("UUID", target.id)
        if item["action"] == "reprioritize":
            await task_svc.update(target_id, priority=item["new_priority"])
            detail = f"priority changed to P{item['new_priority']}"
        else:
            await task_svc.cancel(
                target_id,
                agent_role="ceo",
                cancellation_note=f"[Scales rebalance] {item['rationale']}",
            )
            detail = "cancelled"
        await self._audit_execution(item, target_id=target_id, actor_id=created_by)
        return detail

    async def _audit_execution(
        self, item: dict[str, Any], *, target_id: UUID, actor_id: UUID
    ) -> None:
        """Best-effort audit row for the executed rebalance action — never
        fails the approve.

        Written into ``self.session`` (mirrors ``TaskService.
        _emit_status_transition_audit``) rather than through
        ``AuditService``'s own decoupled session: that separate connection
        commits independently of the target task's priority/cancel mutation
        just executed on THIS session, so a later rollback of this
        transaction would leave a phantom audit row behind (the exact
        F061/F073 gap ``_emit_status_transition_audit`` was fixed to close).
        Still best-effort — a row-add failure here must never fail the
        approve — so exceptions are swallowed same as before."""
        with contextlib.suppress(Exception):
            from roboco.db.tables import AuditLogTable

            self.session.add(
                AuditLogTable(
                    event_type="task.scales_rebalance",
                    agent_id=actor_id,
                    target_type="task",
                    target_id=target_id,
                    severity="info",
                    details={
                        "action": item["action"],
                        "new_priority": item.get("new_priority"),
                        "rationale": item["rationale"][:300],
                    },
                )
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
                "scales",
                item_ref,
                verdict,
                reason,
                exploration_task_id=cast("UUID", task.id),
            )
        except Exception:
            self.log.warning("scales: LEARN record_decision failed (best-effort)")


def get_scales_service(session: AsyncSession) -> ScalesService:
    """Construct a ScalesService bound to ``session``."""
    return ScalesService(session)
