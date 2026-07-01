"""Spawn preflight (Phase 3): flag-gated refusal of a non-gateway delivery role
that would respawn on the same task forever."""

from __future__ import annotations

from unittest.mock import patch

from roboco.config import settings
from roboco.runtime.orchestrator import AgentOrchestrator


def test_reason_none_when_flag_disabled() -> None:
    with (
        patch.object(settings, "spawn_preflight_enabled", False),
        patch(
            "roboco.runtime.orchestrator.GATEWAY_ENABLED_ROLES",
            frozenset({"developer"}),
        ),
    ):
        assert AgentOrchestrator._spawn_preflight_reason("be-qa") is None


def test_reason_set_for_non_gateway_role_when_enabled() -> None:
    with (
        patch.object(settings, "spawn_preflight_enabled", True),
        patch(
            "roboco.runtime.orchestrator.GATEWAY_ENABLED_ROLES",
            frozenset({"developer"}),
        ),
    ):
        reason = AgentOrchestrator._spawn_preflight_reason("be-qa")
        assert reason is not None
        assert "gateway-enabled" in reason


def test_reason_none_for_gateway_role_when_enabled() -> None:
    # be-dev-1 → developer, which is in the real GATEWAY_ENABLED_ROLES.
    with patch.object(settings, "spawn_preflight_enabled", True):
        assert AgentOrchestrator._spawn_preflight_reason("be-dev-1") is None
