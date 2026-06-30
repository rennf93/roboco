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
