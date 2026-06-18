"""The interactive-role routing guard keeps intake/secretary off GROK.

GROK has no interactive runtime yet, and the interactive spawn would inject the
route's creds as ANTHROPIC_* against api.x.ai/v1 (wrong protocol → silent empty
reply). Until the opencode interactive driver lands, a GROK route for those
slugs is downgraded to the Anthropic default. The one-shot delivery roles route
to GROK unchanged. DB-free: exercises `_guard_interactive` directly.
"""

from __future__ import annotations

import structlog
from roboco.models.base import ModelProvider
from roboco.services.llm import AgentRoute, ModelRoutingService


def _svc() -> ModelRoutingService:
    svc = ModelRoutingService.__new__(ModelRoutingService)
    svc.log = structlog.get_logger()
    return svc


def _grok_route() -> AgentRoute:
    return AgentRoute(
        provider_id=None,
        provider_type=ModelProvider.GROK,
        base_url="https://api.x.ai/v1",
        auth_token="xai-key",
        model_name="grok-build-0.1",
    )


def test_grok_interactive_role_downgrades_to_anthropic() -> None:
    svc = _svc()
    for slug in ("intake-1", "secretary-1"):
        route = svc._guard_interactive(_grok_route(), slug, "prompter")
        assert route.provider_type == ModelProvider.ANTHROPIC
        # No xAI creds leak onto the Claude SDK path.
        assert route.base_url is None
        assert route.auth_token is None


def test_grok_delivery_role_is_left_on_grok() -> None:
    svc = _svc()
    route = svc._guard_interactive(_grok_route(), "be-dev-1", "developer")
    assert route.provider_type == ModelProvider.GROK
    assert route.base_url == "https://api.x.ai/v1"


def test_non_grok_interactive_route_is_unchanged() -> None:
    svc = _svc()
    anthropic = AgentRoute(
        provider_id=None,
        provider_type=ModelProvider.ANTHROPIC,
        base_url=None,
        auth_token=None,
        model_name="claude-opus-4-6",
    )
    route = svc._guard_interactive(anthropic, "intake-1", "prompter")
    assert route is anthropic
