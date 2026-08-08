"""
Git Route Helpers

Route-glue helpers backing roboco/api/routes/git.py.
"""

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


def _translate_error(e: ServiceError | GitError) -> HTTPException:
    """Translate service errors to HTTP exceptions."""
    if isinstance(e, NotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=e.message)
    if isinstance(e, UnauthorizedError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=e.message)
    if isinstance(e, ValidationError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=e.message)
    if isinstance(e, GitTimeoutError):
        return HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=e.message
        )
    if isinstance(e, GitCommandError):
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=e.message
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=e.message
    )


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
        _remote_name, _, name = ref.removeprefix("refs/remotes/").partition("/")
        if not name or name == "HEAD":
            return None  # origin/HEAD is a symbolic pointer, not a branch
        return name, True, last_commit
    return None
