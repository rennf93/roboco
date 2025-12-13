"""
Type Converters

Utilities for converting between SQLAlchemy and Python types.
"""

from typing import Any
from uuid import UUID as PythonUUID


def require_uuid(value: Any) -> PythonUUID:
    """
    Convert SQLAlchemy UUID to Python UUID, raising if None.

    Args:
        value: UUID value from SQLAlchemy

    Returns:
        Python UUID

    Raises:
        ValueError: If value is None or cannot be converted
    """
    if value is None:
        raise ValueError("UUID value cannot be None")
    if isinstance(value, PythonUUID):
        return value
    return PythonUUID(str(value))


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
