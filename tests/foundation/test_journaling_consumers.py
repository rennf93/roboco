"""Verify journaling-scope consumers derive from foundation."""

from __future__ import annotations

from roboco.foundation.policy import journaling
from roboco.services.gateway import content_actions
from roboco.services.journal import _SCOPE_TO_TYPE


def test_content_actions_valid_scopes_match_foundation() -> None:
    foundation_values = {s.value for s in journaling.Scope}
    config_values = set(content_actions._VALID_NOTE_SCOPES)
    assert config_values == foundation_values, (
        f"_VALID_NOTE_SCOPES drift: {config_values ^ foundation_values}"
    )


def test_journal_service_scope_to_type_matches_foundation() -> None:
    # Service uses string keys; foundation uses Scope enum keys.
    foundation_str_keys = {s.value: t for s, t in journaling.SCOPE_TO_TYPE.items()}
    assert foundation_str_keys == _SCOPE_TO_TYPE, (
        f"_SCOPE_TO_TYPE drift: "
        f"foundation={foundation_str_keys}, service={_SCOPE_TO_TYPE}"
    )
