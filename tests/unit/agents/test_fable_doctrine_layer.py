"""compose_prompt includes the Fable doctrine layer when the flag is on."""

from __future__ import annotations

from unittest.mock import patch

from roboco.agents.factories._base import compose_prompt
from roboco.models import AgentRole, Team


def test_doctrine_included_when_flag_enabled() -> None:
    with patch("roboco.config.settings.fable_mode_enabled", True):
        prompt = compose_prompt(AgentRole.DEVELOPER, Team.BACKEND, "be-dev-1")
    assert "# Fable Doctrine" in prompt
    assert "Turn discipline" in prompt


def test_doctrine_absent_when_flag_disabled() -> None:
    with patch("roboco.config.settings.fable_mode_enabled", False):
        prompt = compose_prompt(AgentRole.DEVELOPER, Team.BACKEND, "be-dev-1")
    assert "# Fable Doctrine" not in prompt


def test_doctrine_frontmatter_not_leaked() -> None:
    with patch("roboco.config.settings.fable_mode_enabled", True):
        prompt = compose_prompt(AgentRole.DEVELOPER, Team.BACKEND, "be-dev-1")
    assert "keep-coding-instructions" not in prompt


def test_doctrine_applies_to_every_role() -> None:
    with patch("roboco.config.settings.fable_mode_enabled", True):
        for role in AgentRole:
            prompt = compose_prompt(role, None, f"probe-{role.value}")
            assert "# Fable Doctrine" in prompt, f"missing for role={role.value}"
