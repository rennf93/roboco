"""Drain the trails outbox into the Entire CLI, in order per tree.

The orchestrator's loop (``roboco/runtime/engines/entire_trails.py``) calls
``drain_due`` every ``entire_trails_drain_interval_seconds``. Each row runs in
its own transaction under its tree's advisory lock, so the loop and the
inline paths below never work on one tree at once, and is deleted once it
succeeds. A failure backs off exponentially; after
``entire_trails_max_attempts`` the row is marked dead, which holds its
tree's queue (a later row may need it: a finding needs its branch's link),
and the CEO is alerted once.

Each pass first reconciles the trails themselves (``reconcile_trails``): a
trail whose tree's tasks changed since its last sync gets one refresh queued
(plans, new subtasks and status changes all show in its text), and a trail
whose top task has ended gets its close queued.

Two operations also run inline, on the request that needs them, because
their order against GitHub matters: a branch's link before its first PR
opens (``link_before_pr``: linked after, Entire opens a draft PR of its own),
and the CEO's approval before RoboCo merges the root PR
(``approve_before_merge``: Entire pins an approval to the head it approves).
Neither raises into its caller; a failed inline call is queued for the loop.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select

from roboco.config import settings
from roboco.db.base import get_db_context
from roboco.db.tables import (
    EntireTrailChangeTable,
    EntireTrailFindingTable,
    EntireTrailOutboxTable,
    EntireTrailTable,
    TaskTable,
)
from roboco.logging import get_logger
from roboco.models.base import TaskStatus
from roboco.models.notification import CreateNotificationParams
from roboco.services.entire_credentials import get_entire_credentials_service
from roboco.services.entire_trails.cli import (
    EntireCliError,
    EntireCliResult,
    run_entire,
)
from roboco.services.entire_trails.outbox import (
    OP_APPROVE,
    OP_CLOSE_TRAIL,
    OP_FINDING_ADD,
    OP_FINDING_DISMISS,
    OP_FINDING_RESOLVE,
    OP_FINDING_UPDATE,
    OP_LINK,
    OP_SYNC_TRAIL,
    enqueue,
    entire_repository,
    pending_op_exists,
    starts_tree,
    tree_top,
)
from roboco.services.entire_trails.render import TreeRow, trail_fields
from roboco.utils.converters import require_uuid

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# The trails bridge's advisory-lock namespace ("ENTR"); the second key is
# taken from the tree's root id.
_LOCK_CLASS = 0x454E5452
_BACKOFF_BASE_SECONDS = 30
_BACKOFF_CAP_SECONDS = 3600
_ERROR_CAP = 2000
_MAX_TREE_DEPTH = 6
_FINDINGS_PAGE = 100
_TERMINAL = frozenset({TaskStatus.COMPLETED, TaskStatus.CANCELLED})


class TrailNotReadyError(RuntimeError):
    """A row's prerequisite (its branch's link, its finding's add) has not
    happened yet; the row backs off like any failure."""


def _lock_key(root_id: UUID) -> int:
    return int.from_bytes(root_id.bytes[:4], "big", signed=True)


async def _lock_tree(session: AsyncSession, root_id: UUID, *, wait: bool) -> bool:
    if wait:
        await session.execute(
            select(func.pg_advisory_xact_lock(_LOCK_CLASS, _lock_key(root_id)))
        )
        return True
    result = await session.execute(
        select(func.pg_try_advisory_xact_lock(_LOCK_CLASS, _lock_key(root_id)))
    )
    return bool(result.scalar())


async def _tree_rows(session: AsyncSession, root: TaskTable) -> list[TreeRow]:
    """Every task under ``root``, depth first, in sequence order."""
    children: dict[object, list[TaskTable]] = {}
    level = [root.id]
    for _ in range(_MAX_TREE_DEPTH):
        if not level:
            break
        result = await session.execute(
            select(TaskTable)
            .where(TaskTable.parent_task_id.in_(level))
            .order_by(TaskTable.sequence, TaskTable.created_at)
        )
        found = list(result.scalars().all())
        for task in found:
            children.setdefault(task.parent_task_id, []).append(task)
        level = [task.id for task in found]
    rows: list[TreeRow] = []

    def walk(parent_id: object, depth: int) -> None:
        for child in children.get(parent_id, []):
            rows.append(TreeRow(task=child, depth=depth))
            walk(child.id, depth + 1)

    walk(root.id, 0)
    return rows


def _field_args(fields: dict[str, str]) -> list[str]:
    return [
        f"--{name}={fields[name]}" for name in ("title", "body", "type", "priority")
    ]


class _Context:
    """One row's run: its session, the token, and its tree's trail."""

    def __init__(self, session: AsyncSession, token: str, trail: EntireTrailTable):
        self.session = session
        self.token = token
        self.trail = trail

    @property
    def number(self) -> str:
        return str(self.trail.trail_number)

    def change_args(self, repository: str, branch: str) -> list[str]:
        return [f"--repo={repository}", f"--branch={branch}"]

    async def entire(self, *args: str) -> EntireCliResult:
        return await run_entire(args, token=self.token)

    async def ensure_trail(self) -> bool:
        """Create the tree's trail unless it exists; True when created."""
        if self.trail.trail_number is not None:
            return False
        result = await self.entire(
            "trail",
            "create",
            "--no-branch",
            f"--project={self.trail.entire_project}",
            *_field_args(self.trail.create_fields),
            f"--idempotency-key=roboco-trail:{self.trail.root_task_id}",
            "--json",
        )
        data = result.json()
        self.trail.trail_id = str(data["id"])
        self.trail.trail_number = int(data["number"])
        self.trail.trail_url = str(data["url"])
        await self.session.flush()
        return True

    async def current_fields(self, *, closing: bool = False) -> dict[str, str] | None:
        root = await self.session.get(TaskTable, self.trail.root_task_id)
        if root is None:
            return None
        note = None
        if closing:
            when = (root.completed_at or datetime.now(UTC)).date().isoformat()
            note = f"The task ended {root.status} on {when}."
        rows = await _tree_rows(self.session, root)
        return trail_fields(root, rows, closing_note=note)


async def _sync_trail(ctx: _Context, payload: dict[str, Any], *, created: bool) -> None:
    _ = payload
    rendered_at = datetime.now(UTC)
    fields = await ctx.current_fields()
    if fields is None:
        return
    if not (created and fields == ctx.trail.create_fields):
        await ctx.entire(
            "trail",
            "update",
            ctx.number,
            f"--project={ctx.trail.entire_project}",
            *_field_args(fields),
            "--json",
        )
    ctx.trail.synced_at = rendered_at


async def _close_trail(ctx: _Context, payload: dict[str, Any]) -> None:
    _ = payload
    fields = await ctx.current_fields(closing=True)
    args = _field_args(fields) if fields is not None else []
    await ctx.entire(
        "trail",
        "update",
        ctx.number,
        f"--project={ctx.trail.entire_project}",
        *args,
        "--status=closed",
        "--json",
    )
    ctx.trail.closed_at = ctx.trail.synced_at = datetime.now(UTC)


async def _change_number(ctx: _Context, repository: str, branch: str) -> int | None:
    shown = await ctx.entire(
        "trail", "show", ctx.number, f"--project={ctx.trail.entire_project}", "--json"
    )
    repo_name = repository.rsplit("/", 1)[-1]
    for change in shown.json().get("changes") or []:
        if change.get("branch") == branch and change.get("repository") == repo_name:
            return int(change["number"])
    return None


async def _link(ctx: _Context, payload: dict[str, Any]) -> None:
    repository, branch = str(payload["repository"]), str(payload["branch"])
    existing = await _change_row(ctx.session, repository, branch)
    if existing is not None:
        return
    await ctx.entire(
        "trail",
        "link",
        ctx.number,
        f"--project={ctx.trail.entire_project}",
        *ctx.change_args(repository, branch),
        f"--base={payload['base']}",
        f"--idempotency-key=roboco-link:{ctx.trail.root_task_id}:{repository}:{branch}",
        "--json",
    )
    task_id = payload.get("task_id")
    ctx.session.add(
        EntireTrailChangeTable(
            root_task_id=ctx.trail.root_task_id,
            task_id=task_id,
            repository=repository,
            branch=branch,
            base=str(payload["base"]),
            change_number=await _change_number(ctx, repository, branch),
        )
    )
    await ctx.session.flush()


async def _change_row(
    session: AsyncSession, repository: str, branch: str
) -> EntireTrailChangeTable | None:
    result = await session.execute(
        select(EntireTrailChangeTable).where(
            EntireTrailChangeTable.repository == repository,
            EntireTrailChangeTable.branch == branch,
        )
    )
    return result.scalar_one_or_none()


async def _find_by_client_id(
    ctx: _Context, repository: str, branch: str, client_id: str
) -> str | None:
    """The Entire id of the finding added under ``client_id``, if any (a
    retry past Entire's 24 h client-id window must not add it twice)."""
    cursor: str | None = None
    while True:
        args = [
            "trail",
            "finding",
            "list",
            ctx.number,
            *ctx.change_args(repository, branch),
            "--status=any",
            "--freshness=any",
            "--include-dismissed",
            f"--limit={_FINDINGS_PAGE}",
            "--json",
        ]
        if cursor:
            args.append(f"--cursor={cursor}")
        page = (await ctx.entire(*args)).json()
        for finding in page.get("findings") or []:
            if finding.get("clientId") == client_id:
                return str(finding["id"])
        cursor = page.get("next_cursor") or page.get("nextCursor")
        if not page.get("has_more") or not cursor:
            return None


async def _finding_add(ctx: _Context, payload: dict[str, Any], *, retry: bool) -> None:
    finding_id = str(payload["finding_id"])
    if await ctx.session.get(EntireTrailFindingTable, finding_id) is not None:
        return
    repository, branch = str(payload["repository"]), str(payload["branch"])
    if await _change_row(ctx.session, repository, branch) is None:
        raise TrailNotReadyError(
            f"{repository} {branch} is not linked to the trail yet"
        )
    entire_id = (
        await _find_by_client_id(ctx, repository, branch, finding_id) if retry else None
    )
    if entire_id is None:
        location = []
        if payload.get("file"):
            location.append(f"--file={payload['file']}")
            if payload.get("line"):
                location.append(f"--line={payload['line']}")
        added = await ctx.entire(
            "trail",
            "finding",
            "add",
            ctx.number,
            *ctx.change_args(repository, branch),
            f"--severity={payload['severity']}",
            f"--body={payload['body']}",
            *location,
            f"--client-id={finding_id}",
            "--json",
        )
        entire_id = str(added.json()["id"])
    ctx.session.add(
        EntireTrailFindingTable(
            review_finding_id=finding_id,
            entire_finding_id=entire_id,
            repository=repository,
            branch=branch,
        )
    )
    await ctx.session.flush()


async def _mapped_finding(
    ctx: _Context, payload: dict[str, Any]
) -> EntireTrailFindingTable:
    mapped = await ctx.session.get(EntireTrailFindingTable, str(payload["finding_id"]))
    if mapped is None:
        raise TrailNotReadyError(
            f"finding {payload['finding_id']} is not on Entire yet"
        )
    return mapped


async def _finding_update(ctx: _Context, payload: dict[str, Any]) -> None:
    mapped = await _mapped_finding(ctx, payload)
    await ctx.entire(
        "trail",
        "finding",
        "update",
        ctx.number,
        mapped.entire_finding_id,
        *ctx.change_args(mapped.repository, mapped.branch),
        f"--body={payload['body']}",
        "--json",
    )


async def _finding_status(ctx: _Context, payload: dict[str, Any], verb: str) -> None:
    mapped = await _mapped_finding(ctx, payload)
    await ctx.entire(
        "trail",
        "finding",
        verb,
        ctx.number,
        mapped.entire_finding_id,
        *ctx.change_args(mapped.repository, mapped.branch),
        f"--message={payload['message']}",
    )


async def _approve(ctx: _Context, payload: dict[str, Any]) -> None:
    await ctx.entire(
        "trail",
        "approve",
        ctx.number,
        *ctx.change_args(str(payload["repository"]), str(payload["branch"])),
        f"--message={payload['message']}",
    )


async def _run_op(
    ctx: _Context, op: str, payload: dict[str, Any], *, retry: bool
) -> None:
    created = await ctx.ensure_trail()
    handlers: dict[str, Callable[[], Awaitable[None]]] = {
        OP_SYNC_TRAIL: lambda: _sync_trail(ctx, payload, created=created),
        OP_CLOSE_TRAIL: lambda: _close_trail(ctx, payload),
        OP_LINK: lambda: _link(ctx, payload),
        OP_FINDING_ADD: lambda: _finding_add(ctx, payload, retry=retry),
        OP_FINDING_UPDATE: lambda: _finding_update(ctx, payload),
        OP_FINDING_RESOLVE: lambda: _finding_status(ctx, payload, "resolve"),
        OP_FINDING_DISMISS: lambda: _finding_status(ctx, payload, "dismiss"),
        OP_APPROVE: lambda: _approve(ctx, payload),
    }
    handler = handlers.get(op)
    if handler is None:
        raise TrailNotReadyError(f"unknown trails op {op!r}")
    await handler()


def _backoff(attempts: int) -> timedelta:
    seconds = min(
        _BACKOFF_BASE_SECONDS * 2 ** max(attempts - 1, 0), _BACKOFF_CAP_SECONDS
    )
    return timedelta(seconds=seconds)


async def _alert_ceo(session: AsyncSession, row: EntireTrailOutboxTable) -> None:
    """One ack-required CEO notification per row that goes dead."""
    from roboco.models.base import NotificationPriority, NotificationType
    from roboco.services.notification import NotificationService

    try:
        async with session.begin_nested():
            await NotificationService()._create_notification(
                CreateNotificationParams(
                    notification_type=NotificationType.ALERT,
                    priority=NotificationPriority.HIGH,
                    from_agent="system",
                    to_agents=["ceo"],
                    subject="Entire trails: an operation stopped retrying",
                    body=(
                        f"The Entire trails bridge gave up on '{row.op}' for task "
                        f"tree {row.root_task_id} after {row.attempts} attempts; "
                        "that tree's later trail updates wait behind it. Last "
                        f"error: {(row.last_error or '')[:500]}. Fix the cause, "
                        "then retry it from Settings -> Feature Flags -> Entire "
                        "trails."
                    ),
                    requires_ack=True,
                    bypass_purpose_dedup=True,
                ),
                db_session=session,
            )
    except Exception as exc:
        logger.warning("entire trails: CEO alert not sent", error=str(exc))


async def _record_failure(
    session: AsyncSession, row: EntireTrailOutboxTable, error: Exception
) -> None:
    await session.refresh(row)
    row.attempts += 1
    row.last_error = str(error)[:_ERROR_CAP]
    if row.attempts >= settings.entire_trails_max_attempts:
        row.dead = True
        await _alert_ceo(session, row)
    else:
        row.next_attempt_at = datetime.now(UTC) + _backoff(row.attempts)
    logger.warning(
        "entire trails: operation failed",
        op=row.op,
        root_task_id=str(row.root_task_id),
        attempts=row.attempts,
        dead=row.dead,
        error=row.last_error,
    )


async def _run_row(
    session: AsyncSession, token: str, row: EntireTrailOutboxTable
) -> bool:
    """Run one outbox row in the caller's transaction; delete it on success,
    record the failure otherwise. True when it succeeded."""
    trail = await session.get(EntireTrailTable, row.root_task_id)
    if trail is None:
        await session.delete(row)
        return True
    try:
        async with session.begin_nested():
            await _run_op(
                _Context(session, token, trail),
                row.op,
                row.payload,
                retry=row.attempts > 0,
            )
    except Exception as exc:
        await _record_failure(session, row, exc)
        return False
    await session.delete(row)
    return True


async def _token(session: AsyncSession) -> str | None:
    return await get_entire_credentials_service(session).get_decrypted()


async def _drain_tree(root_id: UUID, token: str) -> int:
    done = 0
    while True:
        async with get_db_context(pool="background") as session:
            if not await _lock_tree(session, root_id, wait=False):
                return done
            result = await session.execute(
                select(EntireTrailOutboxTable)
                .where(EntireTrailOutboxTable.root_task_id == root_id)
                .order_by(EntireTrailOutboxTable.id)
                .limit(1)
            )
            row = result.scalar_one_or_none()
            if row is None or row.dead or row.next_attempt_at > datetime.now(UTC):
                return done
            succeeded = await _run_row(session, token, row)
        if not succeeded:
            return done
        done += 1


def _changed_at(task: TaskTable) -> datetime:
    return task.updated_at or task.created_at


async def _reconcile_op(session: AsyncSession, trail: EntireTrailTable) -> str | None:
    """The trail-level op an open trail needs now, if any."""
    if await pending_op_exists(session, trail.root_task_id, OP_CLOSE_TRAIL):
        return None
    root = await session.get(TaskTable, trail.root_task_id)
    if root is None:
        return None
    if root.status in _TERMINAL:
        return OP_CLOSE_TRAIL
    if trail.synced_at is None:
        return OP_SYNC_TRAIL
    rows = await _tree_rows(session, root)
    changed = max(_changed_at(task) for task in [root, *(row.task for row in rows)])
    return OP_SYNC_TRAIL if changed > trail.synced_at else None


async def reconcile_trails() -> int:
    """Queue a refresh for every open trail whose tree changed since its last
    sync, and the close of every trail whose top task ended. Returns the
    rows queued (one live row per trail and op at most)."""
    queued = 0
    async with get_db_context(pool="background") as session:
        result = await session.execute(
            select(EntireTrailTable).where(EntireTrailTable.closed_at.is_(None))
        )
        for trail in result.scalars().all():
            op = await _reconcile_op(session, trail)
            if op is None or await pending_op_exists(session, trail.root_task_id, op):
                continue
            enqueue(session, trail.root_task_id, op, {})
            queued += 1
    return queued


async def drain_due() -> int:
    """One pass: reconcile the open trails, then drain every tree with a due
    row. Returns the rows done; nothing runs without a token."""
    async with get_db_context(pool="background") as session:
        token = await _token(session)
    if token is None:
        return 0
    await reconcile_trails()
    async with get_db_context(pool="background") as session:
        result = await session.execute(
            select(EntireTrailOutboxTable.root_task_id)
            .where(
                EntireTrailOutboxTable.dead.is_(False),
                EntireTrailOutboxTable.next_attempt_at <= datetime.now(UTC),
            )
            .group_by(EntireTrailOutboxTable.root_task_id)
            .order_by(func.min(EntireTrailOutboxTable.id))
        )
        roots = list(result.scalars().all())
    done = 0
    for root_id in roots:
        done += await _drain_tree(root_id, token)
    return done


async def _inline_target(task_id: UUID, project: Any) -> tuple[UUID, str] | None:
    """(tree root, repository) when the task's tree is bridged and its
    project opted in; None otherwise."""
    if not settings.entire_trails_enabled:
        return None
    repository = entire_repository(project)
    if repository is None or not getattr(project, "entire_trails_enabled", False):
        return None
    async with get_db_context() as session:
        task = await session.get(TaskTable, task_id)
        if task is None:
            return None
        top = await tree_top(session, task)
        trail = await session.get(EntireTrailTable, top.id)
        if trail is None or trail.closed_at is not None:
            return None
        return require_uuid(top.id), repository


async def link_before_pr(
    task_id: UUID, project: Any, *, branch: str, base: str
) -> None:
    """Link ``branch`` to its tree's trail now, before its first PR opens.

    A dev branch's link was queued at claim; an integration branch's is made
    here. Never raises: a failure is queued for the loop and the PR opens
    anyway (Entire then gives it a trail of its own, and the queued link
    fails visibly)."""
    target = await _inline_target(task_id, project)
    if target is None:
        return
    root_id, repository = target
    payload = {
        "task_id": str(task_id),
        "repository": repository,
        "branch": branch,
        "base": base,
    }
    try:
        async with get_db_context() as session:
            await _lock_tree(session, root_id, wait=True)
            if await _change_row(session, repository, branch) is not None:
                return
            token = await _token(session)
            if token is None:
                raise EntireCliError("no Entire token is configured")
            trail = await session.get(EntireTrailTable, root_id)
            if trail is None:
                return
            await _run_op(
                _Context(session, token, trail), OP_LINK, payload, retry=False
            )
            await session.execute(
                delete(EntireTrailOutboxTable).where(
                    EntireTrailOutboxTable.op == OP_LINK,
                    EntireTrailOutboxTable.payload["repository"].astext == repository,
                    EntireTrailOutboxTable.payload["branch"].astext == branch,
                )
            )
    except Exception as exc:
        logger.warning(
            "entire trails: link before the PR failed; queued",
            branch=branch,
            error=str(exc),
        )
        await _queue_unless_pending(root_id, OP_LINK, payload)


async def approve_before_merge(
    task_id: UUID, project: Any, *, branch: str, message: str
) -> None:
    """Mirror the CEO's approval onto the root branch's change right before
    RoboCo merges it (Entire pins it to the head it approves). Never raises:
    a failure is queued for the loop."""
    target = await _inline_target(task_id, project)
    if target is None:
        return
    root_id, repository = target
    payload = {"repository": repository, "branch": branch, "message": message}
    try:
        async with get_db_context() as session:
            task = await session.get(TaskTable, task_id)
            top = await session.get(TaskTable, root_id)
            if task is None or top is None or not starts_tree(task, top):
                return
            await _lock_tree(session, root_id, wait=True)
            if await _change_row(session, repository, branch) is None:
                raise TrailNotReadyError(f"{repository} {branch} is not linked")
            token = await _token(session)
            if token is None:
                raise EntireCliError("no Entire token is configured")
            trail = await session.get(EntireTrailTable, root_id)
            if trail is None:
                return
            await _run_op(
                _Context(session, token, trail), OP_APPROVE, payload, retry=False
            )
    except Exception as exc:
        logger.warning(
            "entire trails: approval before the merge failed; queued",
            branch=branch,
            error=str(exc),
        )
        await _queue_unless_pending(root_id, OP_APPROVE, payload)


async def _queue_unless_pending(
    root_id: UUID, op: str, payload: dict[str, Any]
) -> None:
    try:
        async with get_db_context() as session:
            result = await session.execute(
                select(EntireTrailOutboxTable.id).where(
                    EntireTrailOutboxTable.root_task_id == root_id,
                    EntireTrailOutboxTable.op == op,
                    EntireTrailOutboxTable.payload["branch"].astext
                    == payload["branch"],
                )
            )
            if result.first() is None:
                enqueue(session, root_id, op, payload)
    except Exception as exc:
        logger.warning(
            "entire trails: could not queue the retry", op=op, error=str(exc)
        )
