"""enforcement.channel_access coverage."""

from __future__ import annotations

from unittest.mock import patch

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


def test_validate_channel_access_wildcard_allows_anyone() -> None:
    """Line 72: '*' in allowed list grants access to anyone."""

    with patch(
        "roboco.enforcement.channel_access.CHANNEL_ACCESS",
        {"public-channel": {"read": ["*"], "write": [], "silent": []}},
    ):
        assert validate_channel_access("ghost-agent", "public-channel", "read") is True


def test_validate_channel_access_silent_observer_can_read() -> None:
    """Line 80: silent observers can read."""
    # backend-cell has 'auditor' as silent observer.
    assert validate_channel_access("auditor", "backend-cell", "read") is True


# ---------------------------------------------------------------------------
# Auditor is a silent, read-only observer — the catalog must not grant it
# write_roles on any channel. main-pm-board / board-private used to list the
# auditor in write_roles (legacy parity), which let the catalog-only
# enforcement path (the HTTP messaging route -> validate_channel_access)
# authorize an auditor write that the say/dm guard would have blocked.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("channel", ["main-pm-board", "board-private"])
def test_validate_channel_access_auditor_cannot_write_management_channels(
    channel: str,
) -> None:
    """Auditor must not be in write_roles for any channel — silent observer."""
    with pytest.raises(ChannelAccessDeniedError):
        validate_channel_access("auditor", channel, "write")


@pytest.mark.parametrize("channel", ["main-pm-board", "board-private"])
def test_validate_channel_access_auditor_can_still_read_management_channels(
    channel: str,
) -> None:
    """Removing write access must not regress the auditor's silent read."""
    assert validate_channel_access("auditor", channel, "read") is True


def test_validate_channel_access_main_pm_still_writes_main_pm_board() -> None:
    """The legitimate writers (main-pm / board / ceo) are untouched."""
    assert validate_channel_access("main-pm", "main-pm-board", "write") is True
    assert validate_channel_access("ceo", "board-private", "write") is True
