"""Telegram inbound (V2) is gated by default-off config flags (mirrors the
x_feature_spotlight sub-switch pattern)."""

from __future__ import annotations

import os
from unittest import mock

import pytest
from roboco.config import Settings
from roboco.services.settings import (
    FEATURE_FLAGS,
    SettingValidationError,
    validate_setting,
)

_DEFAULT_POLL_INTERVAL_SECONDS = 5.0
_DEFAULT_POLL_TIMEOUT_SECONDS = 25
_DEFAULT_MAX_UPDATES_PER_CYCLE = 50
_OVERRIDE_POLL_INTERVAL_SECONDS = 10.0


def test_telegram_inbound_disabled_by_default() -> None:
    s = Settings()
    assert s.telegram_inbound_enabled is False
    assert s.telegram_poll_interval_seconds == _DEFAULT_POLL_INTERVAL_SECONDS
    assert s.telegram_poll_timeout_seconds == _DEFAULT_POLL_TIMEOUT_SECONDS
    assert s.telegram_max_updates_per_cycle == _DEFAULT_MAX_UPDATES_PER_CYCLE


def test_telegram_inbound_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_TELEGRAM_INBOUND_ENABLED": "true"}):
        assert Settings().telegram_inbound_enabled is True


def test_telegram_poll_interval_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_TELEGRAM_POLL_INTERVAL_SECONDS": "10"}):
        assert (
            Settings().telegram_poll_interval_seconds == _OVERRIDE_POLL_INTERVAL_SECONDS
        )


def test_telegram_inbound_flag_registered_in_feature_flags() -> None:
    assert "telegram_inbound_enabled" in [key for key, _ in FEATURE_FLAGS]


def test_telegram_inbound_flag_validates_as_bool() -> None:
    validate_setting("telegram_inbound_enabled", "true")


def test_telegram_last_update_id_validates_as_int() -> None:
    validate_setting("telegram_last_update_id", "12345")


def test_telegram_last_update_id_rejects_non_int() -> None:
    with pytest.raises(SettingValidationError):
        validate_setting("telegram_last_update_id", "not-a-number")


def test_telegram_last_update_id_rejects_negative() -> None:
    with pytest.raises(SettingValidationError):
        validate_setting("telegram_last_update_id", "-1")


def test_telegram_last_update_id_not_a_feature_flag() -> None:
    """It's an internal cursor, not a panel-tunable master switch."""
    assert "telegram_last_update_id" not in [key for key, _ in FEATURE_FLAGS]
