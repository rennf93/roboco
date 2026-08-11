"""
Task Route Helpers

Route-glue helpers backing roboco/api/routes/tasks.py.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.tables import TaskTable
from roboco.exceptions import GitError
from roboco.models.base import AgentRole, TaskStatus
from roboco.services.base import (
    NotFoundError,
    ServiceError,
    UnauthorizedError,
    ValidationError,
)
from roboco.services.permissions import AgentContext
from roboco.services.task import TaskService

if TYPE_CHECKING:
    from roboco.api.schemas.tasks import TaskUpdate

# #13: lifecycle-bypass hatch states — a privileged PATCH into one of these is a
# forced override that must carry the explicit ``force`` acknowledgement flag.
# The set covers every gate / terminal state a panel drag could paste a task
# into, bypassing the human gate that state represents: COMPLETED (the merge
# decision), AWAITING_QA / AWAITING_DOCUMENTATION / AWAITING_PR_REVIEW /
# AWAITING_PM_REVIEW / AWAITING_CEO_APPROVAL (the review/merge/CEO gates),
# and CANCELLED (the terminal cancel). Without force these are refused so the
# bypass is always an explicit, audited, acknowledged override — never a quiet
# panel click that drops a task into (or out of) a gate.
_HATCH_OVERRIDE_STATES = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.CANCELLED,
        TaskStatus.AWAITING_QA,
        TaskStatus.AWAITING_DOCUMENTATION,
        TaskStatus.AWAITING_PR_REVIEW,
        TaskStatus.AWAITING_PM_REVIEW,
        TaskStatus.AWAITING_CEO_APPROVAL,
    }
)

# Terminal statuses — a privileged PATCH OUT of one of these resurrects
# finished/cancelled work, which must also carry the explicit ``force``
# acknowledgement (mirrors the escalate route's refusal to resurrect).
_RESURRECT_SOURCE_STATES = frozenset({TaskStatus.COMPLETED, TaskStatus.CANCELLED})


@dataclass(frozen=True, slots=True)
class _StatusOverride:
    """Bundle of ``update_task`` override params (keeps the helper ≤ 5 args)."""

    service: TaskService
    task_id: UUID
    task: TaskTable
    new_status: TaskStatus
    force: bool
    has_higher_perms: bool
    agent: AgentContext


async def _refuse_unforced_complete_with_open_pr(req: _StatusOverride) -> None:
    """Admin-complete must merge-or-refuse.

    Completing a task whose PR is still OPEN strands its commits unmerged
    (bit the CEO twice live, 2026-07-02). Checked before the generic hatch
    text so the refusal names the PR and the consequence instead of a vague
    gate message; ``force`` stays the deliberate, audited escape.
    """
    if req.new_status != TaskStatus.COMPLETED or req.force:
        return
    open_ws = await req.service.open_pr_ref(req.task)
    if open_ws is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Task still has OPEN PR #{open_ws.pr_number}"
                f" ({open_ws.pr_url}); completing it now would strand"
                " those commits unmerged. Merge the PR first (or approve"
                " via POST /api/tasks/{id}/ceo-approve), or pass"
                ' "force": true to strand it deliberately.'
            ),
        )


async def _apply_forced_status_override(req: _StatusOverride) -> TaskTable:
    """Apply an audited admin status override, gating the lifecycle bypass.

    Extracted from ``update_task`` so the route's complexity stays readable.
    Refuses a non-privileged caller, and refuses a bypass into a hatch state
    without the explicit ``force`` flag; otherwise delegates to the audited
    ``admin_set_status`` and asserts the override landed. A live agent
    stranded by the claim clearing (e.g. a review-queue target) is evicted
    by ``admin_set_status`` itself, not here: that chokepoint is what every
    caller (this route, the Secretary's override action) routes through, so
    the eviction fires regardless of which caller triggered it and
    regardless of ``force``.
    """
    if req.new_status == req.task.status:
        return req.task
    if not req.has_higher_perms:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only privileged roles may override task status.",
        )
    await _refuse_unforced_complete_with_open_pr(req)
    if req.new_status in _HATCH_OVERRIDE_STATES and not req.force:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Overriding a task into "
                f"{req.new_status.value} bypasses the lifecycle gate; pass "
                '"force": true to acknowledge the forced override.'
            ),
        )
    # Resurrecting a terminal task (completed / cancelled -> anything) is a
    # bypass of the merge / cancel decision; it too requires the explicit force
    # acknowledgement. The target-only hatch gate above misses this because the
    # target (e.g. in_progress) is not itself a hatch state.
    if req.task.status in _RESURRECT_SOURCE_STATES and not req.force:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Task is in the terminal state {req.task.status.value};"
                " resurrecting it past the lifecycle gate requires"
                ' "force": true to acknowledge the override.'
            ),
        )
    task = await req.service.admin_set_status(
        req.task_id,
        req.new_status,
        actor_id=req.agent.agent_id,
        actor_role=getattr(req.agent, "role", None),
        force=req.force,
    )
    if not task:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Task status override failed unexpectedly",
        )
    return task


# The CEO's "PM lighter" scope: cell_pm/main_pm may PATCH this content-only
# slice — the same allowlist the Secretary's edit directive originally had
# (before it grew to Secretary FULL). No status changes, no structural/
# ownership fields (routes.tasks._PRIVILEGED_UPDATE_FIELDS), no git fields —
# those stay on the lifecycle-verb surface (delegate/reassign/complete/...).
_PM_LIGHTER_UPDATE_FIELDS: frozenset[str] = frozenset(
    {"title", "description", "acceptance_criteria", "priority"}
)

# Roles that get the lighter slice above instead of the full ASSIGN-holding
# admin bypass. TaskAction.ASSIGN is not team-scoped (see
# can_perform_task_action), so a cell_pm would otherwise ride the same
# unrestricted bypass CEO/Board/Auditor get, on any team's task — the
# own-team restriction below is enforced independently of that permission.
_PM_LIGHTER_ROLES: frozenset[AgentRole] = frozenset(
    {AgentRole.CELL_PM, AgentRole.MAIN_PM}
)


def _pm_editor_scope(
    agent: AgentContext, task: TaskTable, *, has_higher_perms: bool
) -> bool:
    """Return True if ``agent`` gets the "PM lighter" content-only slice.

    Raises 403 outright for a cell PM outside its own team — ASSIGN itself
    is not team-scoped (see ``can_perform_task_action``), so without this
    check a cross-team cell PM would fall through to the wider CEO/Board/
    Auditor admin bypass on ``has_higher_perms`` alone.
    """
    is_pm_editor = has_higher_perms and agent.role in _PM_LIGHTER_ROLES
    if is_pm_editor and agent.role == AgentRole.CELL_PM and agent.team != task.team:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Cell PM may only update tasks belonging to their own team "
                f"({agent.team}); this task is on {task.team}."
            ),
        )
    return is_pm_editor


def _enforce_pm_lighter_fields(
    updates: dict[str, Any],
    null_clears: dict[str, Any],
    new_status: TaskStatus | None,
) -> None:
    """Refuse anything past the content-only allowlist for a PM-lighter editor.

    "No status changes beyond what they already have" — status rides the
    lifecycle verbs, never this PATCH surface, for cell_pm/main_pm.
    """
    disallowed = (updates.keys() | null_clears.keys()) - _PM_LIGHTER_UPDATE_FIELDS
    if not disallowed and new_status is None:
        return
    reasons = []
    if disallowed:
        reasons.append(f"disallowed fields {sorted(disallowed)}")
    if new_status is not None:
        reasons.append("status changes are not part of the PM PATCH surface")
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"PM roles may only edit {sorted(_PM_LIGHTER_UPDATE_FIELDS)} via "
            "PATCH; " + "; ".join(reasons)
        ),
    )


def _translate_error(e: ServiceError) -> HTTPException:
    """Service errors → HTTP status. Kept at route layer; everything else moves."""
    if isinstance(e, NotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=e.message)
    if isinstance(e, UnauthorizedError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=e.message)
    if isinstance(e, ValidationError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=e.message)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=e.message
    )


# ---------------------------------------------------------------------------
# Route-layer helpers — extracted to keep the three complex routes ≤ rank B.
# ---------------------------------------------------------------------------


def _task_is_awaiting_pm_review(task: Any) -> bool:
    """Return True if the task is in the awaiting_pm_review state."""
    from roboco.models.base import TaskStatus as _TS

    return (
        task.status == _TS.AWAITING_PM_REVIEW
        or getattr(task.status, "value", None) == "awaiting_pm_review"
    )


# Nullable task fields that may be explicitly cleared via PATCH.
# After TaskService.update() gains its not-None guard, null-clears for these
# fields are handled at the route layer by direct setattr on the ORM object.
_NULLABLE_TASK_FIELDS: frozenset[str] = frozenset(
    {"assigned_to", "parent_task_id", "project_id", "budget_usd"}
)


def _pop_null_clears(updates: dict[str, Any]) -> dict[str, None]:
    """Remove and return explicitly-set-to-None nullable fields from *updates*.

    TaskService.update() skips None values (not-None guard), so null-clearing
    a field must be done at the route layer.  This helper splits the intent:
    it pops the null-clears from *updates* (modifying it in-place) and returns
    them so the caller can apply them directly on the ORM object.
    """
    clears: dict[str, None] = {}
    for field in _NULLABLE_TASK_FIELDS:
        if field in updates and updates[field] is None:
            clears[field] = updates.pop(field)
    return clears


def _apply_null_clears(task: Any, null_clears: dict[str, None]) -> None:
    """Set *null_clears* fields to None on the ORM task object.

    Unassigning implies releasing the claim: a cleared assigned_to with a
    surviving claimed_by/active_claimant_id keeps routing the task to the
    stale claimant while the next agent's content writes bounce.
    """
    for field in null_clears:
        setattr(task, field, None)
    if "assigned_to" in null_clears:
        task.claimed_by = None
        task.claimed_at = None
        task.active_claimant_id = None


def _reassert_batch_shape(task: Any) -> None:
    """Raise HTTP 400 if a mutation broke the task's MegaTask shape. Raised
    before any commit, so a violation rolls back cleanly."""
    from roboco.services.task import TaskService as _TaskService

    try:
        _TaskService.assert_batch_shape_intact(task)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


async def _resolve_assigned_to_slug(
    data: "TaskUpdate", db: AsyncSession
) -> "TaskUpdate":
    """Resolve an assigned_to slug to a UUID string; returns (possibly modified) data.

    If assigned_to was not set or is already a valid UUID or null, returns
    *data* unchanged.  If it is an agent slug, looks up the agent and replaces
    the slug with the UUID string so downstream transform helpers parse it
    correctly.  Raises HTTPException 422 when the slug cannot be found.
    """
    if "assigned_to" not in data.model_fields_set or data.assigned_to is None:
        return data
    try:
        UUID(data.assigned_to)
        return data  # already a valid UUID — no resolution needed
    except ValueError:
        pass
    from roboco.services.repositories.query_helpers import get_agent_by_slug

    agent_row = await get_agent_by_slug(db, data.assigned_to)
    if agent_row is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error": {
                    "code": "ASSIGNEE_NOT_FOUND",
                    "message": f"No agent with slug or UUID '{data.assigned_to}'",
                    "hint": "Use an agent slug (e.g. 'be-dev-1') or UUID",
                }
            },
        ) from None
    return data.model_copy(update={"assigned_to": str(agent_row.id)})


def _first_cell_map_project_id(task: Any) -> UUID | None:
    """First distinct project_id from a task's ad-hoc per-cell map.

    Mirrors the product-root ``distinct_project_ids(...)[0]`` first-project
    resolution: dedupes by project_id (a monorepo mapped across cells shares
    one project), ordered by cell team for determinism. Returns None when the
    task carries no cell map.
    """
    cell_map = getattr(task, "cell_projects", None) or []
    seen: set[UUID] = set()
    for mapping in sorted(cell_map, key=lambda m: m.team.value):
        pid = UUID(str(mapping.project_id))
        if pid not in seen:
            seen.add(pid)
            return pid
    return None


async def _project_for_complete(task: Any, db: AsyncSession) -> Any:
    """Resolve the project for complete_task's pre-merge step.

    Returns the project or None if unresolvable (no exception raised — the
    caller simply skips the merge when no project can be found).
    """
    from roboco.services.project import get_project_service

    project_service = get_project_service(db)
    if task.project_id is not None:
        return await project_service.get(UUID(str(task.project_id)))
    if task.product_id is not None:
        from roboco.services.product import get_product_service

        product_service = get_product_service(db)
        pids = await product_service.distinct_project_ids(UUID(str(task.product_id)))
        if pids:
            return await project_service.get(pids[0])
    cell_pid = _first_cell_map_project_id(task)
    if cell_pid is not None:
        return await project_service.get(cell_pid)
    return None


async def _merge_pr_if_awaiting_pm_review(
    task_id: UUID,
    pre_task: Any,
    agent: Any,
    db: AsyncSession,
) -> None:
    """Merge the task's PR when it is in awaiting_pm_review.

    Does nothing when pre_task is None, has no PR, or is not in the right
    state.  Raises HTTPException 400 when the merge itself fails.
    After this returns successfully, *_auto_complete_on_merge* inside the
    git service will have already transitioned the task to *completed*.
    """
    if pre_task is None or pre_task.pr_number is None:
        return
    if not _task_is_awaiting_pm_review(pre_task):
        return

    project = await _project_for_complete(pre_task, db)
    if project is None:
        return

    from roboco.api.schemas.git import GitMergePRRequest
    from roboco.services.git import get_git_service

    git_service = get_git_service(db)
    try:
        await git_service.merge_pr_for_task(
            agent.agent_id,
            agent.role,
            GitMergePRRequest(
                project_slug=project.slug,
                pr_number=pre_task.pr_number,
                task_id=task_id,
                merge_method="squash",
            ),
        )
    except (ServiceError, GitError) as e:
        msg = getattr(e, "message", str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"PR merge failed before completion: {msg}",
        ) from e


async def _resolve_project_for_merge(task: Any, db: AsyncSession) -> Any:
    """Resolve and return the Project required for a merge operation.

    Handles both direct project_id and product_id→project resolution.
    Raises HTTPException 400 if no project can be resolved or found.
    """
    from roboco.services.project import get_project_service

    project_service = get_project_service(db)
    if task.project_id is not None:
        resolved_id = UUID(str(task.project_id))
    elif task.product_id is not None:
        from roboco.services.product import get_product_service

        product_service = get_product_service(db)
        project_ids = await product_service.distinct_project_ids(
            UUID(str(task.product_id))
        )
        if not project_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"NO_PROJECT: Product {task.product_id} has no cell->project "
                    "mapping; cannot resolve workspace for merge."
                ),
            )
        resolved_id = project_ids[0]
    else:
        cell_pid = _first_cell_map_project_id(task)
        if cell_pid is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "NO_PROJECT: Task has neither project_id, product_id, nor a "
                    "cell->project map; cannot resolve workspace for merge. Set a "
                    "target on the task first."
                ),
            )
        resolved_id = cell_pid
    project = await project_service.get(resolved_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"NO_PROJECT: Project {resolved_id} not found; "
                "cannot resolve workspace for merge."
            ),
        )
    return project
