"""
Type Converters

Utilities for converting between SQLAlchemy and Python types.
"""

from typing import Any
from uuid import UUID as PythonUUID


class InvalidIdentifierError(ValueError):
    """A malformed/None identifier reached UUID coercion.

    A ``ValueError`` subclass so existing ``except ValueError`` callers keep
    working, but typed so a caller can distinguish a bad identifier from any
    other exception instead of broad-catching and silently swallowing it
    (#25).
    """


def require_uuid(value: Any) -> PythonUUID:
    """
    Convert SQLAlchemy UUID to Python UUID, raising if None.

    Args:
        value: UUID value from SQLAlchemy

    Returns:
        Python UUID

    Raises:
        InvalidIdentifierError: If value is None or cannot be parsed as a UUID.
            A ``ValueError`` subclass, so existing ``except ValueError`` /
            ``except Exception`` callers are unaffected, but typed so callers
            can handle a bad identifier distinctly instead of swallowing it.
    """
    if value is None:
        raise InvalidIdentifierError("UUID value cannot be None")
    if isinstance(value, PythonUUID):
        return value
    try:
        return PythonUUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise InvalidIdentifierError(f"invalid UUID identifier: {value!r}") from exc


def repo_key(git_url: str) -> str:
    """Normalized repo identity — case / ``.git`` suffix / trailing-slash
    insensitive.

    Two projects registered with git_url strings that differ only by those
    accidentals are the SAME repo for ci_watch / dep_update dedupe (a monorepo
    often registers several cell-projects on one git_url, and a re-registered
    canonical project may carry a slightly different string). The orchestrator
    collapses its poll set by this key; the dedupe queries mirror it so the
    one-open-task-per-repo invariant holds across the accidentals (#1267).
    """
    return git_url.lower().rstrip("/").removesuffix(".git")


def to_python_uuid(value: Any) -> PythonUUID | None:
    """
    Convert SQLAlchemy UUID to Python UUID.

    Handles both SQLAlchemy UUID types and standard Python UUIDs.

    Args:
        value: UUID value from SQLAlchemy or None

    Returns:
        Python UUID or None
    """
    if value is None:
        return None
    if isinstance(value, PythonUUID):
        return value
    # Convert via string for SQLAlchemy UUID types
    return PythonUUID(str(value))


def compute_file_range(
    *,
    total: int,
    line: int | None,
    context: int,
    explicit_range: tuple[int, int] | None,
    max_lines: int,
) -> tuple[int, int, bool]:
    """Resolve the (start, end, truncated) slice for a file-content read.

    An explicit ``explicit_range`` (start, end) wins; else ``line`` centers a
    context window; else the whole file. Whichever branch resolves the
    window, it is capped at ``max_lines`` lines afterward. Returns 1-based
    inclusive [start, end] and whether the slice is shorter than the file.
    """
    if explicit_range is not None:
        s, e_ = explicit_range
    elif line is not None:
        s = max(1, line - context)
        e_ = min(total, line + context)
    else:
        s, e_ = 1, total

    s = max(1, min(s, total))
    e_ = max(s, min(e_, total))

    truncated = e_ < total
    if e_ - s + 1 > max_lines:
        e_ = s + max_lines - 1
        truncated = True
    return s, e_, truncated


def parse_branch_line(line: str) -> tuple[str, bool, str | None] | None:
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


def to_python_uuid_list(values: list[Any] | None) -> list[PythonUUID]:
    """
    Convert list of SQLAlchemy UUIDs to Python UUIDs.

    Args:
        values: List of UUID values from SQLAlchemy or None

    Returns:
        List of Python UUIDs (empty list if input is None)
    """
    if values is None:
        return []
    result: list[PythonUUID] = []
    for v in values:
        converted = to_python_uuid(v)
        if converted is not None:
            result.append(converted)
    return result
