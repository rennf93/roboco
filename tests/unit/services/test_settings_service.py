"""Tests for SettingsService + setting validation."""

from __future__ import annotations

from typing import Any

import pytest
from roboco.services.settings import (
    SettingValidationError,
    get_settings_service,
    validate_setting,
)


def test_validate_setting_rejects_unknown_key() -> None:
    with pytest.raises(SettingValidationError):
        validate_setting("not_a_real_setting", "x")


def test_validate_retention_requires_positive_int() -> None:
    validate_setting("transcript_retention_days", "7")  # ok, no raise
    with pytest.raises(SettingValidationError):
        validate_setting("transcript_retention_days", "0")
    with pytest.raises(SettingValidationError):
        validate_setting("transcript_retention_days", "abc")


_DEFAULT_RETENTION = 14
_NEW_RETENTION = 30


@pytest.mark.asyncio
async def test_get_int_returns_default_when_unset(db_session: Any) -> None:
    svc = get_settings_service(db_session)
    assert (
        await svc.get_int("transcript_retention_days", _DEFAULT_RETENTION)
        == _DEFAULT_RETENTION
    )


@pytest.mark.asyncio
async def test_set_then_get_roundtrips(db_session: Any) -> None:
    svc = get_settings_service(db_session)
    await svc.set("transcript_retention_days", str(_NEW_RETENTION))
    assert (
        await svc.get_int("transcript_retention_days", _DEFAULT_RETENTION)
        == _NEW_RETENTION
    )
    assert (await svc.all())["transcript_retention_days"] == str(_NEW_RETENTION)


@pytest.mark.asyncio
async def test_set_rejects_invalid_value(db_session: Any) -> None:
    svc = get_settings_service(db_session)
    with pytest.raises(SettingValidationError):
        await svc.set("transcript_retention_days", "-5")
