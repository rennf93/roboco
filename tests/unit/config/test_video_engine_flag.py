"""The video engine is gated by default-off config flags (mirrors the X engine)."""

from __future__ import annotations

import os
from unittest import mock

from roboco.config import Settings
from roboco.services.settings import FEATURE_FLAGS, validate_setting

_DEFAULT_MAX_OPEN = 5


def test_video_engine_disabled_by_default() -> None:
    s = Settings()
    assert s.video_engine_enabled is False
    assert s.video_on_release is False
    assert s.video_on_spotlight is False
    assert s.video_max_open_posts == _DEFAULT_MAX_OPEN


def test_video_engine_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_VIDEO_ENGINE_ENABLED": "true"}):
        assert Settings().video_engine_enabled is True


def test_video_on_release_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_VIDEO_ON_RELEASE": "true"}):
        assert Settings().video_on_release is True


def test_video_on_spotlight_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_VIDEO_ON_SPOTLIGHT": "true"}):
        assert Settings().video_on_spotlight is True


def test_video_engine_flags_registered_in_feature_flags() -> None:
    keys = [key for key, _ in FEATURE_FLAGS]
    assert "video_engine_enabled" in keys
    assert "video_on_release" in keys
    assert "video_on_spotlight" in keys


def test_video_engine_flag_validates_as_bool() -> None:
    validate_setting("video_engine_enabled", "true")
