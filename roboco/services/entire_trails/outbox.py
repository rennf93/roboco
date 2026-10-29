"""The trails outbox's vocabulary: its operations, how a row is written, and
which tree and Entire repository a task belongs to."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from roboco.db.tables import (
    EntireTrailFindingTable,
    EntireTrailOutboxTable,
    EntireTrailTable,
    TaskTable,
)
from roboco.exceptions import GitError
from roboco.foundation.policy.batch import is_batch_umbrella
from roboco.foundation.policy.forge import extract_host
from roboco.services.forge.github import GitHubProvider
from roboco.utils.converters import require_uuid

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

# Outbox operations (``EntireTrailOutboxTable.op``). Every op first creates
# the tree's trail if it does not exist yet; ``sync_trail`` then refreshes its
# text, and ``link`` makes a branch one of its changes.
OP_SYNC_TRAIL = "sync_trail"
OP_CLOSE_TRAIL = "close_trail"
OP_LINK = "link"
OP_FINDING_ADD = "finding_add"
OP_FINDING_UPDATE = "finding_update"
OP_FINDING_RESOLVE = "finding_resolve"
OP_FINDING_DISMISS = "finding_dismiss"
OP_APPROVE = "approve"

_GITHUB_HOST = "github.com"


def entire_repository(project: Any) -> str | None:
    """The project's repository as Entire names it (``gh/<owner>/<repo>``),
    or None for anything not on github.com (Entire projects are
    ``gh/<owner>``)."""
    git_url = getattr(project, "git_url", None)
    if not git_url or getattr(project, "git_provider", None) not in (None, "github"):
        return None
    if extract_host(git_url) != _GITHUB_HOST:
        return None
    try:
        ref = GitHubProvider().parse_repo_ref(git_url)
    except GitError:
        return None
    return f"gh/{ref.owner}/{ref.repo}"


def entire_project_of(repository: str) -> str:
    """``gh/<owner>`` for ``gh/<owner>/<repo>``."""
    forge, owner, _repo = repository.split("/", 2)
    return f"{forge}/{owner}"


async def tree_top(session: AsyncSession, task: TaskTable) -> TaskTable:
    """The task's topmost ancestor: its coordination root, or the MegaTask
    umbrella above a root-subtask (one trail per tree)."""
    top = task
    seen = {top.id}
    while top.parent_task_id is not None and top.parent_task_id not in seen:
        parent = await session.get(TaskTable, top.parent_task_id)
        if parent is None:
            break
        seen.add(parent.id)
        top = parent
    return top


def starts_tree(task: TaskTable, top: TaskTable) -> bool:
    """The task is its tree's root, or a MegaTask root-subtask (its umbrella
    is branchless, so the umbrella's trail starts with a root-subtask)."""
    if task.id == top.id:
        return True
    return task.parent_task_id == top.id and is_batch_umbrella(
        batch_id=top.batch_id, parent_task_id=top.parent_task_id
    )


async def bridged_trail(
    session: AsyncSession, task: TaskTable
) -> EntireTrailTable | None:
    """The trail of the task's tree while it still takes operations: None
    when the tree is not bridged, or its trail is closed or closing."""
    top = await tree_top(session, task)
    trail = await session.get(EntireTrailTable, top.id)
    if trail is None or trail.closed_at is not None:
        return None
    if await pending_op_exists(session, require_uuid(top.id), OP_CLOSE_TRAIL):
        return None
    return trail


def enqueue(
    session: AsyncSession, root_task_id: UUID, op: str, payload: dict[str, Any]
) -> None:
    """Add one outbox row to the caller's transaction."""
    session.add(
        EntireTrailOutboxTable(
            root_task_id=root_task_id,
            op=op,
            payload=payload,
            next_attempt_at=datetime.now(UTC),
        )
    )


async def pending_op_exists(session: AsyncSession, root_task_id: UUID, op: str) -> bool:
    """Whether a live (not dead) ``op`` row is queued for the tree."""
    result = await session.execute(
        select(EntireTrailOutboxTable.id)
        .where(
            EntireTrailOutboxTable.root_task_id == root_task_id,
            EntireTrailOutboxTable.op == op,
            EntireTrailOutboxTable.dead.is_(False),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def finding_is_mirrored(session: AsyncSession, finding_id: UUID) -> bool:
    """Whether the ledger row was (or is queued to be) added to Entire."""
    if await session.get(EntireTrailFindingTable, finding_id) is not None:
        return True
    result = await session.execute(
        select(EntireTrailOutboxTable.id)
        .where(
            EntireTrailOutboxTable.op == OP_FINDING_ADD,
            EntireTrailOutboxTable.payload["finding_id"].astext == str(finding_id),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None
