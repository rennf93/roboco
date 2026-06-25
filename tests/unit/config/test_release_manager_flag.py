"""Gated release manager is gated by default-off config flags (mirrors self-heal)."""

from __future__ import annotations

import os
from unittest import mock

from roboco.config import Settings
from roboco.services.settings import FEATURE_FLAGS, validate_setting

_DEFAULT_MIN_COMMITS = 8


def test_release_manager_disabled_by_default() -> None:
    s = Settings()
    assert s.release_manager_enabled is False
    assert s.release_min_commits == _DEFAULT_MIN_COMMITS


def test_release_manager_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_RELEASE_MANAGER_ENABLED": "true"}):
        assert Settings().release_manager_enabled is True


def test_release_min_commits_reads_env_var() -> None:
    override = 12
    with mock.patch.dict(os.environ, {"ROBOCO_RELEASE_MIN_COMMITS": str(override)}):
        assert Settings().release_min_commits == override


def test_release_manager_flag_registered_in_feature_flags() -> None:
    assert "release_manager_enabled" in [key for key, _ in FEATURE_FLAGS]


def test_release_manager_flag_validates_as_bool() -> None:
    validate_setting("release_manager_enabled", "true")
