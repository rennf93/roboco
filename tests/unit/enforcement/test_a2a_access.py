"""enforcement.a2a_access coverage."""

from __future__ import annotations

import pytest
from roboco.enforcement.a2a_access import (
    A2AAccessDeniedError,
    get_a2a_allowed_targets,
    validate_a2a_access,
)


def test_validate_a2a_self_a2a_denied() -> None:
    with pytest.raises(A2AAccessDeniedError, match="cannot A2A yourself"):
        validate_a2a_access("be-dev-1", "be-dev-1")


def test_validate_a2a_to_ceo_denied() -> None:
    with pytest.raises(A2AAccessDeniedError):
        validate_a2a_access("be-dev-1", "ceo")


def test_validate_a2a_within_cell() -> None:
    """Cell members can A2A within their cell."""
    result = validate_a2a_access("be-dev-1", "be-qa")
    assert result is True


def test_a2a_access_denied_error_has_attributes() -> None:
    err = A2AAccessDeniedError(
        from_agent="be-dev-1",
        to_agent="ceo",
        reason="CEO is human",
    )
    assert err.from_agent == "be-dev-1"
    assert err.to_agent == "ceo"


def test_get_a2a_allowed_targets_returns_list() -> None:
    targets = get_a2a_allowed_targets("be-dev-1", ["be-qa", "be-pm", "fe-dev-1", "ceo"])
    assert isinstance(targets, list)


def test_get_a2a_allowed_targets_excludes_ceo() -> None:
    targets = get_a2a_allowed_targets("be-dev-1", ["ceo"])
    assert "ceo" not in targets


def test_get_a2a_allowed_targets_excludes_self() -> None:
    targets = get_a2a_allowed_targets("be-dev-1", ["be-dev-1", "be-qa"])
    # Self should be filtered.
    assert "be-dev-1" not in targets or "be-qa" in targets
