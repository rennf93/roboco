"""Unit tests for the Decisions tier resolver (spec section 1 chain)."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import roboco.config as cfg
from roboco.services.decisions import resolver
from roboco.services.settings import SettingsService


@pytest.fixture(autouse=True)
def _reset_resolver_state() -> Iterator[None]:
    resolver.reset_health_cache()
    yield
    resolver.reset_health_cache()


def _mock_session() -> MagicMock:
    return MagicMock()


def _flag_stack(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool = True,
    laya: bool = True,
    openrouter: bool = False,
) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", enabled)
    monkeypatch.setattr(cfg.settings, "decisions_tier_laya_enabled", laya)
    monkeypatch.setattr(cfg.settings, "decisions_tier_openrouter_enabled", openrouter)


@pytest.mark.asyncio
async def test_master_flag_off_resolves_to_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_stack(monkeypatch, enabled=False, laya=True)
    monkeypatch.setattr(resolver, "_sidecar_healthy", AsyncMock(return_value=True))
    assert await resolver.resolve_endpoint(_mock_session()) is None


@pytest.mark.asyncio
async def test_healthy_sidecar_resolves_to_laya(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_stack(monkeypatch)
    monkeypatch.setattr(resolver, "_sidecar_healthy", AsyncMock(return_value=True))
    endpoint = await resolver.resolve_endpoint(_mock_session())
    assert endpoint is not None
    assert endpoint.tier == "laya"
    assert endpoint.api_key is None
    assert endpoint.base_url == cfg.settings.decisions_base_url


@pytest.mark.asyncio
async def test_unhealthy_sidecar_with_opted_in_fallback_and_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_stack(monkeypatch, laya=False, openrouter=True)
    monkeypatch.setattr(
        resolver, "_openrouter_api_key", AsyncMock(return_value="sk-or-key")
    )
    endpoint = await resolver.resolve_endpoint(_mock_session())
    assert endpoint is not None
    assert endpoint.tier == "openrouter"
    assert endpoint.api_key == "sk-or-key"
    assert endpoint.model == "typesafe/jev-1.13"


@pytest.mark.asyncio
async def test_unhealthy_sidecar_no_fallback_resolves_to_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_stack(monkeypatch, laya=False, openrouter=False)
    assert await resolver.resolve_endpoint(_mock_session()) is None


@pytest.mark.asyncio
async def test_opted_in_fallback_missing_key_notifies_ceo_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_stack(monkeypatch, laya=False, openrouter=True)
    monkeypatch.setattr(resolver, "_openrouter_api_key", AsyncMock(return_value=None))
    notify = AsyncMock()
    monkeypatch.setattr(resolver, "_notify_ceo_missing_openrouter_key", notify)

    first = await resolver.resolve_endpoint(_mock_session())
    second = await resolver.resolve_endpoint(_mock_session())
    assert first is None and second is None
    assert notify.await_count == 1  # once per boot, never per call


@pytest.mark.asyncio
async def test_opted_in_fallback_missing_key_falls_back_to_healthy_laya(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_stack(monkeypatch, laya=True, openrouter=True)
    # Sidecar reports healthy, but with NO key the fallback tier must not be
    # selected: the chain prefers Laya, and the missing-key branch only runs
    # when Laya could not serve.
    monkeypatch.setattr(resolver, "_sidecar_healthy", AsyncMock(return_value=True))
    monkeypatch.setattr(resolver, "_openrouter_api_key", AsyncMock(return_value=None))
    notify = AsyncMock()
    monkeypatch.setattr(resolver, "_notify_ceo_missing_openrouter_key", notify)

    endpoint = await resolver.resolve_endpoint(_mock_session())
    assert endpoint is not None and endpoint.tier == "laya"
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_laya_disabled_by_config_falls_to_openrouter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_stack(monkeypatch, laya=False, openrouter=True)
    monkeypatch.setattr(
        resolver, "_openrouter_api_key", AsyncMock(return_value="sk-or-key")
    )
    endpoint = await resolver.resolve_endpoint(_mock_session())
    assert endpoint is not None and endpoint.tier == "openrouter"


@pytest.mark.asyncio
async def test_health_probe_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag_stack(monkeypatch)
    probe = AsyncMock(return_value=True)
    monkeypatch.setattr(resolver, "_sidecar_healthy", probe)
    await resolver.resolve_endpoint(_mock_session())
    await resolver.resolve_endpoint(_mock_session())
    assert probe.await_count == 2  # resolve is per-call, health is cached inside


class TestOpenRouterKeyResolution:
    @pytest.mark.asyncio
    async def test_decrypted_key_returned(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = MagicMock()
        row.id = "00000000-0000-0000-0000-000000000001"
        session = MagicMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = row
        session.execute = AsyncMock(return_value=result)

        provider_svc = MagicMock()
        provider_svc.get_decrypted_token = AsyncMock(return_value="sk-or-live")
        monkeypatch.setattr(
            "roboco.services.provider.get_provider_service",
            lambda s: provider_svc,
        )
        assert await resolver._openrouter_api_key(session) == "sk-or-live"

    @pytest.mark.asyncio
    async def test_no_row_returns_none(self) -> None:
        session = MagicMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result)
        assert await resolver._openrouter_api_key(session) is None


@pytest.mark.asyncio
async def test_missing_key_notification_names_the_fix_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CEO notification is ack-required and names the exact fix (spec 5)."""
    _flag_stack(monkeypatch, laya=False, openrouter=True)
    monkeypatch.setattr(resolver, "_openrouter_api_key", AsyncMock(return_value=None))
    captured = {}

    class _FakeNotificationService:
        def __init__(self) -> None:
            pass

        async def _create_notification(self, params: Any, **kwargs: object) -> None:
            captured["params"] = params

    import roboco.services.notification as notification_module

    monkeypatch.setattr(
        notification_module,
        "NotificationService",
        _FakeNotificationService,
    )
    await resolver._notify_ceo_missing_openrouter_key(_mock_session())
    params = captured["params"]
    assert params.requires_ack is True
    assert params.to_agents == ["ceo"]
    assert "Settings -> AI Providers" in params.body


@pytest.mark.asyncio
async def test_pilot_mode_off_when_master_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock

    from roboco.services.decisions.pilots import PilotMode, pilot_mode

    monkeypatch.setattr(cfg.settings, "decisions_enabled", False)
    monkeypatch.setattr(SettingsService, "get", AsyncMock(return_value="on"))
    assert await pilot_mode(MagicMock(), "self_heal") is PilotMode.OFF


@pytest.mark.asyncio
async def test_pilot_mode_defaults_off_for_unset_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock

    from roboco.services.decisions.pilots import PilotMode, pilot_mode

    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    monkeypatch.setattr(SettingsService, "get", AsyncMock(return_value=None))
    assert await pilot_mode(MagicMock(), "parking") is PilotMode.OFF


@pytest.mark.asyncio
async def test_pilot_mode_reads_shadow_row(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    from roboco.services.decisions.pilots import PilotMode, pilot_mode

    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    monkeypatch.setattr(SettingsService, "get", AsyncMock(return_value="shadow"))
    assert await pilot_mode(MagicMock(), "steer_gate") is PilotMode.SHADOW


@pytest.mark.asyncio
async def test_pilot_mode_reads_tool_spotlight_row_as_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock

    from roboco.services.decisions.pilots import PilotMode, pilot_mode
    from roboco.services.settings import validate_setting

    validate_setting("decisions.pilot.tool_spotlight", "on")
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    monkeypatch.setattr(SettingsService, "get", AsyncMock(return_value="on"))
    assert await pilot_mode(MagicMock(), "tool_spotlight") is PilotMode.ON


@pytest.mark.asyncio
async def test_startup_check_fires_when_opted_in_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_stack(monkeypatch, laya=True, openrouter=True)
    monkeypatch.setattr(resolver, "_openrouter_api_key", AsyncMock(return_value=None))
    notify = AsyncMock()
    monkeypatch.setattr(resolver, "_notify_ceo_missing_openrouter_key", notify)

    await resolver.check_openrouter_fallback_at_startup(_mock_session())
    assert notify.await_count == 1
    # The boot marker is consumed: the lazy per-call path can never
    # double-fire after the startup check has spoken.
    assert resolver._BOOT_STATE["openrouter_key_warning_sent"] is True

    await resolver.check_openrouter_fallback_at_startup(_mock_session())
    assert notify.await_count == 1


@pytest.mark.asyncio
async def test_startup_check_silent_when_key_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_stack(monkeypatch, laya=False, openrouter=True)
    monkeypatch.setattr(
        resolver, "_openrouter_api_key", AsyncMock(return_value="sk-or-key")
    )
    notify = AsyncMock()
    monkeypatch.setattr(resolver, "_notify_ceo_missing_openrouter_key", notify)

    await resolver.check_openrouter_fallback_at_startup(_mock_session())
    notify.assert_not_awaited()
    # No key warning fired, so the marker stays available to the lazy path.
    assert resolver._BOOT_STATE["openrouter_key_warning_sent"] is False


@pytest.mark.asyncio
async def test_startup_check_noop_when_master_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_stack(monkeypatch, enabled=False, openrouter=True)
    key = AsyncMock(return_value=None)
    monkeypatch.setattr(resolver, "_openrouter_api_key", key)

    await resolver.check_openrouter_fallback_at_startup(_mock_session())
    key.assert_not_awaited()


@pytest.mark.asyncio
async def test_startup_check_noop_when_fallback_not_opted_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag_stack(monkeypatch, laya=True, openrouter=False)
    key = AsyncMock(return_value=None)
    monkeypatch.setattr(resolver, "_openrouter_api_key", key)

    await resolver.check_openrouter_fallback_at_startup(_mock_session())
    key.assert_not_awaited()


@pytest.mark.asyncio
async def test_pilot_mode_env_on_list_arms_when_row_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator-deploy arming: an env slug list arms a pilot ON when the
    settings store has no row for it (NAS deploy posture)."""
    from unittest.mock import MagicMock

    from roboco.services.decisions.pilots import PilotMode, pilot_mode

    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    monkeypatch.setattr(cfg.settings, "decisions_pilots_on", "self_heal,parking")
    monkeypatch.setattr(cfg.settings, "decisions_pilots_shadow", "")
    monkeypatch.setattr(SettingsService, "get", AsyncMock(return_value=None))
    assert await pilot_mode(MagicMock(), "self_heal") is PilotMode.ON
    assert await pilot_mode(MagicMock(), "parking") is PilotMode.ON


@pytest.mark.asyncio
async def test_pilot_mode_env_shadow_list_and_unset_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock

    from roboco.services.decisions.pilots import PilotMode, pilot_mode

    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    monkeypatch.setattr(cfg.settings, "decisions_pilots_on", "self_heal")
    monkeypatch.setattr(cfg.settings, "decisions_pilots_shadow", "steer_gate")
    monkeypatch.setattr(SettingsService, "get", AsyncMock(return_value=None))
    assert await pilot_mode(MagicMock(), "steer_gate") is PilotMode.SHADOW
    assert await pilot_mode(MagicMock(), "never_heard_of_it") is PilotMode.OFF


@pytest.mark.asyncio
async def test_pilot_mode_settings_row_beats_env_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The panel row is the finer control: it wins over the env arming."""
    from unittest.mock import MagicMock

    from roboco.services.decisions.pilots import PilotMode, pilot_mode

    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    monkeypatch.setattr(cfg.settings, "decisions_pilots_on", "self_heal")
    monkeypatch.setattr(SettingsService, "get", AsyncMock(return_value="shadow"))
    assert await pilot_mode(MagicMock(), "self_heal") is PilotMode.SHADOW
