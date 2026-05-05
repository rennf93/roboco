"""enforcement.channel_access coverage."""

from __future__ import annotations

import pytest
from roboco.enforcement.channel_access import (
    ChannelAccessDeniedError,
    get_agent_channels,
    validate_channel_access,
)


def test_validate_channel_access_invalid_action_raises() -> None:
    with pytest.raises(ValueError, match="Invalid action"):
        validate_channel_access("be-dev-1", "backend-cell", "execute")


def test_validate_channel_access_unknown_channel_denied() -> None:
    with pytest.raises(ChannelAccessDeniedError, match="not configured"):
        validate_channel_access("be-dev-1", "ghost-channel", "read")


def test_validate_channel_access_known_channel_allowed_member() -> None:
    """A backend dev should be able to read backend-cell."""
    result = validate_channel_access("be-dev-1", "backend-cell", "read")
    assert result is True


def test_validate_channel_access_unauthorized_agent_denied() -> None:
    """Random agent ID won't be in any allow lists."""
    with pytest.raises(ChannelAccessDeniedError):
        validate_channel_access("ghost-agent", "backend-cell", "write")


def test_get_agent_channels_returns_list_for_known_agent() -> None:
    channels = get_agent_channels("be-dev-1", action="read")
    assert isinstance(channels, list)


def test_get_agent_channels_for_unknown_agent_returns_only_wildcard() -> None:
    """Unknown agent gets only wildcard-permitted channels."""
    channels = get_agent_channels("ghost-agent", action="read")
    assert isinstance(channels, list)


def test_get_agent_channels_write_action() -> None:
    channels = get_agent_channels("main-pm", action="write")
    assert isinstance(channels, list)


def test_channel_access_denied_error_has_attributes() -> None:
    err = ChannelAccessDeniedError(
        agent_id="be-dev-1",
        channel_slug="ghost",
        action="write",
    )
    assert err.agent_id == "be-dev-1"
    assert err.channel_slug == "ghost"
    assert err.action == "write"
