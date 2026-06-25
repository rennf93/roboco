"""The dependency-update bot is gated by default-off config flags."""

from __future__ import annotations

import os
from unittest import mock

from roboco.config import Settings
from roboco.services.settings import FEATURE_FLAGS, validate_setting

_DEFAULT_INTERVAL = 604800
_DEFAULT_MAX_OPEN = 3
_DEFAULT_MAX_PER_CYCLE = 1


def test_dep_update_disabled_by_default() -> None:
    s = Settings()
    assert s.dep_update_enabled is False
    assert s.dep_update_interval_seconds == _DEFAULT_INTERVAL
    assert s.dep_update_max_open_tasks == _DEFAULT_MAX_OPEN
    assert s.dep_update_max_per_cycle == _DEFAULT_MAX_PER_CYCLE


def test_dep_update_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_DEP_UPDATE_ENABLED": "true"}):
        assert Settings().dep_update_enabled is True


def test_dep_update_flag_registered_in_feature_flags() -> None:
    assert "dep_update_enabled" in [key for key, _ in FEATURE_FLAGS]


def test_dep_update_flag_validates_as_bool() -> None:
    validate_setting("dep_update_enabled", "true")
