"""
Prompter Live Route Helpers

Route-glue helpers backing roboco/api/routes/prompter_live.py.
"""

from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.api.deps import get_orchestrator
from roboco.api.schemas.prompter_live import StartLiveResponse
from roboco.services.base import NotFoundError, ServiceError, ValidationError
from roboco.services.project import get_project_service
from roboco.services.prompter import compose_batch_redraft_message
from roboco.services.task import get_task_service


def translate_service_error(e: ServiceError) -> HTTPException:
    """Service error → HTTP status (mirrors the legacy prompter route)."""
    if isinstance(e, NotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": e.message},
        )
    if isinstance(e, ValidationError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "validation_error",
                "message": e.message,
                "field": e.field,
            },
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"error": "internal_error", "message": e.message},
    )


async def intake_scope_for_task(
    db: AsyncSession, task: Any
) -> tuple[str | None, str | None]:
    """Return (project_slug, product_id) intake scope for a task — exactly one."""
    if task.product_id is not None:
        return None, str(task.product_id)
    if task.project_id is not None:
        proj = await get_project_service(db).get(UUID(str(task.project_id)))
        return (proj.slug if proj else None), None
    return None, None


async def start_batch_re_interview(
    db: AsyncSession, umbrella: Any, entries: list[dict[str, Any]]
) -> StartLiveResponse:
    """Cold re-interview for a MegaTask umbrella.

    Recovers the batch's multi-repo scope from its root-subtasks' own project /
    cell-map targets (no single project/product lives on the branchless
    umbrella) and seeds a batch-aware redraft message. 400 only when nothing is
    recoverable (e.g. every root-subtask was itself cancelled).
    """
    task_service = get_task_service(db)
    umbrella_id = UUID(str(umbrella.id))
    children = await task_service.get_live_subtasks(umbrella_id)
    project_ids = await task_service.distinct_projects_for_batch(umbrella_id)
    if not project_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This MegaTask has no recoverable projects to re-interview against.",
        )
    initial_message = compose_batch_redraft_message(umbrella, children, entries)

    session_id = uuid4().hex
    try:
        await get_orchestrator().start_intake_session(
            session_id,
            project_ids=[str(pid) for pid in project_ids],
            initial_message=initial_message,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start re-interview session: {exc}",
        ) from exc
    return StartLiveResponse(session_id=session_id, project_ids=project_ids)
