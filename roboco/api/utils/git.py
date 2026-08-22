"""
Git Route Helpers

Route-glue helpers backing roboco/api/routes/git.py.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.exceptions import GitCommandError, GitError, GitTimeoutError
from roboco.services.base import (
    NotFoundError,
    ServiceError,
    UnauthorizedError,
    ValidationError,
)
from roboco.services.project import get_project_service

# Cap an unbounded whole-file read so a huge file can't flood the panel.
_FILE_MAX_LINES = 2000


def _compute_file_range(
    *,
    total: int,
    line: int | None,
    context: int,
    start: int | None,
    end: int | None,
) -> tuple[int, int, bool]:
    """Resolve the (start, end, truncated) slice for a file-content read.

    Explicit ``start``/``end`` win; else ``line`` centers a context window;
    else the whole file. Whichever branch resolves the window, it is capped
    at ``_FILE_MAX_LINES`` lines afterward. Returns 1-based inclusive
    [start, end] and whether the slice is shorter than the file.
    """
    if start is not None and end is not None:
        s, e_ = start, end
    elif line is not None:
        s = max(1, line - context)
        e_ = min(total, line + context)
    else:
        s, e_ = 1, total

    s = max(1, min(s, total))
    e_ = max(s, min(e_, total))

    truncated = e_ < total
    if e_ - s + 1 > _FILE_MAX_LINES:
        e_ = s + _FILE_MAX_LINES - 1
        truncated = True
    return s, e_, truncated


# Ordered (exception type, HTTP status) pairs — first match wins. Kept as a
# lookup table rather than an if/elif chain so _translate_error stays a
# single, low-complexity dispatch (mirrors roboco/api/utils/pitch.py's
# _SERVICE_ERROR_HTTP).
_ERROR_STATUS_MAP: tuple[tuple[type[Exception], int], ...] = (
    (NotFoundError, status.HTTP_404_NOT_FOUND),
    (UnauthorizedError, status.HTTP_403_FORBIDDEN),
    (ValidationError, status.HTTP_400_BAD_REQUEST),
    (GitTimeoutError, status.HTTP_504_GATEWAY_TIMEOUT),
    (GitCommandError, status.HTTP_500_INTERNAL_SERVER_ERROR),
)


def _translate_error(e: ServiceError | GitError) -> HTTPException:
    """Translate service errors to HTTP exceptions."""
    for exc_type, code in _ERROR_STATUS_MAP:
        if isinstance(e, exc_type):
            return HTTPException(status_code=code, detail=e.message)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=e.message
    )


async def _bounded_git_stage[T](
    coro: Coroutine[Any, Any, T], *, timeout: float, stage: str
) -> T:
    """Await ``coro`` bounded by ``timeout``; fail closed with a stage-named 504.

    Sibling to ``evidence_legs.run_bounded_leg`` but for a route that must
    FAIL CLOSED (no soft degrade): both timeout shapes — asyncio's own
    ``TimeoutError`` from ``wait_for``'s own cancellation, and
    ``GitTimeoutError`` raised from inside ``GitService._run_git``'s own
    internal subprocess bound — translate to the same stage-naming 504
    instead of a bare/generic message. Cancelling a thread-backed git call
    only stops AWAITING it, not necessarily the underlying subprocess — the
    same caveat evidence_legs.py documents for its own soft-degrading legs.
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except (TimeoutError, GitTimeoutError) as e:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"git diff timed out during {stage} after {timeout:g}s",
        ) from e


async def _resolve_project_slug(identifier: str, db: AsyncSession) -> str:
    """Resolve a project identifier (UUID string or slug) to its slug.

    Callers pass whatever string they have — a human-readable slug like
    "roboco" or a UUID like "3fa85f64-5717-4562-b3fc-2c963f66afa6".
    We try UUID first; if the string is not a valid UUID we treat it as
    a slug directly.  In both cases we verify the project exists and
    return the canonical slug so downstream git-service calls work.
    """
    service = get_project_service(db)
    try:
        uuid = UUID(identifier)
        project = await service.get(uuid)
    except ValueError:
        project = await service.get_by_slug(identifier)

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project not found: {identifier}",
        )
    return str(project.slug)


def _parse_remote_ref(
    ref: str, last_commit: str | None
) -> tuple[str, bool, str | None] | None:
    """Classify a `refs/remotes/...` ref, or None for the origin/HEAD pointer."""
    _remote_name, _, name = ref.removeprefix("refs/remotes/").partition("/")
    if not name or name == "HEAD":
        return None  # origin/HEAD is a symbolic pointer, not a branch
    return name, True, last_commit


def _parse_branch_line(line: str) -> tuple[str, bool, str | None] | None:
    """Classify one `%(refname)|%(objectname:short)` line as (name, is_remote,
    last_commit), or None for skippable entries (blank, origin/HEAD, other ref
    namespaces). Full refname, not `:short` — a remote-tracking ref shortens to
    `origin/<branch>`, indistinguishable from a local branch literally named
    that; classify on the `refs/heads/` vs `refs/remotes/` prefix instead.
    """
    if not line:
        return None
    parts = line.split("|")
    ref = parts[0]
    last_commit = parts[1] if len(parts) > 1 else None
    if ref.startswith("refs/heads/"):
        return ref.removeprefix("refs/heads/"), False, last_commit
    if ref.startswith("refs/remotes/"):
        return _parse_remote_ref(ref, last_commit)
    return None
