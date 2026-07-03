"""The sandboxed per-agent test DB/Redis is gated by a default-off config flag."""

from __future__ import annotations

import os
from unittest import mock

from roboco.config import Settings
from roboco.services.settings import FEATURE_FLAGS, validate_setting


def test_sandbox_db_disabled_by_default() -> None:
    assert Settings().sandbox_db_enabled is False


def test_sandbox_db_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_SANDBOX_DB_ENABLED": "true"}):
        assert Settings().sandbox_db_enabled is True


def test_sandbox_db_flag_registered_in_feature_flags() -> None:
    assert "sandbox_db_enabled" in [key for key, _ in FEATURE_FLAGS]


def test_sandbox_db_flag_validates_as_bool() -> None:
    validate_setting("sandbox_db_enabled", "true")
