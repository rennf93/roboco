"""Verify journaling-scope consumers derive from foundation."""

from __future__ import annotations

from roboco.foundation.policy import journaling
from roboco.services.gateway import content_actions


def test_content_actions_valid_scopes_match_foundation() -> None:
    foundation_values = {s.value for s in journaling.Scope}
    config_values = set(content_actions._VALID_NOTE_SCOPES)
    assert config_values == foundation_values, (
        f"_VALID_NOTE_SCOPES drift: {config_values ^ foundation_values}"
    )
