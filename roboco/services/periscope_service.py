"""PeriscopeService — the CEO's per-finding approve/dismiss glue over a
completed Periscope market brief.

The Periscope engine opens a HELD exploration task (``board_periscope``
source); the Head of Marketing files ONE brief onto it via
``propose_market_brief`` (a headline + 1-7 cited findings, persisted as a
marker payload — see
``roboco.foundation.policy.content.markers.get_market_brief``) and the
exploration task completes in that same call — a report, not a per-item
queue (mirrors ``RoadmapService``'s docstring on this point exactly).

Unlike the exploration task, each FINDING still carries its own
proposed/approved/rejected status the CEO decides on afterward — that is
what this service is for. ``approve_finding`` materializes one finding as a
PENDING, Main-PM-owned root task (``source=periscope``, ``assigned_to=
main-pm`` — see ``RoadmapService._materialize``'s docstring for why never a
parentless BACKLOG task); ``reject_finding`` records the reason ("dismiss" —
no task). Both are idempotent per finding.

A finding carries no ``project_slug`` the way a roadmap item does (Periscope
reads the market, not a repo). The target project resolves to RoboCo's own
project (``settings.self_heal_project_slug``, the same fallback
``PeriscopeEngine._roboco_project`` already uses to anchor the exploration
task itself) — a market signal is process/strategy input about the org, not
about any one customer repo, and RoboCo is the only project every findings
consumer has in common.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from roboco.config import settings
from roboco.foundation.policy.content import markers
from roboco.models.base import TaskStatus, Team
from roboco.services.base import BaseService
from roboco.services.board_programs import learn_ref
from roboco.services.task import PERISCOPE_ITEM_SOURCE, PERISCOPE_SOURCE

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable

_TERMINAL_ITEM_STATUSES = ("approved", "rejected")


@dataclass(frozen=True)
class MarketBriefFindingResult:
    """Outcome of an approve/reject call on one market-brief finding.

    `status` is one of: approved, already_approved, rejected,
    already_rejected, invalid_state.
    """

    status: str
    finding_id: str
    materialized_task_id: str | None
    detail: str


class PeriscopeService(BaseService):
    """Approve / reject findings within a completed Periscope market brief."""

    service_name = "periscope_service"

    async def approve_finding(
        self, task_id: UUID, finding_id: str, *, created_by: UUID
    ) -> MarketBriefFindingResult | None:
        """Materialize one proposed finding as a Main-PM-owned root task.

        Returns None when ``task_id`` carries no Periscope brief or
        ``finding_id`` does not exist on it. Idempotent: an already-approved
        finding returns its stored materialized task id without creating a
        duplicate. An already-rejected finding cannot be approved.
        """
        task, payload, finding = await self._find_finding(task_id, finding_id)
        if task is None or payload is None or finding is None:
            return None
        if finding["status"] == "approved":
            return MarketBriefFindingResult(
                status="already_approved",
                finding_id=finding_id,
                materialized_task_id=finding.get("materialized_task_id"),
                detail="this finding was already approved",
            )
        if finding["status"] != "proposed":
            return MarketBriefFindingResult(
                status="invalid_state",
                finding_id=finding_id,
                materialized_task_id=None,
                detail=(
                    f"finding is {finding['status']!r}, not proposed — cannot approve"
                ),
            )
        try:
            new_task = await self._materialize(finding, created_by=created_by)
        except ValueError as exc:
            return MarketBriefFindingResult(
                status="invalid_state",
                finding_id=finding_id,
                materialized_task_id=None,
                detail=str(exc),
            )
        finding["status"] = "approved"
        finding["materialized_task_id"] = str(new_task.id)
        markers.set_market_brief(task, payload)
        await self._record_learn(task, finding, "approved")
        await self.session.flush()
        return MarketBriefFindingResult(
            status="approved",
            finding_id=finding_id,
            materialized_task_id=str(new_task.id),
            detail="materialized as a Main-PM-owned task",
        )

    async def reject_finding(
        self, task_id: UUID, finding_id: str, reason: str
    ) -> MarketBriefFindingResult | None:
        """Dismiss one proposed finding, recording the CEO's reason.

        Idempotent: an already-rejected finding returns its stored reason
        without re-recording. An already-approved finding cannot be
        rejected (irreversible — a task already exists for it).
        """
        task, payload, finding = await self._find_finding(task_id, finding_id)
        if task is None or payload is None or finding is None:
            return None
        if finding["status"] == "rejected":
            return MarketBriefFindingResult(
                status="already_rejected",
                finding_id=finding_id,
                materialized_task_id=None,
                detail="this finding was already dismissed",
            )
        if finding["status"] != "proposed":
            return MarketBriefFindingResult(
                status="invalid_state",
                finding_id=finding_id,
                materialized_task_id=finding.get("materialized_task_id"),
                detail=(
                    f"finding is {finding['status']!r}, not proposed — cannot dismiss"
                ),
            )
        finding["status"] = "rejected"
        finding["reject_reason"] = reason
        markers.set_market_brief(task, payload)
        await self._record_learn(task, finding, "rejected", reason)
        await self.session.flush()
        return MarketBriefFindingResult(
            status="rejected",
            finding_id=finding_id,
            materialized_task_id=None,
            detail="dismissed; feeds the next cycle's prompt",
        )

    async def _find_finding(
        self, task_id: UUID, finding_id: str
    ) -> tuple[TaskTable | None, dict[str, Any] | None, dict[str, Any] | None]:
        """Resolve (exploration task, brief payload, one finding) or (None,
        None, None). Deep-copies the stored marker before mutating it — see
        ``RoadmapService._find_item``'s identical dirty-check rationale.

        A finding authored before this feature shipped carries no ``status``
        key at all — ``setdefault`` treats it as ``proposed`` rather than
        crashing on a missing key.
        """
        from roboco.services.task import get_task_service

        task = await get_task_service(self.session).get(task_id)
        if task is None or task.source != PERISCOPE_SOURCE:
            return None, None, None
        stored = markers.get_market_brief(task)
        if stored is None:
            return None, None, None
        payload = copy.deepcopy(stored)
        finding = next(
            (f for f in payload.get("findings", []) if f.get("id") == finding_id), None
        )
        if finding is None:
            return None, None, None
        finding.setdefault("status", "proposed")
        return task, payload, finding

    async def _materialize(
        self, finding: dict[str, Any], *, created_by: UUID
    ) -> TaskTable:
        """Turn one approved finding into a real Main-PM-owned root task,
        anchored on the RoboCo project (see module docstring for why).
        ``team=Team.MAIN_PM`` (via ``BatchPlacement.team_override``), matching
        ``TaskService.approve_and_start`` — a market signal has no natural
        owning cell, so unlike ``RoadmapService._materialize`` there is no
        per-item cell to preserve as a delegation hint."""
        from roboco.seeds.initial_data import AGENT_UUIDS
        from roboco.services.prompter import BatchPlacement, get_prompter_service

        project = await self._roboco_project()
        if project is None or project.id is None:
            raise ValueError(
                "the RoboCo project (settings.self_heal_project_slug) is not "
                "resolvable — cannot anchor a materialized task"
            )
        draft = {
            "title": f"Market signal: {finding['claim']}"[:200],
            "objective": finding["claim"],
            "notes": [
                f"Relevance: {finding['relevance']}",
                f"Source: {finding['source_url']}",
            ],
            "acceptance_criteria": [
                f"The market signal is addressed: {finding['claim']}",
                "A note explains what changed in response and why.",
            ],
            "project_id": str(project.id),
            "team": Team.BACKEND.value,
            "priority": 2,
            "source": PERISCOPE_ITEM_SOURCE,
        }
        return await get_prompter_service(self.session).create_task_from_draft(
            draft,
            created_by,
            status=TaskStatus.PENDING,
            assigned_to=UUID(AGENT_UUIDS["main-pm"]),
            placement=BatchPlacement(team_override=Team.MAIN_PM),
        )

    async def _roboco_project(self) -> Any:
        """Mirrors ``PeriscopeEngine._roboco_project`` exactly — the same
        fallback anchor a Periscope exploration task itself resolves against."""
        from roboco.services.project import get_project_service

        slug = (settings.self_heal_project_slug or "roboco-api").strip()
        return await get_project_service(self.session).get_by_slug(slug)

    async def _record_learn(
        self,
        task: TaskTable,
        finding: dict[str, Any],
        verdict: str,
        reason: str | None = None,
    ) -> None:
        """Best-effort LEARN: a record_decision failure must never break the
        CEO's approve/reject — mirrors ``RoadmapService._record_learn``.

        ``learn_ref`` expects a ``title``/``target_task_title`` field; a
        finding carries neither, so it's wrapped with its ``claim`` under
        ``title`` rather than reinventing the truncation/fallback logic.
        """
        try:
            from roboco.services.board_programs import get_board_program_engine

            await get_board_program_engine(self.session).record_decision(
                "periscope",
                learn_ref({"title": finding.get("claim")}),
                verdict,
                reason,
                exploration_task_id=cast("UUID", task.id),
            )
        except Exception:
            self.log.warning("periscope: LEARN record_decision failed (best-effort)")


def get_periscope_service(session: AsyncSession) -> PeriscopeService:
    """Construct a PeriscopeService bound to ``session``."""
    return PeriscopeService(session)
