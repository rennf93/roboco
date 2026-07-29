"""CoronerService — the CEO's approve/dismiss glue over a completed
Coroner postmortem's ONE process change.

The Coroner engine opens a HELD, EVENT-triggered postmortem-exploration task
(``board_coroner`` source) when an incident task bounces 3+ times, is
cancelled after work started, or is budget-blocked. The Auditor autopsies it
and files the postmortem via ``propose_postmortem`` (persisted as a marker
payload — see
``roboco.foundation.policy.content.markers.get_coroner_postmortem``), which
completes the exploration task in that same call — a report, not a per-item
queue.

Unlike Periscope/Sentinel, a postmortem is ONE process change, not a list of
items (``roboco.services.coroner_engine``'s own docstring: "a postmortem is
one process change, not a list of items") — so there is no item id to key
on, just the task id. ``approve_process_change`` materializes it as a
PENDING, Main-PM-owned root task (``source=coroner``, ``assigned_to=
main-pm`` — see ``RoadmapService._materialize``'s docstring for why never a
parentless BACKLOG task); ``reject_process_change`` records the reason
("dismiss" — no task). Both are idempotent. A ``kind="playbook"`` change
already routed straight into the playbook curation queue at propose time
(``ContentActions._draft_coroner_playbook``) — it carries marker status
``not_applicable`` and both actions here refuse it outright (result status
``invalid_state``): nothing is left to decide.

A process change carries no ``project_slug`` the way a roadmap item does.
The target project resolves to the INCIDENT task's own project (re-fetched
via the ``coroner_incident`` marker's ``incident_task_id`` — the same
resolution ``CoronerEngine._originate`` already uses to anchor the
postmortem-exploration task itself: "Project is the incident's own — an
autopsy is about that incident, wherever it lived"), falling back to
RoboCo's own project (``settings.self_heal_project_slug``) when the incident
is gone or carries no project — mirroring ``CoronerEngine._originate``'s own
``incident.project_id or await self._roboco_project_id()`` fallback exactly.
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
from roboco.services.task import CORONER_ITEM_SOURCE, CORONER_SOURCE

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable

PLAYBOOK_KIND = "playbook"


@dataclass(frozen=True)
class ProcessChangeResult:
    """Outcome of an approve/reject call on a postmortem's process change.

    `status` is one of: approved, already_approved, rejected,
    already_rejected, invalid_state.
    """

    status: str
    materialized_task_id: str | None
    detail: str


class CoronerService(BaseService):
    """Approve / reject the process change on a completed Coroner postmortem."""

    service_name = "coroner_service"

    async def approve_process_change(
        self, task_id: UUID, *, created_by: UUID
    ) -> ProcessChangeResult | None:
        """Materialize the postmortem's process change as a Main-PM-owned
        root task.

        Returns None when ``task_id`` carries no Coroner postmortem.
        Idempotent: an already-approved change returns its stored
        materialized task id without creating a duplicate. An
        already-rejected change cannot be approved. A ``kind="playbook"``
        change refuses outright — it already drafted into the playbook
        queue at propose time.
        """
        task, payload, process_change = await self._find(task_id)
        if task is None or payload is None or process_change is None:
            return None
        if process_change["kind"] == PLAYBOOK_KIND:
            return self._playbook_result()
        if process_change["status"] == "approved":
            return ProcessChangeResult(
                status="already_approved",
                materialized_task_id=process_change.get("materialized_task_id"),
                detail="this process change was already approved",
            )
        if process_change["status"] != "proposed":
            return ProcessChangeResult(
                status="invalid_state",
                materialized_task_id=None,
                detail=(
                    f"process change is {process_change['status']!r}, not "
                    "proposed — cannot approve"
                ),
            )
        try:
            new_task = await self._materialize(task, payload, created_by=created_by)
        except ValueError as exc:
            return ProcessChangeResult(
                status="invalid_state", materialized_task_id=None, detail=str(exc)
            )
        process_change["status"] = "approved"
        process_change["materialized_task_id"] = str(new_task.id)
        payload["process_change"] = process_change
        markers.set_coroner_postmortem(task, payload)
        await self._record_learn(task, process_change, "approved")
        await self.session.flush()
        return ProcessChangeResult(
            status="approved",
            materialized_task_id=str(new_task.id),
            detail="materialized as a Main-PM-owned task",
        )

    async def reject_process_change(
        self, task_id: UUID, reason: str
    ) -> ProcessChangeResult | None:
        """Dismiss the postmortem's process change, recording the CEO's
        reason.

        Idempotent: an already-rejected change returns its stored reason
        without re-recording. An already-approved change cannot be rejected.
        A ``kind="playbook"`` change refuses outright — see
        ``approve_process_change``.
        """
        task, payload, process_change = await self._find(task_id)
        if task is None or payload is None or process_change is None:
            return None
        if process_change["kind"] == PLAYBOOK_KIND:
            return self._playbook_result()
        if process_change["status"] == "rejected":
            return ProcessChangeResult(
                status="already_rejected",
                materialized_task_id=None,
                detail="this process change was already dismissed",
            )
        if process_change["status"] != "proposed":
            return ProcessChangeResult(
                status="invalid_state",
                materialized_task_id=process_change.get("materialized_task_id"),
                detail=(
                    f"process change is {process_change['status']!r}, not "
                    "proposed — cannot dismiss"
                ),
            )
        process_change["status"] = "rejected"
        process_change["reject_reason"] = reason
        payload["process_change"] = process_change
        markers.set_coroner_postmortem(task, payload)
        await self._record_learn(task, process_change, "rejected", reason)
        await self.session.flush()
        return ProcessChangeResult(
            status="rejected",
            materialized_task_id=None,
            detail="dismissed; feeds the next cycle's prompt",
        )

    @staticmethod
    def _playbook_result() -> ProcessChangeResult:
        return ProcessChangeResult(
            status="invalid_state",
            materialized_task_id=None,
            detail=(
                "this process change already drafted as a playbook — see the "
                "playbook review queue, there is nothing else to decide here"
            ),
        )

    async def _find(
        self, task_id: UUID
    ) -> tuple[TaskTable | None, dict[str, Any] | None, dict[str, Any] | None]:
        """Resolve (exploration task, postmortem payload, the process
        change) or (None, None, None). Deep-copies the stored marker before
        mutating it — see ``RoadmapService._find_item``'s identical
        dirty-check rationale.

        A postmortem authored before this feature shipped carries no
        ``status`` key on its process change at all — ``setdefault`` treats
        it as ``proposed`` rather than crashing on a missing key.
        """
        from roboco.services.task import get_task_service

        task = await get_task_service(self.session).get(task_id)
        if task is None or task.source != CORONER_SOURCE:
            return None, None, None
        stored = markers.get_coroner_postmortem(task)
        if stored is None:
            return None, None, None
        payload = copy.deepcopy(stored)
        process_change = payload.get("process_change")
        if not isinstance(process_change, dict):
            return None, None, None
        process_change.setdefault("status", "proposed")
        return task, payload, process_change

    async def _materialize(
        self,
        task: TaskTable,
        payload: dict[str, Any],
        *,
        created_by: UUID,
    ) -> TaskTable:
        """Turn the approved process change into a real Main-PM-owned root
        task, anchored on the incident's own project (see module docstring
        for why). ``payload`` is the full deep-copied postmortem dict
        ``_find`` already read — reused here rather than re-reading the
        task's marker column mid-transaction, before this same call's
        caller has written the approved status back.

        ``team=Team.MAIN_PM`` (via ``BatchPlacement.team_override``), matching
        ``TaskService.approve_and_start`` — the incident's own team (resolved
        by ``_resolve_target``) is no longer the task's ``team`` column; it
        survives as a Notes delegation hint instead."""
        from roboco.seeds.initial_data import AGENT_UUIDS
        from roboco.services.prompter import BatchPlacement, get_prompter_service

        project_id, team = await self._resolve_target(task)
        if project_id is None:
            raise ValueError(
                "neither the incident's own project nor the RoboCo project "
                "(settings.self_heal_project_slug) is resolvable — cannot "
                "anchor a materialized task"
            )
        process_change = payload["process_change"]
        notes = [f"Root cause: {payload.get('root_cause', '')}".strip()]
        incident_summary = payload.get("incident_summary")
        if incident_summary:
            notes.append(f"Incident: {incident_summary}")
        notes.append(
            f"Delegation hint: the incident lived in the {team.value} cell — "
            f"delegate into the {team.value} cell."
        )
        draft = {
            "title": f"Postmortem follow-up: {process_change['description']}"[:200],
            "objective": process_change["description"],
            "notes": notes,
            "acceptance_criteria": [process_change["description"]],
            "project_id": str(project_id),
            "team": team.value,
            "priority": 2,
            "source": CORONER_ITEM_SOURCE,
        }
        return await get_prompter_service(self.session).create_task_from_draft(
            draft,
            created_by,
            status=TaskStatus.PENDING,
            assigned_to=UUID(AGENT_UUIDS["main-pm"]),
            placement=BatchPlacement(team_override=Team.MAIN_PM),
        )

    async def _resolve_target(self, task: TaskTable) -> tuple[UUID | None, Team]:
        """(project_id, team) for the materialized follow-up — the
        incident's own, else RoboCo's project + Team.BACKEND. Mirrors
        ``CoronerEngine._originate``'s ``incident.project_id or await
        self._roboco_project_id()`` exactly, extended to also resolve the
        incident's own team, which ``_materialize`` now carries forward as a
        Notes delegation hint rather than the materialized task's ``team``
        column (forced to ``Team.MAIN_PM`` — see ``_materialize``'s
        docstring)."""
        incident_ref = markers.get_coroner_incident(task) or {}
        incident_task_id = incident_ref.get("incident_task_id")
        if incident_task_id:
            from roboco.services.task import get_task_service

            incident = await get_task_service(self.session).get(UUID(incident_task_id))
            if incident is not None and incident.project_id is not None:
                return cast("UUID", incident.project_id), incident.team or Team.BACKEND
        project = await self._roboco_project()
        if project is not None:
            return cast("UUID", project.id), Team.BACKEND
        return None, Team.BACKEND

    async def _roboco_project(self) -> Any:
        """Mirrors ``CoronerEngine._roboco_project_id`` exactly — the same
        fallback anchor the postmortem-exploration task itself resolves
        against when the incident carries no project."""
        from roboco.services.project import get_project_service

        slug = (settings.self_heal_project_slug or "roboco-api").strip()
        return await get_project_service(self.session).get_by_slug(slug)

    async def _record_learn(
        self,
        task: TaskTable,
        process_change: dict[str, Any],
        verdict: str,
        reason: str | None = None,
    ) -> None:
        """Best-effort LEARN: a record_decision failure must never break the
        CEO's approve/reject — mirrors ``RoadmapService._record_learn``.

        ``learn_ref`` expects a ``title``/``target_task_title`` field; a
        process change carries neither, so it's wrapped with its
        ``description`` under ``title`` rather than reinventing the
        truncation/fallback logic.
        """
        try:
            from roboco.services.board_programs import get_board_program_engine

            await get_board_program_engine(self.session).record_decision(
                "coroner",
                learn_ref({"title": process_change.get("description")}),
                verdict,
                reason,
                exploration_task_id=cast("UUID", task.id),
            )
        except Exception:
            self.log.warning("coroner: LEARN record_decision failed (best-effort)")


def get_coroner_service(session: AsyncSession) -> CoronerService:
    """Construct a CoronerService bound to ``session``."""
    return CoronerService(session)
