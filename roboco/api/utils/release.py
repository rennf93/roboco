"""
Release Route Helpers

Route-glue helpers backing roboco/api/routes/release.py.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from roboco.api.deps import CurrentAgentContext, require_ceo_role
from roboco.api.schemas.release import (
    ReleaseGapModel,
    ReleaseProposalResponse,
    ReleaseReportModel,
)
from roboco.foundation.policy.content import markers
from roboco.services.release_proposal import is_approve_in_flight

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable


def _require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="view or act on release proposals")


def _status_value(task: "TaskTable") -> str:
    raw = task.status
    return raw.value if hasattr(raw, "value") else str(raw)


def _to_response(task: "TaskTable") -> ReleaseProposalResponse:
    report = markers.get_release_report(task) or {}
    outcome = markers.get_release_execute_outcome(task)
    return ReleaseProposalResponse(
        task_id=str(task.id),
        title=task.title,
        status=_status_value(task),
        required_changes=markers.get_release_required_changes(task),
        execute_status=outcome[0] if outcome else None,
        execute_detail=outcome[1] if outcome else None,
        execute_in_flight=is_approve_in_flight(UUID(str(task.id))),
        report=ReleaseReportModel(
            proposed_version=report.get("proposed_version", ""),
            bump_kind=report.get("bump_kind", ""),
            change_summary=report.get("change_summary", []),
            drafted_changelog=report.get("drafted_changelog", ""),
            version_bump_plan=report.get("version_bump_plan", []),
            gaps=[ReleaseGapModel(**gap) for gap in report.get("gaps", [])],
            migration_notes=report.get("migration_notes", []),
            gate_state=report.get("gate_state", "unknown"),
        ),
    )
