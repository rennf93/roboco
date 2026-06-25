"""Multi-repo CI-watch is gated by default-off config flags (mirrors self-heal)."""

from __future__ import annotations

import os
from unittest import mock

from roboco.config import Settings
from roboco.services.settings import FEATURE_FLAGS, validate_setting

_DEFAULT_INTERVAL = 1800
_DEFAULT_MAX_OPEN = 3
_DEFAULT_MAX_PER_CYCLE = 1


def test_ci_watch_disabled_by_default() -> None:
    s = Settings()
    assert s.ci_watch_enabled is False
    assert s.ci_watch_interval_seconds == _DEFAULT_INTERVAL
    assert s.ci_watch_max_open_tasks == _DEFAULT_MAX_OPEN
    assert s.ci_watch_max_per_cycle == _DEFAULT_MAX_PER_CYCLE
    assert s.ci_watch_default_workflow == "ci.yml"


def test_ci_watch_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_CI_WATCH_ENABLED": "true"}):
        assert Settings().ci_watch_enabled is True


def test_ci_watch_flag_registered_in_feature_flags() -> None:
    assert "ci_watch_enabled" in [key for key, _ in FEATURE_FLAGS]


def test_ci_watch_flag_validates_as_bool() -> None:
    validate_setting("ci_watch_enabled", "true")
