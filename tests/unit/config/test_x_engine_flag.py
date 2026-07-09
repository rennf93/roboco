"""The X engine is gated by default-off config flags (mirrors release manager)."""

from __future__ import annotations

import os
from unittest import mock

from roboco.config import Settings
from roboco.services.settings import FEATURE_FLAGS, validate_setting

_DEFAULT_INTERVAL = 1800
_DEFAULT_MAX_OPEN = 10
_DEFAULT_SPOTLIGHT_INTERVAL_SECONDS = 86400  # 1 day


def test_x_engine_disabled_by_default() -> None:
    s = Settings()
    assert s.x_engine_enabled is False
    assert s.x_mentions_interval_seconds == _DEFAULT_INTERVAL
    assert s.x_max_open_posts == _DEFAULT_MAX_OPEN


def test_x_engine_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_X_ENGINE_ENABLED": "true"}):
        assert Settings().x_engine_enabled is True


def test_x_mentions_interval_reads_env_var() -> None:
    override = 900
    with mock.patch.dict(
        os.environ, {"ROBOCO_X_MENTIONS_INTERVAL_SECONDS": str(override)}
    ):
        assert Settings().x_mentions_interval_seconds == override


def test_x_engine_flag_registered_in_feature_flags() -> None:
    assert "x_engine_enabled" in [key for key, _ in FEATURE_FLAGS]


def test_x_engine_flag_validates_as_bool() -> None:
    validate_setting("x_engine_enabled", "true")


def test_x_feature_spotlight_disabled_by_default() -> None:
    assert Settings().x_feature_spotlight_enabled is False


def test_x_feature_spotlight_flag_registered_in_feature_flags() -> None:
    assert "x_feature_spotlight_enabled" in [key for key, _ in FEATURE_FLAGS]


def test_x_feature_spotlight_interval_defaults_to_one_day() -> None:
    """CEO directive: default cadence is 1 day (was 3 days) — the engine's
    own smart-cadence guard stretches this to 3x when quiet instead of the
    interval itself defaulting slower."""
    assert (
        Settings().x_feature_spotlight_interval_seconds
        == _DEFAULT_SPOTLIGHT_INTERVAL_SECONDS
    )
