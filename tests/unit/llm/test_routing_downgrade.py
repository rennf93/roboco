"""#20/#3: ModelRoutingService silent-downgrade on a disabled configured provider.

A configured-but-disabled provider was indistinguishable from "no assignment":
``resolve_for_agent`` fell straight to the legacy Anthropic path with no signal,
so an operator who disabled a provider got no warning that spawns were bypassing
it. The fix surfaces the bypass with a warning (graceful degradation, but not
silent) and adds an opt-in ``ROBOCO_ROUTING_STRICT`` fail-closed for operators
who'd rather a misconfigured provider stall a spawn than run on the wrong one.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.models.base import ModelProvider
from roboco.services.llm import ModelRoutingService, _ResolvedAssignment

_AGENT_SLUG = "be-dev-1"


def _disabled_resolved() -> _ResolvedAssignment:
    provider = MagicMock(
        enabled=False, id="prov-disabled", type=ModelProvider.OLLAMA_CLOUD
    )
    return _ResolvedAssignment(provider=provider, model_name="grok-build")


def _svc() -> ModelRoutingService:
    svc = ModelRoutingService(MagicMock())
    # structlog's bound logger — swap for a mock so we can assert call sites.
    object.__setattr__(svc, "log", MagicMock())
    return svc


@pytest.mark.asyncio
async def test_resolve_for_agent_warns_on_disabled_configured_provider() -> None:
    """The disabled-provider bypass is surfaced (warning), not silent."""
    svc = _svc()
    with patch.object(
        svc, "_resolve_assignment", AsyncMock(return_value=_disabled_resolved())
    ):
        route = await svc.resolve_for_agent(_AGENT_SLUG)

    # Graceful degradation: falls through to the legacy Anthropic path.
    assert route.provider_type == ModelProvider.ANTHROPIC
    # The bypass is logged as a warning (not silent).
    svc.log.warning.assert_called_once()
    assert "disabled" in svc.log.warning.call_args.args[0].lower()


@pytest.mark.asyncio
async def test_resolve_for_agent_strict_raises_on_disabled_provider() -> None:
    """ROBOCO_ROUTING_STRICT refuses to silently downgrade — a disabled
    configured provider raises instead of running on the legacy path."""
    svc = _svc()
    with (
        patch.object(
            svc, "_resolve_assignment", AsyncMock(return_value=_disabled_resolved())
        ),
        patch("roboco.services.llm.settings", MagicMock(routing_strict=True)),
        pytest.raises(RuntimeError, match="routing_strict"),
    ):
        await svc.resolve_for_agent(_AGENT_SLUG)


@pytest.mark.asyncio
async def test_resolve_for_agent_no_assignment_is_silent_legacy_fallback() -> None:
    """A genuinely unassigned agent (no row at all) is the designed legacy
    fallback — that path must stay silent (no spurious 'disabled' warning),
    so the warning is specific to a configured-but-disabled provider."""
    svc = _svc()
    with patch.object(svc, "_resolve_assignment", AsyncMock(return_value=None)):
        route = await svc.resolve_for_agent(_AGENT_SLUG)

    assert route.provider_type == ModelProvider.ANTHROPIC
    svc.log.warning.assert_not_called()
