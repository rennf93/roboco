"""Panel-tunable feature flags — validation, bool read, and startup overlay (#9).

Flags persist in system_settings as 'true'/'false' and are overlaid onto the
config singleton at startup; an unset flag keeps its env/config default.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.config import settings as cfg
from roboco.services import settings as settings_mod
from roboco.services.settings import (
    SettingsService,
    SettingValidationError,
    validate_setting,
)


def test_feature_flags_are_writable_as_bool() -> None:
    validate_setting("external_pr_enabled", "true")
    validate_setting("research_enabled", "FALSE")  # case-insensitive
    validate_setting("internal_pr_enabled", "  true  ")  # whitespace tolerated


def test_non_bool_value_rejected() -> None:
    with pytest.raises(SettingValidationError):
        validate_setting("external_pr_enabled", "yes")


def test_unknown_key_rejected() -> None:
    with pytest.raises(SettingValidationError):
        validate_setting("not_a_real_flag", "true")


@pytest.mark.asyncio
async def test_get_bool_parses_and_defaults() -> None:
    svc = SettingsService(MagicMock())
    object.__setattr__(svc, "get", AsyncMock(return_value="true"))
    assert await svc.get_bool("k", default=False) is True
    object.__setattr__(svc, "get", AsyncMock(return_value="false"))
    assert await svc.get_bool("k", default=True) is False
    object.__setattr__(svc, "get", AsyncMock(return_value=None))
    assert await svc.get_bool("k", default=True) is True  # unset → default


@pytest.mark.asyncio
async def test_apply_overrides_stored_flags_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Baseline env defaults.
    monkeypatch.setattr(cfg, "external_pr_enabled", False)
    monkeypatch.setattr(cfg, "research_enabled", True)

    # Only external_pr_enabled has a stored override; research_enabled is unset.
    stored = {"external_pr_enabled": "true"}

    async def fake_get(_self: SettingsService, key: str) -> str | None:
        return stored.get(key)

    monkeypatch.setattr(SettingsService, "get", fake_get)
    applied = await settings_mod.apply_persisted_feature_flags(MagicMock())

    assert "external_pr_enabled" in applied
    assert cfg.external_pr_enabled is True  # stored override applied
    assert cfg.research_enabled is True  # unset → env default untouched
    assert "research_enabled" not in applied


@pytest.mark.asyncio
async def test_effective_values_use_env_default_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "strategy_engine_enabled", True)

    async def fake_get(_self: SettingsService, _key: str) -> str | None:
        return None  # nothing stored

    monkeypatch.setattr(SettingsService, "get", fake_get)
    effective = await settings_mod.feature_flag_effective_values(MagicMock())
    assert effective["strategy_engine_enabled"] is True  # falls back to env
