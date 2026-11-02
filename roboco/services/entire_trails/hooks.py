"""The lifecycle side of the trails bridge: outbox rows written in the
caller's own transaction, so an operation reaches Entire if and only if the
RoboCo change it mirrors commits.

The trail's own text (plan, task table, outcome) is not hooked here: the
drain loop refreshes it whenever the tree's tasks changed, and closes the
trail once the tree's top task has ended (``drain.reconcile_trails``).

Every hook is a no-op while ``entire_trails_enabled`` is off, and for a tree
that is not bridged. A tree is bridged when its first branch is cut on an
opted-in GitHub project (``branch_created``); a tree already running when its
project opted in never is, so it keeps Entire's own per-PR trails. Each hook
runs in a savepoint and logs instead of raising: the mirror never fails the
RoboCo operation it rides on.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from roboco.config import settings
from roboco.db.tables import (
    EntireTrailChangeTable,
    EntireTrailTable,
    ProjectTable,
    TaskTable,
)
from roboco.foundation.identity import Role, role_for_uuid_or_none
from roboco.logging import get_logger
from roboco.services.entire_trails.outbox import (
    OP_FINDING_ADD,
    OP_FINDING_DISMISS,
    OP_FINDING_RESOLVE,
    OP_FINDING_UPDATE,
    OP_LINK,
    OP_SYNC_TRAIL,
    bridged_trail,
    enqueue,
    entire_project_of,
    entire_repository,
    finding_is_mirrored,
    starts_tree,
    tree_top,
)
from roboco.services.entire_trails.render import (
    addressed_finding_body,
    entire_severity,
    finding_body,
    trail_fields,
)
from roboco.utils.converters import require_uuid

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskReviewFindingTable

logger = get_logger(__name__)

# Claimants whose branch is an integration branch: it gains commits as child
# PRs merge into it, so it links at submit_up / submit_root, right before its
# own PR (linked at claim, Entire would open a PR for it minutes after the
# first child merge).
_INTEGRATION_ROLES = frozenset({Role.CELL_PM, Role.MAIN_PM})


@asynccontextmanager
async def _mirror_write(
    session: AsyncSession, hook: str, task_id: object
) -> AsyncIterator[None]:
    try:
        async with session.begin_nested():
            yield
    except Exception as exc:
        logger.warning(
            "entire trails: outbox write skipped",
            hook=hook,
            task_id=str(task_id),
            error=str(exc),
        )


def links_at_submit(agent_id: UUID | None) -> bool:
    """Whether a branch claimed by ``agent_id`` is an integration branch."""
    return role_for_uuid_or_none(agent_id) in _INTEGRATION_ROLES


@dataclass(frozen=True)
class BranchCut:
    """A task branch just cut and pushed at claim."""

    name: str
    base: str
    claimed_by: UUID | None


async def branch_created(
    session: AsyncSession, task: TaskTable, project: Any, cut: BranchCut
) -> None:
    """A task's branch was cut and pushed (at claim). Starts the tree's
    trail when this is the tree's first branch on an opted-in project, and
    queues the link unless the branch is an integration branch."""
    if not settings.entire_trails_enabled:
        return
    repository = entire_repository(project)
    if repository is None or not getattr(project, "entire_trails_enabled", False):
        return
    async with _mirror_write(session, "branch_created", task.id):
        top = await tree_top(session, task)
        trail = await session.get(EntireTrailTable, top.id)
        if trail is None:
            if not starts_tree(task, top):
                return
            trail = EntireTrailTable(
                root_task_id=top.id,
                entire_project=entire_project_of(repository),
                create_fields=trail_fields(top, []),
            )
            session.add(trail)
            await session.flush()
            enqueue(session, require_uuid(top.id), OP_SYNC_TRAIL, {})
        if trail.closed_at is not None:
            return
        if entire_project_of(repository) != trail.entire_project:
            logger.warning(
                "entire trails: branch not linked, its repository is outside "
                "the trail's Entire project",
                repository=repository,
                entire_project=trail.entire_project,
            )
            return
        if links_at_submit(cut.claimed_by):
            return
        enqueue(
            session,
            require_uuid(top.id),
            OP_LINK,
            {
                "task_id": str(task.id),
                "repository": repository,
                "branch": cut.name,
                "base": cut.base,
            },
        )


async def _finding_target(
    session: AsyncSession, task: TaskTable
) -> tuple[UUID, str, str] | None:
    """(tree root, repository, branch) a task's findings go to, or None."""
    trail = await bridged_trail(session, task)
    if trail is None:
        return None
    if task.project_id is not None and task.branch_name:
        project = await session.get(ProjectTable, task.project_id)
        repository = entire_repository(project)
        if repository is None or not getattr(project, "entire_trails_enabled", False):
            return None
        return trail.root_task_id, repository, str(task.branch_name)
    # A coordination root without a project of its own: its branch is linked
    # once per repository at submit_root; its findings go to the first.
    result = await session.execute(
        select(EntireTrailChangeTable)
        .where(EntireTrailChangeTable.task_id == task.id)
        .order_by(EntireTrailChangeTable.linked_at)
        .limit(1)
    )
    change = result.scalar_one_or_none()
    if change is None:
        return None
    return trail.root_task_id, change.repository, change.branch


async def findings_recorded(
    session: AsyncSession, rows: Sequence[TaskReviewFindingTable]
) -> None:
    """New ledger rows (one producer call, one task): queue them as trail
    findings, their text rendered now (see ``render``)."""
    if not settings.entire_trails_enabled or not rows:
        return
    task_id = rows[0].task_id
    async with _mirror_write(session, "findings_recorded", task_id):
        task = await session.get(TaskTable, task_id)
        if task is None:
            return
        target = await _finding_target(session, task)
        if target is None:
            return
        root_id, repository, branch = target
        for row in rows:
            enqueue(
                session,
                root_id,
                OP_FINDING_ADD,
                {
                    "finding_id": str(row.id),
                    "repository": repository,
                    "branch": branch,
                    "severity": entire_severity(row.severity),
                    "body": finding_body(row, task),
                    "file": row.file,
                    "line": row.line,
                },
            )


async def _mirrored_root(
    session: AsyncSession, row: TaskReviewFindingTable
) -> tuple[UUID, TaskTable] | None:
    if not await finding_is_mirrored(session, row.id):
        return None
    task = await session.get(TaskTable, row.task_id)
    if task is None:
        return None
    trail = await bridged_trail(session, task)
    return (trail.root_task_id, task) if trail is not None else None


async def finding_addressed(session: AsyncSession, row: TaskReviewFindingTable) -> None:
    """The author says the finding is fixed: its trail finding says so too,
    and stays open until a reviewer verifies it."""
    if not settings.entire_trails_enabled:
        return
    async with _mirror_write(session, "finding_addressed", row.task_id):
        mirrored = await _mirrored_root(session, row)
        if mirrored is None:
            return
        root_id, task = mirrored
        enqueue(
            session,
            root_id,
            OP_FINDING_UPDATE,
            {"finding_id": str(row.id), "body": addressed_finding_body(row, task)},
        )


async def findings_verified(
    session: AsyncSession,
    rows: Sequence[TaskReviewFindingTable],
    *,
    verified_by: str | None,
) -> None:
    """A reviewer confirmed the fixes: resolve the trail findings."""
    if not settings.entire_trails_enabled or not rows:
        return
    async with _mirror_write(session, "findings_verified", rows[0].task_id):
        for row in rows:
            mirrored = await _mirrored_root(session, row)
            if mirrored is None:
                continue
            who = verified_by or f"the {row.origin} review"
            commit = row.addressed_by_commit or "an unrecorded commit"
            enqueue(
                session,
                mirrored[0],
                OP_FINDING_RESOLVE,
                {
                    "finding_id": str(row.id),
                    "message": f"verified by {who} at {commit}",
                },
            )


async def finding_waived(session: AsyncSession, row: TaskReviewFindingTable) -> None:
    """A finding was waived: dismiss its trail finding with the note."""
    if not settings.entire_trails_enabled:
        return
    async with _mirror_write(session, "finding_waived", row.task_id):
        mirrored = await _mirrored_root(session, row)
        if mirrored is None:
            return
        enqueue(
            session,
            mirrored[0],
            OP_FINDING_DISMISS,
            {
                "finding_id": str(row.id),
                "message": f"waived: {row.resolution_note or 'no note'}",
            },
        )
