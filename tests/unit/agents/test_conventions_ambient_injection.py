"""compose_prompt appends the architectural-standard ambient layer when given."""

from __future__ import annotations

from roboco.agents.factories._base import compose_prompt
from roboco.models import AgentRole, Team

_AMBIENT = "## Architectural Standard\n- `app/routers`: HTTP routes"


def test_ambient_layer_included_when_provided() -> None:
    prompt = compose_prompt(
        AgentRole.DEVELOPER, Team.BACKEND, "be-dev-1", ambient=_AMBIENT
    )
    assert "## Architectural Standard" in prompt


def test_ambient_absent_when_none() -> None:
    prompt = compose_prompt(AgentRole.DEVELOPER, Team.BACKEND, "be-dev-1")
    assert "## Architectural Standard" not in prompt


def test_empty_ambient_not_injected() -> None:
    prompt = compose_prompt(AgentRole.DEVELOPER, Team.BACKEND, "be-dev-1", ambient="")
    assert "## Architectural Standard" not in prompt
