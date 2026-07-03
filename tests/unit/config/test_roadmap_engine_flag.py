"""The board roadmap engine is gated by default-off config flags (mirrors
release manager / X engine)."""

from __future__ import annotations

import os
from unittest import mock

from roboco.config import Settings
from roboco.services.settings import FEATURE_FLAGS, validate_setting

_DEFAULT_INTERVAL = 604800
_DEFAULT_MIN_ITEMS = 3
_DEFAULT_MAX_ITEMS = 7


def test_roadmap_engine_disabled_by_default() -> None:
    s = Settings()
    assert s.roadmap_engine_enabled is False
    assert s.roadmap_interval_seconds == _DEFAULT_INTERVAL
    assert s.roadmap_min_items_per_cycle == _DEFAULT_MIN_ITEMS
    assert s.roadmap_max_items_per_cycle == _DEFAULT_MAX_ITEMS


def test_roadmap_engine_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_ROADMAP_ENGINE_ENABLED": "true"}):
        assert Settings().roadmap_engine_enabled is True


def test_roadmap_interval_reads_env_var() -> None:
    override = 3600
    with mock.patch.dict(
        os.environ, {"ROBOCO_ROADMAP_INTERVAL_SECONDS": str(override)}
    ):
        assert Settings().roadmap_interval_seconds == override


def test_roadmap_engine_flag_registered_in_feature_flags() -> None:
    assert "roadmap_engine_enabled" in [key for key, _ in FEATURE_FLAGS]


def test_roadmap_engine_flag_validates_as_bool() -> None:
    validate_setting("roadmap_engine_enabled", "true")
