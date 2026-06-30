"""utils.converters coverage."""

from __future__ import annotations

from uuid import uuid4

import pytest
from roboco.utils.converters import (
    InvalidIdentifierError,
    require_uuid,
    to_python_uuid,
    to_python_uuid_list,
)


def test_require_uuid_passes_through() -> None:
    u = uuid4()
    assert require_uuid(u) is u


def test_require_uuid_parses_string() -> None:
    u = uuid4()
    assert require_uuid(str(u)) == u


def test_require_uuid_raises_for_none() -> None:
    with pytest.raises(ValueError, match="cannot be None"):
        require_uuid(None)


def test_require_uuid_none_raises_typed_identifier_error() -> None:
    """#25: a None identifier raises the typed InvalidIdentifierError (a
    ValueError subclass), not a bare ValueError, so callers can distinguish a
    bad identifier from any other exception instead of broad-catching."""
    with pytest.raises(InvalidIdentifierError, match="cannot be None"):
        require_uuid(None)


def test_require_uuid_unparseable_raises_typed_identifier_error() -> None:
    """#25: an unparseable identifier raises InvalidIdentifierError, not a bare
    ValueError — the dropped-identifier pattern must surface as a typed error."""
    with pytest.raises(InvalidIdentifierError, match="invalid UUID identifier"):
        require_uuid("not-a-uuid")


def test_invalid_identifier_error_is_value_error() -> None:
    """Back-compat: InvalidIdentifierError subclasses ValueError so existing
    `except ValueError` callers keep catching it."""
    assert issubclass(InvalidIdentifierError, ValueError)


def test_to_python_uuid_returns_none_for_none() -> None:
    assert to_python_uuid(None) is None


def test_to_python_uuid_passes_through_uuid() -> None:
    u = uuid4()
    assert to_python_uuid(u) is u


def test_to_python_uuid_parses_string() -> None:
    u = uuid4()
    assert to_python_uuid(str(u)) == u


def test_to_python_uuid_list_returns_empty_for_none() -> None:
    assert to_python_uuid_list(None) == []


def test_to_python_uuid_list_converts_strings() -> None:
    u1, u2 = uuid4(), uuid4()
    result = to_python_uuid_list([str(u1), str(u2)])
    assert result == [u1, u2]


def test_to_python_uuid_list_empty() -> None:
    assert to_python_uuid_list([]) == []
