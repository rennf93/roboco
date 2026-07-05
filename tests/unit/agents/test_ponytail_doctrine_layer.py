"""Role-scoped Ponytail doctrine composed when fable_mode_enabled is on.

Ponytail is bundled with Fable (no separate flag). Developers get the full
ladder; every other role gets the ethos-only cut (no ladder). Absent
entirely when the flag is off.
"""

from __future__ import annotations

from unittest.mock import patch

from roboco.agents.factories._base import compose_prompt
from roboco.config import settings
from roboco.models import AgentRole, Team


def test_doctrine_absent_when_flag_disabled() -> None:
    with patch("roboco.config.settings.fable_mode_enabled", False):
        prompt = compose_prompt(AgentRole.DEVELOPER, Team.BACKEND, "be-dev-1")
    assert "# Ponytail Doctrine" not in prompt


def test_full_ladder_for_developer() -> None:
    with patch("roboco.config.settings.fable_mode_enabled", True):
        prompt = compose_prompt(AgentRole.DEVELOPER, Team.BACKEND, "be-dev-1")
    assert "# Ponytail Doctrine" in prompt
    assert "## The ladder" in prompt


def test_ethos_only_for_non_developer() -> None:
    with patch("roboco.config.settings.fable_mode_enabled", True):
        prompt = compose_prompt(AgentRole.QA, Team.BACKEND, "be-qa-1")
    assert "# Ponytail Doctrine (ethos)" in prompt
    assert "## The ladder" not in prompt


def test_every_role_gets_some_doctrine() -> None:
    with patch("roboco.config.settings.fable_mode_enabled", True):
        for role in AgentRole:
            prompt = compose_prompt(role, None, f"probe-{role.value}")
            assert "Ponytail" in prompt, f"missing for role={role.value}"


def test_frontmatter_not_leaked() -> None:
    with patch("roboco.config.settings.fable_mode_enabled", True):
        prompt = compose_prompt(AgentRole.DEVELOPER, Team.BACKEND, "be-dev-1")
    assert "Forces the laziest solution" not in prompt


def test_intensity_injected_for_developer() -> None:
    with (
        patch("roboco.config.settings.fable_mode_enabled", True),
        patch("roboco.config.settings.ponytail_intensity", "ultra"),
    ):
        prompt = compose_prompt(AgentRole.DEVELOPER, Team.BACKEND, "be-dev-1")
    assert "Operative intensity: ultra" in prompt


def test_intensity_not_applied_to_non_developer() -> None:
    with (
        patch("roboco.config.settings.fable_mode_enabled", True),
        patch("roboco.config.settings.ponytail_intensity", "ultra"),
    ):
        prompt = compose_prompt(AgentRole.QA, Team.BACKEND, "be-qa-1")
    assert "Operative intensity" not in prompt  # ethos gets no dial


def test_intensity_config_default() -> None:
    assert settings.ponytail_intensity == "full"
