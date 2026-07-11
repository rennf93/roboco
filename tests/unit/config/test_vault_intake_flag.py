"""Vault intake watcher — gated by default-off config flags (mirrors the
roadmap engine / X engine idiom)."""

from __future__ import annotations

import os
from unittest import mock

from roboco.config import Settings
from roboco.services.settings import FEATURE_FLAGS, validate_setting

_DEFAULT_INTERVAL = 300
_DEFAULT_MAX_PER_CYCLE = 3
_DEFAULT_MAX_OPEN_DRAFTS = 10


def test_vault_intake_disabled_by_default() -> None:
    s = Settings()
    assert s.vault_intake_enabled is False
    assert s.vault_intake_interval_seconds == _DEFAULT_INTERVAL
    assert s.vault_intake_dir == "RoboCo/Inbox"
    assert s.vault_intake_max_per_cycle == _DEFAULT_MAX_PER_CYCLE
    assert s.vault_intake_max_open_drafts == _DEFAULT_MAX_OPEN_DRAFTS


def test_vault_intake_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_VAULT_INTAKE_ENABLED": "true"}):
        assert Settings().vault_intake_enabled is True


def test_vault_intake_dir_reads_env_var() -> None:
    with mock.patch.dict(os.environ, {"ROBOCO_VAULT_INTAKE_DIR": "Foo/Bar"}):
        assert Settings().vault_intake_dir == "Foo/Bar"


def test_vault_intake_flag_registered_in_feature_flags() -> None:
    assert "vault_intake_enabled" in [key for key, _ in FEATURE_FLAGS]


def test_vault_intake_flag_validates_as_bool() -> None:
    validate_setting("vault_intake_enabled", "true")
