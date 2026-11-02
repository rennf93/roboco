"""compose_prompt names run_sandbox_tests only when the sandboxes are armed."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from roboco.agents.factories._base import compose_prompt
from roboco.models import AgentRole, Team

_MARKER = "# Token Factory Sandboxes"


@pytest.mark.parametrize("role", [AgentRole.DEVELOPER, AgentRole.QA])
def test_layer_included_for_dev_and_qa_when_armed(role: AgentRole) -> None:
    with patch("roboco.config.settings.token_factory_sandboxes_enabled", True):
        prompt = compose_prompt(role, Team.BACKEND, f"probe-{role.value}")
    assert _MARKER in prompt
    assert "run_sandbox_tests(command=" in prompt


@pytest.mark.parametrize("role", list(AgentRole))
def test_layer_absent_for_every_role_when_disarmed(role: AgentRole) -> None:
    with patch("roboco.config.settings.token_factory_sandboxes_enabled", False):
        prompt = compose_prompt(role, None, f"probe-{role.value}")
    assert _MARKER not in prompt


@pytest.mark.parametrize(
    "role", [r for r in AgentRole if r not in (AgentRole.DEVELOPER, AgentRole.QA)]
)
def test_layer_absent_for_other_roles_when_armed(role: AgentRole) -> None:
    with patch("roboco.config.settings.token_factory_sandboxes_enabled", True):
        prompt = compose_prompt(role, None, f"probe-{role.value}")
    assert _MARKER not in prompt
